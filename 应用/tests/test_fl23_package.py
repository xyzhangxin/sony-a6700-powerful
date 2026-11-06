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
    def test_close_restores_cp_support_to_original(self):
        recipe=synthetic();recipe['entries'][0]['device']=2
        p=SimpleNamespace(profile=Profile(recipe));value=bytearray(512);value[4]=1
        self.assertEqual(plan_blocks(p,[bytes(value)],'close')[1],[bytes(512)])


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


class UninstallTests(unittest.TestCase):
    """Exercise the real host orchestration with separate code/parameter stores."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.events=[];self.fail=None
        recipe=synthetic();recipe['entries'].append(dict(recipe['entries'][0],index=1,device=2))
        self.spec=dict(key=123,bytes=2,before_sha256=sha(b'\0\0'),after_sha256=sha(b'\1\0'),
                       edits=[dict(offset=0,disabled='00',enabled='01')])
        recipe['cp_units']=[self.spec]
        self.package=SimpleNamespace(recipe=recipe,profile=Profile(recipe),carrier=b'synthetic carrier')
        self.code=[bytes(512),bytes(512)];self.unit=b'\0\0';self.mask=0
        self.ap={hex(0x02cf036f):'00',hex(0x02cf0ccc):'00',hex(0x02cf0370):'00'*13}
        outer=self
        class Wire:
            def send(_,category,cfg):
                outer.events.append('save')
                if outer.fail=='save':raise OSError('SAVE reply lost')
                return 0,b''
        self.backend=SimpleNamespace(channel=Wire(),_fl23_transport_uncertain=False)
        class FakeSession:
            def __init__(_,package,backend,invoke,directory):
                _.package=package;_.backend=backend;_.directory=directory
            def blocks(_,before=None,after=None):
                if before is None:return list(outer.code)
                if before!=outer.code:raise DeviceError('stale code')
                if outer.fail=='code':raise DeviceError('code transaction failed')
                if before[1]!=after[1] and after[1][4]==0:
                    assert after[0][4]==0 and outer.unit==b'\0\0'
                    outer.events.append('cp-code-original')
                elif after[0][4]==0:outer.events.append('startup-original')
                outer.code=list(after);return []
            def units(_):return [outer.unit]
            def cp_unit(_,row,value=None):
                if value is None:return outer.unit
                if value==b'\0\0':
                    assert outer.code[0][4]==0
                    outer.events.append('cp-parameter-original')
                else:outer.events.append('cp-parameter-enabled')
                outer.unit=value
                return b''
            def selection(_):return int(outer.ap[hex(0x02cf036f)],16)
            def ap_values(_):return dict(outer.ap)
            def file(_,op=0,offset=0,data=b'',length=0):
                if outer.fail=='unknown-file' and op==5:raise DeviceError('unknown file')
                if op==1:outer.mask|=2;outer.upload=b''
                elif op==2:outer.upload+=data
                elif op==3:
                    assert outer.upload==outer.package.carrier;outer.mask=(outer.mask|1)&~2
                elif op==6:
                    assert all(c[4]==0 for c in outer.code) and outer.unit==b'\0\0'
                    outer.events.append('file-cleanup')
                    if outer.fail=='cleanup':raise DeviceError('unlink failed')
                    outer.mask=0
                return dict(present=bool(outer.mask&1),mask=outer.mask,
                            data=outer.package.carrier[offset:offset+length] if length else b'')
        self.fake_session=FakeSession
    def installed(self,startup=True,mask=1):
        self.code=[]
        for on in (startup,True):
            b=bytearray(512);b[4]=int(on);self.code.append(bytes(b))
        self.unit=b'\1\0';self.mask=mask
    def perform(self,action='close'):
        import contextlib
        import a6700_unlock.fl23_package as package
        @contextlib.contextmanager
        def files(*_):yield None
        with mock.patch.object(package,'Package',return_value=self.package),mock.patch.object(package,'Session',self.fake_session),mock.patch.object(package,'service_files',files):
            return package.perform(self.backend,action,self.directory)
    def test_full_close_restores_startup_then_parameters_then_code_then_files(self):
        self.installed();ap=dict(self.ap);r=self.perform()
        self.assertEqual(r['status'],'completed');self.assertTrue(r['verified'])
        self.assertEqual(self.events,['startup-original','cp-parameter-original','save','cp-code-original','file-cleanup'])
        self.assertTrue(all(c==bytes(512) for c in self.code));self.assertEqual(self.unit,b'\0\0')
        self.assertEqual(self.mask,0);self.assertEqual(self.ap,ap)
        self.assertEqual(r['final_features'][0]['state'],'disabled')
    def test_old_retained_cp_and_off_file_are_not_reported_as_already_closed(self):
        self.installed(startup=False,mask=4);r=self.perform()
        self.assertEqual(r['status'],'completed');self.assertTrue(r['verified']);self.assertEqual(self.mask,0)
        self.assertIn('cp-code-original',self.events)
    def test_unknown_file_and_nonstock_custom_reference_stop_before_writes(self):
        for failure in ('unknown-file','custom-reference'):
            self.installed();self.events=[]
            self.fail=failure if failure=='unknown-file' else None
            self.ap[hex(0x02cf0ccc)]='11' if failure=='custom-reference' else '00'
            r=self.perform()
            self.assertEqual(r['status'],'error');self.assertFalse(r['write_attempted'])
            self.assertEqual(self.events,[]);self.assertEqual(self.unit,b'\1\0')
    def test_save_loss_leaves_cp_code_and_files_and_never_claims_success(self):
        self.installed();self.fail='save';r=self.perform()
        self.assertEqual(r['status'],'uncertain');self.assertFalse(r['verified'])
        self.assertEqual(self.events.count('save'),1)
        self.assertEqual(self.code[1][4],1);self.assertEqual(self.mask,1)
        self.assertTrue(self.backend._fl23_transport_uncertain)
    def test_cleanup_failure_is_not_reported_as_fully_removed(self):
        self.installed();self.fail='cleanup';r=self.perform()
        self.assertEqual(r['status'],'uncertain');self.assertFalse(r['verified'])
        self.assertEqual(self.mask,1);self.assertTrue(all(c==bytes(512) for c in self.code))
    def test_complete_install_close_roundtrip_and_idempotence(self):
        self.assertEqual(self.perform('unlock')['status'],'completed')
        self.assertEqual(self.perform()['status'],'completed')
        self.events=[];r=self.perform()
        self.assertEqual(r['status'],'no-op');self.assertFalse(r['write_attempted'])
        self.assertEqual(self.events,[])
    def test_new_options_keep_full_cleanup_of_old_retained_cp_and_off_file(self):
        from a6700_unlock.fl23_package import perform_options
        import a6700_unlock.fl23_package as module
        import contextlib
        self.package.carriers={1:self.package.carrier,2:b'names',3:b'both'}
        self.installed(startup=False,mask=4)
        @contextlib.contextmanager
        def files(*_):yield None
        with mock.patch.object(module,'Session',self.fake_session),mock.patch.object(module,'service_files',files):
            result=perform_options(self.package,self.backend,'close',self.directory,lambda _:None,['fl23'])
        self.assertTrue(result['verified'],result);self.assertEqual(self.mask,0)
        self.assertEqual(self.unit,b'\0\0');self.assertTrue(all(c==bytes(512) for c in self.code))


class IndependentOptionsTests(unittest.TestCase):
    def test_every_independent_transition_preserves_other_flag_and_cp(self):
        import contextlib
        import a6700_unlock.fl23_package as module
        import itertools
        for flags,feature,action in itertools.product(range(4),('fl23','creative_look_names'),('unlock','close')):
            with self.subTest(flags=flags,feature=feature,action=action),tempfile.TemporaryDirectory() as tmp:
                recipe=synthetic();recipe['entries'].append(dict(recipe['entries'][0],index=1,device=2))
                spec=dict(key=123,bytes=2,before_sha256=sha(b'\0\0'),after_sha256=sha(b'\1\0'),
                          edits=[dict(offset=0,disabled='00',enabled='01')]);recipe['cp_units']=[spec]
                package=SimpleNamespace(recipe=recipe,profile=Profile(recipe),
                    carriers={i:bytes([i])*1100 for i in (1,2,3)})
                bit=1 if feature=='fl23' else 2
                wanted=flags|bit if action=='unlock' else flags&~bit
                state=dict(flags=flags,startup=bool(flags),cp=bool(flags&1),unit=bytes([int(bool(flags&1)),0]),events=[])
                class Channel:
                    def send(self,*_):state['events'].append('save');return 0,b''
                backend=SimpleNamespace(channel=Channel())
                class Fake:
                    def __init__(self,p,b,i,d):self.package=p;self.backend=b;self.directory=Path(d)
                    def blocks(self,before=None,after=None):
                        actual=[]
                        for on in (state['startup'],state['cp']):
                            v=bytearray(512);v[4]=int(on);actual.append(bytes(v))
                        if before is None:return actual
                        if before!=actual:raise DeviceError('stale preimage')
                        if after[1][4]!=actual[1][4]:
                            if state['startup']:raise AssertionError('CP changed with active startup')
                            state['events'].append('cp-code')
                        state['startup']=bool(after[0][4]);state['cp']=bool(after[1][4]);return []
                    def units(self):return [state['unit']]
                    def cp_unit(self,row,value=None):
                        if value is None:return state['unit']
                        if state['startup']:raise AssertionError('CP parameters changed before startup disabled')
                        state['events'].append('cp-unit');state['unit']=value;return b''
                    def selection(self):return 0
                    def ap_values(self):return {hex(0x02cf036f):'00',hex(0x02cf0ccc):'00',hex(0x02cf0370):'00'*13}
                    def file(self,op=0,offset=0,data=b'',length=0,flags=1):
                        if op in (1,2,3,6) and state['startup']:raise AssertionError('carrier changed with active startup')
                        if op==1:state['upload']=b''
                        elif op==2:state['upload']+=data
                        elif op==3:
                            if state['upload']!=package.carriers[flags]:raise AssertionError('wrong variant')
                            state['flags']=flags
                        elif op==6:
                            if state['cp'] or state['unit']!=b'\0\0':raise AssertionError('cleanup before complete CP removal')
                            state['flags']=0
                        actual=state['flags']
                        return dict(present=bool(actual),mask=int(bool(actual)),flags=actual,
                            data=package.carriers[actual][offset:offset+length] if actual and length else b'')
                @contextlib.contextmanager
                def files(*_):yield None
                with mock.patch.object(module,'Session',Fake),mock.patch.object(module,'service_files',files):
                    result=module.perform_options(package,backend,action,Path(tmp),lambda _:None,[feature])
                self.assertTrue(result['verified'],result);self.assertEqual(state['flags'],wanted)
                self.assertEqual(result['status'],'no-op' if wanted==flags else 'completed')
                self.assertEqual(result['write_attempted'],wanted!=flags)
                self.assertEqual(state['startup'],bool(wanted));self.assertEqual(state['cp'],bool(wanted&1))
                self.assertEqual(state['unit'],bytes([int(bool(wanted&1)),0]))
                if feature=='creative_look_names':self.assertEqual(state['events'],[])
                self.assertEqual({r['id']:r['state'] for r in result['final_features']},dict(
                    fl23='enabled' if wanted&1 else 'disabled',creative_look_names='enabled' if wanted&2 else 'disabled'))


class FileMachine(native.Machine):
    def __init__(self,binary,config,filesystem,fail=None):
        super().__init__(SimpleNamespace(entries=[]),binary,config,[])
        self.fs=filesystem;self.fail=fail;self.written=[];self.renamed=[];self.unlinked=[]
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
        elif nr==35:
            path=self.cstring(b)
            if a!=(1<<64)-100 or c or path not in ('/setting/fl23','/setting/fl23.new','/setting/fl23.off'):
                raise AssertionError('unbounded unlink')
            if self.fail=='unlink':rc=-5
            elif path not in self.fs:rc=-2
            else:del self.fs[path];self.unlinked.append(path)
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
    def run_job(self,fs,op=0,offset=0,data=b'',length=0,fail=None,flags=0):
        nonce=bytes(range(16));config=struct.pack('<4sI16s32s4I',b'A67F',1,nonce,bytes.fromhex(self.recipe['profile_sha256']),op,offset,len(data) if op==2 else length,flags)+data
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
    def test_complete_cleanup_removes_only_three_verified_fixed_paths(self):
        paths=('/setting/fl23','/setting/fl23.new','/setting/fl23.off')
        fs={p:bytearray(self.carrier) for p in paths};fs['/setting/unrelated']=bytearray(b'keep')
        self.assertEqual(self.run_job(fs,5)[:2],(0,7))
        status,mask,_,vm=self.run_job(fs,6)
        self.assertEqual((status,mask),(0,0));self.assertEqual(vm.unlinked,list(paths))
        self.assertEqual(fs,{'/setting/unrelated':bytearray(b'keep')})
        self.assertEqual(self.run_job(fs,6)[:2],(0,0))
    def test_unknown_or_partial_file_blocks_cleanup_before_any_unlink(self):
        for bad in ('/setting/fl23','/setting/fl23.new','/setting/fl23.off'):
            fs={p:bytearray(self.carrier) for p in ('/setting/fl23','/setting/fl23.new','/setting/fl23.off')}
            fs[bad]=bytearray(b'unknown or partial');before={k:bytes(v) for k,v in fs.items()}
            self.assertNotEqual(self.run_job(fs,5)[0],0)
            status,_,_,vm=self.run_job(fs,6)
            self.assertNotEqual(status,0);self.assertEqual(vm.unlinked,[])
            self.assertEqual({k:bytes(v) for k,v in fs.items()},before)
    def test_unlink_and_directory_sync_errors_are_reported(self):
        fs={'/setting/fl23':bytearray(self.carrier)}
        self.assertNotEqual(self.run_job(fs,6,fail='unlink')[0],0)
        self.assertIn('/setting/fl23',fs)
        self.assertNotEqual(self.run_job(fs,6,fail='sync')[0],0)
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


@unittest.skipUnless(native.AVAILABLE,'native emulator unavailable')
class FileVariantNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name);cls.recipe=synthetic()
        cls.carriers={f:bytes([f])*n for f,n in ((1,1100),(2,1300),(3,1500))}
        (p/'recipe.json').write_text(json.dumps(cls.recipe));(p/'carrier.bin').write_bytes(cls.carriers[1])
        cls.out=p/'out';variants=cls.out/'variants'
        for f in (2,3):
            folder=variants/str(f);(folder/'runtime').mkdir(parents=True)
            (folder/'carrier.bin').write_bytes(cls.carriers[f]);(folder/'runtime/manifest.json').write_text('{"files":{}}')
        builder.build(p/'recipe.json',p/'carrier.bin',ROOT/'src/a6700_unlock/native/fl23_sha256.h',destination=cls.out,variants_path=variants)
        cls.binary=(cls.out/'files.elf').read_bytes()
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    run_job=FileNativeTests.run_job
    def test_all_variants_are_identified_and_atomic_switch_preserves_live_until_commit(self):
        for old in (1,2,3):
            for new in (1,2,3):
                fs={'/setting/fl23':bytearray(self.carriers[old])}
                status,detail,data,_=self.run_job(fs,length=len(self.carriers[old]))
                self.assertEqual((status,detail>>8,data),(0,old,self.carriers[old]))
                self.assertEqual(self.run_job(fs,1,flags=new)[0],0)
                self.assertEqual(self.run_job(fs,2,data=self.carriers[new],flags=new)[0],0)
                self.assertEqual(bytes(fs['/setting/fl23']),self.carriers[old])
                self.assertEqual(self.run_job(fs,3,flags=new)[0],0)
                self.assertEqual(bytes(fs['/setting/fl23']),self.carriers[new])
    def test_wrong_requested_variant_and_unknown_content_cannot_commit(self):
        fs={'/setting/fl23':bytearray(self.carriers[1]),'/setting/fl23.new':bytearray(self.carriers[2])}
        status,_,_,vm=self.run_job(fs,3,flags=3)
        self.assertEqual(status,1);self.assertEqual(vm.renamed,[])
        self.assertEqual(fs['/setting/fl23'],self.carriers[1])
        fs['/setting/fl23']=bytearray(b'unknown')
        self.assertEqual(self.run_job(fs,3,flags=2)[0],1)
    def test_cleanup_accepts_known_mixed_variants_but_preserves_unknown_before_unlink(self):
        fs={path:bytearray(self.carriers[f]) for path,f in zip(('/setting/fl23','/setting/fl23.new','/setting/fl23.off'),(1,2,3))}
        self.assertEqual(self.run_job(fs,5)[:2],(0,7))
        fs['/setting/fl23.off'][0]=99
        status,_,_,vm=self.run_job(fs,6)
        self.assertEqual(status,1);self.assertFalse(vm.unlinked);self.assertEqual(len(fs),3)
        fs['/setting/fl23.off']=bytearray(self.carriers[3])
        self.assertEqual(self.run_job(fs,6)[:2],(0,0));self.assertEqual(fs,{})
    def test_read_and_append_bounds_follow_actual_and_requested_variant(self):
        fs={'/setting/fl23':bytearray(self.carriers[1])}
        self.assertEqual(self.run_job(fs,offset=1099,length=2)[0],2)
        self.assertEqual(self.run_job(fs,1,flags=2)[0],0)
        self.assertEqual(self.run_job(fs,2,offset=1300,data=b'x',flags=2)[0],2)
        self.assertEqual(self.run_job(fs,1,flags=4)[0],2)


if __name__=='__main__':unittest.main()
