import io
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_native as native
from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,UC_ARM64_REG_X4,UC_ARM64_REG_X5

ROOT=Path(__file__).resolve().parents[1]
SOURCES=(
    ROOT/'src/a6700_unlock/data/fl23/runtime/main/main-slot-storage.c',
    ROOT/'src/a6700_unlock/data/fl23/runtime/main/main-slot-ram-save.c',
    ROOT/'src/a6700_unlock/data/fl23/runtime/main/main-slot-file-state.c',
)
STATE_PATH='/setting/fl23nbk'
EXTENSION=0x5000000
STACK=0x3000000


def compile_state():
    clang = shutil.which('clang') or ('/usr/bin/clang' if Path('/usr/bin/clang').is_file() else None)
    if not clang:
        raise RuntimeError('clang is required to build FL23 state tests')
    linker = (
        shutil.which('aarch64-elf-ld')
        or ('/opt/homebrew/bin/aarch64-elf-ld' if Path('/opt/homebrew/bin/aarch64-elf-ld').is_file() else None)
        or shutil.which('ld.lld')
    )
    if not linker:
        raise RuntimeError('aarch64 linker is required to build FL23 state tests')
    with tempfile.TemporaryDirectory(prefix='fl23-state-') as directory:
        work=Path(directory);objects=[]
        for index,source in enumerate(SOURCES):
            obj=work/f'{index}.o';objects.append(obj)
            subprocess.run([clang,'-target','aarch64-none-elf','-O2',
                '-mgeneral-regs-only','-mstrict-align','-fPIC','-ffreestanding',
                '-fno-builtin','-fno-stack-protector','-c',str(source),'-o',str(obj)],check=True)
        binary=work/'state.elf'
        subprocess.run([linker,'-shared','-Bsymbolic',
            '-z','defs','-z','noexecstack','--build-id=none','-o',str(binary),
            *(str(obj) for obj in objects)],check=True)
        return binary.read_bytes()


