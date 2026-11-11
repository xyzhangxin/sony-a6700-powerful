"""Fixed native setting reset, independently observed before/after every call."""
import hashlib
from importlib.resources import files
import json
import secrets
import struct
import time

from .close_state import SETTINGS,read_close_state
from .device_protocol import DeviceError
from .desktop.aftercare import FL23_STARTUP_NOTICE

TARGETS={'ftp':'off','sound':'on','shutter':'off','exposure':'pasm'}
ORDER=('ftp','sound','shutter','exposure')


class SettingRestartRequired(DeviceError):
    pass


RESTART_MESSAGE=('FTP已关闭并保存，传输及存储卡访问已停止，但网络退出尚未完成。'
                 '请正常关机、断开USB和外接供电、取出电池，等待10秒后重新开机，再连接检查；不会重复关闭FTP。'
                 +FL23_STARTUP_NOTICE)


def setting_assets():
    root=files('a6700_unlock');data=root.joinpath('data/native')
    try:
        m=json.loads(data.joinpath('close-setting.json').read_text())
        names={'close-setting-source.c','close-setting.c','close-setting-profile.h',
               'close-state-profile.h','close-state-sha256.h'}
        if (m.get('schema')!=1 or m.get('kind')!=76 or m.get('model')!='ILCE-6700' or
                m.get('firmware')!='2.00' or set(m.get('sources',{}))!=names or
                m.get('targets')!=TARGETS):raise DeviceError('自动复位组件清单不匹配')
        for name,digest in m['sources'].items():
            if hashlib.sha256(data.joinpath(name).read_bytes()).hexdigest()!=digest:
                raise DeviceError('自动复位组件来源校验失败')
        for name in ('close_state.c','close_setting.c'):
            raw=root.joinpath('native/'+name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=m['native_sources'][name]:
                raise DeviceError('自动复位组件尚未按当前源码重建')
        binary=data.joinpath('close-setting.elf').read_bytes()
        if len(binary)>1048572 or hashlib.sha256(binary).hexdigest()!=m['payload_sha256']:
            raise DeviceError('自动复位组件二进制校验失败')
        return binary,m
    except (OSError,KeyError,ValueError) as exc:
        raise DeviceError('自动复位组件缺失或损坏') from exc


def setting_request(feature,before,nonce):
    if feature not in TARGETS or before not in SETTINGS[feature][3]:raise DeviceError('未知复位请求')
    old=SETTINGS[feature][3].index(before)
    if old not in ((0,),(1,2),(0,),(1,))[ORDER.index(feature)]:raise DeviceError('只允许已验证的开启到默认状态复位')
    if len(nonce)!=16:raise DeviceError('复位请求标识不正确')
    return struct.pack('<4sI16sIIII',b'A67P',1,nonce,76,ORDER.index(feature)+1,old,1024)


def parse_setting_reply(raw,cfg):
    if len(raw)!=1068:raise DeviceError('自动复位回复长度不符，执行状态待核对')
    header=struct.unpack_from('<4sI16sIIQI',raw)
    expected=(b'A67B',1,cfg[8:24],0,76,int.from_bytes(cfg[28:36],'little'),1024)
    if header!=expected:raise DeviceError('自动复位未获得成功回复，已停止后续修改')
    if struct.unpack_from('<2Q',raw,44+20*8)!=(1,1) or any(raw[44+22*8:300]):
        raise DeviceError('原生设置调用未确认返回')


def _returned_setting(run):
    """Only a returned native call with restored service permits observation."""
    try:
        r=json.loads((run/'result.json').read_text())
        cfg=(run/'request.bin').read_bytes()
        intent=json.loads((run/'intent.json').read_text())
        if (r.get('service_restore_verified') is not True or
                hashlib.sha256(cfg).hexdigest()!=intent['request_sha256']):return False
        parse_setting_reply((run/'reply.bin').read_bytes(),cfg)
        return True
    except (OSError,KeyError,ValueError,DeviceError,struct.error):return False


def _ready(feature,row):
    good=row.get('verified') is True and row.get('current')==row.get('saved')==TARGETS[feature]
    if feature=='ftp':good=good and all(row.get(k) is True for k in ('transfer_idle','connection_idle','media_released'))
    if feature=='exposure':good=good and row.get('recall_inactive') is True
    return good


def _restart_ready(feature,state):
    row=state.get('features',{}).get('ftp',{})
    return (feature=='ftp' and state.get('camera_idle') is True and row.get('verified') is True and
            row.get('current')==row.get('saved')=='off' and row.get('io_idle') is True)


def _proof(run,state):
    return dict(state=state,intent_sha256=hashlib.sha256((run/'intent.json').read_bytes()).hexdigest(),
                result_sha256=hashlib.sha256((run/'result.json').read_bytes()).hexdigest())


def pending_settings(directory):
    """Call under the shared device lock, before authentication or a power cycle."""
    from pathlib import Path
    from .operations import default_state_dir
    pending=[]
    for root in {Path(directory),default_state_dir()}:
        for intent in (root/'runs').glob('close-setting-*/intent.json'):
            final=intent.with_name('result.json')
            try:result=json.loads(final.read_text())
            except (OSError,ValueError):result={}
            if result.get('status')=='SETTING_RESET_VERIFIED' and result.get('service_restore_verified') is True:continue
            try:
                proof=json.loads(intent.with_name('verification.json').read_text())
                feature=result['feature'];state=proof['state']
                if (_returned_setting(intent.parent) and proof['intent_sha256']==hashlib.sha256(intent.read_bytes()).hexdigest()
                        and proof['result_sha256']==hashlib.sha256(final.read_bytes()).hexdigest()
                        and state['camera_idle'] is True and _ready(feature,state['features'][feature])):continue
            except (OSError,ValueError,KeyError):pass
            pending.append(intent.parent)
    return pending


def guard_pending_settings(directory,*,observe_returned=False,allow_restart=False):
    for run in pending_settings(directory):
        if observe_returned and _returned_setting(run):continue
        if allow_restart and _returned_setting(run):
            try:
                proof=json.loads((run/'restart.json').read_text())
                result=json.loads((run/'result.json').read_text())
                if proof==_proof(run,proof['state']) and _restart_ready(result['feature'],proof['state']):continue
            except (OSError,ValueError,KeyError):pass
        raise DeviceError('上次机身设置复位尚未独立确认，请保留记录并先核对：'+str(run))


def verify_pending_settings(backend,directory):
    """Observe only proven-returned calls; never resend a setting operation."""
    from .operations import save_json
    for run in pending_settings(directory):
        if not _returned_setting(run):raise DeviceError('上次原生调用是否退出未知，不能重新访问相机')
        old=json.loads((run/'result.json').read_text());feature=old['feature']
        state=read_close_state(backend,ORDER,secrets.token_hex(16))
        if not state['camera_idle'] or not _ready(feature,state['features'][feature]):
            if _restart_ready(feature,state):
                if not (run/'restart.json').exists():save_json(run/'restart.json',_proof(run,state))
                raise SettingRestartRequired(RESTART_MESSAGE)
            raise DeviceError('原生调用已经返回，但设置或资源释放仍未完成；未重复发送设置')
        save_json(run/'verification.json',_proof(run,state))


def reset_close_setting(backend,feature,target):
    from .operations import default_state_dir,make_directory,save_json
    from .fl23_package import service_files,save_bytes
    if TARGETS.get(feature)!=target:raise DeviceError('自动复位目标不受支持')
    binary,manifest=setting_assets()
    before=read_close_state(backend,ORDER,secrets.token_hex(16))
    if before['camera_idle'] is not True or before['features']['exposure']['recall_inactive'] is not True:
        raise DeviceError('请停止录像并退出存储调用后再关闭功能')
    current=before['features'][feature]
    if current['current']!=current['saved']:raise DeviceError('运行设置与保存设置尚未一致')
    cfg=setting_request(feature,current['current'],secrets.token_bytes(16))
    run=default_state_dir()/'runs'/('close-setting-'+secrets.token_hex(12));make_directory(run)
    result=dict(action='reset-close-setting',feature=feature,target=target,status='NOT_SENT',
                write_attempted=False,settings_write_attempted=False,verified=False,service_restore_verified=False)
    try:
        with service_files(backend,run) as invoke:
            save_json(run/'intent.json',dict(feature=feature,target=target,before=before,
                payload_sha256=manifest['payload_sha256'],request_sha256=hashlib.sha256(cfg).hexdigest()))
            save_bytes(run/'request.bin',cfg)
            result.update(status='SETTING_RESET_UNCONFIRMED',settings_write_attempted=True)
            raw=invoke(binary,cfg);save_bytes(run/'reply.bin',raw)
            parse_setting_reply(raw,cfg)
        result['service_restore_verified']=True
        # Only observations repeat. The setting command is sent exactly once.
        deadline=time.monotonic()+15
        for attempt in range(30):
            state=read_close_state(backend,ORDER,secrets.token_hex(16))
            row=state['features'][feature]
            if not state['camera_idle']:raise DeviceError('复位后相机进入录像状态，已停止')
            if any(state['features'][f]!=before['features'][f] for f in ORDER if f!=feature):
                raise DeviceError('复位影响了其他功能设置，已停止后续修改')
            good=row['current']==row['saved']==target
            if feature=='ftp':good=good and all(row[k] for k in ('transfer_idle','connection_idle','media_released'))
            if feature=='exposure':good=good and row['recall_inactive']
            result['last_observed']=state
            if good:
                result.update(status='SETTING_RESET_VERIFIED',verified=True)
                return
            if time.monotonic()>=deadline:break
            time.sleep(.25)
        if _restart_ready(feature,state):
            result['status']='SETTING_RESET_RESTART_REQUIRED'
            raise SettingRestartRequired(RESTART_MESSAGE)
        raise DeviceError('复位后的运行、保存或资源释放未在限定时间内确认；不会重复发送设置')
    except BaseException as exc:
        result['error']=str(exc) if isinstance(exc,DeviceError) else type(exc).__name__
        raise
    finally:
        save_json(run/'result.json',result)
        if result['status']=='SETTING_RESET_RESTART_REQUIRED':
            save_json(run/'restart.json',_proof(run,result['last_observed']))
