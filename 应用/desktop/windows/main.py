"""Windows transport shell; Mac-aligned views live in views.py/design.py."""
import argparse
from pathlib import Path
import sys
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from client import ServiceClient
from design import STYLE
from dialogs import OperationConfirmation
from views import Views

DRIVER_HELP = '''首次连接时，应用会通过一次管理员授权启动专用驱动助手：

1. 自动检查并准备 MSC（0E76）的 WinUSB。
2. 相机进入维护服务（0336）后，由同一助手完成绑定。
3. 两种身份共用 Windows 自带 WinUSB；以后启动无需重复安装。
4. 主应用和固件操作不会提权，也不会自动安装其他设备的驱动。

更换为 WinUSB 后，资源管理器可能无法直接访问相机 SD 卡。
需要恢复普通文件传输时，在设备管理器找到实际替换的相机设备，
卸载该设备驱动并重新连接，使 Windows 恢复 USBSTOR；不要删除其他设备。

自动准备失败时，可使用 Zadig 仅为同一相机手动安装 WinUSB：
https://zadig.akeo.ie/
不要关闭驱动签名或 Windows 安全保护。'''


class Window(Views,QMainWindow):
    def __init__(self,demo=False):
        super().__init__()
        self.setWindowTitle('Powerful6700')
        self.resize(1060,770);self.setMinimumSize(980,720)
        self.client=ServiceClient(self)
        self.client.event.connect(self.on_event);self.client.exited.connect(self.on_exit)
        self.state={};self.pending_mode=None;self.closing=False
        self.transitioning=False;self.inflight=None;self.operation_started=False;self.page_key=None
        self.build_shell(demo)
        QTimer.singleShot(0,self.start)

    @property
    def physical(self):
        return self.mode.currentIndex() == 0

    def start(self):
        try:
            self.transitioning = False; self.inflight = None; self.state = {}; self.page_key = None
            self.operation_started = False; self.scroll.setEnabled(True)
            self.client.start(self.physical)
            self.command('environment')
        except Exception:
            self.render_interrupted('本机服务未能启动。请保留完整目录，重新解压应用。')

    def command(self, name, **values):
        if name in ('inspect', 'unlock', 'close', 'update_fl23'):
            if self.inflight or self.state.get('busy'):
                return
            self.inflight = name
            self.operation_started = False
            self.mode.setEnabled(False)
            self.mode_segments.setEnabled(False)
            self.scroll.setEnabled(False)
        try:
            self.client.send(name, **values)
        except (OSError, RuntimeError):
            self.render_interrupted('本机服务连接已中断；上一次操作不会自动重发。')

    def change_mode(self):
        if self.state.get('busy') or self.inflight:
            return
        self.pending_mode = self.mode.currentIndex()
        self.transitioning = True; self.mode.setEnabled(False)
        self.render_loading('正在正常结束原服务…')
        if self.client.alive():
            self.command('shutdown')
        else:
            self.on_exit(self.client.generation, 0)

    def on_exit(self, generation, code):
        if generation != self.client.generation:
            return
        if self.closing:
            QTimer.singleShot(0, self.close); return
        if self.transitioning:
            self.pending_mode = None; self.start(); return
        uncertain = self.inflight in ('unlock', 'close', 'update_fl23') or (self.state.get('busy') and self.state.get('action') in ('unlock', 'close'))
        self.render_interrupted('结果待核实：服务在功能操作中退出。请保留本机记录，先重新检查，不要盲目重复写入。'
                                if uncertain else '本机服务已退出。重新启动服务后，重新准备并连接相机。')

    def on_event(self, generation, event):
        if generation != self.client.generation or self.transitioning or self.closing:
            return
        if event.get('type') == 'state':
            self.state = event['state']
            if self.state.get('busy'):
                self.operation_started = True
                self.inflight = self.state.get('action')
            elif self.operation_started:
                self.inflight = None
            self.scroll.setEnabled(not self.inflight or self.operation_started)
            self.render()
        elif event.get('type') == 'progress':
            message=event.get('message', '正在等待结果…')
            self.progress.append(message)
            if getattr(self,'progress_log_label',None):
                self.progress_log_label.setText('\n'.join(self.progress))
            if getattr(self, 'progress_label', None) and not self.cancel_requested:
                self.progress_label.setText(message)
        elif event.get('type') == 'error':
            self.inflight = None
            self.scroll.setEnabled(True)
            QMessageBox.information(self, '暂时无法继续', event.get('message', '请先完成前置步骤。'))


    def driver_help(self):
        box=QMessageBox(self);box.setWindowTitle('Windows 驱动说明与恢复');box.setText(DRIVER_HELP)
        link=box.addButton('打开 Zadig 官方网站',QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close);box.exec()
        if box.clickedButton()==link:QDesktopServices.openUrl(QUrl('https://zadig.akeo.ie/'))

    def confirm_write(self, action):
        if self.inflight or self.state.get('busy'):
            return
        rows = [r for r in self.state.get('features', []) if r.get('selected') and r.get('available', True)]
        if not rows:
            return
        target='enabled' if action=='unlock' else 'disabled'
        affected=[r for r in rows if r.get('state')!=target or (action=='unlock' and r.get('update_available'))]
        if not affected:
            return
        selection={r['id'] for r in rows}
        dialog=OperationConfirmation(self,action,affected,self.physical,len(rows)-len(affected))
        try:
            result=dialog.exec()
        finally:
            dialog.dim.hide();dialog.dim.deleteLater();dialog.deleteLater()
        current={r['id'] for r in self.state.get('features',[]) if r.get('selected') and r.get('available',True)}
        if result==OperationConfirmation.DialogCode.Accepted and current==selection and not self.inflight and self.state.get('compatible'):
            self.command(action)

    def closeEvent(self, event: QCloseEvent):
        if self.state.get('busy') or self.inflight:
            QMessageBox.information(self, '请等待当前操作', '正在操作，暂不能关闭。可请求取消并等待明确结果。')
            event.ignore(); return
        if self.client.alive():
            self.closing = True; self.mode.setEnabled(False); self.command('shutdown'); event.ignore(); return
        event.accept()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--demo', action='store_true')
    parser.add_argument('--offline-self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.offline_self_test and args.report is None:
        parser.error('--offline-self-test requires --report')
    app = QApplication(sys.argv[:1]); app.setStyle('Fusion')
    font=QFont('Microsoft YaHei UI' if sys.platform=='win32' else 'PingFang SC');font.setPixelSize(12);app.setFont(font);app.setStyleSheet(STYLE)
    window = Window(args.demo or args.offline_self_test); window.show()
    if args.offline_self_test:
        from smoke import run
        return run(window, args.report)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
