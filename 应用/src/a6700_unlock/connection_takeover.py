"""Explicit, PID-bound takeover of a competing same-user camera tool.

Never removes a lock, resets USB, authenticates a camera, or signals a process
while merely inspecting. A fresh confirmation is required for each attempt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import time
from urllib.parse import urlsplit
import urllib.request
import uuid

from .operations import default_state_dir, host_lock_path, make_directory, save_json


def lock_busy(path):
    import fcntl
    try:
        with Path(path).open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def process_identity(pid):
    if type(pid) is not int or pid <= 1 or pid in (os.getpid(), os.getppid()):
        return None
    try:
        def value(field):
            return subprocess.check_output(['ps', '-p', str(pid), '-o', field + '='],
                                           text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
        uid, started, executable = int(value('uid')), value('lstart'), value('comm')
        if uid != os.getuid() or not started or not executable:
            return None
        if executable.startswith(('/System/', '/usr/libexec/', '/sbin/', '/usr/sbin/')):
            return None
        return dict(pid=pid, uid=uid, started=started, executable=executable,
                    name=Path(executable).name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def unique_owner(path):
    """Open handles are candidates, not ownership evidence by themselves.

    Only offer takeover when flock is held and exactly one other process has
    the inode open. Multiple/unknown owners must be handled in their own apps.
    """
    if not lock_busy(path):
        return None
    try:
        result = subprocess.run(['lsof', '-nP', '-Fpc', '--', str(path)], capture_output=True,
                                text=True, timeout=3)
        if result.returncode:
            return None
        pids = {int(line[1:]) for line in result.stdout.splitlines()
                if line.startswith('p') and line[1:].isdigit()}
        if len(pids) != 1:
            return None
        return process_identity(pids.pop())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def known_active_task(path, owner):
    """Optional local tool registration; normal users need no debug service."""
    registration = Path(path).parent / 'bench.json'
    try:
        info_stat = registration.lstat()
        if not stat.S_ISREG(info_stat.st_mode) or info_stat.st_uid != os.getuid() or info_stat.st_mode & 0o077:
            return False
        info = json.loads(registration.read_text())
        if info.get('pid') != owner['pid']:
            return False
        url = urlsplit(info['url'])
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
            return False
        req = urllib.request.Request(info['url'].rstrip('/') + '/api/state', data=b'{}',
            headers={'X-Validator-Token': info['token'], 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            reply = json.loads(response.read(262144))
        if not reply.get('ok'):
            return False
        state = reply['result']
        return bool(state.get('busy') or state.get('job') or state.get('jobs_queued'))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError):
        # Absence of a cooperative status interface is not proof of activity.
        # The explicit user confirmation remains required for unknown tools.
        return False


class TakeoverManager:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_state_dir()
        self.pending = None

    def prepare(self, path):
        self.pending = None
        base = dict(available=False, confirmation='', owner='', pid=0,
                    detail='请先在其他相机软件中结束连接，再重新连接。')
        path = Path(path)
        if path != host_lock_path():
            return dict(base, detail='此操作记录目录仍被其他任务使用，请等待该任务完成。')
        if not lock_busy(path):
            return dict(base, detail='占用已释放，可直接重新连接。')
        owner = unique_owner(path)
        if owner is None:
            return dict(base, detail='无法唯一确认占用进程，请先退出其他相机软件，再重新连接。')
        if known_active_task(path, owner):
            return dict(base, owner=owner['name'], pid=owner['pid'],
                        detail='已检测到占用工具仍有设备任务，请先在该工具中结束任务。')
        inode = path.stat()
        token = secrets.token_hex(16)
        self.pending = dict(owner=owner, path=path, inode=(inode.st_dev, inode.st_ino),
                            confirmation=token, expires=time.monotonic() + 120)
        return dict(base, available=True, confirmation=token, owner=owner['name'], pid=owner['pid'],
                    detail='请确认其他相机软件没有正在拍摄、传输或写入固件。强制接管会结束此占用进程。')

    def _revalidate(self, plan):
        path, owner = plan['path'], plan['owner']
        info = path.stat()
        if (info.st_dev, info.st_ino) != plan['inode'] or unique_owner(path) != owner:
            raise ValueError('占用者已变化，本次未强制接管，请重新连接检查。')
        if known_active_task(path, owner):
            raise ValueError('占用工具当前仍有设备任务，未强制结束，请先结束该任务。')

    def execute(self, confirmation, confirmed, progress=lambda _: None):
        plan, self.pending = self.pending, None
        if (not plan or confirmed is not True or not isinstance(confirmation, str)
                or not secrets.compare_digest(plan['confirmation'], confirmation)
                or time.monotonic() > plan['expires']):
            raise ValueError('接管确认已失效，请重新连接检查占用状态。')
        path, owner = plan['path'], plan['owner']
        info = path.stat()
        if (info.st_dev, info.st_ino) != plan['inode']:
            raise ValueError('连接锁已变化，请重新连接检查，本次不结束任何进程。')
        if not lock_busy(path):
            return dict(released=True, signals=[], already_released=True)
        self._revalidate(plan)
        record = self.directory / 'connection-takeovers' / uuid.uuid4().hex
        make_directory(record)
        save_json(record / 'intent.json', dict(owner=owner, lock_inode=list(plan['inode']),
                  user_confirmed=True, device_access=False, camera_writes=False))
        signals = []
        try:
            progress('正在结束占用工具：%s（PID %s）…' % (owner['name'], owner['pid']))
            for signum, duration in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
                if not lock_busy(path):
                    break
                self._revalidate(plan)
                os.kill(owner['pid'], signum)
                signals.append(signal.Signals(signum).name)
                until = time.monotonic() + duration
                while time.monotonic() < until and lock_busy(path):
                    time.sleep(0.1)
            result = dict(released=not lock_busy(path), signals=signals, device_access=False,
                          camera_writes=False, pid=owner['pid'])
            save_json(record / 'result.json', result)
            if not result['released']:
                raise ValueError('占用尚未释放，请先退出相关软件，再重新连接。')
            return result
        except BaseException as exc:
            if not (record / 'result.json').exists():
                save_json(record / 'result.json', dict(released=False, signals=signals,
                          error=str(exc), device_access=False, camera_writes=False))
            raise
