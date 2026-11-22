"""Fixed installed-loader observation; no caller supplied payload or paths."""
import hashlib
import json
import re
import struct
from importlib.resources import files
from .device_protocol import DeviceError
from .package_installation import PackageBinding


def assets():
    root = files('a6700_unlock').joinpath('data/native/custom-sound-binding')
    raw_manifest = root.joinpath('manifest.json').read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != '30f64620d6bb9eb404981a761f8e5f24e5c2554f66c8b4b5c421d2c3873363c3':
        raise DeviceError('声音加载器观察清单不匹配')
    manifest = json.loads(raw_manifest)
    if (manifest.get('schema') != 'a6700.custom-sound-binding/1'
            or manifest.get('host_sha256') != '799c133ff8fdf5ff0f095fcdec92f6c0ea03c68e0112a8288836ac244d7a2d0a'
            or manifest.get('files', {}).get('probe.elf') != '517fa7952f580e58d21b6ca4e7dbcb2e871b3a8400eb08016eba64ac78ff3cda'):
        raise DeviceError('不支持的声音加载器观察版本')
    for name, digest in manifest['files'].items():
        if '/' in name or name.startswith('.') or hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() != digest:
            raise DeviceError('声音加载器观察资源不匹配')
    return manifest, root.joinpath('probe.elf').read_bytes()


def request(nonce):
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise DeviceError('加载器观察 nonce 无效')
    return struct.pack('<4sI16sIQI', b'A67P', 1, nonce, 71, 0, 16384)


AUTOLOAD_MAGIC = 0x314c444e554f5343
WRAPPER_MAGIC = 0x31574d4f43334c46


def _all_receipts(manifest):
    if not isinstance(manifest, dict):
        return set()
    receipts = set((manifest.get('receipts') or {}).values())
    receipts.update((manifest.get('legacy_receipts') or {}).values())
    return receipts


class CustomSoundBindingError(DeviceError):
    """Structured error carrying allowlisted probe state for status-8 guidance."""

    def __init__(self, message, native_status=None, diagnostic=None):
        super().__init__(message)
        self.native_status = native_status
        self.diagnostic = diagnostic


