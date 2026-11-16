"""Allowlisted host failure reports. No device access and no raw log attachment."""
import io
import json
import platform
import re
import sys
import uuid
import time
import zipfile
from .diagnostic_records import FEATURES, feature_rows

FAILURES = {
    'error', 'blocked', 'rejected', 'uncertain', 'verification-failed',
    'recovery-required', 'rolled-back', 'partial', 'connection-busy',
    'close-preparation-required', 'close-preparation-uncertain',
}
ACTIONS = {'inspect', 'unlock', 'close', 'update_fl23', 'update_ftp', 'restore'}
CHECK_IDS = {'python', 'platform', 'pyusb', 'capture-compiler', 'libusb', 'payload-integrity'}
COMPATIBILITY_DETAILS = {
    'structure': '相机固件结构与本版本的适配记录不一致',
    'target-content': '相机功能所在区域的内容与本版本的适配记录不一致',
    'unknown-feature-state': '发现本版本无法识别的功能状态',
    'feature-dependency': '功能依赖状态不符合预期',
}
STAGES = (
    ('正在寻找唯一的 MSC 相机', 'find_msc', 'connect'),
    ('第一次 USB 认证', 'authenticate_msc', 'connect'),
    ('等待相机进入服务模式', 'wait_service', 'connect'),
    ('第二次 USB 认证', 'authenticate_service', 'connect'),
    ('确认相机服务响应', 'confirm_service', 'connect'),
    ('正在认证相机 USB 服务模式', 'authenticate', 'connect'),
    ('正在准备相机服务暂存目录', 'mount', 'connect'),
    ('正在挂载', 'mount', 'connect'),
    ('正在接管相机 MSC 接口', 'claim_interface', 'connect'),
    ('正在检查关闭前', 'check_settings', 'prepare_settings'),
    ('正在读取', 'read_targets', 'inspect'),
    ('检查完成：预计修改', 'plan_ready', 'plan'),
    ('正在执行所选功能', 'write_targets', 'execute'),
    ('正在从备份恢复', 'restore_targets', 'execute'),
    ('正在分段安装', 'install_package', 'execute'),
    ('正在安装所选创意外观', 'install_package', 'execute'),
    ('正在更新 FL2／FL3', 'update_package', 'execute'),
    ('正在提交', 'commit_package', 'execute'),
    ('正在独立回读', 'readback', 'verify'),
    ('写入及独立回读通过', 'readback_passed', 'verify'),
    ('FL2／FL3 写入及独立回读通过', 'readback_passed', 'verify'),
    ('正在退出', 'release_service', 'exit'),
)


def compatibility_checks(plan):
    """Reduce local plan issues to categories safe for display and feedback."""
    categories = set()
    for issue in plan.get('issues', ()):
        kind = issue.get('kind')
        if kind == 'sector-guard-mismatch':
            categories.add('target-content' if issue.get('role') == 'target' else 'structure')
        elif kind == 'unknown-field':
            categories.add('unknown-feature-state')
        elif kind == 'feature-requirement':
            categories.add('feature-dependency')
    return [category for category in COMPATIBILITY_DETAILS if category in categories]


class FailureTrace:
    """Record fixed stage IDs only; progress text never enters a report."""
    def __init__(self):
        self.identity = None
        self.events = []
        self.started = 0

    def begin(self, identity, snapshot=None):
        self.identity, self.events, self.started = identity, [], time.monotonic()
        snapshot = snapshot or {}
        if sys.platform == 'darwin':
            os_version = platform.mac_ver()[0]
            if re.fullmatch(r'\d{1,4}(?:\.\d{1,4}){1,3}', os_version):
                self.add('macos_' + os_version, 'environment', 'observed')
        elif sys.platform == 'win32':
            os_version = platform.win32_ver()[1]
            if re.fullmatch(r'[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}', os_version):
                self.add('windows_' + os_version, 'environment', 'observed')
        for check in snapshot.get('environment', {}).get('checks', []):
            if check.get('id') in CHECK_IDS and type(check.get('ok')) is bool:
                self.add('check_' + check['id'], 'environment', 'passed' if check['ok'] else 'failed')
        if snapshot.get('ready') is True:
            self.add('camera_preparation', 'preparation', 'user_confirmed', source='user')
        if snapshot.get('action') not in ('inspect', None):
            for feature in snapshot.get('features', []):
                if feature.get('id') in FEATURES and feature.get('selected') is True:
                    self.add('selected_feature', 'plan', 'user_confirmed', source='user', feature_id=feature['id'])

    def add(self, step, phase, status, **fields):
        fields.setdefault('source', 'core')
        elapsed = min(86400000, max(0, int((time.monotonic() - self.started) * 1000)))
        self.events.append(dict(step_id=step, phase=phase, status=status, elapsed_ms=elapsed, **fields))

    def progress(self, message):
        for marker, step, phase in STAGES:
            if message.startswith(marker):
                if len(self.events) < 64 and (not self.events or self.events[-1]['step_id'] != step):
                    self.add(step, phase, 'passed' if step in ('readback_passed', 'plan_ready') else 'started')
                return

    def report(self, snapshot):
        result = operation_failure(snapshot)
        if result and self.identity == snapshot.get('operation_id'):
            result['events'] = list(self.events)
            if self.events and self.events[-1]['status'] in ('started', 'passed') and self.events[-1]['phase'] != 'environment':
                result['stage_id'] = self.events[-1]['phase']
            elapsed = min(86400000, max(0, int((time.monotonic() - self.started) * 1000)))
            outcome = snapshot['result']
            for field, phase in (('write_attempted', 'execute'), ('verified', 'verify')):
                if type(outcome.get(field)) is bool:
                    step = field + ('_yes' if outcome[field] else '_no')
                    result['events'].append(dict(step_id=step, phase=phase, status='observed', source='core', elapsed_ms=elapsed))
            categories = outcome.get('compatibility_checks', ())
            for category in COMPATIBILITY_DETAILS:
                if category in categories:
                    result['stage_id'] = 'inspect'
                    result['events'].append(dict(step_id='compatibility_' + category,
                                                 phase='inspect', status='failed',
                                                 source='core', elapsed_ms=elapsed))
            cleanup = outcome.get('cleanup_status')
            if cleanup in ('stop-sent', 'msc-observed', 'unconfirmed'):
                result['events'].append(dict(step_id='exit_' + cleanup, phase='exit',
                                             status='unknown' if cleanup == 'unconfirmed' else 'observed', source='core', elapsed_ms=elapsed))
            result['events'].append(dict(step_id='operation_result', phase=result['stage_id'],
                                         status='failed', source='core', elapsed_ms=elapsed))
        return result


