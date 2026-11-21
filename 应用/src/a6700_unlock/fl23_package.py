"""FL23-only package loading, fixed planning and locked-session operations.

No research imports, addresses supplied by UI, device discovery or lock
bypass. The caller owns the same authenticated backend/device lock as the
four existing features. Host/source mismatches fail before payload upload.
"""
from __future__ import annotations

import contextlib
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import struct
import uuid

from .device_protocol import DeviceError,request,response
from .operations import save_json,make_directory,record_warning
from . import host_io

sha=lambda data:hashlib.sha256(data).hexdigest()

# These exact carriers publish FL23's menu lookups before the custom sound
# stage compares them with its factory preimages. Keep them recognizable so
# a device already running one can close either feature using the same tool.
_INCOMPATIBLE_MENU_CARRIERS = {
    5: '637f7aaf73ba2b2c2c8e8853e6ac23440a0fa982f6cc08383b5f2dd0e301e9b9',
    7: 'e974acbe0028ad51f4c5a5fa167b625289bf927c122ef795424f75f283c882db',
}


def save_bytes(path,data):
    fd=host_io.open_exclusive(path)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    host_io.sync_directory(Path(path).parent)


class Profile:
    def __init__(self,recipe):
        self.data=recipe;self.entries=recipe['entries'];self.digest=recipe['profile_sha256']
    def masks(self,**unused):
        result=[]
        for row in self.entries:
            mask=bytearray(64)
            for edit in row['edits']:
                for at in range(edit['offset'],edit['offset']+len(bytes.fromhex(edit['disabled']))):
                    mask[at//8]|=1<<(at%8)
            result.append(bytes(mask))
        return result


class Package:
    def __init__(self,root=None,*,menu_ui=None):
        from concurrent.futures import ThreadPoolExecutor
        self.root=root or files('a6700_unlock').joinpath('data/fl23')
        self.manifest=json.loads(self.root.joinpath('manifest.json').read_text())
        self.recipe=json.loads(self.root.joinpath('recipe.json').read_text())
        canonical=dict(self.recipe);digest=canonical.pop('profile_sha256')
        if (sha(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode())!=digest or
            self.manifest.get('profile_sha256')!=digest or self.manifest.get('names') is not False or
            self.manifest.get('deployment')!='file-carrier' or self.recipe.get('model')!='ILCE-6700' or
            self.recipe.get('firmware')!='2.00' or self.recipe.get('names') is not False):
            raise DeviceError('FL23 安装包内容或适配不匹配')
        self.profile=Profile(self.recipe);self.carrier=self.root.joinpath('carrier.bin').read_bytes()
        if sha(self.carrier)!=self.manifest['carrier_sha256'] or len(self.carrier)!=self.manifest['carrier_bytes']:
            raise DeviceError('FL23 外部载荷校验失败')
        self.payloads={}
        if set(self.manifest['payloads'])!={'reader','writer','files'}:raise DeviceError('FL23 固定载荷清单不完整')
        for name,row in self.manifest['payloads'].items():
            data=self.root.joinpath(name+'.elf').read_bytes()
            if sha(data)!=row['sha256'] or len(data)!=row['bytes']:raise DeviceError('FL23 原生事务校验失败')
            self.payloads[name]=data
        checks=[]
        for name,digest in self.manifest['source_sha256'].items():
            checks.append((self.root.joinpath(name),digest,'FL23 原生源码与载荷不一致'))
        for name,digest in self.manifest.get('upstream_source_sha256',{}).items():
            checks.append((files('a6700_unlock').joinpath(name),digest,'FL23 源码变化后尚未重新构建安装组件'))
        if 'runtime_source_sha256' in self.manifest:
            raw=self.root.joinpath('runtime/manifest.json').read_bytes()
            if sha(raw)!=self.manifest['runtime_source_sha256']:raise DeviceError('FL23 运行库源码清单不一致')
            for name,digest in json.loads(raw)['files'].items():
                path=Path(name)
                if path.is_absolute() or '..' in path.parts:
                    raise DeviceError('FL23 运行库源码与构建不一致')
                checks.append((self.root.joinpath('runtime/'+name),digest,'FL23 运行库源码与构建不一致'))
        self.carriers={1:self.carrier}
        variants=self.manifest.get('variants',{})
        if variants and set(variants) not in ({'2','3'},{'2','3','4','5','6','7'}):
            raise DeviceError('创意外观／快门声组合清单不完整')
        for flags,row in variants.items():
            folder=self.root.joinpath('variants/'+flags)
            data=folder.joinpath('carrier.bin').read_bytes()
            runtime=folder.joinpath('runtime/manifest.json').read_bytes()
            if len(data)!=row['bytes'] or sha(data)!=row['sha256'] or sha(runtime)!=row['runtime_sha256']:
                raise DeviceError('创意外观组合载荷校验失败')
            runtime_manifest=json.loads(runtime)
            check_base=self.root if runtime_manifest.get('file_root')=='fl23' else folder.joinpath('runtime')
            for name,digest in runtime_manifest['files'].items():
                path=Path(name)
                if path.is_absolute() or '..' in path.parts:
                    raise DeviceError('创意外观组合源码与载荷不一致')
                checks.append((check_base.joinpath(name),digest,'创意外观组合源码与载荷不一致'))
            self.carriers[int(flags)]=data
        if checks:
            def verify(item):
                resource,expected_digest,message=item
                if sha(resource.read_bytes())!=expected_digest:
                    return message
                return None
            with ThreadPoolExecutor(max_workers=min(8,len(checks))) as pool:
                for failure in pool.map(verify,checks):
                    if failure is not None:
                        raise DeviceError(failure)
        revision=self.manifest.get('carrier_revision',1)
        legacy=self.manifest.get('legacy_carriers',{})
        if type(revision) is not int or not 1<=revision<=5:
            raise DeviceError('FL23 功能包版本清单不匹配')
        if legacy:
            if revision<2:raise DeviceError('FL23 功能包版本清单不匹配')
            if revision==2 and set(legacy)!={'5','7'}:raise DeviceError('FL23 功能包版本清单不匹配')
        self.file_specs={f:dict(flags=f,revision=revision if (revision>=3 or f&1) else 1,bytes=len(data),sha256=sha(data))
                         for f,data in self.carriers.items()}
        for ident,row in legacy.items():
            numeric=int(ident)
            expected_flags=(numeric&7) if numeric>=256 else (numeric&3)
            expected_rev=(numeric>>8) if numeric>=256 else 1
            if (row.get('flags')!=expected_flags or row.get('revision')!=expected_rev or
                    not 1<=expected_rev<revision or
                    type(row.get('bytes')) is not int or not 1024<=row['bytes']<=2098176 or
                    len(bytes.fromhex(row['sha256']))!=32 or
                    row['sha256'] in {r['sha256'] for r in self.file_specs.values()}):
                raise DeviceError('FL23 旧版识别清单不匹配')
            self.file_specs[numeric]=dict(row)
        self.menu_ui=menu_ui
        if self.menu_ui is None and root is None and set(self.carriers)>={4,5,6,7}:
            from .custom_sound_capability_package import load_custom_sound_capability_package, load_custom_sound_menu_ui_assets
            from .custom_sound_native import load_custom_sound_payload
            load_custom_sound_capability_package()
            load_custom_sound_payload()
            self.menu_ui=load_custom_sound_menu_ui_assets()


_AVAILABILITY_CACHE=None


def availability():
    """Verify bundled FL23/creative-look assets once per process."""
    global _AVAILABILITY_CACHE
    if _AVAILABILITY_CACHE is not None:
        return _AVAILABILITY_CACHE[:2]
    try:
        pkg=Package()
        flags=(True,{1,2,3}<=set(pkg.carriers),{1,2,3,4,5,6,7}<=set(pkg.carriers) and pkg.menu_ui is not None)
    except (OSError,ValueError,KeyError,DeviceError):
        return (False,False)
    _AVAILABILITY_CACHE=flags
    return flags[:2]


def available():
    return availability()[0]


def names_available():
    return availability()[1]


def custom_sound_availability():
    availability()
    return bool(_AVAILABILITY_CACHE and _AVAILABILITY_CACHE[2])


def plan_blocks(package,sectors,action):
    if action not in ('inspect','unlock','close'):raise ValueError('Unknown FL23 action')
    if len(sectors)!=len(package.profile.entries) or any(len(s)!=512 for s in sectors):
        raise DeviceError('FL23 固定范围读取不完整')
    states=[];after=[];changes=0
    for row,raw,mask in zip(package.profile.entries,sectors,package.profile.masks()):
        normalized=bytes(0 if mask[i//8]&(1<<(i%8)) else v for i,v in enumerate(raw))
        if sha(normalized)!=row['unmanaged_sha256']:raise DeviceError('FL23 非目标内容发生变化')
        target=bytearray(raw)
        for edit in row['edits']:
            at=edit['offset'];old=bytes.fromhex(edit['disabled']);new=bytes.fromhex(edit['enabled']);actual=raw[at:at+len(old)]
            allowed=(old,new,*(bytes.fromhex(v) for v in edit.get('legacy',[])))
            if len(old)!=len(new) or actual not in allowed:raise DeviceError('FL23 当前代码不是已支持状态')
            states.append((row['device'],'enabled' if actual==new else 'disabled' if actual==old else 'legacy'))
            desired=new if action=='unlock' else old if action=='close' else actual
            target[at:at+len(old)]=desired
        changes+=sum(a!=b for a,b in zip(raw,target));after.append(bytes(target))
    ap=[state for dev,state in states if dev!=2];cp=[state for dev,state in states if dev==2]
    combined=lambda items:'enabled' if items and set(items)=={'enabled'} else 'disabled' if items and set(items)=={'disabled'} else 'partial'
    return dict(startup=combined(ap),cp=combined(cp),changed_bytes=changes),after


def _command(backend,code,data):
    body=backend._command(code,data)
    if len(body)<4 or body[:4]!=bytes(4):raise DeviceError('FL23 服务命令失败；不自动重发')
    return body[4:]


@contextlib.contextmanager
def service_files(backend,directory):
    """Preserve both service files; restore library via non-executing alias."""
    directory=Path(directory);make_directory(directory);original={};dirty=False;uncertain=False
    backend._fl23_transport_uncertain=False
    ensure=getattr(backend,'ensure_jiritsu_mount',None)
    if callable(ensure):ensure()
    elif _command(backend,0x4002,bytes(3)):raise DeviceError('FL23 服务挂载返回异常')
    def get(ident):return _command(backend,0x4008,bytes((0,ident,0)))
    for ident in (0,2):
        a,b=get(ident),get(ident)
        if not a or a!=b or len(a)>1048572:raise DeviceError('FL23 服务文件前态不稳定或不受支持')
        original[ident]=a
        path=directory/('service-'+str(ident)+'.before')
        save_bytes(path,a)
    def invoke(binary,config):
        nonlocal dirty,uncertain
        if uncertain:raise DeviceError('FL23 传输结果未知；请保留报告，不重发执行器')
        dirty=True
        try:
            if _command(backend,0x4010,b'\0\0\0'+config):raise DeviceError('Unexpected FL23 configuration reply')
            if _command(backend,0x4007,b'\0\2\0'+binary):raise DeviceError('Unexpected FL23 execution reply')
            return get(0)
        except BaseException:
            uncertain=True;raise
    try:yield invoke
    finally:
        uncertain=uncertain or backend._fl23_transport_uncertain
        if dirty and not uncertain:
            if _command(backend,0x400f,b'\0/tmp/jiritsu/./libJit.so'):raise DeviceError('FL23 服务恢复路径未确认')
            for ident,data in ((255,original[2]),(0,original[0])):
                if _command(backend,0x4010,bytes((0,ident,0))+data):raise DeviceError('FL23 服务恢复返回异常')
            if any(get(ident)!=original[ident] for ident in (2,0)):raise DeviceError('FL23 服务文件恢复回读不一致')
        elif uncertain:
            backend.cleanup_warning='FL23 执行结果未知，服务文件待独立恢复；请保留本机记录，不重复写入。'


class Session:
    def __init__(self,package,backend,invoke,directory):
        self.package,self.backend,self.invoke,self.directory=package,backend,invoke,Path(directory)
        self.events=0;self.uncertain=False
    def exchange_payload(self,label,binary,config,parser):
        self.events+=1;prefix=self.directory/f'{self.events:03d}-{label}'
        save_json(prefix.with_suffix('.intent.json'),dict(payload_sha256=sha(binary),request_sha256=sha(config)))
        save_bytes(prefix.with_suffix('.request'),config)
        raw=self.invoke(binary,config)
        save_bytes(prefix.with_suffix('.reply'),raw)
        return parser(raw)
    def exchange(self,name,config,parser):
        return self.exchange_payload(name,self.package.payloads[name],config,parser)
    def blocks(self,before=None,after=None):
        nonce=uuid.uuid4().bytes;writing=before is not None;p=self.package.profile
        cfg=request(p,nonce,before,after)
        result=self.exchange('writer' if writing else 'reader',cfg,lambda raw:response(raw,nonce,0 if writing else len(p.entries)))
        if result['status']:raise DeviceError('FL23 固定事务拒绝：'+str(result))
        return result['sectors']
    def menu_part_blocks(self,part,before=None,after=None):
        from .custom_sound_capability_package import menu_ui_request,menu_ui_response
        nonce=uuid.uuid4().bytes;writing=before is not None
        cfg=menu_ui_request(part,nonce,before,after)
        binary=part['writer'] if writing else part['reader']
        label=part['name']+('-writer' if writing else '-reader')
        result=self.exchange_payload(label,binary,cfg,lambda raw:menu_ui_response(raw,nonce,part,writing=writing))
        if result['status']:raise DeviceError('自定义快门声菜单UI事务拒绝：'+str(result))
        return result['sectors']
    def inspect_menu_ui(self):
        assets=getattr(self.package,'menu_ui',None)
        if not assets:return None
        from .custom_sound_capability_package import plan_menu_ui_part
        records=[];part_states=[]
        for part in assets['parts']:
            observed=self.menu_part_blocks(part)
            detail,_=plan_menu_ui_part(part,observed,'inspect')
            records.append(dict(detail=detail,part=part['name']))
            part_states.append(detail['state'])
        state='enabled' if part_states and set(part_states)=={'enabled'} else 'disabled' if part_states and set(part_states)=={'disabled'} else 'partial'
        return dict(state=state,parts=records)
    def apply_menu_ui(self,action,progress=lambda _:None):
        if action not in ('unlock','close'):raise ValueError('Unsupported menu UI action')
        assets=getattr(self.package,'menu_ui',None)
        if not assets:raise DeviceError('缺少自定义快门声菜单UI资产')
        from .custom_sound_capability_package import plan_menu_ui_part
        parts=list(assets['parts']) if action=='unlock' else list(reversed(assets['parts']))
        wanted='enabled' if action=='unlock' else 'disabled'
        changed_bytes=0
        for part in parts:
            observed=self.menu_part_blocks(part)
            detail,target=plan_menu_ui_part(part,observed,action)
            if detail['changed_bytes']==0 and detail['state']==wanted:
                continue
            progress(('正在写入' if action=='unlock' else '正在恢复')+'自定义快门声菜单UI：'+part['name']+'…')
            self.menu_part_blocks(part,observed,target)
            readback=self.menu_part_blocks(part)
            check,_=plan_menu_ui_part(part,readback,'inspect')
            if readback!=target or check['state']!=wanted:
                raise DeviceError('自定义快门声菜单UI独立回读不一致：'+part['name'])
            changed_bytes+=detail['changed_bytes']
        return changed_bytes
    def wipe_csnd(self):
        from .custom_sound_native import custom_sound_wipe_request,custom_sound_response,load_custom_sound_payload
        nonce=uuid.uuid4().bytes
        result=self.exchange_payload('csnd-wipe',load_custom_sound_payload(),
            custom_sound_wipe_request(nonce),lambda raw:custom_sound_response(raw,nonce))
        if result['status']:raise DeviceError('清除 /setting/csnd 目录及音频文件失败：'+str(result))
        return result
    def file(self,op=0,offset=0,data=b'',length=0,flags=1):
        p=self.package;nonce=uuid.uuid4().bytes
        cfg=struct.pack('<4sI16s32s4I',b'A67F',1,nonce,bytes.fromhex(p.profile.digest),op,offset,len(data) if op==2 else length,flags if flags!=1 else 0)+data
        def parse(raw):
            if len(raw)<44:raise DeviceError('FL23 文件事务回复不完整')
            magic,version,returned,status,n,detail,_,_=struct.unpack_from('<4sI16s5I',raw)
            if magic!=b'A67F' or version!=1 or returned!=nonce or len(raw)!=44+n or n>65536:
                raise DeviceError('FL23 文件事务版本／nonce／长度不匹配')
            if status:raise DeviceError('FL23 文件事务拒绝：'+str(status))
            ident=(detail>>8 or 1) if detail&1 else 0
            spec=getattr(p,'file_specs',{}).get(ident)
            if detail&1 and hasattr(p,'file_specs') and spec is None:raise DeviceError('FL23 功能包版本不在识别清单中')
            return dict(present=bool(detail&1),mask=detail&255,file_id=ident,
                flags=spec['flags'] if spec else ident,revision=spec['revision'] if spec else 1,data=raw[44:])
        return self.exchange('files',cfg,parse)
    def cp_unit(self,row,value=None):
        key=row['key'];writing=value is not None
        cfg=struct.pack('<HHII',0x603,0x41 if writing else 0x40,key,1)+(value if writing else b'')
        self.events+=1;prefix=self.directory/f'{self.events:03d}-cp'
        save_json(prefix.with_suffix('.intent.json'),dict(key=key,writing=writing,request_sha256=sha(cfg)))
        save_bytes(prefix.with_suffix('.request'),cfg)
        try:
            code,body=self.backend.channel.send(0x40,cfg)
            if body and len(body)%512==0:
                target=self.backend.channel.target
                tail=bytes(target.device.read(target.endpoint_in,512,timeout=10000))
                if tail:raise DeviceError('FL23 CP 数据终止包异常')
        except BaseException:
            self.backend._fl23_transport_uncertain=True;raise
        if code or (writing and body) or (not writing and len(body)!=row['bytes']):raise DeviceError('FL23 CP 参数传输未确认')
        save_json(prefix.with_suffix('.result.json'),dict(code=code,bytes=len(body),sha256=sha(body)))
        return body
    def units(self):
        result=[]
        for row in self.package.recipe['cp_units']:
            a,b=self.cp_unit(row),self.cp_unit(row)
            if a!=b or sha(a) not in (row['before_sha256'],row['after_sha256']):raise DeviceError('FL23 CP 参数不是已验证前态')
            result.append(a)
        return result
    def selection(self):
        code,value=self.backend.channel.send(0x40,struct.pack('<HHI',0x603,1,0x02cf036f))
        if code or len(value)!=1 or value[0] not in (*range(16),17,18):raise DeviceError('FL23 当前外观选择无法确认')
        return value[0]
    def ap_values(self):
        # The normal/MR keys already verified for all six custom slots in
        # current/still/movie groups, plus the native main selection pair.
        specs=[(base+2*i+attribute,13 if attribute else 1)
            for base in (0x02cf0ccc,0x02cf0fa9,0x02cf0fc9)
            for i in range(6) for attribute in (0,1)]
        specs.extend(((0x02cf036f,1),(0x02cf0370,13)))
        values={}
        for key,width in specs:
            try:code,value=self.backend.channel.send(0x40,struct.pack('<HHI',0x603,1,key))
            except BaseException:
                self.backend._fl23_transport_uncertain=True;raise
            if code or len(value)!=width:raise DeviceError('FL23 原有外观／MR 设置读取未确认')
            values[hex(key)]=value.hex()
        return values


def inspect(session):
    before=session.blocks();detail,_=plan_blocks(session.package,before,'inspect')
    units=session.units();file=session.file();audit=session.file(5);selected=session.selection()
    menu=session.inspect_menu_ui() if getattr(session.package,'menu_ui',None) is not None and hasattr(session,'inspect_menu_ui') else None
    file['owned_mask']=audit['mask']
    unit_enabled=all(sha(value)==row['after_sha256'] for row,value in zip(session.package.recipe['cp_units'],units))
    unit_original=all(sha(value)==row['before_sha256'] for row,value in zip(session.package.recipe['cp_units'],units))
    flags=file.get('flags',1) if file['present'] else 0
    enabled=detail['startup']=='enabled' and detail['cp']=='enabled' and unit_enabled and bool(flags&1)
    disabled=detail['cp']=='disabled' and unit_original and (
        (detail['startup']=='disabled' and not file['owned_mask']) or
        (detail['startup']=='enabled' and file['present'] and not (flags&1)))
    if hasattr(session.package,'carriers') and len(session.package.carriers)>1:
        detail['flags']=flags if detail['startup']=='enabled' else 0
        detail['names_state']=(('enabled' if flags&2 else 'disabled') if detail['startup']=='enabled' and file['present'] else
            ('disabled' if detail['startup']=='disabled' and not file['owned_mask'] else 'partial'))
        if 4 in session.package.carriers:
            menu_state=menu['state'] if menu is not None else ('enabled' if (flags&4 and detail['startup']=='enabled' and file['present']) else 'disabled')
            if detail['startup']=='enabled' and file['present'] and (flags&4) and menu_state=='enabled':
                detail['custom_sound_state']='enabled'
            elif menu_state=='disabled' and ((detail['startup']=='disabled' and not file['owned_mask']) or (detail['startup']=='enabled' and file['present'] and not (flags&4))):
                detail['custom_sound_state']='disabled'
            else:
                detail['custom_sound_state']='partial'
            detail['menu']=menu
    state='enabled' if enabled else 'disabled' if disabled else 'partial'
    row=dict(id='fl23',name='FL2／FL3 创意外观',state=state,available=True)
    target=getattr(session.package,'manifest',{}).get('carrier_revision',1)
    installed=file.get('revision',1) if enabled else 0 if disabled else None
    row.update(installed_revision=installed,target_revision=target,
        update_available=enabled and installed<target,
        update_state=('available' if installed<target else 'current') if enabled else 'uninstalled' if disabled else 'unknown')
    return row,detail,before,units,file,selected


def pause_startup_branches(package,before):
    """Pause only the two existing activation instructions for a file update."""
    after=list(before);branches=[]
    for i,entry in enumerate(package.profile.entries):
        if not entry.get('boot_entry'):continue
        if entry['device'] not in (0,1) or len(entry['edits'])!=1:raise DeviceError('FL23 启动入口结构不匹配')
        edit=entry['edits'][0];at=edit['offset'];old=bytes.fromhex(edit['disabled']);on=bytes.fromhex(edit['enabled'])
        if len(old)!=4 or len(on)!=4 or before[i][at:at+4]!=on:raise DeviceError('FL23 更新前启动入口状态不完整')
        raw=bytearray(before[i]);raw[at:at+4]=old;after[i]=bytes(raw);branches.append(entry['device'])
    if sorted(branches)!=[0,1]:raise DeviceError('FL23 更新需要已知的两个启动入口')
    return after


def require_stock_values(values):
    # Preserve custom settings, rather than clearing an entire settings bank.
    for key,value in values.items():
        raw=bytes.fromhex(value)
        if len(raw)==1 and raw[0]>(15 if key==hex(0x02cf036f) else 9):
            raise DeviceError('请先将当前外观及使用FL2／FL3的自定义槽切回原有外观，再完整卸载。')


def update_units(session,units,action):
    """Restore or enable only fixed CP deltas, preserving original AP/MR values."""
    wanted='after_sha256' if action=='unlock' else 'before_sha256'
    destination='enabled' if action=='unlock' else 'disabled'
    source='disabled' if action=='unlock' else 'enabled'
    specs=session.package.recipe['cp_units']
    if all(sha(value)==row[wanted] for row,value in zip(specs,units)):return
    ap_before=session.ap_values()
    if action=='close':require_stock_values(ap_before)
    save_json(session.directory/'ap-before.json',ap_before)
    for row,current in zip(specs,units):
        if sha(current)==row[wanted]:continue
        if session.cp_unit(row)!=current:raise DeviceError('FL23 CP 参数写前发生变化')
        target=bytearray(current)
        for edit in row['edits']:
            at=edit['offset'];old=bytes.fromhex(edit[source]);new=bytes.fromhex(edit[destination])
            if target[at:at+len(old)]!=old:raise DeviceError('FL23 CP 参数字段变化')
            target[at:at+len(old)]=new
        if sha(target)!=row[wanted]:raise DeviceError('FL23 CP 参数目标校验失败')
        session.cp_unit(row,bytes(target))
        if session.cp_unit(row)!=target:raise DeviceError('FL23 CP 参数独立回读失败')
    cfg=struct.pack('<HHH',0x603,3,0x2a4)
    save_json(session.directory/'cp-save-intent.json',dict(action=action,request_hex=cfg.hex(),unit_sha256=[r[wanted] for r in specs]))
    try:code,body=session.backend.channel.send(0x40,cfg)
    except BaseException:
        session.backend._fl23_transport_uncertain=True;raise
    if code or body:raise DeviceError('FL23 CP 保存回复未确认')
    saved=session.units()
    if any(sha(value)!=row[wanted] for row,value in zip(specs,saved)):
        raise DeviceError('FL23 保存后 CP 参数状态不一致')
    ap_after=session.ap_values();save_json(session.directory/'ap-after.json',ap_after)
    if ap_after!=ap_before:raise DeviceError('FL23 CP 保存改变了原有外观／MR 设置')


def perform(backend,action,directory,progress=lambda _:None,features=None,*,validate_close=False,validate_unlock=False):
    """Caller holds device lock; install is journaled, never auto-replayed."""
    if (action not in ('inspect','unlock','close')
            or ((validate_close or validate_unlock) and action!='inspect')
            or (validate_close and validate_unlock)):
        raise ValueError('未知创意外观操作')
    package=Package()
    if len(getattr(package,'carriers',{}))>1:
        return perform_options(package,backend,action,directory,progress,features,
                               validate_close=validate_close,validate_unlock=validate_unlock)
    if features is not None and set(features)!={'fl23'}:raise DeviceError('中文名称安装组件尚未准备完成')
    run=Path(directory)/'runs'/('fl23-'+uuid.uuid4().hex)
    make_directory(run)
    result=dict(action=action,status='error',device_access=True,write_attempted=False,verified=False,
        run_directory=str(run),feature='fl23',profile_sha256=package.profile.digest,full_backup=False)
    try:
        with service_files(backend,run) as invoke:
            session=Session(package,backend,invoke,run)
            progress('正在读取 FL2／FL3 固定代码、参数与载荷状态…')
            row,detail,before,units,file,selected=inspect(session)
            result['plan']=dict(compatible=True,features=[row],read_sectors=len(package.profile.entries)+len(package.recipe['guards']),
                read_bytes=(len(package.profile.entries)+len(package.recipe['guards']))*512,selected_features=['fl23'],changed_bytes=0)
            if action=='close' or validate_close:
                require_stock_values(session.ap_values())
                if not file['owned_mask'] and detail['startup']!='disabled':
                    raise DeviceError('当前是旧研究启动版本，请先完成 FL2／FL3 安装迁移，再使用本版关闭。')
                result['close_ready']=True
            if action=='inspect':result['status']='inspected'
            elif (action=='unlock' and row['state']=='enabled') or (action=='close' and row['state']=='disabled'):
                result.update(status='no-op',verified=True,final_features=[row])
            else:
                if action=='close' and selected in (17,18):raise DeviceError('请先在相机把创意外观切回 ST，再关闭 FL2／FL3。')
                if action=='close' and not file['owned_mask'] and detail['startup']!='disabled':
                    raise DeviceError('当前是旧研究启动版本，请先完成 FL2／FL3 安装迁移，再使用本版关闭。')
                if action=='close':require_stock_values(session.ap_values())
                detail,after=plan_blocks(package,before,action)
                save_json(run/'preimages.json',dict(sectors=[s.hex() for s in before],cp_units=[v.hex() for v in units],full_backup=False))
                save_json(run/'write-intent.json',dict(action=action,profile_sha256=package.profile.digest,changed_bytes=detail['changed_bytes']))
                result['write_attempted']=True
                if action=='unlock':
                    if not file['present']:
                        progress('正在分段安装 FL2／FL3 外部载荷…');session.file(1)
                        for at in range(0,len(package.carrier),65536):session.file(2,at,package.carrier[at:at+65536])
                        session.file(3)
                    # Independent file reads, not the commit ACK, establish
                    # the installed carrier before any startup entry changes.
                    observed=b''.join(session.file(0,at,length=min(65536,len(package.carrier)-at))['data'] for at in range(0,len(package.carrier),65536))
                    if observed!=package.carrier:raise DeviceError('FL23 载荷独立回读失败')
                    update_units(session,units,'unlock')
                else:
                    # Make persistent startup inert before removing its CP
                    # dependencies. All stages remain in this locked session.
                    inert=[target if row['device']!=2 else current
                           for row,current,target in zip(package.profile.entries,before,after)]
                    if inert!=before:
                        progress('正在撤回 FL23 AP／WBI 启动入口…')
                        session.blocks(before,inert)
                        if session.blocks()!=inert:raise DeviceError('FL23 启动入口撤回独立回读失败')
                        before=inert
                    progress('正在恢复 FL23 专属 CP 参数…')
                    update_units(session,units,'close')
                if before!=after:
                    progress('正在提交 FL2／FL3 代码与启动入口…')
                    session.blocks(before,after)
                    if session.blocks()!=after:raise DeviceError('FL23 代码独立回读不一致')
                if action=='close':
                    progress('正在清除已校验的 FL23 功能包及停用副本…')
                    session.file(6)
                    if session.file(5)['mask']:raise DeviceError('FL23 功能包清除后仍有残留')
                final,_,_,_,_,_=inspect(session)
                wanted='enabled' if action=='unlock' else 'disabled'
                if final['state']!=wanted:raise DeviceError('FL23 最终安装状态不一致')
                result['plan']['changed_bytes']=detail['changed_bytes']
                result.update(status='completed',verified=True,final_features=[final],restart_required=True)
                progress('FL2／FL3 写入及独立回读通过；冷启动后检查菜单和成像。')
        result['service_restore_verified']=True
    except BaseException as exc:
        result.update(status='uncertain' if result['write_attempted'] else 'error',error=str(exc) or type(exc).__name__)
    save_json(run/'result.json',result)
    return result


def perform_options(package,backend,action,directory,progress,features=None,*,validate_close=False,validate_unlock=False):
    """One shared loader, independent flags 1..7, CP for FL23, 23-sector menu-ui + /setting/csnd wipe for custom sound."""
    from .creative_looks import FEATURES,FEATURE_IDS
    has_custom=4 in getattr(package,'carriers',{})
    valid_ids=FEATURE_IDS if has_custom else ('fl23','creative_look_names')
    bits={'fl23':1,'creative_look_names':2,'custom_shutter_sound':4}
    selected=tuple(features or ('fl23',))
    if action not in ('inspect','unlock','close') or not set(selected)<=set(valid_ids) or len(set(selected))!=len(selected):
        raise ValueError('未知或重复的功能包选项')
    run=Path(directory)/'runs'/('looks-'+uuid.uuid4().hex);make_directory(run)
    result=dict(action=action,status='error',device_access=True,write_attempted=False,verified=False,
        run_directory=str(run),selected_features=list(selected),profile_sha256=package.profile.digest,full_backup=False)
    def rows(row,detail):
        items=[row,dict(id='creative_look_names',name=FEATURES[1]['name'],state=detail['names_state'],available=True)]
        if has_custom:
            items.append(dict(id='custom_shutter_sound',name=FEATURES[2]['name'],requires=['sound'],
                              state=detail['custom_sound_state'],available=True))
        return items
    try:
        with service_files(backend,run) as invoke:
            session=Session(package,backend,invoke,run)
            progress('正在读取创意外观与自定义快门声载荷、参数和菜单状态…')
            row,detail,before,units,file,main=inspect(session)
            observed=rows(row,detail)
            menu_read_sectors=sum(p['manifest']['sector_count'] for p in package.menu_ui['parts']) if getattr(package,'menu_ui',None) else 0
            total_read_sectors=len(package.profile.entries)+len(package.recipe['guards'])+menu_read_sectors
            result['plan']=dict(compatible=True,features=observed,selected_features=list(selected),
                read_sectors=total_read_sectors,read_bytes=total_read_sectors*512,changed_bytes=0)
            legacy_condition=(
                'fl23' in selected and detail['startup']=='disabled' and not file['present']
                and not (file['owned_mask']&~4) and any(r['state']=='partial' for r in observed)
            )
            custom_only_partial=(
                has_custom and 'custom_shutter_sound' in selected
                and all(r['state'] in ('enabled','disabled') for r in observed if r['id']!='custom_shutter_sound')
                and detail.get('custom_sound_state')=='partial'
                and (file['present'] or detail['startup']=='disabled')
            )
            current=detail['flags']
            target=current
            planned_action = 'unlock' if validate_unlock else 'close' if validate_close else action
            if planned_action != 'inspect':
                for feature in selected:
                    bit=bits[feature]
                    target=(target|bit) if planned_action=='unlock' else (target&~bit)
            candidate=package.carriers.get(target)
            refused_digest=_INCOMPATIBLE_MENU_CARRIERS.get(target)
            blocked=(planned_action!='inspect' and candidate is not None and refused_digest
                     and sha(candidate)==refused_digest)
            if not blocked and planned_action=='close':
                if any(r['state']=='partial' for r in observed) and not legacy_condition and not custom_only_partial:
                    raise DeviceError('功能包安装状态不完整，未开始关闭任何所选功能。')
                if 'fl23' in selected:
                    require_stock_values(session.ap_values())
                result['close_ready']=True
            if blocked:
                result['plan']['compatible']=False
                error='当前版本的 FL2／FL3 与自定义快门声共用菜单入口，联合冷启动校验失败。'
                if planned_action=='close':
                    error+='请同时关闭 FL2／FL3 或自定义快门声。'
                result.update(error=error, after_flags=target)
            elif action=='inspect':
                result['status']='inspected'
            else:
                legacy_close=action=='close' and legacy_condition
                # Do not infer an unselected option from incomplete state.
                if any(r['state']=='partial' for r in observed) and not legacy_close and not custom_only_partial:
                    raise DeviceError('功能包安装状态不完整；请先核对记录并完成恢复，未自动覆盖另一选项。')
                target_rev=getattr(package,'manifest',{}).get('carrier_revision',1)
                installed_rev=file.get('revision',1)
                repair_update=action=='unlock' and (
                    ('fl23' in selected and row.get('update_available',False)) or
                    (bool(target) and file['present'] and installed_rev<target_rev and any(bits[f]&current for f in selected))
                )
                menu_needs_work=has_custom and (
                    (bool(target&4) and detail.get('custom_sound_state')!='enabled') or
                    (not (target&4) and (bool(current&4) or detail.get('custom_sound_state')!='disabled'))
                )
                if target==current and not legacy_close and not repair_update and not menu_needs_work:
                    result.update(status='no-op',verified=True,final_features=observed)
                else:
                    if (current&1 or legacy_close) and not target&1:
                        require_stock_values(session.ap_values())
                    _,on=plan_blocks(package,before,'unlock');_,off=plan_blocks(package,before,'close')
                    after=[(on if (target&1 if entry['device']==2 else target) else off)[i]
                           for i,entry in enumerate(package.profile.entries)]
                    inert=(pause_startup_branches(package,before) if repair_update and target==current and not menu_needs_work else
                        [off[i] if entry['device']!=2 else raw for i,(entry,raw) in enumerate(zip(package.profile.entries,before))])
                    save_json(run/'preimages.json',dict(sectors=[s.hex() for s in before],cp_units=[u.hex() for u in units],full_backup=False))
                    changed=sum(sum(a!=b for a,b in zip(x,y)) for x,y in zip(before,after))
                    save_json(run/'write-intent.json',dict(before_flags=current,after_flags=target,selected_features=selected,
                        carrier_sha256=sha(package.carriers[target]) if target else None,changed_bytes=changed,
                        package_update=repair_update,installed_revision=row.get('installed_revision'),target_revision=row.get('target_revision')))
                    result['write_attempted']=True
                    if before!=inert:
                        progress('正在暂停开机常驻加载入口…');session.blocks(before,inert)
                        if session.blocks()!=inert:raise DeviceError('开机加载入口暂停回读失败')
                        before=inert
                    if target:
                        carrier=package.carriers[target]
                        if file.get('flags')!=target or not file['present'] or repair_update:
                            if file.get('owned_mask',0)&2:raise DeviceError('存在未完成的 /setting/fl23 暂存事务，未重复上传')
                            progress('正在更新 /setting/fl23 修复包…' if repair_update else '正在安装所选 /setting/fl23 功能组合…');session.file(1,flags=target)
                            for at in range(0,len(carrier),65536):session.file(2,at,carrier[at:at+65536],flags=target)
                            session.file(3,flags=target)
                        data=b''.join(session.file(0,at,length=min(65536,len(carrier)-at),flags=target)['data'] for at in range(0,len(carrier),65536))
                        if data!=carrier:raise DeviceError('/setting/fl23 组合载荷独立回读失败')
                    menu_changed=0
                    if has_custom and getattr(package,'menu_ui',None) is not None:
                        if target&4 and detail.get('custom_sound_state')!='enabled':
                            menu_changed+=session.apply_menu_ui('unlock',progress)
                        elif not (target&4) and (bool(current&4) or detail.get('custom_sound_state')!='disabled'):
                            menu_changed+=session.apply_menu_ui('close',progress)
                            progress('正在彻底清除 /setting/csnd 所有自定义音频与状态文件…')
                            session.wipe_csnd()
                    elif has_custom and not (target&4) and bool(current&4):
                        progress('正在彻底清除 /setting/csnd 所有自定义音频与状态文件…')
                        session.wipe_csnd()
                    if (current^target)&1 or legacy_close:
                        progress('正在更新 FL2／FL3 专属 CP 依赖…')
                        update_units(session,units,'unlock' if target&1 else 'close')
                    if before!=after:
                        progress('正在提交代码变更与开机加载入口…');session.blocks(before,after)
                        if session.blocks()!=after:raise DeviceError('代码变更独立回读失败')
                    if not target:
                        session.file(6)
                        if session.file(5)['mask']:raise DeviceError('/setting/fl23 功能包仍有残留')
                    final,fd,_,final_units,_,_=inspect(session);final_rows=rows(final,fd)
                    if fd['flags']!=target or any(r['state']!=('enabled' if target&bits[r['id']] else 'disabled') for r in final_rows):
                        raise DeviceError('功能包组合最终状态不一致')
                    if repair_update and final.get('update_available'):raise DeviceError('FL23 修复包版本独立回读未通过')
                    if not (current^target)&1 and not legacy_close and final_units!=units:
                        raise DeviceError('功能包更新改变了原有 CP 参数')
                    result['plan']['changed_bytes']=changed+menu_changed
                    result.update(status='completed',verified=True,final_features=final_rows,restart_required=True,
                        before_flags=current,after_flags=target,package_updated=repair_update)
                    progress('所选功能写入及回读通过；冷启动后生效。')
        result['service_restore_verified']=True
    except BaseException as exc:
        result.update(status='uncertain' if result['write_attempted'] else 'error',error=str(exc) or type(exc).__name__)
    save_json(run/'result.json',result)
    return result
