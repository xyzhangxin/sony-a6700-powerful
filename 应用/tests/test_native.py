"""Execute the actual packaged AArch64 code against synthetic syscall devices.

Optional test dependencies: unicorn, pyelftools. Never uses USB or a camera.
"""
import io
from pathlib import Path
import struct
import unittest

from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.device_protocol import request, response
from a6700_unlock.usb.backend import payloads

try:
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_INTR
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X8, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC
    AVAILABLE=True
except ImportError:
    AVAILABLE=False


class Machine:
    def __init__(self, profile, binary, config, sectors, failure=None):
        self.profile=profile;self.failure=failure;self.failed=False
        self.blocks={(e["device"],e["device_offset"]):bytearray(s) for e,s in zip(profile.entries,sectors)}
        self.file=bytearray(config);self.fds={};self.next_fd=3;self.calls=[];self.write_count=0
        self.uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        elf=ELFFile(io.BytesIO(binary));self.base=0x100000
        segments=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
        end=max(s['p_vaddr']+s['p_memsz'] for s in segments)
        self.uc.mem_map(self.base,(end+4095)//4096*4096)
        for segment in segments:self.uc.mem_write(self.base+segment['p_vaddr'],segment.data())
        relocations=elf.get_section_by_name('.rela.dyn')
        for r in relocations.iter_relocations() if relocations else []:
            if r['r_info_type']!=1027:raise RuntimeError('Unexpected native relocation')
            self.uc.mem_write(self.base+r['r_offset'],struct.pack('<Q',self.base+r['r_addend']))
        init=elf.get_section_by_name('.init_array')
        self.entry=int.from_bytes(self.uc.mem_read(self.base+init['sh_addr'],8),'little')
        self.stop=0x8000000
        self.uc.mem_map(self.stop,4096)
        self.uc.mem_map(0x3000000,0x100000)
        self.uc.reg_write(UC_ARM64_REG_SP,0x30ffff0)
        self.uc.reg_write(UC_ARM64_REG_X30,self.stop)
        self.uc.hook_add(UC_HOOK_INTR,self.syscall)
    def cstring(self,ptr):
        data=bytearray()
        for i in range(256):
            b=self.uc.mem_read(ptr+i,1)
            if b==b'\0':return data.decode()
            data+=b
        raise RuntimeError('Unbounded native path')
    def ret(self,value):self.uc.reg_write(UC_ARM64_REG_X0,value & ((1<<64)-1))
    def syscall(self,uc,intno,data):
        n=uc.reg_read(UC_ARM64_REG_X8)
        a,b,c,d=[uc.reg_read(r) for r in (UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3)]
        self.calls.append(n)
        if n==56:
            path=self.cstring(b)
            if path=='/tmp/jiritsu/tmmc.jit':
                item=['result',0]
                if c & 0x200:self.file=bytearray()
            elif path in self.profile.data['devices']:
                item=[self.profile.data['devices'].index(path),0]
            else:raise RuntimeError('Native accessed unexpected path: '+path)
            fd=self.next_fd;self.next_fd+=1;self.fds[fd]=item;self.ret(fd)
        elif n==57:
            del self.fds[a];self.ret(0)
        elif n in (63,64):
            item=self.fds[a]
            if item[0]!='result':raise RuntimeError('Unexpected ordinary device I/O')
            at=item[1]
            if n==63:
                value=bytes(self.file[at:at+c]);uc.mem_write(b,value);self.ret(len(value));item[1]+=len(value)
            else:
                value=bytes(uc.mem_read(b,c))
                if len(self.file)<at+c:self.file.extend(bytes(at+c-len(self.file)))
                self.file[at:at+c]=value;item[1]+=c;self.ret(c)
        elif n in (67,68):
            dev=self.fds[a][0];offset=d//512*512;within=d%512
            block=self.blocks[(dev,offset)]
            count=min(c,512-within)
            if n==67:
                value=bytes(block[within:within+count])
                if self.failure=='readback' and self.write_count and not self.failed:
                    self.failed=True;value=bytes([value[0]^1])+value[1:]
                uc.mem_write(b,value);self.ret(len(value))
            else:
                self.write_count+=1
                if self.failure=='short-write' and not self.failed:
                    count=min(count,7);self.failed=True
                elif self.failure=='write' and not self.failed:
                    self.failed=True;self.ret(-5);return
                elif self.failure=='rollback-fails':
                    if self.write_count==2:self.failed=True
                    if self.failed:self.ret(-5);return
                block[within:within+count]=bytes(uc.mem_read(b,count));self.ret(count)
        elif n==82:
            if self.fds[a][0]!='result' and self.failure=='fsync' and not self.failed:
                self.failed=True;self.ret(-5)
            else:self.ret(0)
        else:raise RuntimeError(f'Unexpected AArch64 syscall {n}')
    def run(self):
        self.uc.emu_start(self.entry,self.stop,count=10000000)
        if self.uc.reg_read(UC_ARM64_REG_PC)!=self.stop:raise RuntimeError('Native did not finish')
        return bytes(self.file)


@unittest.skipUnless(AVAILABLE,'native execution tests require unicorn and pyelftools')
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=load_device_profile(load_catalog());cls.assets=payloads(cls.profile)
        cls.before=[bytes(512) for e in cls.profile.entries]
        cls.after=[]
        for mask in cls.profile.masks(writable_only=True):
            value=bytearray(512)
            for i in range(512):
                if mask[i//8] & (1<<(i%8)):
                    value[i]=1;break
            cls.after.append(bytes(value))
        cls.nonce=bytes(range(16))
    def writer(self,failure=None,current=None,config=None):
        config=config or request(self.profile,self.nonce,self.before,self.after)
        machine=Machine(self.profile,self.assets['writer'],config,current or self.before,failure)
        result=response(machine.run(),self.nonce,0)
        blocks=[bytes(machine.blocks[(r['device'],r['device_offset'])]) for r in self.profile.entries]
        self.assertNotIn(74,machine.calls)
        return result,blocks,machine
    def test_reader_reports_exact_sparse_data(self):
        machine=Machine(self.profile,self.assets['reader'],request(self.profile,self.nonce),self.before)
        result=response(machine.run(),self.nonce,len(self.before))
        self.assertEqual(result['status'],0);self.assertEqual(result['sectors'],self.before)
        self.assertEqual(machine.write_count,0)
    def test_parked_feature_bytes_are_rejected_even_by_forged_native_request(self):
        # Bypass the Python request validator: exercise the compiled ELF policy.
        for feature in ('pixel_shift', 'dynamic_active'):
            for edit in [next(e for e in self.profile.catalog.edits if e.feature == feature)]:
                with self.subTest(feature=feature, edit=edit.id):
                    row=next(r for r in self.profile.entries if r.get('image')==edit.image
                             and r['file_offset']<=edit.offset<r['file_offset']+512)
                    raw=bytearray(request(self.profile,self.nonce,self.before,self.before))
                    raw[56+row['index']*1024+512+edit.offset-row['file_offset']]=1
                    result,blocks,machine=self.writer(config=bytes(raw))
                    self.assertEqual(result['status'],2)
                    self.assertEqual(machine.write_count,0)
                    self.assertEqual(blocks,self.before)
    def test_writer_uses_actual_fsync_and_leaves_guards_untouched(self):
        result,blocks,machine=self.writer()
        self.assertEqual(result['status'],0);self.assertEqual(blocks,self.after)
        self.assertIn(82,machine.calls)
    def test_short_write_makes_progress_and_completes(self):
        result,blocks,_=self.writer('short-write')
        self.assertEqual(result['status'],0);self.assertEqual(blocks,self.after)
    def test_unknown_preimage_blocks_all_writes(self):
        current=list(self.before);current[-1]=b'x'+current[-1][1:]
        result,blocks,machine=self.writer(current=current)
        self.assertEqual(result['status'],1);self.assertEqual(machine.write_count,0)
        self.assertEqual(blocks,current)
    def test_readonly_guard_cannot_be_modified_by_request(self):
        raw=bytearray(request(self.profile,self.nonce,self.before,self.after))
        guard=next(r['index'] for r in self.profile.entries if r['role']=='guard')
        raw[56+guard*1024+512]=1
        result,_,machine=self.writer(config=bytes(raw))
        self.assertEqual(result['status'],2);self.assertEqual(machine.write_count,0)
    def test_write_fsync_and_readback_failures_roll_back(self):
        for failure,code in [('write',5),('fsync',6),('readback',7)]:
            with self.subTest(failure=failure):
                result,blocks,_=self.writer(failure)
                self.assertEqual(result['status'],code);self.assertTrue(result['rolled_back'])
                self.assertEqual(blocks,self.before)
    def test_rollback_failure_is_explicit(self):
        result,blocks,_=self.writer('rollback-fails')
        self.assertEqual(result['status'],8);self.assertFalse(result['rolled_back'])
        self.assertNotEqual(blocks,self.before)
    def test_profile_digest_mismatch_is_rejected(self):
        raw=bytearray(request(self.profile,self.nonce,self.before,self.after));raw[24]^=1
        result,_,machine=self.writer(config=bytes(raw))
        self.assertEqual(result['status'],2);self.assertEqual(machine.write_count,0)


if __name__=='__main__':unittest.main()
