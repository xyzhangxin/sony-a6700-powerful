"""核对RAM工程的固定上游提交和输入哈希，不自动下载或执行上游脚本。"""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def checked_upstream(path):
    path = path.resolve()
    pin = json.loads((ROOT / '上游来源锁定.json').read_text())
    commit = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != pin['commit']:
        raise ValueError('上游HEAD与固定提交不符')
    for name, expected in pin['files'].items():
        source = path / name
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('上游文件发生变化：' + name)
    return path
