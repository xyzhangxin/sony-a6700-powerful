import struct
import unittest
from a6700_unlock.custom_sound_binding import assets,parse,request
from a6700_unlock.device_protocol import DeviceError

class BindingTests(unittest.TestCase):
    def fixture(self):
        m,_=assets(); nonce=bytes(range(16)); raw=bytearray(44+16384)
        struct.pack_into('<4sI16sIIQI',raw,0,b'A67B',1,nonce,0,71,0,16384)
        bias,base=0x19000000,0x52000000
        words=[0]*32
        for i,v in {0:1,1:123,2:124,3:1,6:bias,8:100,9:100,10:1,11:0x50000000,12:1,14:37}.items():words[i]=v
        struct.pack_into('<32Q',raw,44,*words)
        raw[300:336]=b'01234567-89ab-cdef-0123-456789abcdef'
        raw[556+1120:556+1632]=bytes.fromhex(m['receipts']['7'])
        for at,value in [(8,bias),(328,base),(336,m['module_bytes'])]:struct.pack_into('<Q',raw,556+at,value)
        struct.pack_into('<4Q',raw,44+4608,1,2,0,base)
        for sample in range(2):
            for i,f in enumerate(m['fields']):
                value={'image_base':base,'image_bytes':m['module_bytes'],
                    'host.report.startup_ready':1,'host.report.bindings_ready':1}.get(f['name'],0)
                struct.pack_into('<Q',raw,44+m['scalar_offset']+sample*8*len(m['fields'])+8*i,value)
            at=44+m['spans_offset']+sample*m['span_bytes']
            for s in m['spans']:
                data=bytearray.fromhex(s['after_hex'])
                for p in s['pointers']:
                    value=struct.unpack_from('<Q',data,p['offset'])[0]+{'app':bias,'module':base,'absolute':0}[p['after']]
                    struct.pack_into('<Q',data,p['offset'],value)
                raw[at:at+len(data)]=data;at+=len(data)
            at=44+m['entries_offset']+sample*m['entry_bytes']
            for name,offset,encoded in m['entries']:
                data=struct.pack('<IIQQ',0x23d7,0,bias+0x3781000,0) if name=='sound_options_eighth_entry' else bytes.fromhex(encoded)
                raw[at:at+len(data)]=data;at+=len(data)
        return m,nonce,bytes(raw)

    def test_complete_observation_binds_body_host_profile(self):
        m,n,_=self.fixture()
        for flag in ('4','5','6','7'):
            _,n,r=self.fixture()
            raw=bytearray(r)
            raw[556+1120:556+1632]=bytes.fromhex(m['receipts'][flag])
            binding=parse(bytes(raw),n,m,'c'*64,'a'*64)
            self.assertEqual(binding.device_identity_sha256,'c'*64)
            self.assertEqual(binding.package_sha256,m['host_sha256'])
        self.assertEqual(request(n)[8:24],n)

    def test_nonce_owner_boot_and_every_hook_entry_tamper_rejected(self):
        m,n,r=self.fixture()
        targets=[8,24,44,308,556+1120,44+4608]
        for sample in range(2):
            at=44+m['spans_offset']+sample*m['span_bytes']
            targets.extend(range(at,at+m['span_bytes'],8))
            at=44+m['entries_offset']+sample*m['entry_bytes']
            targets.extend(range(at,at+m['entry_bytes'],8))
        for at in targets:
            bad=bytearray(r);bad[at]^=1
            with self.subTest(at=at),self.assertRaises(DeviceError):parse(bytes(bad),n,m,'c'*64,'a'*64)
        with self.assertRaises(DeviceError):parse(r[:-1],n,m,'c'*64,'a'*64)

    def test_uncertain_sound_owner_rejected_in_both_samples(self):
        m,n,r=self.fixture()
        names=['dmm.uncertain','dmm.copy_failed','host.runtime.playback.unresolved']
        if any(f['name']=='dmm.arena.blocked' for f in m['fields']):
            names += ['dmm.arena.blocked','dmm.arena.used','dmm.arena.parent.handle','dmm.arena.tokens[0]']
        for sample in range(2):
            for name in names:
                index=next(i for i,f in enumerate(m['fields']) if f['name']==name)
                at=44+m['scalar_offset']+sample*8*len(m['fields'])+8*index
                bad=bytearray(r);struct.pack_into('<Q',bad,at,1)
                with self.subTest(sample=sample,name=name),self.assertRaises(DeviceError):
                    parse(bytes(bad),n,m,'c'*64,'a'*64)
