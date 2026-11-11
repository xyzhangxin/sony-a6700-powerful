"""Interpret source-bound A6700 FTP observations without inventing an idle state.

The native enum FTP_TRANSFER_STATE_INVALID (10) is not by itself proof of
idle. Both stopped-state flags and idle channels/empty read queues are needed;
the lifetime of resident resource managers does not indicate media ownership.
"""
import hashlib
from importlib.resources import files
import json
import secrets
import struct

from .device_protocol import DeviceError


def observer_assets():
    root=files('a6700_unlock')
    data=root.joinpath('data/native')
    try:
        manifest=json.loads(data.joinpath('close-state.json').read_text())
        expected={'close-state-source.c','close-state-profile.h','close-state-sha256.h'}
        if (manifest.get('schema')!=1 or manifest.get('model')!='ILCE-6700' or
                manifest.get('firmware')!='2.00' or manifest.get('kind')!=75 or
                manifest.get('response_bytes')!=1024 or manifest.get('readonly') is not True or
                set(manifest.get('sources',{}))!=expected):
            raise DeviceError('关闭状态读取器清单不匹配')
        for name,digest in manifest['sources'].items():
            if hashlib.sha256(data.joinpath(name).read_bytes()).hexdigest()!=digest:
                raise DeviceError('关闭状态读取器源码校验失败')
        implementation=root.joinpath('native/close_state.c').read_bytes()
        if (hashlib.sha256(implementation).hexdigest()!=manifest['native_source_sha256'] or
                implementation not in data.joinpath('close-state-source.c').read_bytes()):
            raise DeviceError('关闭状态读取器尚未按当前源码重建')
        binary=data.joinpath('close-state.elf').read_bytes()
        if len(binary)>1048572 or hashlib.sha256(binary).hexdigest()!=manifest['payload_sha256']:
            raise DeviceError('关闭状态读取器二进制校验失败')
        return binary,manifest
    except (OSError,KeyError,ValueError) as exc:
        raise DeviceError('关闭状态读取组件缺失或损坏') from exc


def observer_request(nonce):
    if not isinstance(nonce,str) or len(nonce)!=32:
        raise DeviceError('关闭状态请求标识不正确')
    try:raw=bytes.fromhex(nonce)
    except ValueError as exc:raise DeviceError('关闭状态请求标识不正确') from exc
    if len(raw)!=16:raise DeviceError('关闭状态请求标识不正确')
    return struct.pack('<4sI16sIQI',b'A67P',1,raw,75,0,1024)


def parse_observer(raw,nonce,manifest):
    if len(raw)!=1068:raise DeviceError('关闭状态读取回复长度不符')
    h=struct.unpack_from('<4sI16sIIQI',raw)
    if h!=(b'A67B',1,bytes.fromhex(nonce),0,75,0,1024):
        raise DeviceError('关闭状态读取未通过：代码身份、运行状态或请求校验失败')
    w=struct.unpack_from('<32Q',raw,44)
    fields=manifest['fields'];guards=manifest['guards']
    if (w[0]!=1 or not 0<w[1]<1<<31 or not 0<w[2]<1<<31 or not w[3] or w[3]&4095 or
            w[4:8]!=(2,len(fields),len(guards),0) or not w[8] or w[8]&4095 or w[13]>2 or any(w[20:]) or len(fields)>29):
        raise DeviceError('关闭状态双读结果不完整')
    rows=[]
    for i,field in enumerate(fields):
        row=raw[300+i*16:316+i*16];identifier,width=struct.unpack_from('<II',row)
        if identifier!=field['id'] or width!=field['width'] or width not in (1,4) or any(row[8+2*width:]):
            raise DeviceError('关闭状态字段不匹配')
        rows.append(dict(id=hex(identifier),name=field['name'],current=int.from_bytes(row[8:8+width],'little'),
                         shadow=int.from_bytes(row[8+width:8+width*2],'little')))
    if any(raw[300+len(fields)*16:812]) or any(raw[876:]):raise DeviceError('关闭状态包含额外数据')
    channels=[]
    for i in range(w[13]):
        index,state,thread=struct.unpack_from('<IIQ',raw,812+i*32)
        if index!=i or any(raw[828+i*32:844+i*32]):raise DeviceError('FTP传输通道数据不完整')
        channels.append(dict(index=index,state=state,transfer_thread=hex(thread)))
    if any(raw[812+w[13]*32:876]):raise DeviceError('FTP未使用通道包含数据')
    return dict(double_read_identical=True,code_guards_verified=True,rows=rows,
                ftp_resource_table=hex(w[9]),ftp_resource_threads=w[10],
                ftp_client_instance=hex(w[11]),ftp_media_manager=hex(w[12]),
                ftp_client_state=w[15],ftp_channels=channels,ftp_channel_table=hex(w[14]),
                ftp_read_queue=dict(first=hex(w[16]),last=hex(w[17]),first_node=hex(w[18]),last_node=hex(w[19])))


