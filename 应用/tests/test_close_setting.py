"""Single-dispatch native setting adapter, preservation and durable uncertainty."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from a6700_unlock.close_setting import (setting_assets,setting_request,parse_setting_reply,
    reset_close_setting,guard_pending_settings,verify_pending_settings,SettingRestartRequired,ORDER,TARGETS)
from a6700_unlock.device_protocol import DeviceError


def sample():
    return dict(camera_idle=True,features=dict(
        ftp=dict(current='on',saved='on',verified=True,transfer_idle=False,connection_idle=False,media_released=False),
        sound=dict(current='except-shutter',saved='except-shutter',verified=True),
        shutter=dict(current='on',saved='on',verified=True),
        exposure=dict(current='flexible',saved='flexible',verified=True,recall_inactive=True)))


def receipt(cfg):
    body=bytearray(1024);struct.pack_into('<2Q',body,160,1,1)
    return struct.pack('<4sI16sIIQI',b'A67B',1,cfg[8:24],0,76,int.from_bytes(cfg[28:36],'little'),1024)+body


class SettingTests(unittest.TestCase):
    def test_source_bound_assets_and_fixed_requests(self):
        binary,m=setting_assets();self.assertTrue(binary);self.assertEqual(m['targets'],TARGETS)
        for f,before in zip(ORDER,('on','except-shutter','on','flexible')):
            cfg=setting_request(f,before,b'a'*16);parse_setting_reply(receipt(cfg),cfg)
        for f,v in (('sound','off'),('sound','on'),('ftp','off'),('shutter','off'),('exposure','pasm')):
            with self.assertRaises(DeviceError):setting_request(f,v,b'a'*16)
    def test_stale_truncated_error_and_nonreturn_receipts_reject(self):
        cfg=setting_request('sound','except-shutter',b'a'*16);raw=receipt(cfg)
        for offset in (0,8,24,28,32,40,204,212,220):
            bad=bytearray(raw);bad[offset]^=1
            with self.subTest(offset=offset),self.assertRaises(DeviceError):parse_setting_reply(bad,cfg)
        with self.assertRaises(DeviceError):parse_setting_reply(raw[:-1],cfg)
    def run_reset(self,fault=None):
        state=sample();calls=[];events=[];reads=[]
        @contextmanager
        def service(*_):
            events.append('open')
            def invoke(binary,cfg):
                calls.append(cfg)
                if fault=='transport':raise OSError('lost acknowledgement')
                state['features']['sound'].update(current='on',saved='on')
                if fault=='other-setting':state['features']['shutter'].update(current='off',saved='off')
                return receipt(cfg)
            yield invoke
            if fault=='restore':raise DeviceError('service restoration failed')
            events.append('restored')
        def observe(*_):reads.append(1);return deepcopy(state)
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),\
                patch('a6700_unlock.fl23_package.service_files',service),patch('a6700_unlock.close_setting.read_close_state',observe):
            if fault:
                with self.assertRaises((DeviceError,OSError)):reset_close_setting(SimpleNamespace(),'sound','on')
                with self.assertRaises(DeviceError):guard_pending_settings(Path(tmp))
            else:
                reset_close_setting(SimpleNamespace(),'sound','on');guard_pending_settings(Path(tmp))
            result=json.loads(next(Path(tmp).glob('runs/*/result.json')).read_text(encoding='utf-8'))
        self.assertEqual(len(calls),1)
        return result,events,reads
    def test_success_requires_restoration_and_independent_read(self):
        result,events,reads=self.run_reset()
        self.assertEqual(result['status'],'SETTING_RESET_VERIFIED')
        self.assertEqual(events,['open','restored']);self.assertEqual(len(reads),2)
        self.assertTrue(result['settings_write_attempted']);self.assertFalse(result['write_attempted'])
    def test_transport_failure_never_resends_or_claims_rollback(self):
        result,_,reads=self.run_reset('transport')
        self.assertFalse(result['verified']);self.assertEqual(len(reads),1)
    def test_service_failure_does_not_verify_or_reread(self):
        result,_,reads=self.run_reset('restore')
        self.assertFalse(result['service_restore_verified']);self.assertEqual(len(reads),1)
    def test_unselected_setting_change_blocks_followup(self):
        result,_,_=self.run_reset('other-setting');self.assertFalse(result['verified'])
    def test_intent_without_result_blocks_new_session(self):
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)):
            run=Path(tmp)/'runs/close-setting-example';run.mkdir(parents=True);(run/'intent.json').write_text('{}')
            with self.assertRaises(DeviceError):guard_pending_settings(Path(tmp))

    def pending_fixture(self,root,feature='sound'):
        import hashlib
        run=root/'runs/close-setting-pending';run.mkdir(parents=True)
        cfg=setting_request(feature,'on' if feature=='ftp' else 'except-shutter',b'a'*16)
        (run/'request.bin').write_bytes(cfg);(run/'reply.bin').write_bytes(receipt(cfg))
        (run/'intent.json').write_text(json.dumps(dict(request_sha256=hashlib.sha256(cfg).hexdigest())))
        (run/'result.json').write_text(json.dumps(dict(feature=feature,service_restore_verified=True,status='SETTING_RESET_UNCONFIRMED')))
        return run

    def test_only_proven_returned_call_can_be_reobserved(self):
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)):
            root=Path(tmp);run=self.pending_fixture(root)
            with self.assertRaises(DeviceError):guard_pending_settings(root)
            guard_pending_settings(root,observe_returned=True)
            (run/'reply.bin').write_bytes(b'lost')
            with self.assertRaises(DeviceError):guard_pending_settings(root,observe_returned=True)

    def test_reobservation_closes_receipt_without_resending(self):
        state=sample();state['features']['sound'].update(current='on',saved='on')
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),\
                patch('a6700_unlock.close_setting.read_close_state',return_value=state) as read:
            root=Path(tmp);run=self.pending_fixture(root)
            verify_pending_settings(SimpleNamespace(),root);guard_pending_settings(root)
            self.assertEqual(read.call_count,1)
            self.assertTrue((run/'verification.json').exists())
            (run/'intent.json').write_text('{}')
            with self.assertRaises(DeviceError):guard_pending_settings(root)

    def test_unfinished_postcondition_keeps_write_guard(self):
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),\
                patch('a6700_unlock.close_setting.read_close_state',return_value=sample()):
            root=Path(tmp);run=self.pending_fixture(root)
            with self.assertRaises(DeviceError):verify_pending_settings(SimpleNamespace(),root)
            self.assertFalse((run/'verification.json').exists())
            with self.assertRaises(DeviceError):guard_pending_settings(root)

    def test_stopped_ftp_can_require_restart_without_allowing_firmware_write(self):
        state=sample();state['features']['ftp'].update(current='off',saved='off',io_idle=True)
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),\
                patch('a6700_unlock.close_setting.read_close_state',return_value=state):
            root=Path(tmp);run=self.pending_fixture(root,'ftp')
            with self.assertRaises(SettingRestartRequired):verify_pending_settings(SimpleNamespace(),root)
            self.assertTrue((run/'restart.json').exists())
            guard_pending_settings(root,allow_restart=True)
            with self.assertRaises(DeviceError):guard_pending_settings(root)
            state['features']['ftp'].update(transfer_idle=True,connection_idle=True,media_released=True)
            verify_pending_settings(SimpleNamespace(),root);guard_pending_settings(root)

    def test_active_ftp_io_cannot_authorize_restart(self):
        state=sample();state['features']['ftp'].update(current='off',saved='off',io_idle=False)
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),\
                patch('a6700_unlock.close_setting.read_close_state',return_value=state):
            root=Path(tmp);run=self.pending_fixture(root,'ftp')
            with self.assertRaises(DeviceError):verify_pending_settings(SimpleNamespace(),root)
            self.assertFalse((run/'restart.json').exists())
            with self.assertRaises(DeviceError):guard_pending_settings(root,allow_restart=True)


if __name__=='__main__':unittest.main()
