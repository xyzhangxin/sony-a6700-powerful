"""Structured feedback upload following services/cloud/docs/客户端接入.md.

The cloud accepts a small allowlisted JSON object, never the diagnostic ZIP,
raw logs, firmware, paths, credentials, or arbitrary exception text.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from . import __version__

API_BASE = 'https://powerful6700-api.susukele.com'


def _user_agent():
    return 'Powerful6700/' + __version__


def _repair_legacy_payload(feedback):
    """Upgrade the pre-contract recorder event without changing its identity."""
    changed = False
    for event in feedback.get('diagnostic', {}).get('events', []):
        if event.get('phase') == 'start':
            event['phase'] = 'connect'
            changed = True
    return changed


def _arch():
    return 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'


def _channel():
    return 'testing' if '.dev' in __version__ or '-rc.' in __version__ else 'stable'


def build_feedback(result, description=''):
    status = result.get('status', 'error')
    transaction = {'collected': 'verified', 'incomplete': 'partial',
                   'not_requested': 'not_started'}.get(status, 'error')
    error_code = 'none' if status in ('collected', 'not_requested') else 'unknown'
    if result.get('service_recovery_required'):
        error_code = 'service_exit_unconfirmed'
    elif result.get('error_type') == 'TimeoutError':
        error_code = 'usb_timeout'
    elif result.get('error_type') == 'DeviceBusyError':
        error_code = 'device_busy'
    operation_id = result.get('operation_id') or str(uuid.uuid4())
    mode = result.get('mode', 'direct')
    diagnostic = dict(
        app_version=__version__, core_version=__version__,
        platform='macos' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32' else 'linux',
        arch=_arch(), channel=_channel(), operation_id=operation_id,
        action='diagnose', transaction_status=transaction, error_code=error_code,
        stage_id=('guided_reproduce' if mode == 'guided' else
                  'not_requested' if mode == 'not_requested' else 'direct_collect'),
        packages=[], events=list(result.get('events', [])), adapter_version=__version__)
    text = description.strip() or '选择 FL2／FL3 后相机重启。'
    if len(text) > 2000:
        raise ValueError('描述不能超过 2000 字。')
    return dict(schema_version=1, feedback_id=str(uuid.uuid4()),
                consent_version='feedback-v1', description=text, diagnostic=diagnostic)


def recover_diagnostic(state_dir=None, download_dir=None):
    """Recover a collected, not-yet-uploadable report after an app restart.

    Only bounded FL23 ZIP names and existing feedback metadata are read. An
    unfinished camera recorder always takes precedence in the bridge.
    """
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    downloads = Path(download_dir) if download_dir is not None else Path.home() / 'Downloads'
    records = []
    for folder in ('feedback-sent', 'feedback-pending'):
        for path in (state / folder).glob('*.json'):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                stat = path.stat()
                if stat.st_size <= 2 * 1024 * 1024:
                    records.append((stat.st_mtime, path))
            except OSError:
                continue
    if not records:
        return None
    archives = []
    for path in downloads.glob('FL23-故障报告-*.zip'):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 5 * 1024 * 1024:
                archives.append((path.stat().st_mtime, path))
        except OSError:
            continue
    archives = [path for _, path in sorted(archives, reverse=True)[:20]]
    for _, record in sorted(records, reverse=True)[:8]:
        try:
            payload = json.loads(record.read_text())['payload']
            diagnostic = payload['diagnostic']
            if diagnostic['transaction_status'] != 'verified':
                continue
            operation_id = diagnostic['operation_id']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for path in archives:
            try:
                with zipfile.ZipFile(path) as archive:
                    report = json.loads(archive.read('report.json'))
                if (report.get('operation_id') != operation_id or
                        report.get('camera', {}).get('status') != 'collected'):
                    continue
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
            return dict(status='collected', path=str(path), camera=True,
                        operation_id=operation_id, mode=report.get('collection_mode', 'guided'),
                        service_recovery_required=False, events=diagnostic.get('events', []),
                        record_count=len(report.get('local_install', {}).get('records', [])))
    return None


def _save(path, data):
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _error_code(raw):
    try:
        return json.loads(raw).get('error', {}).get('code', 'unknown')
    except (ValueError, AttributeError, TypeError):
        return 'unknown'


def _request_with_retries(opener, request, attempts=3, timeout=15):
    """Retry only transport failures with identical bytes and credentials."""
    last_error = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise last_error


def upload_feedback(result, description='', state_dir=None, api_base=API_BASE, opener=None, include_artifact=False, images=None, connection_factory=None):
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    pending = state / 'feedback-pending'
    sent = state / 'feedback-sent'
    pending.mkdir(parents=True, exist_ok=True)
    operation_id = result.get('operation_id') or str(uuid.uuid4())
    record = pending / (operation_id + '.json')
    sent_record = sent / (operation_id + '.json')
    if sent_record.exists():
        if sent_record.is_symlink() or sent_record.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('已发送反馈记录无效。')
        saved_sent = json.loads(sent_record.read_text())
        candidate = build_feedback(result, description)
        if {k: v for k, v in saved_sent['payload'].items() if k != 'feedback_id'} == \
                {k: v for k, v in candidate.items() if k != 'feedback_id'}:
            # JSON was already accepted. Reuse its identity for attachment
            # retries instead of creating a second feedback for the same form.
            feedback_id = saved_sent['payload']['feedback_id']
            artifact = None
            if include_artifact and result.get('path'):
                artifact = upload_artifact(result, state_dir=state, api_base=api_base, opener=opener)
                if artifact.get('status') != 'received':
                    return dict(status='partial', feedback_id=feedback_id, artifact=artifact,
                                error=artifact.get('error', 'ZIP 附件未接收，已保留在本机。'))
            image_results = upload_images(result, images or [], state_dir=state, api_base=api_base,
                                          connection_factory=connection_factory)
            if any(row.get('status') != 'received' for row in image_results):
                return dict(status='partial', feedback_id=feedback_id, artifact=artifact,
                            images=image_results, error='部分图片未接收，已保留在本机。')
            return dict(status='received', feedback_id=feedback_id, artifact=artifact,
                        images=image_results)
    if record.exists():
        if record.is_symlink() or record.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('待发送反馈记录无效。')
        saved = json.loads(record.read_text())
        feedback, token = saved['payload'], saved['delete_token']
        if _repair_legacy_payload(feedback):
            saved['body_sha256'] = hashlib.sha256(
                json.dumps(feedback, ensure_ascii=False, separators=(',', ':')).encode()
            ).hexdigest()
            temporary = record.with_suffix('.json.new')
            _save(temporary, saved)
            os.replace(temporary, record)
    else:
        feedback = build_feedback(result, description)
        token = secrets.token_hex(32)
        _save(record, dict(payload=feedback, delete_token=token,
                           body_sha256=hashlib.sha256(
                               json.dumps(feedback, ensure_ascii=False, separators=(',', ':')).encode()
                           ).hexdigest(),
                           created_at=datetime.now(timezone.utc).isoformat()))
    body = json.dumps(feedback, ensure_ascii=False, separators=(',', ':')).encode()
    request = urllib.request.Request(
        api_base.rstrip('/') + '/v1/feedback', data=body, method='POST',
        headers={'Content-Type': 'application/json', 'X-Delete-Token': token,
                 'User-Agent': _user_agent(), 'Accept': 'application/json'})
    opener = opener or urllib.request.urlopen
    try:
        status, response_body = _request_with_retries(opener, request)
        data = json.loads(response_body)
        if status in (200, 201) and data.get('status') == 'received':
            sent.mkdir(parents=True, exist_ok=True)
            record.replace(sent / record.name)
            if include_artifact and result.get('path'):
                artifact = upload_artifact(result, state_dir=state, api_base=api_base, opener=opener)
                if artifact.get('status') != 'received':
                    image_results = upload_images(result, images or [], state_dir=state, api_base=api_base, connection_factory=connection_factory)
                    return dict(status='partial', feedback_id=feedback['feedback_id'],
                                expires_at=data.get('expires_at'), artifact=artifact, images=image_results,
                                error=artifact.get('error', 'ZIP 附件未接收，已保留在本机。'))
                image_results = upload_images(result, images or [], state_dir=state, api_base=api_base, connection_factory=connection_factory)
                if any(row.get('status') != 'received' for row in image_results):
                    return dict(status='partial', feedback_id=feedback['feedback_id'],
                                expires_at=data.get('expires_at'), artifact=artifact, images=image_results,
                                error='部分图片未接收，已保留在本机。')
                return dict(status='received', feedback_id=feedback['feedback_id'],
                            expires_at=data.get('expires_at'), artifact=artifact, images=image_results)
            image_results = upload_images(result, images or [], state_dir=state, api_base=api_base, connection_factory=connection_factory)
            if any(row.get('status') != 'received' for row in image_results):
                return dict(status='partial', feedback_id=feedback['feedback_id'],
                            expires_at=data.get('expires_at'), images=image_results,
                            error='部分图片未接收，已保留在本机。')
            return dict(status='received', feedback_id=feedback['feedback_id'],
                        expires_at=data.get('expires_at'), images=image_results)
        return dict(status='error', error='云端返回内容未确认，报告仍保留在本机。',
                        feedback_id=feedback['feedback_id'])
    except urllib.error.HTTPError as exc:
        code = _error_code(exc.read())
        status = {'rate_limited': 'rate_limited', 'daily_budget_exceeded': 'rate_limited',
                  'feedback_conflict': 'conflict', 'feedback_unavailable': 'unavailable',
                  'feedback_expired': 'unavailable', 'feedback_paused': 'paused'}.get(code, 'error')
        return dict(status=status, error_code=code, feedback_id=feedback['feedback_id'],
                    error='云端暂未接收；已保留同一份待发送报告，不会自动重试。')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        detail = str(getattr(exc, 'reason', exc) or exc).strip()
        return dict(status='unknown', feedback_id=feedback['feedback_id'],
                    error='无法连接云端：%s；已保留同一份待发送报告。若本机把 susukele.com 解析到内网地址，请在路由器 DNS 中为 API 域名设置公网例外。' % (detail or '连接失败'))

def upload_artifact(result, state_dir=None, api_base=API_BASE, opener=None):
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    operation_id = result.get('operation_id')
    if not operation_id or not result.get('path'):
        return dict(status='error', error='缺少可上传的 ZIP 附件。')
    candidates = [state / 'feedback-sent' / (operation_id + '.json'),
                  state / 'feedback-pending' / (operation_id + '.json')]
    record = next((p for p in candidates if p.exists()), None)
    if record is None:
        return dict(status='error', error='请先上传结构化反馈，再上传 ZIP 附件。')
    saved = json.loads(record.read_text())
    feedback_id = saved['payload']['feedback_id']
    token = saved['delete_token']
    path = Path(result['path'])
    if path.stat().st_size > 5 * 1024 * 1024:
        return dict(status='error', error='ZIP 超过 5MiB，未上传。')
    raw = path.read_bytes()
    request = urllib.request.Request(
        api_base.rstrip('/') + '/v1/feedback/' + feedback_id + '/artifact',
        data=raw, method='POST',
        headers={'Content-Type': 'application/zip', 'X-Delete-Token': token,
                 'User-Agent': _user_agent(), 'Accept': 'application/json'})
    opener = opener or urllib.request.urlopen
    try:
        status, response_body = _request_with_retries(opener, request)
        data = json.loads(response_body)
        if status in (200, 201) and data.get('status') == 'received':
            return dict(status='received', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return dict(status='error', error='ZIP 附件返回内容未确认，已保留在本机。')
    except urllib.error.HTTPError as exc:
        code = _error_code(exc.read())
        if exc.code == 404:
            return dict(status='unavailable', error='云端尚未提供 ZIP 附件接口，ZIP 已保留在本机。')
        return dict(status='error', error_code=code,
                    error='ZIP 附件未接收，已保留在本机，可稍后手动重试。')
    except (urllib.error.URLError, TimeoutError, OSError):
        return dict(status='unknown', error='ZIP 上传结果未确认，已保留在本机，可稍后手动重试。')

IMAGE_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'heic': 'image/heic',
    'heif': 'image/heif', 'tif': 'image/tiff', 'tiff': 'image/tiff', 'gif': 'image/gif',
    'bmp': 'image/bmp', 'webp': 'image/webp', 'arw': 'image/x-sony-arw',
    'dng': 'image/x-adobe-dng', 'cr2': 'image/x-canon-cr2', 'cr3': 'image/x-canon-cr3',
    'nef': 'image/x-nikon-nef', 'raf': 'image/x-fuji-raf', 'orf': 'image/x-olympus-orf',
    'rw2': 'image/x-panasonic-rw2',
}


def _feedback_record(result, state):
    operation_id = result.get('operation_id')
    for folder in ('feedback-sent', 'feedback-pending'):
        path = state / folder / (str(operation_id) + '.json')
        if path.exists():
            return json.loads(path.read_text())
    raise ValueError('请先上传结构化反馈，再上传图片。')


def upload_image(result, path, state_dir=None, api_base=API_BASE, connection_factory=None):
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    saved = _feedback_record(result, state)
    feedback_id, token = saved['payload']['feedback_id'], saved['delete_token']
    path = Path(path)
    mime = IMAGE_TYPES.get(path.suffix.lower().lstrip('.'))
    if mime is None:
        return dict(status='error', filename=path.name, error='不支持的图片格式。')
    size = path.stat().st_size
    if size > 200 * 1024 * 1024:
        return dict(status='error', filename=path.name, error='图片超过 200MiB，未上传。')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    image_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(result.get('operation_id')) + ':' + str(path) + ':' + sha256))
    target = urllib.parse.urlsplit(api_base)
    if target.scheme != 'https' or not target.hostname:
        raise ValueError('反馈服务必须使用 HTTPS。')
    factory = connection_factory or http.client.HTTPSConnection
    connection = factory(target.hostname, timeout=120)
    try:
        connection.putrequest('PUT', '/v1/feedback/' + feedback_id + '/images/' + image_id)
        connection.putheader('Content-Type', mime)
        connection.putheader('User-Agent', _user_agent())
        connection.putheader('Content-Length', str(size))
        connection.putheader('X-Filename', path.name)
        connection.putheader('X-Content-SHA256', sha256)
        connection.putheader('X-Delete-Token', token)
        connection.endheaders()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        body = response.read()
        if response.status in (200, 201):
            data = json.loads(body)
            if data.get('status') == 'received':
                return dict(status='received', image_id=image_id, filename=path.name, bytes=size, sha256=sha256)
        code = _error_code(body)
        return dict(status='error', image_id=image_id, filename=path.name, error_code=code,
                    error='图片未接收，已保留在本机。')
    except (OSError, TimeoutError):
        return dict(status='unknown', image_id=image_id, filename=path.name,
                    error='图片上传结果未确认，已保留在本机，可稍后重试。')
    finally:
        connection.close()


def upload_images(result, paths, state_dir=None, api_base=API_BASE, connection_factory=None):
    output = []
    for path in list(paths)[:12]:
        output.append(upload_image(result, path, state_dir=state_dir, api_base=api_base,
                                   connection_factory=connection_factory))
    return output