def diagnose_status8(raw, manifest=None):
    """Classify probe status 8 using only allowlisted scalars from the reply."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 44 + 4652:
        diag = dict(native_status=8, category='malformed-probe', recovery_mode='power-cycle-wait')
        return '本次检查未检测到声音加载器的运行端点（原生状态 8：探测回包不完整）。', diag

    words = struct.unpack_from('<32Q', raw, 44)
    boot_socket = bool(words[3])
    matches = int(words[10])
    boot_id = raw[300:336].split(b'\x00', 1)[0].decode('ascii', 'ignore')
    previous_boot_id = manifest.get('previous_boot_id') if isinstance(manifest, dict) else None
    same_boot_session = bool(isinstance(previous_boot_id, str) and previous_boot_id and boot_id == previous_boot_id)

    base = dict(
        native_status=8,
        boot_socket=boot_socket,
        boot_page_matches=matches,
        same_boot_session=same_boot_session,
        receipt_present=False,
        receipt_matched=False,
        wrapper_present=False,
        autoload_present=False,
        autoload_pending_retained=False,
    )

    if not boot_socket:
        base.update(category='no-boot-context', recovery_mode='power-cycle-wait')
        message = (
            '本次检查未检测到声音加载器的运行端点（原生状态 8：未检测到开机引导上下文）。'
            '常见原因是仅拨动电源开关而未取出电池彻底断电，或仍插着外接供电／过早连接 USB。'
            '请断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机等待约 90 秒，'
            '先在相机菜单确认后再重新连接；此情况不需要关闭自定义快门声（关闭会清除 10 槽音频）。'
        )
        return message, base

    if matches != 1:
        base.update(category='boot-page-not-found', recovery_mode='power-cycle-wait')
        message = (
            f'本次检查未检测到声音加载器的运行端点（原生状态 8：引导页匹配数为 {matches}）。'
            '请先断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机静置约 90 秒再连接检查；'
            '此情况暂不需要关闭自定义快门声。'
        )
        return message, base

    page = memoryview(raw)[556:556 + 4096]
    fl23_state, fl23_error = struct.unpack_from('<Qq', page, 264)
    receipt_bytes = bytes(page[1120:1632])
    receipt_present = any(receipt_bytes)
    expected_receipts = _all_receipts(manifest)
    receipt_matched = receipt_present and (receipt_bytes.hex() in expected_receipts)

    w_magic, _w_flags, w_primary_ran, w_primary_rc, _w_res, w_custom_started, w_custom_rc, w_early_abort_rc = (
        struct.unpack_from('<QQQqQqqq', page, 1984)
    )
    wrapper_present = (w_magic == WRAPPER_MAGIC)

    cs_magic, cs_version, cs_status, *cs_detail = struct.unpack_from('<QQq32Q', page, 1664)
    autoload_present = (cs_magic == AUTOLOAD_MAGIC and cs_version >= 1)
    autoload_step = int(cs_detail[0]) if autoload_present else None
    autoload_status = int(cs_status) if autoload_present else None
    pending_retained = bool(autoload_present and (cs_detail[14] or cs_detail[15]))

    base.update(
        fl23_worker_state=int(fl23_state),
        fl23_worker_error=int(fl23_error),
        receipt_present=receipt_present,
        receipt_matched=receipt_matched,
        wrapper_present=wrapper_present,
        autoload_present=autoload_present,
        autoload_pending_retained=pending_retained,
    )
    if wrapper_present:
        base.update(
            wrapper_primary_ran=int(w_primary_ran),
            wrapper_primary_rc=int(w_primary_rc),
            wrapper_custom_started=int(w_custom_started),
            wrapper_custom_rc=int(w_custom_rc),
            wrapper_early_abort_rc=int(w_early_abort_rc),
        )
    if autoload_present:
        base.update(autoload_step=autoload_step, autoload_status=autoload_status)

    if same_boot_session:
        base.update(category='same-boot-session', recovery_mode='power-cycle-wait')
        message = (
            '本次检查未检测到声音加载器的运行端点（原生状态 8：相机仍处于上一次操作的同一次开机进程中）。'
            '请先正常关机并断开 USB 与外接供电，取出电池至少 10 秒后装回，'
            '保持 USB 断开开机静置约 90 秒后再重新连接检查。'
        )
        return message, base

    if autoload_present and autoload_status == -17:
        if autoload_step == 12:
            base.update(category='autoload-pending-bypassed-once', recovery_mode='lossless-recover')
            message = (
                f'本次检查未检测到声音加载器的运行端点（原生状态 8：检测到上次启动未完成，本次已转入保护隔离，'
                f'阶段 {autoload_step}，错误码 {autoload_status}）。'
                '现在可直接点击「无损恢复（保留音频）」清除保护锁并断电等待约 90 秒恢复（会完整保留全部 10 槽音频与设置）；'
                '或再次完整断电拔电池 10 秒后开机静置约 90 秒自动重试一次。'
            )
            return message, base
        base.update(category='autoload-pending-blocked', recovery_mode='lossless-recover')
        detail_hint = (
            '已连续两次启动未完成而进入防死锁隔离状态' if autoload_step == 13
            else '开机自动加载锁仍被上次未完成启动占用'
        )
        message = (
            f'本次检查未检测到声音加载器的运行端点（原生状态 8：{detail_hint}，'
            f'阶段 {autoload_step}，错误码 {autoload_status}）。'
            '仅靠断电重启无法解除此保护锁；请点击「无损恢复（保留音频）」仅清除开机保护锁标记并按提示升级／保持常驻，'
            '不需要关闭自定义快门声（原有 10 槽音频与设置将完整保留）。'
        )
        return message, base

    if autoload_present and autoload_status < 0:
        recovery_mode = 'lossless-recover' if pending_retained else 'power-cycle-wait'
        base.update(category='autoload-failed', recovery_mode=recovery_mode)
        if pending_retained:
            message = (
                f'本次检查未检测到声音加载器的运行端点（原生状态 8：自定义快门声自动加载失败且保护锁已保留，'
                f'阶段 {autoload_step}，错误码 {autoload_status}）。'
                '请点击「无损恢复（保留音频）」清除开机锁残留（完整保留 10 槽音频与设置），'
                '随后按提示断电拔电池 10 秒并保持 USB 断开开机静置约 90 秒。'
            )
        else:
            message = (
                f'本次检查未检测到声音加载器的运行端点（原生状态 8：自定义快门声自动加载未能完成，'
                f'阶段 {autoload_step}，错误码 {autoload_status}）。'
                '请先断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机静置约 90 秒再到菜单确认；'
                '若再次复现且产生开机锁残留，可点击「无损恢复（保留音频）」清理锁标记（保留 10 槽音频）。'
            )
        return message, base

    if wrapper_present and w_custom_started and w_custom_rc != 0:
        recovery_mode = 'lossless-recover' if pending_retained else 'power-cycle-wait'
        base.update(category='autoload-failed', recovery_mode=recovery_mode)
        message = (
            f'本次检查未检测到声音加载器的运行端点（原生状态 8：声音加载阶段返回错误码 {int(w_custom_rc)}）。'
            + ('保护锁已保留；请点击「无损恢复（保留音频）」清除锁标记并按提示冷启动（完整保留 10 槽音频）。'
               if pending_retained else
               '请先断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机等待约 90 秒后在菜单确认；'
               '若持续失败且保留保护锁，可使用「无损恢复（保留音频）」。')
        )
        return message, base

    if wrapper_present and (w_early_abort_rc != 0 or (w_primary_ran and w_primary_rc != 0)):
        code = int(w_early_abort_rc or w_primary_rc)
        base.update(category='carrier-wrapper-failed', recovery_mode='power-cycle-wait')
        message = (
            f'本次检查未检测到声音加载器的运行端点（原生状态 8：组合常驻前序阶段返回 {code}，未继续启动声音加载）。'
            '请先断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机等待约 90 秒后再检查；'
            '若菜单与连接仍持续失败，可先检查并重新更新或恢复 FL23／自定义快门声。'
        )
        return message, base

    if receipt_present and not receipt_matched:
        base.update(category='carrier-receipt-mismatch', recovery_mode='update-carrier')
        message = (
            '本次检查未检测到声音加载器的运行端点（原生状态 8：机身常驻回执与当前应用组合底座不匹配）。'
            '请先核对相机内的常驻版本；如需更新或恢复，可在「功能管理」更新 FL23／重新解锁自定义快门声后断电重启。'
        )
        return message, base

    if fl23_error != 0 and not receipt_matched:
        base.update(category='fl23-worker-failed', recovery_mode='power-cycle-wait')
        message = (
            f'本次检查未检测到声音加载器的运行端点（原生状态 8：FL23 启动线程提前返回 {int(fl23_error)}）。'
            '请先断开 USB 与外接供电、取出电池至少 10 秒后装回，保持 USB 断开开机等待约 90 秒后再检查。'
        )
        return message, base

    base.update(category='startup-in-progress', recovery_mode='power-cycle-wait')
    message = (
        '本次检查未检测到声音加载器的运行端点（原生状态 8：常驻与声音加载仍在启动过程中或尚未完成挂载）。'
        '请不要直接关闭自定义快门声（关闭会清除 10 槽音频）；先断开 USB 与外接供电、取出电池至少 10 秒后装回，'
        '保持 USB 断开开机静置约 90 秒，再到相机菜单确认「电子快门声」出现后重新连接。'
    )
    return message, base


def parse(raw, nonce, manifest, identity, profile):
    def require(ok):
        if not ok:
            raise DeviceError('当前机身加载器、接线或运行状态不匹配')
    if not isinstance(raw, bytes) or len(raw) != 44+16384:
        raise DeviceError('声音加载器回包长度不匹配')
    magic, version, echoed, status, kind, offset, size = struct.unpack_from('<4sI16sIIQI', raw)
    if (magic, version, kind, offset, size) != (b'A67B', 1, 71, 0, 16384):
        raise DeviceError('声音加载器回包格式不匹配')
    if echoed != nonce:
        raise DeviceError('声音加载器回包与本次 USB 请求不对应')
    if status == 8:
        message, diagnostic = diagnose_status8(raw, manifest)
        raise CustomSoundBindingError(message, native_status=8, diagnostic=diagnostic)
    if status:
        raise DeviceError(f'机身拒绝加载器观察（原生状态 {status}）')
    words = struct.unpack_from('<32Q', raw, 44)
    epoch = raw[300:336]
    page = raw[556:4652]
    bias, base = words[6], struct.unpack_from('<Q',page,328)[0]
    require(re.fullmatch(rb'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',epoch)
            and epoch.decode() != manifest['previous_boot_id'])
    require(words[0]==1 and words[3]==1 and words[10]==1 and words[12]==1
            and words[13]==0 and words[14]==37 and not any(words[15:])
            and 0<words[1]<1<<31 and 0<words[2]<1<<31
            and words[11]>0 and words[11]%4096==0
            and words[8]<=4194304 and words[9]<=min(words[8],131072))
    size = manifest['module_bytes']
    require(4096<=bias<0x8000000000-0x15000000 and bias%4096==0
            and 4096<=base<0x8000000000-size and base%4096==0
            and max(bias,base)>=min(bias+0x15000000,base+size))
    require(page[1120:1632].hex() in _all_receipts(manifest)
            and struct.unpack_from('<Q',page,8)[0]==bias
            and struct.unpack_from('<Q',page,336)[0]==size
            and struct.unpack_from('<4Q',raw,44+4608)==(1,2,0,base))
    fields=[]
    for sample in range(2):
        values={}
        for index, field in enumerate(manifest['fields']):
            at=44+manifest['scalar_offset']+sample*8*len(manifest['fields'])+index*8
            data=raw[at:at+8]
            require(not any(data[field['bytes']:]))
            values[field['name']]=int.from_bytes(data,'little')
        require(values['image_base']==base and values['image_bytes']==size
                and values['host.report.startup_ready']==1
                and values['host.report.bindings_ready']==1
                and values['host.runtime.playback.unresolved']==0
                and values['dmm.uncertain']==0 and values['dmm.copy_failed']==0
                and values['capability_session.abandoned']==0)
        if 'dmm.arena.blocked' in values:
            require(values['dmm.arena.blocked']==0)
            used=values['dmm.arena.used']
            tokens=[values[f'dmm.arena.tokens[{i}]'] for i in range(11)]
            require(used<1<<11 and all(bool(token)==bool(used&(1<<i)) for i,token in enumerate(tokens))
                    and len({token for token in tokens if token})==sum(bool(token) for token in tokens))
            parent=values['dmm.arena.parent.handle']
            require(bool(parent)==bool(used))
            if parent:
                require(values['dmm.arena.parent.capacity']>=11*12288
                        and values['dmm.arena.parent.bus_address']>0
                        and values['dmm.arena.canonical_base']>0
                        and values['dmm.arena.canonical_base']%4096==0)
            else:
                require(not any(values[key] for key in ('dmm.arena.parent.capacity',
                    'dmm.arena.parent.bus_address','dmm.arena.canonical_base')))
        fields.append(values)
        at=44+manifest['spans_offset']+sample*manifest['span_bytes']
        for span in manifest['spans']:
            expected=bytearray.fromhex(span['after_hex'])
            for pointer in span['pointers']:
                offset=pointer['offset']
                value=struct.unpack_from('<Q',expected,offset)[0]
                value += {'app':bias,'module':base,'absolute':0}[pointer['after']]
                struct.pack_into('<Q',expected,offset,value)
            require(raw[at:at+len(expected)]==expected)
            at+=len(expected)
        at=44+manifest['entries_offset']+sample*manifest['entry_bytes']
        for name, offset, encoded in manifest['entries']:
            expected=bytes.fromhex(encoded)
            if name=='sound_options_eighth_entry':
                expected=struct.pack('<IIQQ',0x23d7,0,bias+0x3781000,0)
            require(raw[at:at+len(expected)]==expected)
            at+=len(expected)
    # Trace words and counters may move while idle; require ownership and
    # startup-critical state in both independently captured samples above.
    return PackageBinding('custom_shutter_sound',manifest['loader_version'],
        manifest['host_sha256'],profile,identity,'ILCE-6700','2.00')
