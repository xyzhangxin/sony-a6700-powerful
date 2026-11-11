"""Prepare selected settings before removing firmware capabilities.

Adapters must observe runtime AND saved state. A Backup value alone is not
such an adapter. Unknown capabilities fail closed; no generic setting writer
or raw native address is exposed here. The simulator implements this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from .device_protocol import DeviceError
from .close_setting import SettingRestartRequired


@dataclass(frozen=True)
class Rule:
    name: str
    safe: tuple[str, ...]
    active: tuple[str, ...]
    target: str
    target_label: str
    instruction: str


RULES = {
    'ftp': Rule('FTP', ('off',), ('on',), 'off', '关',
                '停止FTP传输并关闭FTP功能；还须确认传输收尾及介质占用已释放。'),
    'sound': Rule('电子快门声音', ('on', 'off'), ('electronic-only', 'except-shutter'),
                  'on', '开', '将音频信号从新增选项切回“开”；原有“开／关”保持。'),
    'shutter': Rule('关机快门帘', ('off',), ('on',), 'off', '关',
                    '先将“关机时快门”设为“关”。'),
    'exposure': Rule('灵活曝光', ('pasm',), ('flexible',), 'pasm', 'P/A/S/M模式',
                     '停止录像、退出存储调用，再将曝光控制类型切回P/A/S/M模式。'),
}


def selected_rules(features):
    return tuple(identifier for identifier in RULES if identifier in features)


def _observe(backend, identifiers):
    reader = getattr(backend, 'read_close_state', None)
    nonce = uuid.uuid4().hex
    snapshot = reader(identifiers, nonce) if callable(reader) else {}
    if not isinstance(snapshot, dict):
        raise DeviceError('关闭前状态回复格式不正确')
    if callable(reader) and snapshot.get('nonce') != nonce:
        raise DeviceError('关闭前状态回复过期或与本次请求不匹配')
    states = snapshot.get('features', {})
    if not isinstance(states, dict):
        raise DeviceError('关闭前功能状态回复格式不正确')
    capabilities = frozenset(getattr(backend, 'automatic_close_settings', ()))
    rows = []
    for identifier in identifiers:
        rule = RULES[identifier]
        state = states.get(identifier, {})
        if not isinstance(state, dict):
            raise DeviceError('关闭前功能状态回复格式不正确')
        # Retain only fixed scalar enums, never arbitrary adapter data (FTP
        # credentials, server configuration and personal paths are excluded).
        allowed = rule.safe + rule.active
        current, saved = state.get('current'), state.get('saved')
        known = (isinstance(current, str) and current in allowed and
                 isinstance(saved, str) and saved in allowed)
        confirmed = snapshot.get('camera_idle') is True and state.get('verified') is True
        reason = ''
        if not known or not confirmed:
            status = 'unknown'
            reason = (('当前版本尚未接入该功能已验证的实机状态读取接口，已保留功能。'
                       if not callable(reader) else '无法可靠确认运行与保存状态。') + rule.instruction)
        elif identifier == 'exposure' and state.get('recall_inactive') is not True:
            status = 'blocked'
            reason = '请先退出存储调用，再重新检查曝光控制类型。'
        elif current != saved:
            status = 'blocked'
            reason = '运行值与保存值不一致，请等待相机保存完成后重新检查。'
        else:
            stopped = True
            if identifier == 'ftp':
                flags = [state.get(k) for k in ('transfer_idle', 'connection_idle', 'media_released')]
                if any(type(value) is not bool for value in flags):
                    reason = 'FTP传输收尾或介质释放状态未知。'
                    status = 'unknown'
                    stopped = None
                else:
                    stopped = all(flags)
            if stopped is not None:
                status = 'safe' if current in rule.safe and stopped else 'needs-reset'
                if status == 'needs-reset' and identifier not in capabilities:
                    reason = (('已读取机身状态，但FTP尚未完全停止。请先在相机中关闭FTP，必要时重启，再重新检查。'
                               if identifier == 'ftp' else
                               '当前连接尚未提供已验证的自动停用接口。' + rule.instruction))
        rows.append(dict(id=identifier, name=rule.name, status=status,
                         current=current if known else None, saved=saved if known else None,
                         target=rule.target, target_label=rule.target_label,
                         automatic=identifier in capabilities, reason=reason))
    return rows


def prepare_close(backend, features, directory, progress=lambda _: None, *, apply=True):
    """Caller holds the device lock, after ALL selected content preflights.

    Every selected feature must be observable/resettable before the first
    setting change. Independent reads establish completion, not setter ACKs.
    Firmware plans must be rebuilt if settings changed. Nothing is retried.
    """
    from .operations import save_json

    identifiers = selected_rules(features)
    if not identifiers:
        return None
    report = dict(status='checking', ready=False, settings_write_attempted=False,
                  settings_verified=False, planned=[], completed=[], checks=[], error='')
    in_flight = False
    try:
        progress('正在检查关闭前的机身设置和运行状态…')
        rows = _observe(backend, identifiers)
        report['checks'] = rows
        blocked = [r for r in rows if r['status'] not in ('safe', 'needs-reset') or
                   (r['status'] == 'needs-reset' and not r['automatic'])]
        report['planned'] = [r['id'] for r in rows if r['status'] == 'needs-reset']
        save_json(directory / 'close-preparation-plan.json', dict(report, apply=apply))
        if blocked:
            report.update(status='required', error='；'.join(r['name'] + '：' + r['reason'] for r in blocked))
        elif not apply:
            report.update(status='planned', ready=True)
        else:
            previous = rows
            for index, identifier in enumerate(report['planned']):
                rule = RULES[identifier]
                progress('正在将%s恢复为“%s”…' % (rule.name, rule.target_label))
                if _observe(backend, identifiers) != previous:
                    raise DeviceError('关闭前状态已变化，已停止；请重新检查全部所选功能')
                setter = getattr(backend, 'reset_close_setting', None)
                if not callable(setter):
                    raise DeviceError('自动停用接口不可用')
                save_json(directory / ('close-setting-%02d-intent.json' % index),
                          dict(feature=identifier, before=next(r for r in previous if r['id'] == identifier),
                               target=rule.target))
                in_flight = True
                report['settings_write_attempted'] = True
                # The adapter owns native stop/wait/save, with bounded waits.
                # It must never replace this with an unverified Backup write.
                setter(identifier, rule.target)
                observed = _observe(backend, identifiers)
                changed = next(r for r in observed if r['id'] == identifier)
                if (changed['status'] != 'safe' or changed['current'] != rule.target or
                        changed['saved'] != rule.target):
                    raise DeviceError('自动停用后运行状态、保存值或资源释放未获独立确认')
                old = {r['id']: r for r in previous}
                if any(row != old[row['id']] for row in observed if row['id'] != identifier):
                    raise DeviceError('自动停用影响了其他所选功能，已停止后续修改')
                save_json(directory / ('close-setting-%02d-verified.json' % index), changed)
                report['completed'].append(identifier)
                in_flight = False
                previous = observed
            final = _observe(backend, identifiers)
            if final != previous or any(r['status'] != 'safe' for r in final):
                raise DeviceError('固件修改前机身状态再次变化，已停止')
            report.update(status='ready', ready=True, settings_verified=True, checks=final)
    except SettingRestartRequired as exc:
        report.update(status='restart-required',restart_required=True,error=str(exc))
    except KeyboardInterrupt:
        report.update(status='uncertain' if in_flight else 'cancelled',
                      error='关闭前准备已中断；已调整的设置保持，不自动恢复运行功能。')
    except Exception as exc:
        report.update(status='uncertain' if in_flight else 'required',
                      error='关闭前准备未完成：' + (str(exc) if isinstance(exc, DeviceError) else type(exc).__name__))
    try:
        save_json(directory / 'close-preparation.json', report)
    except OSError:
        report.update(status='uncertain' if in_flight else 'required', ready=False,
                      error='无法持久记录关闭前准备结果，已停止固件修改。')
    return report


def recheck_preparation(backend, report):
    """Recheck after rebuilding firmware targets; never reuse a stale receipt."""
    if report is not None and report['status'] == 'ready':
        try:
            rows = _observe(backend, tuple(row['id'] for row in report['checks']))
            if rows != report['checks'] or any(row['status'] != 'safe' for row in rows):
                raise DeviceError('重新读取固件期间机身设置已变化，已停止固件修改。')
        except Exception:
            return dict(report, ready=False, status='required',
                        error='固件写入前机身设置或运行状态发生变化／无法确认，请重新检查。')
    return report


def attach_preparation(result, report):
    if report is None:
        return True
    result['close_preparation'] = report
    result['settings_write_attempted'] = report['settings_write_attempted']
    if report['ready']:
        return True
    result['status'] = {'uncertain': 'close-preparation-uncertain',
                        'restart-required': 'close-preparation-restart-required',
                        'cancelled': 'close-preparation-cancelled'}.get(report['status'], 'close-preparation-required')
    result['error'] = report['error']
    return False
