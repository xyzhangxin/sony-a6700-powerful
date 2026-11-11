"""Build the fixed FL23 block/file transactions from a reviewed recipe.

No device access. Recipe contains field deltas and guard hashes, not sector
arrays or a maintainer backup. Existing four-feature payloads stay unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sha=lambda data:hashlib.sha256(data).hexdigest()


def array(data):return '{'+','.join(map(str,data))+'}'


def mask(offsets):
    raw=bytearray(64)
    for at in offsets:
        if not 0<=at<512:raise ValueError('Mask outside sector')
        raw[at//8]|=1<<(at%8)
    return bytes(raw)


def header(recipe):
    entries=recipe['entries'];rows=[];hashes=[];boot=[]
    for i,row in enumerate(entries):
        owned=[e['offset']+j for e in row['edits'] for j in range(len(bytes.fromhex(e['disabled'])))]
        if row['index']!=i or row['device'] not in (0,1,2) or row['device_offset']%512 or len(owned)!=len(set(owned)):
            raise ValueError('Invalid FL23 fixed entry')
        rows.append('{%dUL,%d,%s}'%(row['device_offset'],row['device'],array(mask(owned))))
        hashes.append(array(bytes.fromhex(row['unmanaged_sha256'])))
        if row.get('boot_entry'):
            edits=row['edits']
            if len(edits)!=1 or len(bytes.fromhex(edits[0]['disabled']))!=4 or row['device'] not in (0,1):
                raise ValueError('Boot activation field is not one fixed instruction')
            boot.append('{%du,%du,%s}'%(i,edits[0]['offset'],array(bytes.fromhex(edits[0]['disabled']))))
    if boot and (len(boot)!=2 or not all(r.get('boot_entry') for r in entries[-2:])):
        raise ValueError('AP/WBI boot entries must commit last')
    guards=[]
    for row in recipe['guards']:
        if row['device'] not in (0,1,2) or row['device_offset']%512:raise ValueError('Invalid FL23 guard')
        guards.append('{%dUL,%d,%s,%s}'%(row['device_offset'],row['device'],array(mask(row['ignored_offsets'])),array(bytes.fromhex(row['sha256']))))
    return ('struct Entry {unsigned long offset;unsigned char device;unsigned char mask[64];};\n'+
        f'#define ENTRY_COUNT {len(entries)}u\n'+
        'static const unsigned char PROFILE_DIGEST[32]='+array(bytes.fromhex(recipe['profile_sha256']))+';\n'+
        'static const struct Entry ENTRIES[ENTRY_COUNT]={'+','.join(rows)+'};\n'+
        'static const unsigned char TARGET_HASHES[ENTRY_COUNT][32]={'+','.join(hashes)+'};\n'+
        'struct Guard {unsigned long offset;unsigned char device,ignore[64],digest[32];};\n'+
        'static const struct Guard EXTRA_GUARDS[]={'+','.join(guards)+'};\n'+
        f'#define BOOT_COUNT {len(boot)}u\nstruct BootEntry {{unsigned index,offset;unsigned char disabled[4];}};\n'+
        'static const struct BootEntry BOOT_ENTRIES[]={'+','.join(boot)+'};\n').encode()


GUARDS='''
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
#include "sha256.h"
static int guard_hash(unsigned char *buf,const unsigned char *mask,const unsigned char *expected){
 unsigned char local[512],digest[32];
 for(unsigned i=0;i<512;i++)local[i]=mask[i/8]&(1u<<(i%8))?0:buf[i];
 SHA256 h;sha_init(&h);sha_add(&h,local,512);sha_end(&h,digest);
 return same(digest,expected,32);
}
static unsigned char boot_before[BOOT_COUNT ? BOOT_COUNT : 1][512];
static int current_preimage(unsigned i,const unsigned char*buf,const unsigned char*before){
 for(unsigned n=0;n<BOOT_COUNT;n++)if(BOOT_ENTRIES[n].index==i&&touched[i])return same(buf,boot_before[n],512);
 return same(buf,before,512);
}
'''
PRECHECK='''
    for(unsigned i=0;i<sizeof(EXTRA_GUARDS)/sizeof(EXTRA_GUARDS[0]);i++){
        const struct Guard *g=&EXTRA_GUARDS[i];
        if(!io(SYS_PREAD64,fds[g->device],buf,512,g->offset)){status=4;failed=ENTRY_COUNT+i;goto done;}
        if(!guard_hash(buf,g->ignore,g->digest)){status=1;failed=ENTRY_COUNT+i;goto done;}
    }
'''
DISABLE='''
    if(BUILD_WRITER)for(unsigned n=0;n<BOOT_COUNT;n++){
        const struct BootEntry *b=&BOOT_ENTRIES[n];unsigned i=b->index;
        const struct Entry *e=&ENTRIES[i];const unsigned char *before=input+HEADER+i*1024u;
        for(unsigned j=0;j<512;j++)boot_before[n][j]=before[j];
        for(unsigned j=0;j<4;j++)boot_before[n][b->offset+j]=b->disabled[j];
        if(same(before,boot_before[n],512))continue;
        if(!io(SYS_PREAD64,fds[e->device],buf,512,e->offset)){status=4;failed=i;goto rollback;}
        if(!same(buf,before,512)){status=1;failed=i;goto rollback;}
        touched[i]=1;written++;
        if(!io(SYS_PWRITE64,fds[e->device],boot_before[n],512,e->offset)){status=5;failed=i;goto rollback;}
        if(!sync_fd(fds[e->device])){status=6;failed=i;goto rollback;}
        if(!io(SYS_PREAD64,fds[e->device],buf,512,e->offset)||!same(buf,boot_before[n],512)){status=7;failed=i;goto rollback;}
    }
'''


def compile(source,destination,work,name,*,defines=()):
    obj=work/(name+'.o');out=work/(name+'.elf')
    clang=shutil.which('clang')
    if not clang:raise RuntimeError('clang is required to build FL23 transactions')
    subprocess.run([clang,'-target','aarch64-none-elf','-fPIC','-O2','-ffreestanding',
        '-fno-builtin','-fno-stack-protector','-mgeneral-regs-only','-mno-outline-atomics','-I',str(work),
        *defines,'-c',str(source),'-o',str(obj)],check=True)
    linker = shutil.which('aarch64-elf-ld') or shutil.which('ld.lld')
    if not linker:
        raise RuntimeError('aarch64 linker is required to build FL23 transactions')
    subprocess.run([linker,'-shared','-Bsymbolic','-z','noexecstack',
        '--build-id=none','-o',str(out),str(obj)],check=True)
    data=out.read_bytes()
    if len(data)>1048572:raise ValueError('FL23 transaction exceeds USB bound')
    (destination/(name+'.elf')).write_bytes(data)
    return dict(bytes=len(data),sha256=sha(data))


def build(recipe_path,carrier_path,sha_header,*,destination=None,variants_path=None,legacy_path=None):
    destination=destination or ROOT/'src/a6700_unlock/data/fl23'
    destination.mkdir(parents=True,exist_ok=True)
    recipe=json.loads(Path(recipe_path).read_bytes())
    bound=dict(recipe);digest=bound.pop('profile_sha256')
    if (sha(json.dumps(bound,sort_keys=True,separators=(',',':')).encode())!=digest or
        recipe.get('feature')!='fl23' or recipe.get('names') is not False or len(recipe['entries'])>64):
        raise ValueError('Invalid reviewed FL23 recipe')
    carrier=Path(carrier_path).read_bytes()
    if not 1024<=len(carrier)<=2098176:raise ValueError('FL23 file carrier extent differs')
    source=ROOT/'src/a6700_unlock/native/transaction.c';text=source.read_text()
    boundary='__attribute__((constructor)) static void run(void) {'
    marker='    /* First pass validates every target AND every read-only layout guard. */'
    guard='        if(!BUILD_WRITER) {'
    if any(text.count(m)!=1 for m in (boundary,marker,guard)):raise ValueError('Base transaction integration boundary changed')
    text=text.replace(boundary,GUARDS+'\n'+boundary).replace(marker,PRECHECK+'\n'+marker)
    text=text.replace(guard,'        if(!guard_hash(buf,e->mask,TARGET_HASHES[i])){status=1;failed=i;goto done;}\n'+guard)
    marker='    if(BUILD_WRITER) for(unsigned i=0;i<ENTRY_COUNT;i++) {'
    if text.count(marker)!=1:raise ValueError('Native startup commit boundary changed')
    text=text.replace(marker,DISABLE+'\n'+marker)
    text=text.replace('if(same(before,after,512)) continue;','if(same(before,after,512)&&!touched[i]) continue;')
    text=text.replace('if(!same(buf,before,512)) { status=1;failed=i;goto rollback; }',
                      'if(!current_preimage(i,buf,before)) { status=1;failed=i;goto rollback; }')
    text=text.replace('touched[i]=1;written++;','if(!touched[i])written++;touched[i]=1;')
    if variants_path is None and (destination/'variants').is_dir():
        variants_path=destination/'variants'
    variants={1:carrier}
    if variants_path:
        vr=Path(variants_path)
        for flags in (2,3):
            variants[flags]=(vr/str(flags)/'carrier.bin').read_bytes()
            if not 1024<=len(variants[flags])<=2098176:raise ValueError('Creative Look variant exceeds carrier bounds')
    legacy={}
    if legacy_path is None and (destination/'legacy-carriers.json').is_file():
        legacy_path=destination/'legacy-carriers.json'
    if legacy_path:
        legacy=json.loads(Path(legacy_path).read_bytes())
        if set(legacy)!={'5','7'} or len(variants)!=3:raise ValueError('Legacy carriers require the two exact FL23 combinations')
        for ident,row in legacy.items():
            if (row.get('flags')!=int(ident)&3 or row.get('revision')!=1 or
                    type(row.get('bytes')) is not int or not 1024<=row['bytes']<=2098176 or
                    not isinstance(row.get('sha256'),str) or len(row['sha256'])!=64 or
                    len(bytes.fromhex(row['sha256']))!=32 or row['sha256'] in {sha(v) for v in variants.values()}):
                raise ValueError('Invalid legacy carrier identity')
    specs={f:dict(bytes=len(v),sha256=sha(v)) for f,v in variants.items()}
    specs.update({int(f):row for f,row in legacy.items()})
    file_header=(f'#define FILE_BYTES {max(row["bytes"] for row in specs.values())}u\nstatic const u8 FILE_SHA[32]='+array(bytes.fromhex(sha(carrier)))+';\n'+
                 'static const u8 PROFILE_SHA[32]='+array(bytes.fromhex(digest))+';\n')
    if len(variants)>1:
        file_header+=('struct FileVariant {u32 flags,bytes;u8 digest[32];};\n#define FILE_VARIANT_COUNT '+str(len(specs))+'u\n'+
            'static const struct FileVariant FILE_VARIANTS[]={'+','.join('{%du,%du,%s}'%(f,row['bytes'],array(bytes.fromhex(row['sha256']))) for f,row in specs.items())+'};\n')
    native_sources={'transaction.c':text.encode(),'profile.h':header(recipe),'sha256.h':Path(sha_header).read_bytes(),
        'fl23_files.c':(ROOT/'src/a6700_unlock/native/fl23_files.c').read_bytes(),
        'file-profile.h':file_header.encode()}
    manifest=dict(schema=1,feature='fl23',profile_sha256=digest,carrier_sha256=sha(carrier),carrier_bytes=len(carrier),
        deployment='file-carrier',names=False,hardware_validation='pending',payloads={},
        source_sha256={n:sha(raw) for n,raw in native_sources.items()})
    manifest['upstream_source_sha256']={name:sha((ROOT/'src/a6700_unlock'/name).read_bytes())
        for name in ('native/transaction.c','native/fl23_files.c','native/fl23_sha256.h')}
    with tempfile.TemporaryDirectory(prefix='powerful-fl23-native-') as tmp:
        work=Path(tmp)
        for name,raw in native_sources.items():(work/name).write_bytes(raw)
        for name,mode in (('reader',0),('writer',1)):
            manifest['payloads'][name]=compile(work/'transaction.c',destination,work,name,defines=['-DBUILD_WRITER='+str(mode)])
        manifest['payloads']['files']=compile(work/'fl23_files.c',destination,work,'files')
    # Complete generated C sources stay next to the corresponding binaries;
    # no internal research paths are needed to rebuild these transactions.
    for name,raw in native_sources.items():(destination/name).write_bytes(raw)
    runtime=destination/'runtime/manifest.json'
    if runtime.is_file():manifest['runtime_source_sha256']=sha(runtime.read_bytes())
    if len(variants)>1:
        manifest['variants']={str(f):dict(bytes=len(v),sha256=sha(v),
            runtime_sha256=sha((destination/'variants'/str(f)/'runtime/manifest.json').read_bytes())) for f,v in variants.items() if f!=1}
    if legacy:
        manifest.update(carrier_revision=2,legacy_carriers=legacy)
        (destination/'legacy-carriers.json').write_text(json.dumps(legacy,indent=2)+'\n')
    (destination/'recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
    (destination/'carrier.bin').write_bytes(carrier)
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--recipe',type=Path,required=True)
    p.add_argument('--carrier',type=Path,required=True);p.add_argument('--sha-header',type=Path,required=True)
    p.add_argument('--variants',type=Path)
    p.add_argument('--legacy-carriers',type=Path,help='Exact prior FL23 file identities for in-place revision updates')
    a=p.parse_args();print(json.dumps(build(a.recipe,a.carrier,a.sha_header,variants_path=a.variants,legacy_path=a.legacy_carriers),indent=2))
