"""Build independent, disabled-by-default PP1 local-parameter experiments offline."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from emulate_lut_parameter_helpers import AP_SHA256, check_hash

NATIVE = Path(__file__).resolve().parent / 'native'
ABI = 6
CAPACITY, EVENT_BYTES, TRACE_HEADER_BYTES = 1024, 320, 512
TRACE_BYTES = TRACE_HEADER_BYTES + CAPACITY * EVENT_BYTES
RESPONSE_BYTES = 144 + TRACE_BYTES + 128
ACTIONS = {'snapshot': 1, 'install-experiment': 2, 'read-trace': 3, 'stop-experiment': 4,
           'pause-trace': 5, 'resume-trace': 6, 'mark-trace': 7, 'enable-adapter': 8, 'disable-adapter': 9}
HELPERS = (0x3216168, 0x3215fc0, 0x3440aec, 0x176a090, 0x34d1db0, 0x3210adc,
           0x34e93b0, 0x34e934c, 0x34e9630)
CALLSITES = (0x334e224, 0x334e330, 0x3440d50, 0x34d22f4, 0x34d1ef8, 0x3288f74,
             0x34f3cfc, 0x34f3d2c, 0x34f3d60, 0x34f466c, 0x34f4780, 0x34f480c)
SITE_KINDS = (0, 1, 2, 3, 4, 5, 7, 6, 8, 8, 6, 7)
VENEER_BYTES = 16 * len(HELPERS)
SOURCE_NAMES = ('photo_lut_override_context_adapter.c', 'photo_lut_override_entry.S',
                'photo_lut_override_module.c', 'photo_lut_override_trace.c',
                'photo_lut_override_trace.h', 'photo_lut_override_trace.ld')
SITES=tuple(zip(CALLSITES,SITE_KINDS))
TRACE_SYMBOLS=('plut_trace_eligibility','plut_trace_resolver','plut_trace_send',
               'plut_trace_submit','plut_trace_callback_lookup','plut_trace_release',
               'plut_trace_base','plut_trace_outputs','plut_trace_data')
ACCESSORS=(0x2f77a54,0x2f929b8,0x2f929e0,0x2f92a08,0x2f92a30)


def sha(b): return hashlib.sha256(b).hexdigest()


def arrays(data): return ','.join(str(v) for v in data)


def validate_elf(path, shared=False):
    elf=ELFFile(io.BytesIO(path.read_bytes()))
    if elf['e_machine']!='EM_AARCH64' or elf['e_type']!=('ET_DYN' if shared else 'ET_EXEC'):
        raise ValueError('Unexpected ELF architecture/type')
    dyn=elf.get_section_by_name('.dynsym')
    if dyn:
        undefined=[s.name for s in dyn.iter_symbols() if s.name and s['st_shndx']=='SHN_UNDEF']
        if undefined:raise ValueError(f'Unexpected dynamic imports: {undefined}')
    dynamic=elf.get_section_by_name('.dynamic')
    if dynamic and any(tag.entry['d_tag']=='DT_NEEDED' for tag in dynamic.iter_tags()):
        raise ValueError('Service module must not depend on a target libc/DSO')
    for section in elf.iter_sections():
        if section['sh_type'] in ('SHT_REL','SHT_RELA'):
            for rel in section.iter_relocations():
                if rel['r_info_type']!=1027:raise ValueError('Only AArch64 RELATIVE relocations allowed')
    return elf


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('extracted_root',type=Path)
    p.add_argument('--compiler',required=True,type=Path)
    p.add_argument('--out-dir',required=True,type=Path)
    p.add_argument('--capture-loader', action='store_true', help='Build the final 0.3.1 AP source overlay')
    args=p.parse_args()
    if args.out_dir.exists():p.error('Output directory exists')
    compiler=args.compiler.resolve(strict=True)
    if subprocess.check_output([str(compiler),'version'],text=True).strip()!='0.13.0':
        p.error('Pinned compiler is Zig 0.13.0')
    version = '0.3.1' if args.capture_loader else '0.3.0'
    sources = {name: NATIVE / 'ap-override-0.3.0' / name for name in SOURCE_NAMES}
    if args.capture_loader:
        sources.update({path.name: path for path in (NATIVE / 'ap-capture-loader-0.3.1').iterdir()
                        if path.name in sources})
    ap=(args.extracted_root/'files/nflasha15/lib/appFw.so').read_bytes();check_hash(ap,AP_SHA256)
    elf=ELFFile(io.BytesIO(ap))
    executable=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_flags']&1)
    executable_end=executable['p_vaddr']+executable['p_memsz']
    veneer_va=(executable_end+15)&~15
    veneer_size=VENEER_BYTES
    if veneer_va != 0x4488ef0:raise ValueError('Unexpected fixed padding address')
    if veneer_va+veneer_size>(executable_end+4095)&~4095:
        raise ValueError('No complete 144-byte text-tail padding within the existing page')
    if any(s['p_type']=='PT_LOAD' and s['p_vaddr']<veneer_va+veneer_size and
           s['p_vaddr']+s['p_memsz']>veneer_va for s in elf.iter_segments()):
        raise ValueError('Veneer padding overlaps declared LOAD contents')
    if any(s['sh_flags']&2 and s['sh_addr']<veneer_va+veneer_size and
           s['sh_addr']+s['sh_size']>veneer_va for s in elf.iter_sections()):
        raise ValueError('Veneer padding overlaps allocated section contents')
    padding_offset=executable['p_offset']+veneer_va-executable['p_vaddr']
    veneer_original=ap[padding_offset:padding_offset+veneer_size]
    if veneer_original!=bytes(veneer_size):raise ValueError('Fixed padding is not zero-filled')
    def read(address,size):
        if veneer_va<=address and address+size<=veneer_va+veneer_size:
            return veneer_original[address-veneer_va:address-veneer_va+size]
        segment=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and
                     s['p_vaddr']<=address and address+size<=s['p_vaddr']+s['p_filesz'])
        offset=segment['p_offset']+address-segment['p_vaddr']
        return ap[offset:offset+size]
    sites=[]
    for address,kind in SITES:
        word=struct.unpack('<I',read(address,4))[0]
        delta=word&0x3ffffff
        if delta&0x2000000:delta-=0x4000000
        if word>>26!=0b100101 or address+delta*4!=HELPERS[kind]:
            raise ValueError(f'Callsite mismatch: {address:x}')
        sites.append({'va':address,'kind':kind,'word':word})
    # Decode fixed getter returns instead of assuming model storage layout.
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN
    dis=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);fields=[]
    def getter_address(address):
        instructions=list(dis.disasm(read(address,20),address))
        names=[i.mnemonic for i in instructions]
        if names==['adrp','mov','add','add','ret'] and instructions[3].op_str=='x0, x0, x1':
            if not instructions[1].op_str.startswith('x1, #'):
                raise ValueError('Unsupported getter register constant')
            return sum(int(instructions[i].op_str.split('#')[1],0) for i in (0,1,2))
        if names!=['adrp','add','add','add','ret']:
            raise ValueError(f'Getter mismatch {address:x}')
        total=0
        for i in instructions[:4]:
            immediate=int(i.op_str.split('#')[1].split(',')[0],0)
            total+=immediate*(4096 if 'lsl #12' in i.op_str else 1)
        return total
    for identifier,address in zip((0,0x1577,0x1578,0x1579,0x157a),ACCESSORS):
        fields.append({'id':identifier,'va':getter_address(address),'getter':address})
    named_getters=(('general_mode',0x2f88d28),('eligibility_context',0x2f6f630),
        ('pp',0x2f77a54),('log',0x2f75aa0),('direct',0x2f77a7c),
        ('cinematic_mode',0x2f770cc),('cinematic_look',0x2f7b974),
        ('pplut1',0x2f929b8),('pplut2',0x2f929e0),('pplut3',0x2f92a08),
        ('pplut4',0x2f92a30),('slots',0x2f928c8))
    adapter_fields=[dict(name=name,getter=address,va=getter_address(address)) for name,address in named_getters]
    guards=[]
    windows={(s['va']-16,32) for s in sites}|{(a,32) for a in ACCESSORS}
    windows.update((a,min(64,veneer_va+veneer_size-a)) for a in range(veneer_va,veneer_va+veneer_size,64))
    windows.update((address,20) for _,address in named_getters)
    for site in sites:
        delta=veneer_va+site['kind']*16-site['va']
        if delta&3 or not -(1<<27)<=delta<1<<27:raise ValueError('Fixed padding veneer is not BL reachable')
    # Full observed helper windows and the transaction/callback request layout.
    # SHA-256 uses bounded windows: the 144-byte padding is split 64+64+16.
    extra_windows=((0x334e1a4,0x240),(0x34d1ec4,0x140),(0x34d2190,0x180),
                   (0x40759dc,0x20),(0x32862fc,12),(0x34f3ce0,0x50),(0x40b6100,4))
    for start,length in list(zip(HELPERS,(0x50,0x1a8,0x12c,0x10,0x38,0x94,0x1b4,0x64,0x44)))+list(extra_windows):
        windows.update((a,min(64,start+length-a)) for a in range(start,start+length,64))
    for address,size in sorted(windows):
        if not 0<size<=64:raise ValueError('Guard exceeds module scratch bound')
        guards.append({'va':address,'size':size,'sha256':sha(read(address,size))})
    args.out_dir.mkdir(parents=True,exist_ok=False)
    args.out_dir=args.out_dir.resolve()
    commands=[]
    with tempfile.TemporaryDirectory(prefix='plut-module-') as tmp:
        work=Path(tmp)
        for name, source in sources.items():
            shutil.copyfile(source, work / name)
        common=[str(compiler),'cc','-target','aarch64-freestanding-none','-O2',
            '-mcpu=generic','-mno-outline-atomics','-mgeneral-regs-only','-mstrict-align',
            '-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
            '-fno-asynchronous-unwind-tables','-nostdlib','-Wall','-Wextra','-Werror']
        def run(extra):
            command=common+extra;commands.append(command)
            subprocess.run(command,cwd=work,check=True)
        run(['-fpie','-no-pie','-Wl,--no-gc-sections',*[f'-Wl,-u,{name}' for name in TRACE_SYMBOLS],
             '-Wl,-u,plut_trace_control',
             '-Wl,-T,photo_lut_override_trace.ld','-Wl,-e,plut_trace_eligibility','-Wl,--build-id=none',
             '-Wl,-z,noexecstack','photo_lut_override_trace.c','photo_lut_override_entry.S',
             'photo_lut_override_context_adapter.c','-o','trace.elf'])
        trace=validate_elf(work/'trace.elf');symbols=trace.get_section_by_name('.symtab')
        names={s.name:s['st_value'] for s in symbols.iter_symbols()}
        if names['plut_locator']!=0x4000:raise ValueError('Trace locator layout mismatch')
        if names['bridge_config']!=0x4050 or names['plut_pp1_runtime']!=0x4090:
            raise ValueError('PP1 resident state layout mismatch')
        code=bytearray()
        for segment in trace.iter_segments():
            if segment['p_type']!='PT_LOAD' or not segment['p_filesz']:continue
            end=segment['p_vaddr']+segment['p_filesz']
            if end>0x4000:raise ValueError('Trace file data exceeds code window')
            if len(code)<end:code.extend(bytes(end-len(code)))
            code[segment['p_vaddr']:end]=segment.data()
        profile='/* Generated fixed official 2.00 callsite profile. */\n'
        profile+='static const u8 trace_code[]={'+arrays(code)+'};\n'
        profile+='static const u64 trace_offsets[]={'+','.join(str(names[n]) for n in TRACE_SYMBOLS)+'};\n'
        profile+='static const u64 trace_control_offset='+str(names['plut_trace_control'])+';\n'
        profile+='static const u64 bridge_config_offset='+str(names['bridge_config'])+';\n'
        profile+='static const u64 pp1_runtime_offset='+str(names['plut_pp1_runtime'])+';\n'
        profile+='struct Getter {u64 getter,va;};\nstatic const struct Getter model_getters[]={'+','.join('{%d,%d}'%(f['getter'],f['va']) for f in adapter_fields[:2])+'};\n'
        profile+='static const u64 veneer_base='+str(veneer_va)+';\n'
        profile+='static const u8 veneer_original[]={'+arrays(veneer_original)+'};\n'
        profile+='struct Site {u64 va;u32 kind,word;};\nstatic const struct Site sites[]={'+','.join('{%d,%d,%d}'%(s['va'],s['kind'],s['word']) for s in sites)+'};\n'
        profile+='struct Field {u32 id;u64 va;};\nstatic const struct Field fields[]={'+','.join('{%d,%d}'%(f['id'],f['va']) for f in fields)+'};\n'
        profile+='static const u64 helpers[]={'+','.join(str(h) for h in HELPERS)+'};\n'
        profile+='struct Guard {u64 va;u32 size;u8 digest[32];};\nstatic const struct Guard guards[]={'+','.join('{%d,%d,{%s}}'%(g['va'],g['size'],arrays(bytes.fromhex(g['sha256']))) for g in guards)+'};\n'
        (work/'photo_lut_override_profile.h').write_text(profile,encoding='ascii')
        for name,action in ACTIONS.items():
            run(['-target','aarch64-linux-musl','-fPIC','-shared','-Wl,-Bsymbolic','-Wl,-z,defs','-Wl,-z,noexecstack',
                '-Wl,--build-id=none',f'-DPLUT_ACTION={action}','photo_lut_override_module.c','-o',name+'.elf'])
            validate_elf(work/(name+'.elf'),shared=True)
            shutil.copyfile(work/(name+'.elf'),args.out_dir/(name+'.elf'))
        for name in ('trace.elf','photo_lut_override_profile.h'):
            shutil.copyfile(work/name,args.out_dir/name)
    manifest={'schema':'a6700-lab/photo-lut-parameter-override-experiment/1','version':version,'abi':ABI,'model':'ILCE-6700','firmware':'2.00',
        'ap_reference_sha256':AP_SHA256,'purpose':'Offline build of the historical photo PP1 AP research module',
        'camera_tested':False,'observer_only':False,'parameter_adapter':True,'adapter_default_enabled':False,
        'parameter_observation_only':False,'imaging_output_override':not args.capture_loader,
        'parameter_override_sites':[0x34f4780] if args.capture_loader else [0x34f4780,0x34f480c],
        'parameter_override_slots':[17,18],
        'cp_readiness_gate_implemented':False,'buffer_lease_verified':False,
        'native_order':'outputs-before-upload-descriptor-base','main_policy':'preserve-native-input',
        'helper_vas':list(HELPERS),'site_kind_map':list(SITE_KINDS),
        'request_bytes':48,'response_bytes':RESPONSE_BYTES,
        'trace_bytes':TRACE_BYTES,'event_bytes':EVENT_BYTES,'record_limit':CAPACITY,'resident_mapping_bytes':0x56000,
        'resident_layout':'fixed-padding-veneers-split-data/1','resident_code_mapping_bytes':0x5000,
        'resident_data_mapping_bytes':0x51000,
        'veneer_padding':{'va':veneer_va,'bytes':veneer_size,'original_sha256':sha(veneer_original),
            'executable_pt_load_end':executable_end,'allocated_section_overlap':False,
            'private_process_memory_only':True,'retained_inert_until_process_exit':True},
        'install_capture':'paused','capture_reuses_slots':False,
        'trace_locator_offset':0x4000,'sites':sites,'fields':fields,'guards':guards,
        'adapter_fields':adapter_fields,'bridge_config_offset':0x4050,'pp1_runtime_offset':0x4090,
        'diagnostic_bytes':128,'frame_record':{'offset':0x58,'bytes':16,'frame_bytes':0x70,
            'object_register':'x19','frame_equals_sp_required':True},
        'trace_offsets':{n:names[n] for n in TRACE_SYMBOLS+('plut_trace_control',)},
        'sources':{name:sha(source.read_bytes()) for name, source in sources.items()},
        'build_script_sha256':sha(Path(__file__).read_bytes()),
        'compiler_sha256':sha(compiler.read_bytes()),
        'compiler_version':subprocess.check_output([str(compiler),'version'],text=True).strip(),
        'payloads':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in args.out_dir.glob('*.elf')},
        'profile_sha256':sha((args.out_dir/'photo_lut_override_profile.h').read_bytes()),
        'commands':commands}
    (args.out_dir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Built offline AP objects:', {n:r['bytes'] for n,r in manifest['payloads'].items()})
    print('Fixed model addresses:',[(hex(f['id']),hex(f['va'])) for f in fields])


if __name__=='__main__':main()
