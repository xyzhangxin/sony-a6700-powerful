"""Optional usage events with persistent IDs, bounded queue and background delivery."""
from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
from threading import Lock, RLock, Thread
import urllib.request
import uuid
import time

from . import __version__
from .feedback import API_BASE, _arch, _channel
from .device_protocol import DeviceBusyError
from .host_io import default_state_dir, exclusive_lock
import sys

DEFAULT_NOTICE = 'usage-default-on-v1'
EXPLICIT_OPT_IN = 'usage-v1'

PAGES = {'prepare', 'features', 'progress', 'result'}
SUCCESS_ACTIONS = {'unlock', 'close', 'restore', 'update_fl23', 'update_ftp'}


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UsageReporter:
    def __init__(self, directory=None, opener=None):
        self.directory = Path(directory) if directory is not None else default_state_dir()
        self.path = self.directory / 'usage.json'
        self.lock = RLock()
        self.delivery_lock = Lock()
        self.opener = opener or urllib.request.build_opener(_NoRedirects()).open
        self.worker = None
        self.last_page = None
        self.next_delivery = 0
        self.seen_results = deque(maxlen=256)
        self.session_started = False
        self.state = self._read_state()

    def _read_state(self):
        empty = dict(enabled=False, queue=[], installation_id='', delete_token='', consent_version=DEFAULT_NOTICE)
        try:
            attributes = self.path.lstat()
            if not stat.S_ISREG(attributes.st_mode) or attributes.st_size > 256 * 1024:
                return empty
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if type(saved['enabled']) is not bool or not isinstance(saved['queue'], list) or len(saved['queue']) > 256:
                return empty
            if saved['enabled']:
                uuid.UUID(saved['installation_id'])
                if len(bytes.fromhex(saved['delete_token'])) != 32:
                    return empty
            for entry in saved['queue']:
                if (not isinstance(entry, dict) or
                        set(entry) - {'schema_version','consent_version','installation_id','event_id','app_version','platform','arch','channel','kind','page','action'} or
                        entry.get('installation_id') != saved['installation_id']):
                    return empty
            saved.setdefault('consent_version', EXPLICIT_OPT_IN)
            if saved['consent_version'] not in (DEFAULT_NOTICE, EXPLICIT_OPT_IN):
                return empty
            return saved
        except FileNotFoundError:
            return dict(empty, enabled=True)
        except (OSError, ValueError, KeyError, TypeError):
            return empty

    @contextmanager
    def _state_transaction(self):
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
            guard_path = self.directory / 'usage.lock'
            if guard_path.is_symlink():
                raise ValueError('Unexpected statistics lock path')
            handle = os.open(guard_path, flags, 0o600)
            with os.fdopen(handle, 'r+b') as stream, exclusive_lock(stream):
                self.state = self._read_state()
                yield

    def _ensure_identity(self):
        if not self.state['installation_id']:
            self.state.update(installation_id=str(uuid.uuid4()), delete_token=secrets.token_hex(32))

    def start_session(self):
        with self._state_transaction():
            if self.state['enabled'] and not self.state['installation_id']:
                self._ensure_identity()
                self._save()
            self.session_started = True

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ('usage-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                os.chmod(temporary, 0o600)
                json.dump(self.state, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot(self):
        with self.lock:
            return dict(enabled=self.state['enabled'], queued=len(self.state['queue']))

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('统计选项无效。')
        with self._state_transaction():
            if self.state['enabled'] == enabled:
                return self.snapshot()
            previous = dict(self.state)
            if enabled:
                self._ensure_identity()
                self.state['consent_version'] = EXPLICIT_OPT_IN
            else:
                self.state['queue'] = []
            self.state['enabled'] = enabled
            try:
                self._save()
            except OSError:
                self.state = previous
                raise
        if enabled and self.session_started and self.last_page:
            self.record('page_view', page=self.last_page)
        return self.snapshot()

    def record(self, kind, *, page=None, action=None):
        if kind not in {'page_view', 'flash_success', 'operation_error'}:
            raise ValueError('Unknown usage event')
        if kind == 'page_view' and page not in PAGES:
            raise ValueError('Unknown page')
        if kind != 'page_view' and action not in SUCCESS_ACTIONS | {'inspect', 'takeover'}:
            raise ValueError('Unknown operation')
        if kind == 'flash_success' and action not in SUCCESS_ACTIONS:
            raise ValueError('Unknown successful write')
        if not self.session_started:
            return
        with self._state_transaction():
            if not self.state['enabled']:
                return
            self._ensure_identity()
            event = dict(schema_version=1, consent_version=self.state['consent_version'],
                         installation_id=self.state['installation_id'], event_id=str(uuid.uuid4()),
                         app_version=__version__, platform='macos' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32' else 'linux',
                         arch=_arch(), channel=_channel(), kind=kind)
            event['page' if kind == 'page_view' else 'action'] = page if kind == 'page_view' else action
            self.state['queue'] = (self.state['queue'] + [event])[-256:]
            self._save()
        self.start_delivery()

    def observe(self, snapshot):
        if snapshot.get('simulation') or not self.session_started:
            return
        if self.state['enabled'] and self.state['queue']:
            self.start_delivery()
        page = {'notice': 'prepare', 'preparation': 'prepare', 'home': 'features', 'running': 'progress', 'result': 'result'}.get(snapshot.get('phase'))
        if page and page != self.last_page:
            self.last_page = page
            self.record('page_view', page=page)
        result = snapshot.get('result') or {}
        identity = snapshot.get('operation_id')
        if not result or not identity or snapshot.get('busy') or identity in self.seen_results:
            return
        self.seen_results.append(identity)
        action = snapshot.get('action')
        if action not in SUCCESS_ACTIONS | {'inspect', 'takeover'}:
            return
        if result.get('status') in ('completed', 'restored') and result.get('success') and snapshot.get('write_attempted') and action in SUCCESS_ACTIONS:
            self.record('flash_success', action=action)
        elif result.get('status') in {'error', 'blocked', 'rejected', 'partial', 'uncertain', 'rolled-back', 'verification-failed', 'recovery-required', 'connection-busy', 'close-preparation-required', 'close-preparation-uncertain'}:
            self.record('operation_error', action=action)

    def start_delivery(self):
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return
            if time.monotonic() < self.next_delivery:
                return
            self.next_delivery = time.monotonic() + 30
            self.worker = Thread(target=self.flush, daemon=True)
            self.worker.start()

    def flush(self):
        if not self.delivery_lock.acquire(blocking=False):
            return
        try:
            while True:
                with self._state_transaction():
                    if not self.state['enabled'] or not self.state['queue']:
                        return
                    event = self.state['queue'][0]
                    token = self.state['delete_token']
                request = urllib.request.Request(API_BASE + '/v1/telemetry/events',
                    data=json.dumps(event, separators=(',', ':')).encode(),
                    headers={'Content-Type': 'application/json', 'X-Delete-Token': token,
                             'User-Agent': 'Powerful6700/' + __version__})
                try:
                    with self.opener(request, timeout=5) as response:
                        reply = json.loads(response.read(4096))
                        if (response.status not in (200, 201) or not isinstance(reply, dict)
                                or reply.get('event_id') != event['event_id']):
                            return
                except (OSError, ValueError):
                    return
                with self._state_transaction():
                    self.state['queue'] = [item for item in self.state['queue'] if item['event_id'] != event['event_id']]
                    self._save()
        except (OSError, ValueError, DeviceBusyError):
            return
        finally:
            self.delivery_lock.release()
