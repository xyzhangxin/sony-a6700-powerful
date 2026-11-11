"""Process transport only: no USB, firmware rules, or automatic operation replay."""
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from PySide6.QtCore import QObject, Signal


class ServiceClient(QObject):
    event = Signal(int, object)
    exited = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.generation = 0

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def start(self, physical):
        if self.alive():
            raise RuntimeError('请先正常结束原服务。')
        if getattr(sys, 'frozen', False):
            args = [str(Path(sys.executable).parent/'service/Powerful6700.Service.exe')]
        else:
            args = [sys.executable, '-B', '-u', str(Path(__file__).with_name('service.py'))]
        if physical:
            args.append('--physical')
        self.generation += 1
        generation = self.generation
        self.process = process = subprocess.Popen(args, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='strict', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def receive():
            try:
                for line in process.stdout:
                    if len(line) > 2_000_000:
                        raise ValueError('服务响应过长。')
                    self.event.emit(generation, json.loads(line))
            except (ValueError, UnicodeError):
                self.event.emit(generation, dict(type='error', message='服务响应损坏，请结束连接并重新检查。'))
            finally:
                process.stdout.close()
                code = process.wait()
                process.stdin.close()
                self.exited.emit(generation, code)

        # Drain stderr without displaying raw diagnostics/identifiers in the UI.
        def drain():
            try:
                for _ in process.stderr:
                    pass
            finally:
                process.stderr.close()
        Thread(target=receive, daemon=True).start()
        Thread(target=drain, daemon=True).start()

    def send(self, command, **values):
        if not self.alive():
            raise RuntimeError('本机服务未运行，请重新启动服务。')
        self.process.stdin.write(json.dumps(dict(command=command, **values), ensure_ascii=False)+'\n')
        self.process.stdin.flush()