def operation_failure(snapshot):
    result = snapshot.get('result') or {}
    if snapshot.get('simulation') or snapshot.get('busy'):
        return None
    if snapshot.get('action') not in ACTIONS or result.get('status') not in FAILURES:
        return None
    operation_id = str(uuid.UUID(snapshot['operation_id']))
    message = str(result.get('error') or result.get('detail') or '')
    code = 'unknown'
    if result['status'] == 'connection-busy':
        code = 'device_busy'
    elif result['status'] in ('blocked', 'rejected'):
        code = 'guard_mismatch'
    elif result['status'] == 'verification-failed':
        code = 'readback_failed'
    elif re.search(r'timeout|timed out|超时', message, re.I):
        code = 'usb_timeout'
    elif re.search(r'permission|access denied|权限|授权', message, re.I):
        code = 'permission_denied'
    elif re.search(r'no Sony|未发现|未找到|没有找到', message, re.I):
        code = 'device_missing'
    status_code = re.search(r'状态[：:]\s*([0-9a-fA-F]{8})(?![0-9a-fA-F])', message)
    transaction = {
        'verification-failed': 'error', 'recovery-required': 'uncertain',
        'connection-busy': 'blocked', 'rejected': 'blocked',
        'close-preparation-required': 'blocked',
        'close-preparation-uncertain': 'uncertain', 'rolled-back': 'rolled_back',
    }.get(result['status'], result['status'])
    report = dict(
        status='operation_failure', mode='operation_failure',
        operation_id=operation_id, action=snapshot['action'],
        transaction_status=transaction, error_code=code,
        stage_id='connect' if snapshot['action'] == 'inspect' else 'verify' if code == 'readback_failed' else 'execute',
        camera_status_code=status_code.group(1).lower() if status_code else None,
        events=[],
    )
    digest = snapshot.get('adapter_set_sha256')
    if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
        report['package_set_sha256'] = digest
    return report


def build_failure_archive(report, snapshot, outcome, known_edits=()):
    """Serialize only bounded fields from the completed operation in memory."""
    outcome = outcome or {}
    result = {
        'operation_id': report['operation_id'], 'action': report['action'],
        'status': report['transaction_status'], 'stage': report['stage_id'],
        'error_code': report['error_code'], 'events': report['events'],
    }
    if report.get('camera_status_code'):
        result['camera_status_code'] = report['camera_status_code']
    for key in ('write_attempted', 'settings_write_attempted', 'verified', 'rolled_back'):
        value = outcome.get(key, snapshot.get('result', {}).get(key))
        if type(value) is bool:
            result[key] = value
    cleanup = outcome.get('cleanup_status')
    if cleanup in ('stop-sent', 'msc-observed', 'unconfirmed'):
        result['cleanup_status'] = cleanup
    for key in ('selected_features', 'completed_features', 'remaining_features'):
        value = outcome.get(key, [])
        if isinstance(value, list):
            result[key] = sorted({name for name in value if isinstance(name, str) and name in FEATURES})
    digest = snapshot.get('adapter_set_sha256')
    if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
        result['adapter_set_sha256'] = digest

    files = {'result.json': result}
    plan = outcome.get('plan')
    if isinstance(plan, dict):
        issues = []
        for row in plan.get('issues', [])[:512]:
            kind = row.get('kind')
            if kind == 'sector-guard-mismatch' and row.get('role') in ('guard', 'target'):
                issue = {'kind': kind, 'role': row['role']}
                if type(row.get('index')) is int and 0 <= row['index'] <= 65535:
                    issue['check_index'] = row['index']
                issues.append(issue)
            elif kind == 'unknown-field':
                issue = {'kind': kind}
                edit = row.get('edit_id')
                if isinstance(edit, str) and edit in known_edits:
                    issue['check_id'] = edit
                issues.append(issue)
            elif kind == 'feature-requirement':
                issue = {'kind': kind}
                for key in ('feature', 'requires'):
                    value = row.get(key)
                    if isinstance(value, str) and value in FEATURES:
                        issue[key] = value
                issues.append(issue)
        safe_plan = dict(issues=issues, features=feature_rows(plan.get('features')))
        if type(plan.get('compatible')) is bool:
            safe_plan['compatible'] = plan['compatible']
        files['plan.json'] = safe_plan
    manifest = {'schema_version': 1, 'kind': 'operation-failure-diagnostics',
                'operation_id': result['operation_id'], 'files': list(files)}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in {'report.json': manifest, **files}.items():
            archive.writestr(zipfile.ZipInfo(name), json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    return buffer.getvalue()
