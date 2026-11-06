"""FL23 field planning and actual file-transaction ELF, no physical device."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from a6700_unlock.fl23_package import service_files,Session

from a6700_unlock.fl23_package import plan_blocks,Profile,Package
from a6700_unlock.device_protocol import DeviceError,request,response
import test_native as native

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_fl23',ROOT/'scripts/build_fl23.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
sha=lambda data:hashlib.sha256(data).hexdigest()


def synthetic():
    old=bytes(512);norm=sha(old)
    recipe=dict(schema=1,feature='fl23',model='ILCE-6700',firmware='2.00',names=False,
        devices=['/dev/nflasha15','/dev/nflasha5','/dev/nflasha8'],cp_units=[],guards=[],
        entries=[dict(index=0,device=0,device_offset=512,unmanaged_sha256=norm,
            edits=[dict(offset=4,disabled='00',enabled='01',legacy=['02'])])])
    recipe['profile_sha256']=sha(json.dumps(recipe,sort_keys=True,separators=(',',':')).encode())
    return recipe


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.package=SimpleNamespace(profile=Profile(synthetic()))
    def test_only_owned_byte_changes_and_exact_reverse(self):
        before=[bytes(512)];detail,after=plan_blocks(self.package,before,'unlock')
        self.assertEqual(detail['changed_bytes'],1);self.assertEqual(after[0][4],1)
        self.assertEqual(plan_blocks(self.package,after,'close')[1],before)
    def test_known_legacy_migrates_unknown_and_nonowned_refuse(self):
        data=bytearray(512);data[4]=2
        self.assertEqual(plan_blocks(self.package,[bytes(data)],'unlock')[1][0][4],1)
        data[4]=3
        with self.assertRaises(DeviceError):plan_blocks(self.package,[bytes(data)],'unlock')
        data[4]=0;data[5]=1
        with self.assertRaises(DeviceError):plan_blocks(self.package,[bytes(data)],'unlock')
    def test_close_retains_cp_support(self):
        recipe=synthetic();recipe['entries'][0]['device']=2
        p=SimpleNamespace(profile=Profile(recipe));value=bytearray(512);value[4]=1
        self.assertEqual(plan_blocks(p,[bytes(value)],'close')[1],[bytes(value)])


class ServiceTests(unittest.TestCase):
    class Wire:
        def __init__(self,fail=False):
            self.files={0:b'original configuration',2:b'original library'};self.calls=[];self.fail=fail;self.alias=False
            self.cleanup_warning=None
        def _command(self,code,data):
            self.calls.append((code,data))
            if code==0x4008:return bytes(4)+self.files[data[1]]
            if code==0x400f:
                if data!=b'\0/tmp/jiritsu/./libJit.so':raise AssertionError('unsafe alias')
                self.alias=True
            elif code==0x4010:
                ident=data[1]
                if ident==255:
                    if not self.alias:raise AssertionError('library restore without alias')
                    ident=2
                self.files[ident]=data[3:]
            elif code==0x4007:
                self.files[2]=data[3:];self.files[0]=b'new report'
                if self.fail:raise OSError('execution ACK lost')
            else:raise AssertionError('unknown command')
            return bytes(4)
    def test_both_service_files_restored_by_nonexecuting_alias(self):
        wire=self.Wire();original=dict(wire.files)
        with tempfile.TemporaryDirectory() as tmp:
            with service_files(wire,Path(tmp)) as invoke:self.assertEqual(invoke(b'new native',b'config'),b'new report')
        self.assertEqual(wire.files,original)
        self.assertEqual(sum(code==0x4007 for code,_ in wire.calls),1)
        self.assertTrue(wire.alias)
    def test_unknown_execution_not_replayed_or_restored_blindly(self):
        wire=self.Wire(True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError),service_files(wire,Path(tmp)) as invoke:invoke(b'new native',b'config')
        self.assertEqual(sum(code==0x4007 for code,_ in wire.calls),1)
        self.assertFalse(wire.alias);self.assertIsNotNone(wire.cleanup_warning)
    def test_unstable_backup_stops_before_upload(self):
        wire=self.Wire();wire.files[0]=b''
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeviceError),service_files(wire,Path(tmp)):raise AssertionError('entered')
        self.assertTrue(all(code==0x4008 for code,_ in wire.calls))
    def test_preservation_reads_exact_38_native_keys_and_rejects_short_reply(self):
        calls=[]
        def send(category,packet):
            self.assertEqual(category,0x40);kind,command,key=struct.unpack('<HHI',packet)
            self.assertEqual((kind,command),(0x603,1));calls.append(key)
            return 0,bytes(13 if key in (0x02cf0370,) or key in {base+2*i+1 for base in (0x02cf0ccc,0x02cf0fa9,0x02cf0fc9) for i in range(6)} else 1)
        session=Session(None,SimpleNamespace(channel=SimpleNamespace(send=send)),None,Path('.'))
        self.assertEqual(len(session.ap_values()),38);self.assertEqual(len(set(calls)),38)
        session.backend.channel.send=lambda *a:(0,b'')
        with self.assertRaises(DeviceError):session.ap_values()


class FileMachine(native.Machine):
    def __init__(self,binary,config,filesystem,fail=None):
        super().__init__(SimpleNamespace(entries=[]),binary,config,[])
        self.fs=filesystem;self.fail=fail;self.written=[];self.renamed=[]
    def syscall(self,u,number,data):
        nr=u.reg_read(native.UC_ARM64_REG_X8);a,b,c,d=[u.reg_read(r) for r in (native.UC_ARM64_REG_X0,native.UC_ARM64_REG_X1,native.UC_ARM64_REG_X2,native.UC_ARM64_REG_X3)]
        rc=0
        if nr==56:
            path=self.cstring(b);signed=a if a<1<<63 else a-(1<<64)
            if signed!=-100:raise AssertionError('foreign directory')
            if path=='/tmp/jiritsu/tmmc.jit':
                if c&0x200:self.file=bytearray()
            elif path=='/setting':
                if c!=0x84000:raise AssertionError('wrong directory UAPI')
            elif path in ('/setting/fl23','/setting/fl23.new','/setting/fl23.off'):
                if c&0x88000!=0x88000:raise AssertionError('missing AArch64 NOFOLLOW/CLOEXEC')
                if c&0x80 and path in self.fs:self.ret(-17);return
                if path not in self.fs:
                    if c&0x40:self.fs[path]=bytearray()
                    else:self.ret(-2);return
            else:raise AssertionError('arbitrary path '+path)
            fd=self.next_fd;self.next_fd+=1;self.fds[fd]=[path,0];self.ret(fd);return
        if nr==57:
            if a not in self.fds:raise AssertionError('double close')
            del self.fds[a]
        elif nr==38:
            source=self.cstring(b);dest=self.cstring(d)
            if (source,dest) not in (('/setting/fl23.new','/setting/fl23'),('/setting/fl23','/setting/fl23.off')):
                raise AssertionError('arbitrary rename')
            self.fs[dest]=self.fs.pop(source);self.renamed.append((source,dest))
        elif nr in (62,63,64,67,68,82):
            if a not in self.fds:raise AssertionError('foreign fd')
            path,position=self.fds[a];content=self.file if path=='/tmp/jiritsu/tmmc.jit' else self.fs.get(path,bytearray())
            if nr==82:
                if self.fail=='sync' and path!='/tmp/jiritsu/tmmc.jit':rc=-5
            elif nr==62:
                if c!=2 or b:raise AssertionError('unbounded seek')
                rc=len(content)
            elif nr in (63,67):
                at=position if nr==63 else d;data=bytes(content[at:at+c]);u.mem_write(b,data);rc=len(data)
                if nr==63:self.fds[a][1]+=rc
            else:
                at=position if nr==64 else d
                if path!='/tmp/jiritsu/tmmc.jit' and path!='/setting/fl23.new':raise AssertionError('non-staging file write')
                if len(content)<at+c:content.extend(bytes(at+c-len(content)))
                content[at:at+c]=u.mem_read(b,c);rc=c
                if nr==64:self.fds[a][1]+=rc
                self.written.append(path)
        else:raise AssertionError('Unexpected file transaction syscall '+str(nr))
        self.ret(rc)
    def run(self):
        self.uc.emu_start(self.entry,self.stop,count=10000000)
        if self.uc.reg_read(native.UC_ARM64_REG_PC)!=self.stop:raise AssertionError('native execution bound')
        if self.fds:raise AssertionError('leaked descriptor')
        return bytes(self.file)


@unittest.skipUnless(native.AVAILABLE,'native emulator unavailable')
class FileNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name);recipe=synthetic();cls.recipe=recipe
        cls.carrier=b'FL23 synthetic file '+bytes(range(256))*4
        (p/'recipe.json').write_text(json.dumps(recipe));(p/'carrier.bin').write_bytes(cls.carrier)
        cls.out=p/'out'
        builder.build(p/'recipe.json',p/'carrier.bin',ROOT/'src/a6700_unlock/native/fl23_sha256.h',destination=cls.out)
        cls.binary=(cls.out/'files.elf').read_bytes();cls.profile=Profile(recipe)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_job(self,fs,op=0,offset=0,data=b'',length=0,fail=None):
        nonce=bytes(range(16));config=struct.pack('<4sI16s32s4I',b'A67F',1,nonce,bytes.fromhex(self.recipe['profile_sha256']),op,offset,len(data) if op==2 else length,0)+data
        vm=FileMachine(self.binary,config,fs,fail);raw=vm.run()
        h=struct.unpack_from('<4sI16s5I',raw)
        self.assertEqual(h[:3],(b'A67F',1,nonce));self.assertEqual(len(raw),44+h[4])
        return h[3],h[5],raw[44:],vm
    def test_full_upload_atomic_commit_independent_read_and_close(self):
        fs={};self.assertEqual(self.run_job(fs)[0:2],(0,0))
        self.assertEqual(self.run_job(fs,1)[0],0)
        self.assertEqual(self.run_job(fs,2,data=self.carrier)[0],0)
        self.assertNotIn('/setting/fl23',fs)
        self.assertEqual(self.run_job(fs,3)[0],0)
        status,present,data,vm=self.run_job(fs,length=len(self.carrier))
        self.assertEqual((status,present,data),(0,1,self.carrier))
        self.assertEqual(self.run_job(fs,4)[0],0);self.assertNotIn('/setting/fl23',fs)
        self.assertEqual(bytes(fs['/setting/fl23.off']),self.carrier)
    def test_wrong_append_offset_never_mutates_existing_staging(self):
        fs={'/setting/fl23.new':bytearray(b'old')};before=bytes(fs['/setting/fl23.new'])
        self.assertEqual(self.run_job(fs,2,data=b'other')[0],1);self.assertEqual(bytes(fs['/setting/fl23.new']),before)
    def test_partial_or_corrupt_body_never_commits(self):
        for value in (self.carrier[:-1],b'X'+self.carrier[1:],self.carrier+b'X'):
            fs={'/setting/fl23.new':bytearray(value)}
            self.assertEqual(self.run_job(fs,3)[0],1);self.assertNotIn('/setting/fl23',fs)
    def test_unknown_live_or_closed_file_is_preserved(self):
        for op in (1,4):
            fs={'/setting/fl23':bytearray(b'unknown')}
            self.assertEqual(self.run_job(fs,op)[0],1);self.assertEqual(fs['/setting/fl23'],b'unknown')
        fs={'/setting/fl23':bytearray(self.carrier),'/setting/fl23.off':bytearray(b'unknown')}
        self.assertEqual(self.run_job(fs,4)[0],1);self.assertIn('/setting/fl23',fs)
    def test_existing_staging_is_not_truncated(self):
        fs={'/setting/fl23.new':bytearray(b'partial')}
        self.assertEqual(self.run_job(fs,1)[0],3);self.assertEqual(fs['/setting/fl23.new'],b'partial')
    def test_package_assets_detect_source_and_carrier_tampering(self):
        p=Package(self.out);self.assertEqual(p.carrier,self.carrier)
        path=self.out/'carrier.bin';before=path.read_bytes()
        try:
            path.write_bytes(b'X'+before[1:])
            with self.assertRaises(DeviceError):Package(self.out)
        finally:path.write_bytes(before)


if __name__=='__main__':unittest.main()
