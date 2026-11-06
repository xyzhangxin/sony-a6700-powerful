"""Packaged observer and USB service-file lifecycle; no camera accesses."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from a6700_unlock.close_state import observer_assets,observer_request,parse_observer,read_ftp_state
from a6700_unlock.device_protocol import DeviceError


def reply(nonce,manifest):
    body=bytearray(1024)
    meta=[1,534,1354,0x7000000000,2,len(manifest['fields']),len(manifest['guards']),0,
          0x7100000000,0xd4a000,2,0xd49000,0xd4aa00,2,0xd4b000,1,
          0xd4c004,0xd4c004,0xd4d000,0xd4d000]+[0]*12
    struct.pack_into('<32Q',body,0,*meta)
    values={0x5a5:1,0x265:1,0xe1a:1,0x93e:10}
    for i,field in enumerate(manifest['fields']):
        at=256+i*16;struct.pack_into('<II',body,at,field['id'],field['width'])
        value=values.get(field['id'],0).to_bytes(field['width'],'little')
        body[at+8:at+8+field['width']*2]=value*2
    for i in range(2):struct.pack_into('<IIQ',body,768+i*32,i,0,0)
    return struct.pack('<4sI16sIIQI',b'A67B',1,bytes.fromhex(nonce),0,75,0,1024)+body


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.binary,self.manifest=observer_assets()
        self.nonce='ab'*16
    def test_source_bound_parser_and_request(self):
        cfg=observer_request(self.nonce)
        self.assertEqual(len(cfg),40)
        r=parse_observer(reply(self.nonce,self.manifest),self.nonce,self.manifest)
        self.assertEqual(len(r['ftp_channels']),2)
        self.assertEqual(r['ftp_read_queue']['first'],r['ftp_read_queue']['last'])
    def test_reply_mutation_and_error_are_rejected(self):
        original=reply(self.nonce,self.manifest)
        for offset in (0,4,8,24,28,32,40,44,300,304,828,900):
            bad=bytearray(original);bad[offset]^=1
            with self.subTest(offset=offset),self.assertRaises(DeviceError):
                parse_observer(bad,self.nonce,self.manifest)
    def test_oversized_field_list_is_rejected_before_parsing(self):
        m=deepcopy(self.manifest);m['fields']*=2
        with self.assertRaises(DeviceError):parse_observer(reply(self.nonce,self.manifest),self.nonce,m)
    def test_corrupt_assets_fail_before_service_upload(self):
        original=Path(__file__).resolve().parents[1]/'src/a6700_unlock'
        for damage in ('close-state.elf','close-state-source.c','close-state-profile.h'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'data/native').mkdir(parents=True);(root/'native').mkdir()
                for name in ('close-state.json','close-state.elf',*self.manifest['sources']):
                    (root/'data/native'/name).write_bytes((original/'data/native'/name).read_bytes())
                (root/'native/close_state.c').write_bytes((original/'native/close_state.c').read_bytes())
                (root/'data/native'/damage).write_bytes(b'bad')
                with patch('a6700_unlock.close_state.files',return_value=root),self.assertRaises(DeviceError):observer_assets()
    def test_service_restores_before_returning_safe_result(self):
        events=[]
        @contextmanager
        def service(backend,directory):
            events.append('open')
            try:yield lambda binary,cfg:reply(cfg[8:24].hex(),self.manifest)
            finally:events.append('restored')
        def send(category,cfg):
            self.assertEqual((category,cfg),(0x40,struct.pack('<HHI',0x603,1,0x02cf03e0)))
            events.append('saved-read');return 0,b'\x01'
        backend=SimpleNamespace(channel=SimpleNamespace(send=send))
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),patch('a6700_unlock.fl23_package.service_files',service):
            state=read_ftp_state(backend,self.nonce)
            report=json.loads(next(Path(tmp).glob('runs/*/result.json')).read_text())
        self.assertTrue(state['features']['ftp']['media_released'])
        self.assertEqual(events,['open','saved-read','saved-read','restored'])
        self.assertTrue(report['service_restore_verified'])
        self.assertFalse(report['write_attempted'])
    def test_saved_read_failure_marks_uncertain_transport_without_retry(self):
        calls=[]
        @contextmanager
        def service(*_):yield lambda binary,cfg:reply(cfg[8:24].hex(),self.manifest)
        def fail(*_):calls.append(1);raise OSError('lost response')
        backend=SimpleNamespace(channel=SimpleNamespace(send=fail))
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),patch('a6700_unlock.fl23_package.service_files',service):
            with self.assertRaises(OSError):read_ftp_state(backend,self.nonce)
        self.assertTrue(backend._fl23_transport_uncertain)
        self.assertEqual(calls,[1])
    def test_service_restore_failure_does_not_return_success(self):
        @contextmanager
        def service(*_):
            yield lambda binary,cfg:reply(cfg[8:24].hex(),self.manifest)
            raise DeviceError('restore failed')
        backend=SimpleNamespace(channel=SimpleNamespace(send=lambda *_:(0,b'\x01')))
        with tempfile.TemporaryDirectory() as tmp,patch('a6700_unlock.operations.default_state_dir',return_value=Path(tmp)),patch('a6700_unlock.fl23_package.service_files',service):
            with self.assertRaises(DeviceError):read_ftp_state(backend,self.nonce)


if __name__=='__main__':unittest.main()
