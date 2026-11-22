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

    def test_missing_loader_endpoint_retains_native_status(self):
        manifest, nonce, response = self.fixture()
        raw = bytearray(response)
        struct.pack_into('<I', raw, 24, 8)
        with self.assertRaisesRegex(DeviceError, '未检测到声音加载器的运行端点.*原生状态 8'):
            parse(bytes(raw), nonce, manifest, 'c' * 64, 'a' * 64)

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

    def test_status8_subdiagnosis_classifies_boot_wrapper_autoload_and_receipt_states(self):
        from a6700_unlock.custom_sound_binding import AUTOLOAD_MAGIC, WRAPPER_MAGIC, CustomSoundBindingError

        def parse_status8(raw, manifest):
            with self.assertRaises(CustomSoundBindingError) as ctx:
                parse(bytes(raw), nonce, manifest, 'c' * 64, 'a' * 64)
            exc = ctx.exception
            self.assertIsInstance(exc, DeviceError)
            self.assertEqual(exc.native_status, 8)
            self.assertRegex(str(exc), '未检测到声音加载器的运行端点.*原生状态 8')
            self.assertNotIn('01234567-89ab', str(exc.diagnostic))
            self.assertNotIn(hex(0x19000000), str(exc.diagnostic))
            self.assertNotIn(hex(0x52000000), str(exc.diagnostic))
            return exc

        manifest, nonce, response = self.fixture()

        # 1. No boot context socket (e.g. standby power switch without 10s battery pull)
        no_ctx = bytearray(response)
        struct.pack_into('<I', no_ctx, 24, 8)
        struct.pack_into('<Q', no_ctx, 44 + 8 * 3, 0)
        exc = parse_status8(no_ctx, manifest)
        self.assertEqual(exc.diagnostic['category'], 'no-boot-context')
        self.assertEqual(exc.diagnostic['recovery_mode'], 'power-cycle-wait')
        self.assertIn('无需在工具中' if False else '不需要关闭自定义快门声', str(exc))

        # 2. Boot socket exists, but page not matched
        no_page = bytearray(response)
        struct.pack_into('<I', no_page, 24, 8)
        struct.pack_into('<Q', no_page, 44 + 8 * 10, 0)
        exc = parse_status8(no_page, manifest)
        self.assertEqual(exc.diagnostic['category'], 'boot-page-not-found')
        self.assertEqual(exc.diagnostic['recovery_mode'], 'power-cycle-wait')

        # 3. Same boot session as previous recorded boot_id
        same_boot = bytearray(response)
        struct.pack_into('<I', same_boot, 24, 8)
        same_manifest = dict(manifest, previous_boot_id='01234567-89ab-cdef-0123-456789abcdef')
        exc = parse_status8(same_boot, same_manifest)
        self.assertEqual(exc.diagnostic['category'], 'same-boot-session')
        self.assertTrue(exc.diagnostic['same_boot_session'])

        # 4. Early boot with zeroed receipt and no autoload header -> startup-in-progress
        early = bytearray(response)
        struct.pack_into('<I', early, 24, 8)
        early[556 + 1120:556 + 1632] = b'\x00' * 512
        exc = parse_status8(early, manifest)
        self.assertEqual(exc.diagnostic['category'], 'startup-in-progress')
        self.assertEqual(exc.diagnostic['recovery_mode'], 'power-cycle-wait')
        self.assertFalse(exc.diagnostic['receipt_present'])

        # 5. Stale .shutter-autoload-pending marker (-17 == -EEXIST) -> lossless-recover (step 2, 12, 13)
        for step, expected_cat in (
            (2, 'autoload-pending-blocked'),
            (12, 'autoload-pending-bypassed-once'),
            (13, 'autoload-pending-blocked'),
        ):
            pending_eexist = bytearray(response)
            struct.pack_into('<I', pending_eexist, 24, 8)
            struct.pack_into('<QQq32Q', pending_eexist, 556 + 1664, AUTOLOAD_MAGIC, 1, -17, step, *([0] * 31))
            exc = parse_status8(pending_eexist, manifest)
            self.assertEqual(exc.diagnostic['category'], expected_cat)
            self.assertEqual(exc.diagnostic['recovery_mode'], 'lossless-recover')
            self.assertEqual(exc.diagnostic['autoload_status'], -17)
            self.assertEqual(exc.diagnostic['autoload_step'], step)

        # 6. Autoload failed with detail[14] retained pending lock vs clean failure
        retained = bytearray(response)
        struct.pack_into('<I', retained, 24, 8)
        detail = [0] * 32
        detail[0] = 5
        detail[14] = 1
        struct.pack_into('<QQq32Q', retained, 556 + 1664, AUTOLOAD_MAGIC, 1, -110, *detail)
        exc = parse_status8(retained, manifest)
        self.assertEqual(exc.diagnostic['category'], 'autoload-failed')
        self.assertEqual(exc.diagnostic['recovery_mode'], 'lossless-recover')
        self.assertTrue(exc.diagnostic['autoload_pending_retained'])

        # 6b. Rev-5 legacy receipt still validates normal parse without false mismatch
        legacy_response = bytearray(response)
        legacy_receipt = bytes.fromhex(manifest['legacy_receipts']['1287'])
        legacy_response[556 + 1120:556 + 1632] = legacy_receipt
        parsed_legacy = parse(bytes(legacy_response), nonce, manifest, 'c' * 64, 'a' * 64)
        self.assertEqual(parsed_legacy.package_id, 'custom_shutter_sound')

        # 7. Non-zero non-matching receipt -> carrier-receipt-mismatch
        mismatch = bytearray(response)
        struct.pack_into('<I', mismatch, 24, 8)
        mismatch[556 + 1120] ^= 0xff
        exc = parse_status8(mismatch, manifest)
        self.assertEqual(exc.diagnostic['category'], 'carrier-receipt-mismatch')
        self.assertEqual(exc.diagnostic['recovery_mode'], 'update-carrier')

        # 8. Wrapper primary/early abort -> carrier-wrapper-failed
        wrapper_fail = bytearray(response)
        struct.pack_into('<I', wrapper_fail, 24, 8)
        struct.pack_into('<QQQqQqqq', wrapper_fail, 556 + 1984, WRAPPER_MAGIC, 7, 1, -5, 0, 0, 0, -5)
        exc = parse_status8(wrapper_fail, manifest)
        self.assertEqual(exc.diagnostic['category'], 'carrier-wrapper-failed')
        self.assertEqual(exc.diagnostic['wrapper_early_abort_rc'], -5)
