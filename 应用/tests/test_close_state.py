"""Native FTP enums and runtime evidence, independent of a camera."""
import unittest

from a6700_unlock.close_state import FTP_FIELDS, SETTINGS, ftp_snapshot, setting_snapshot
from a6700_unlock.device_protocol import DeviceError


class FTPStateTests(unittest.TestCase):
    def sample(self):
        ones = {'setting_ftp_function': 1, 'ftp_transfer_canceling': 1,
                'ftp_stopping': 1, 'ftp_transfer_state': 10}
        return dict(double_read_identical=True, code_guards_verified=True,
            ftp_resource_table='0x2000', ftp_client_instance='0x1000', ftp_media_manager='0x3000', ftp_resource_threads=2,
            ftp_client_state=1,ftp_channel_table='0x4000',
            ftp_channels=[dict(index=i,state=0,transfer_thread='0x0') for i in range(2)],
            ftp_read_queue=dict(first='0x5004',last='0x5004',first_node='0x6000',last_node='0x6000'),
            rows=[dict(id=hex(spec[0]), name=name, current=ones.get(name, 0), shadow=ones.get(name, 0))
                  for name,spec in FTP_FIELDS.items()])
    def test_only_complete_stopped_and_resource_free_state_passes(self):
        r=ftp_snapshot(self.sample(), 1, 'nonce')
        self.assertEqual(r['nonce'], 'nonce')
        self.assertTrue(r['features']['ftp']['media_released'])
        self.assertEqual(r['features']['ftp']['current'], 'off')
    def test_native_zero_stopping_and_canceling_are_not_idle(self):
        for field in ('ftp_stopping', 'ftp_transfer_canceling'):
            state=self.sample()
            row=next(r for r in state['rows'] if r['name']==field);row.update(current=0,shadow=0)
            self.assertFalse(ftp_snapshot(state,1,'n')['features']['ftp']['transfer_idle'])
    def test_no_transfer_icon_cannot_hide_live_resources(self):
        for case in ('thread','channel','client','queue'):
            state=self.sample()
            if case=='thread':state['ftp_channels'][0]['transfer_thread']='0x7000'
            elif case=='channel':state['ftp_channels'][0]['state']=2
            elif case=='client':state['ftp_client_state']=6
            else:state['ftp_read_queue']['last']='0x5008'
            self.assertFalse(ftp_snapshot(state,1,'n')['features']['ftp']['media_released'])
            self.assertFalse(ftp_snapshot(state,1,'n')['features']['ftp']['io_idle'])
    def test_inconsistent_or_missing_resource_detail_is_rejected(self):
        for case in ('missing','count','table','queue','boolean'):
            state=self.sample()
            if case=='missing':del state['ftp_channels']
            elif case=='count':state['ftp_resource_threads']=1
            elif case=='table':state['ftp_channel_table']='0x0'
            elif case=='queue':state['ftp_read_queue']['last']=None
            else:state['ftp_channels'][0]['state']=False
            with self.assertRaises(DeviceError):ftp_snapshot(state,1,'n')
    def test_pending_network_work_is_not_idle(self):
        for name in FTP_FIELDS:
            if not name.startswith('is_'):continue
            state=self.sample();row=next(r for r in state['rows'] if r['name']==name)
            row.update(current=1,shadow=1)
            self.assertFalse(ftp_snapshot(state,1,'n')['features']['ftp']['media_released'])

    def test_network_teardown_keeps_firmware_guard_but_distinguishes_completed_io(self):
        state=self.sample()
        next(r for r in state['rows'] if r['id']=='0xe0f').update(current=1,shadow=1)
        row=ftp_snapshot(state,1,'n')['features']['ftp']
        self.assertTrue(row['io_idle'])
        self.assertFalse(row['connection_idle'])
        self.assertFalse(row['media_released'])
    def test_unknown_incomplete_or_unstable_rejected(self):
        for case in ('unknown','missing','duplicate','shadow','boolean','guards'):
            state=self.sample()
            if case=='unknown':state['rows'][0]['current']=999
            elif case=='missing':state['rows'].pop()
            elif case=='duplicate':state['rows'].append(state['rows'][0])
            elif case=='shadow':state['rows'][0]['shadow']=1
            elif case=='boolean':state['rows'][0]['current']=False
            else:state['code_guards_verified']=False
            with self.assertRaises(DeviceError):ftp_snapshot(state,1,'n')
    def test_saved_and_runtime_are_reported_separately(self):
        r=ftp_snapshot(self.sample(),0,'n')['features']['ftp']
        self.assertEqual((r['current'],r['saved']),('off','on'))
    def test_internal_state_shadow_is_not_a_saved_setting(self):
        state=self.sample()
        next(r for r in state['rows'] if r['id']=='0xe13').update(current=3,shadow=0)
        result=ftp_snapshot(state,0,'n')['features']['ftp']
        self.assertFalse(result['transfer_idle'])
        self.assertFalse(result['media_released'])
    def test_recording_blocks_camera_idle(self):
        state=self.sample();state['rows'][0].update(current=1,shadow=1)
        self.assertFalse(ftp_snapshot(state,1,'n')['camera_idle'])

    def settings_sample(self):
        sample=self.sample()
        for f,(i,n,_,values) in SETTINGS.items():
            if f!='ftp':sample['rows'].append(dict(id=hex(i),name=n,current=0,shadow=0))
        for i,n in ((0x66,'expand_mr_recall_state'),(0x376,'shoot_set_registration_recall_status'),
                    (0x377,'shoot_set_registration_recall_execution_status'),
                    (0x1171,'is_operating_shoot_set_registration_recall')):
            sample['rows'].append(dict(id=hex(i),name=n,current=0,shadow=0))
        return sample

    def test_all_four_settings_keep_independent_saved_values(self):
        result=setting_snapshot(self.settings_sample(),dict(ftp=1,sound=3,shutter=1,exposure=1),'n')
        self.assertEqual(result['features']['sound']['current'],'on')
        self.assertEqual(result['features']['sound']['saved'],'off')
        self.assertEqual(result['features']['shutter']['saved'],'off')
        self.assertEqual(result['features']['exposure']['saved'],'flexible')
        self.assertTrue(result['features']['exposure']['recall_inactive'])

    def test_each_recall_guard_blocks_exposure(self):
        for i in (0x66,0x376,0x377,0x1171):
            state=self.settings_sample()
            next(r for r in state['rows'] if r['id']==hex(i)).update(current=1,shadow=1)
            self.assertFalse(setting_snapshot(state,{'exposure':1},'n')['features']['exposure']['recall_inactive'])

    def test_sound_native_four_values_and_unknown(self):
        for value,wanted in enumerate(('on','electronic-only','except-shutter','off')):
            state=self.settings_sample()
            next(r for r in state['rows'] if r['id']=='0x69a').update(current=value,shadow=value)
            result=setting_snapshot(state,{'sound':value},'n')['features']['sound']
            self.assertEqual((result['current'],result['saved']),(wanted,wanted))
        with self.assertRaises(DeviceError):setting_snapshot(state,{'sound':4},'n')

    def test_missing_recall_or_unstable_setting_rejected(self):
        state=self.settings_sample();state['rows'].pop()
        with self.assertRaises(DeviceError):setting_snapshot(state,{'exposure':0},'n')
        state=self.settings_sample()
        next(r for r in state['rows'] if r['id']=='0x6b2')['shadow']=1
        with self.assertRaises(DeviceError):setting_snapshot(state,{'shutter':1},'n')


if __name__=='__main__':unittest.main()