def read_ftp_state(backend,nonce):
    return read_close_state(backend, ('ftp',), nonce)


# Native Backup descriptors in A6700 2.00 bind each enum to one byte.
# These are settings only; FTP server configuration is never requested.
SETTINGS = {
    'ftp': (0x5a5, 'setting_ftp_function', 0x02cf03e0, ('on', 'off')),
    'sound': (0x69a, 'setting_audio_signals', 0x02cf048b,
              ('on', 'electronic-only', 'except-shutter', 'off')),
    'shutter': (0x6b2, 'setting_image_sensor_barrier', 0x02cf04a0, ('on', 'off')),
    'exposure': (0x497, 'setting_movie_exposure_mode_setting', 0x02cf02ae,
                 ('pasm', 'flexible')),
}


def setting_snapshot(runtime, saved, nonce):
    if runtime.get('double_read_identical') is not True or runtime.get('code_guards_verified') is not True:
        raise DeviceError('机身设置未通过双读及代码身份验证')
    def scalar(identifier, name, domain):
        rows=[r for r in runtime.get('rows',[]) if r.get('id')==hex(identifier)]
        if (len(rows)!=1 or rows[0].get('name')!=name or
                type(rows[0].get('current')) is not int or rows[0]['current'] not in domain or
                type(rows[0].get('shadow')) is not int or rows[0]['current']!=rows[0]['shadow']):
            raise DeviceError('机身设置缺失、变化或枚举未知：'+name)
        return rows[0]['current']
    idle=all(scalar(i,n,range(16))==0 for i,n in (
        (0x17,'movie_recording_status'),(0x18,'movie_recording_status_2nd')))
    state=dict(nonce=nonce,camera_idle=idle,features={})
    for feature,value in saved.items():
        if feature not in SETTINGS:raise DeviceError('未知关闭设置')
        identifier,name,_,values=SETTINGS[feature]
        if type(value) is not int or not 0<=value<len(values):raise DeviceError('保存设置枚举未知')
        current=scalar(identifier,name,range(len(values)))
        if feature=='ftp':
            state['features']['ftp']=ftp_snapshot(runtime,value,nonce)['features']['ftp']
        else:
            row=dict(current=values[current],saved=values[value],verified=True)
            if feature=='exposure':
                row['recall_inactive']=all(scalar(i,n,range(count))==0 for i,n,count in (
                    (0x66,'expand_mr_recall_state',3),
                    (0x376,'shoot_set_registration_recall_status',4),
                    (0x377,'shoot_set_registration_recall_execution_status',4),
                    (0x1171,'is_operating_shoot_set_registration_recall',2)))
            state['features'][feature]=row
    return state