class StateMachine:
    def __init__(self,binary,filesystem,fail=None):
        self.fs={path:bytearray(data) for path,data in filesystem.items()}
        self.fail=fail;self.fds={};self.next_fd=3;self.calls=[];self.writes=[];self.syncs=[]
        self.uc=native.Uc(native.UC_ARCH_ARM64,native.UC_MODE_ARM)
        from elftools.elf.elffile import ELFFile
        elf=ELFFile(io.BytesIO(binary));self.base=0x100000
        segments=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
        end=max(s['p_vaddr']+s['p_memsz'] for s in segments)
        self.uc.mem_map(self.base,(end+4095)//4096*4096)
        for segment in segments:self.uc.mem_write(self.base+segment['p_vaddr'],segment.data())
        relocations=elf.get_section_by_name('.rela.dyn')
        for relocation in relocations.iter_relocations() if relocations else []:
            if relocation['r_info_type']!=1027:raise AssertionError('unexpected state relocation')
            self.uc.mem_write(self.base+relocation['r_offset'],
                struct.pack('<Q',self.base+relocation['r_addend']))
        symbols=elf.get_section_by_name('.symtab')
        self.symbols={symbol.name:symbol['st_value'] for symbol in symbols.iter_symbols()}
        self.stop=0x8000000;self.uc.mem_map(self.stop,4096)
        self.uc.mem_map(STACK,0x100000)
        self.uc.reg_write(native.UC_ARM64_REG_SP,STACK+0xffff0)
        self.uc.hook_add(native.UC_HOOK_INTR,self.syscall)

    def cstring(self,pointer):
        data=bytearray()
        for index in range(256):
            value=bytes(self.uc.mem_read(pointer+index,1))
            if value==b'\0':return data.decode()
            data+=value
        raise AssertionError('unbounded state path')

    def ret(self,value):self.uc.reg_write(native.UC_ARM64_REG_X0,value&((1<<64)-1))

    def syscall(self,uc,intno,data):
        number=uc.reg_read(native.UC_ARM64_REG_X8)
        a,b,c,d=[uc.reg_read(register) for register in (
            native.UC_ARM64_REG_X0,native.UC_ARM64_REG_X1,native.UC_ARM64_REG_X2,native.UC_ARM64_REG_X3)]
        self.calls.append(number)
        if number==56:
            directory=a if a<1<<63 else a-(1<<64)
            path=self.cstring(b)
            if directory!=-100 or path!=STATE_PATH:raise AssertionError('foreign state open')
            if c==0x88000:
                if path not in self.fs:self.ret(-2);return
            elif c==0x88042:
                if path not in self.fs:self.fs[path]=bytearray()
            else:raise AssertionError('foreign state open flags')
            fd=self.next_fd;self.next_fd+=1;self.fds[fd]=[path,0];self.ret(fd);return
        if number==57:
            if a not in self.fds:raise AssertionError('state double close')
            del self.fds[a];self.ret(0);return
        if number==62:
            if a not in self.fds or c!=2:raise AssertionError('foreign state seek')
            self.ret(len(self.fs[self.fds[a][0]]));return
        if number in (67,68):
            if a not in self.fds:raise AssertionError('state I/O on closed fd')
            path=self.fds[a][0];offset=d;count=c
            if number==67:
                value=bytes(self.fs[path][offset:offset+count])
                uc.mem_write(b,value);self.ret(len(value));return
            value=bytes(uc.mem_read(b,count))
            if len(self.fs[path])<offset+count:self.fs[path].extend(bytes(offset+count-len(self.fs[path])))
            self.fs[path][offset:offset+count]=value;self.writes.append((offset,count));self.ret(count);return
        if number==82:
            self.syncs.append(a)
            self.ret(-5 if self.fail=='fsync' else 0);return
        raise AssertionError('unexpected state syscall '+str(number))

    def call(self,name,args=()):
        address=self.base+self.symbols[name]
        for index,value in enumerate(args):
            self.uc.reg_write((UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,
                UC_ARM64_REG_X3,UC_ARM64_REG_X4,UC_ARM64_REG_X5)[index],value)
        self.uc.reg_write(native.UC_ARM64_REG_X30,self.stop)
        self.uc.emu_start(address,self.stop,count=10000000)
        if self.uc.reg_read(native.UC_ARM64_REG_PC)!=self.stop:raise AssertionError('state function did not return')
        value=self.uc.reg_read(native.UC_ARM64_REG_X0)
        if value>=1<<63:return value-(1<<64)
        if value<1<<32 and value&0x80000000:return value-(1<<32)
        return value


@unittest.skipUnless(native.AVAILABLE,'native execution tests require unicorn and pyelftools')
class FileStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.binary=compile_state()

    def machine(self,fs=None,fail=None):
        machine=StateMachine(self.binary,fs or {},fail)
        machine.uc.mem_map(EXTENSION,4096)
        return machine

    def initialize(self,machine):
        self.assertEqual(machine.call('fl23_main_initialize',[EXTENSION]),0)
        self.assertEqual(machine.call('fl23_main_bind',[EXTENSION]),0)

    def set_values(self,machine,values):
        for group in range(11):
            for slot in range(2):
                value=values[group*2+slot]&0xffffffff
                machine.call(f'fl23_slot_{group:02d}_{slot}_record_set',[value])
                machine.call(f'fl23_slot_{group:02d}_{slot}_snapshot')

    def read_values(self,machine):
        return [machine.call(f'fl23_slot_{group:02d}_{slot}_record_get')&0xffffffff
                for group in range(11) for slot in range(2)]

    def test_save_load_roundtrip_and_slot_alternation(self):
        values=(1,2,3,4,5,6,(-1)&0xffffffff,0,1,(-1)&0xffffffff,2,(-2)&0xffffffff,
                3,4,5,(-5)&0xffffffff,6,7,2,3,8,9)
        machine=self.machine();self.initialize(machine);self.set_values(machine,values)
        self.assertEqual(machine.call('fl23_state_save',[EXTENSION,17]),0)
        self.assertEqual(len(machine.fs[STATE_PATH]),8192);self.assertIn(82,machine.calls)
        self.initialize(machine);selector=0
        self.assertEqual(machine.call('fl23_state_load',[EXTENSION,EXTENSION+2048]),0)
        selector=struct.unpack('<I',machine.uc.mem_read(EXTENSION+2048,4))[0]
        self.assertEqual(selector,17);self.assertEqual(self.read_values(machine),list(values))

        second=(2,3,4,5,6,7,0,1,2,0,3,(-1)&0xffffffff,4,5,6,0,7,8,3,4,9,8)
        self.set_values(machine,second)
        self.assertEqual(machine.call('fl23_state_save',[EXTENSION,18]),0)
        self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_load',[EXTENSION,EXTENSION+2048]),0)
        selector=struct.unpack('<I',machine.uc.mem_read(EXTENSION+2048,4))[0]
        self.assertEqual(selector,18);self.assertEqual(self.read_values(machine),list(second))
        first=bytes(machine.fs[STATE_PATH][:4096]);second_record=bytes(machine.fs[STATE_PATH][4096:])
        self.assertEqual(first[:8],b'FL23ST01');self.assertEqual(second_record[:8],b'FL23ST01')

    def test_corrupt_active_record_falls_back_to_previous(self):
        values=tuple((index%9)+1 for index in range(22))
        machine=self.machine();self.initialize(machine);self.set_values(machine,values)
        self.assertEqual(machine.call('fl23_state_save',[EXTENSION,17]),0)
        second=(2,3,4,5,6,7,0,1,2,0,3,(-1)&0xffffffff,4,5,6,0,7,8,3,4,9,8)
        self.set_values(machine,second);self.assertEqual(machine.call('fl23_state_save',[EXTENSION,18]),0)
        machine.fs[STATE_PATH][4096:4097]=b'X'
        self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_load',[EXTENSION,EXTENSION+2048]),0)
        selector=struct.unpack('<I',machine.uc.mem_read(EXTENSION+2048,4))[0]
        self.assertEqual(selector,17);self.assertEqual(self.read_values(machine),list(values))

    def test_missing_short_unknown_and_invalid_files(self):
        machine=self.machine();self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_load',[EXTENSION,EXTENSION+2048]),-2)
        machine=self.machine({STATE_PATH:b'X'*4096});self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_load',[EXTENSION,EXTENSION+2048]),-1)
        machine=self.machine({STATE_PATH:b'A'*8192});self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_save',[EXTENSION,17]),-1)
        self.assertEqual(bytes(machine.fs[STATE_PATH]),b'A'*8192)

    def test_invalid_selector_and_sync_failure_do_not_create_records(self):
        machine=self.machine();self.initialize(machine)
        for selector in (16,19,0xffffffff):
            self.assertEqual(machine.call('fl23_state_save',[EXTENSION,selector]),-22)
            self.assertNotIn(STATE_PATH,machine.fs)
        machine=self.machine(fail='fsync');self.initialize(machine)
        self.assertEqual(machine.call('fl23_state_save',[EXTENSION,17]),-5)
        self.assertEqual(len(machine.fs[STATE_PATH]),8192)

    def test_ram_save_hook_uses_bound_state_and_cached_page(self):
        values=tuple((index%9)+1 for index in range(22))
        machine=self.machine();self.initialize(machine);self.set_values(machine,values)
        self.assertEqual(machine.call('fl23_state_bind_extension',[EXTENSION]),0)
        stub=0x6000000;machine.uc.mem_map(stub,4096)
        machine.uc.mem_write(stub,bytes.fromhex('000080d2c0035fd6'))
        self.assertEqual(machine.call('fl23_ram_save_bind',[stub,stub,0]),0)
        payload=EXTENSION+3000;machine.uc.mem_write(payload,bytes((17,)))
        self.assertEqual(machine.call('fl23_ram_save_normal_adapter',[0x02cf036f,payload]),0)
        self.assertIn(STATE_PATH,machine.fs);self.assertEqual(len(machine.fs[STATE_PATH]),8192)
        writes=len(machine.writes)
        self.assertEqual(machine.call('fl23_ram_save_normal_adapter',[0x02cf036f,payload]),0)
        self.assertEqual(len(machine.writes),writes)


if __name__=='__main__':unittest.main()