def read_close_state(backend,features,nonce):
    """Already-authenticated caller holds the device lock through cleanup.

    Restore temporary service files before allowing any firmware transaction.
    No saved-setting write or native setter is called by this reader.
    """
    from .operations import default_state_dir,make_directory,save_json
    from .fl23_package import service_files,save_bytes
    features=tuple(features)
    if not features or len(set(features))!=len(features) or any(f not in SETTINGS for f in features):
        raise DeviceError('关闭状态请求范围不正确')
    binary,manifest=observer_assets()
    config=observer_request(nonce)
    run=default_state_dir()/'runs'/('close-observation-'+secrets.token_hex(12))
    make_directory(run)
    report=dict(action='inspect-close-state',status='error',device_access=True,
                write_attempted=False,settings_write_attempted=False,verified=False)
    try:
        with service_files(backend,run) as invoke:
            save_bytes(run/'request.bin',config)
            raw=invoke(binary,config);save_bytes(run/'reply.bin',raw)
            runtime=parse_observer(raw,nonce,manifest)
            saved={}
            for feature in features:
                _,_,key,values=SETTINGS[feature]
                reads=[]
                for _ in range(2):
                    try:code,value=backend.channel.send(0x40,struct.pack('<HHI',0x603,1,key))
                    except BaseException:
                        backend._fl23_transport_uncertain=True
                        raise
                    if code or len(value)!=1 or value[0]>=len(values):
                        raise DeviceError('保存状态读取未确认：'+feature)
                    reads.append(value[0])
                if reads[0]!=reads[1]:raise DeviceError('保存状态在读取期间发生变化：'+feature)
                saved[feature]=reads[0]
        report.update(service_restore_verified=True,saved_settings=saved)
        state=setting_snapshot(runtime,saved,nonce)
        report.update(status='CLOSE_STATE_OBSERVED',verified=True,state=state)
        return state
    except BaseException as exc:
        report['error']=str(exc) if isinstance(exc,DeviceError) else type(exc).__name__
        raise
    finally:
        save_json(run/'result.json',report)


FTP_FIELDS = {
    'movie_recording_status': (0x17, range(16)),
    'movie_recording_status_2nd': (0x18, range(16)),
    'setting_ftp_function': (0x5a5, (0, 1)),
    'ftp_transfer_canceling': (0x265, (0, 1)),
    'ftp_transfer_state': (0x93e, range(11)),
    'ftp_stopping': (0xe1a, (0, 1)),
    'ftp_function_internal_state': (0xe13, range(6)),
    'is_ftp_transfer_content_add_processing': (0x1d0, (0, 1)),
    'is_connected_to_ftp_server': (0x1d4, (0, 1)),
    'is_tried_to_connect_ftp_server': (0x1d5, (0, 1)),
    'is_network_start_wait_comp': (0xe0c, (0, 1)),
    'is_network_stop_wait_comp': (0xe0d, (0, 1)),
    'is_network_connect_wait_comp': (0xe0e, (0, 1)),
    'is_network_disconnect_wait_comp': (0xe0f, (0, 1)),
    'is_ftp_connect_wait_comp': (0xe10, (0, 1)),
    'is_ftp_disconnect_wait_comp': (0xe11, (0, 1)),
    'is_ftp_stopped_proc_waiting': (0xe12, (0, 1)),
    'is_ftp_restart': (0xe17, (0, 1)),
    'is_ftp_func_temporary_off': (0xe18, (0, 1)),
    'is_transferring_infra_ftp': (0xe1d, (0, 1)),
}


def ftp_snapshot(runtime, saved, nonce):
    """No cached hints or settings writes. Unknown data rejects the operation."""
    if runtime.get('double_read_identical') is not True or runtime.get('code_guards_verified') is not True:
        raise DeviceError('FTP运行状态未通过双读及代码身份验证')
    if type(saved) is not int or saved not in (0, 1):
        raise DeviceError('FTP保存值不是已知枚举')
    values = {}
    for row in runtime.get('rows', []):
        name = row.get('name')
        if name not in FTP_FIELDS:
            continue
        identifier, domain = FTP_FIELDS[name]
        if (name in values or row.get('id') != hex(identifier) or
                type(row.get('current')) is not int or row['current'] not in domain or
                type(row.get('shadow')) is not int or row['shadow'] not in domain or
                (row['current'] != row['shadow'] and name != 'ftp_function_internal_state')):
            raise DeviceError('FTP状态缺失、变化或不在已知枚举范围')
        # Native 2e4020c -> 2d88d38 changes only current for this derived
        # state; its +4 slot is not a synchronized saved-setting value.
        # Both complete samples must still match, and both enums are checked.
        values[name] = row['current']
    if set(values) != set(FTP_FIELDS):
        raise DeviceError('FTP关闭前状态集合不完整')
    for name in ('ftp_resource_table', 'ftp_client_instance', 'ftp_media_manager'):
        value = runtime.get(name)
        if not isinstance(value, str) or not value.startswith('0x'):
            raise DeviceError('FTP资源对象状态未获确认')
        try:
            pointer = int(value, 16)
        except ValueError as exc:
            raise DeviceError('FTP资源对象格式不正确') from exc
        if pointer < 0 or pointer >= 0x8000000000:
            raise DeviceError('FTP资源对象地址范围不正确')
    count = runtime.get('ftp_resource_threads')
    if type(count) is not int or not 0 <= count <= 64:
        raise DeviceError('FTP读线程计数未知')
    idle = values['movie_recording_status'] == values['movie_recording_status_2nd'] == 0
    pending = [name for name in values if name.startswith('is_')]
    quiescent = (all(values[name] == 0 for name in pending) and
                 values['ftp_transfer_canceling'] == 1 and values['ftp_stopping'] == 1 and
                 values['ftp_function_internal_state'] == 0 and values['ftp_transfer_state'] == 10)
    # Resource managers live for the application lifetime. The native stop
    # joins/destroys the per-transfer thread, nulls +0x10 and returns the
    # channel to state 0. The read-order deque must also be empty. Never use
    # a resident manager's existence (or a transfer icon alone) as occupancy.
    channels=runtime.get('ftp_channels')
    queue=runtime.get('ftp_read_queue')
    if not isinstance(channels,list) or len(channels)>2 or not isinstance(queue,dict):
        raise DeviceError('FTP实际传输通道或读请求队列未获确认')
    def pointer(value,alignment=8):
        if not isinstance(value,str) or not value.startswith('0x'):
            raise DeviceError('FTP资源地址未知')
        try:out=int(value,16)
        except ValueError as exc:raise DeviceError('FTP资源地址不正确') from exc
        if out<0 or out>=0x8000000000 or out&(alignment-1):raise DeviceError('FTP资源地址越界或未对齐')
        return out
    for i,channel in enumerate(channels):
        if (not isinstance(channel,dict) or type(channel.get('index')) is not int or channel['index']!=i or
                type(channel.get('state')) is not int or channel['state'] not in (0,1,2,3,4,0xffffffff)):
            raise DeviceError('FTP传输通道状态未知')
        pointer(channel.get('transfer_thread'))
    first,last,first_node,last_node=[pointer(queue.get(k),4 if k in ('first','last') else 8)
                                   for k in ('first','last','first_node','last_node')]
    table=pointer(runtime.get('ftp_channel_table'))
    client_state=runtime.get('ftp_client_state')
    if type(client_state) is not int or not 0<=client_state<=16:
        raise DeviceError('FTP客户端状态未知')
    if (bool(table)!=bool(channels) or len(channels)!=count or
            bool(first)!=bool(last) or bool(first_node)!=bool(last_node)):
        raise DeviceError('FTP资源表与通道数不一致')
    resources_idle=(all(c['state']==0 and pointer(c['transfer_thread'])==0 for c in channels) and
                    client_state in (0,1) and first==last and first_node==last_node)
    if int(runtime['ftp_media_manager'],16)!=0 and not(first and first_node):resources_idle=False
    io_idle=(resources_idle and values['ftp_function_internal_state']==0 and values['ftp_stopping']==1 and
             values['ftp_transfer_canceling']==1 and values['ftp_transfer_state']==10 and
             all(values[n]==0 for n in ('is_connected_to_ftp_server','is_tried_to_connect_ftp_server',
                 'is_ftp_transfer_content_add_processing','is_transferring_infra_ftp','is_ftp_connect_wait_comp',
                 'is_ftp_disconnect_wait_comp','is_ftp_stopped_proc_waiting','is_ftp_restart','is_ftp_func_temporary_off')))
    return dict(nonce=nonce, camera_idle=idle, features=dict(ftp=dict(
        current='off' if values['setting_ftp_function'] == 1 else 'on',
        saved='off' if saved == 1 else 'on', verified=True,
        transfer_idle=quiescent, connection_idle=quiescent,
        media_released=quiescent and resources_idle,io_idle=io_idle)))
