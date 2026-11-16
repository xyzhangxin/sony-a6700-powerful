"""Windows transport shell; Mac-aligned views live in views.py/design.py."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from PySide6.QtCore import QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QVBoxLayout
from client import ServiceClient
from design import STYLE, ACCENT, Symbol, label
from feedback import FeedbackDialog, FailureFeedbackDialog
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
    def __init__(self,demo=False, *, enable_usage_session=True):
        super().__init__()
        self.setWindowTitle('Powerful6700')
        self.resize(1060,770);self.setMinimumSize(980,720)
        self.client=ServiceClient(self)
        self.client.event.connect(self.on_event);self.client.exited.connect(self.on_exit)
        self.state={};self.pending_mode=None;self.closing=False;self.update_status=None;self.update_available=False;self._auto_update_check=False
        self.transitioning=False;self.inflight=None;self.operation_started=False;self.page_key=None
        self.update_preferences = QSettings('Powerful6700', 'ApplicationUpdates')
        self.checked_app_at_startup = False
        self.enable_usage_session = enable_usage_session
        self.app_update_request = None
        self.manual_app_update = False
        self.app_release = None
        self.app_update_archive = None
        self.app_download_progress = None
        self.feedback_dialog=None;self.failure_dialog=None;self.preparation_acknowledged=False
        self.feedback_toast = None
        self.feedback_toast_timer = QTimer(self)
        self.feedback_toast_timer.setSingleShot(True)
        self.feedback_toast_timer.timeout.connect(self.dismiss_feedback_toast)
        self.build_shell(demo)
        QTimer.singleShot(0,self.start)

    @property
    def physical(self):
        return self.mode.currentIndex() == 0

    def start(self):
        try:
            self.transitioning = False; self.inflight = None; self.state = {}; self.page_key = None
            self.operation_started = False; self.preparation_acknowledged=False; self.scroll.setEnabled(True)
            self.client.start(self.physical)
            self.app_update_request = None
            self.app_download_progress = None
            if self.app_update_archive is None:
                self.app_release = None
                self.dismiss_app_update()
            self.command('environment')
            if self.physical:
                if self.enable_usage_session:
                    self.client.send('start_usage')
                if not self.checked_app_at_startup and self.update_preferences.value('checkAtStartup', True, type=bool):
                    self.checked_app_at_startup = True
                    self.check_app_update(automatic=True)
                self._auto_update_check=True
                self.client.send('check_updates')
        except Exception:
            self.render_interrupted('本机服务未能启动。请保留完整目录，重新解压应用。')

    def command(self, name, **values):
        if name in ('prepare_and_inspect', 'inspect', 'unlock', 'close', 'update_fl23', 'update_ftp', 'check_updates'):
            if self.inflight or self.state.get('busy'):
                return
            if name == 'check_updates':
                if self._auto_update_check:
                    self._auto_update_check=False
                    self.client.send('check_updates')
                    return
                self.update_status = None
                self.update_available = False
            self.inflight = 'inspect' if name=='prepare_and_inspect' else name
            self.operation_started = False
            self.mode.setEnabled(False)
            self.mode_segments.setEnabled(False)
            self.scroll.setEnabled(False)
        try:
            self.client.send(name, **values)
            return True
        except (OSError, RuntimeError):
            if name not in ('preview_failure_feedback', 'upload_failure_feedback'):
                self.render_interrupted('本机服务连接已中断；上一次操作不会自动重发。')
            return False

    def check_app_update(self, automatic=False):
        if not self.physical or self.inflight or self.state.get('busy') or self.app_update_request:
            return
        self.manual_app_update = not automatic
        try:
            self.request_app_update('check_app_update')
        except (OSError, RuntimeError):
            self.finish_app_update({'status': 'unavailable'})

    def request_app_update(self, command):
        self.app_update_request = uuid4().hex
        self.client.send(command, api_version=1, request_id=self.app_update_request)

    def update_app_download(self):
        if self.app_update_archive:
            archive = Path(self.app_update_archive)
            if archive.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(archive.parent)))
                return
            self.app_update_archive = None
        if not self.physical or not self.app_release or self.inflight or self.state.get('busy') or self.app_update_request:
            return
        self.app_download_progress = 0
        self.show_app_update()
        try:
            self.request_app_update('download_app_update')
        except (OSError, RuntimeError):
            self.finish_app_download({'status': 'unavailable'})

    def finish_app_download(self, result):
        self.app_update_request = None
        self.app_download_progress = None
        if result.get('status') == 'downloaded' and result.get('path'):
            self.app_update_archive = result['path']
        else:
            message = '安装包校验失败，请重新下载。' if result.get('status') == 'verification-failed' else '下载未完成，请重新检查应用更新或稍后重试。'
            QMessageBox.information(self, '应用更新', message)
        self.show_app_update()

    def show_app_update(self):
        if self.app_download_progress is not None:
            title = f'正在下载新版 · {self.app_download_progress}%'
        elif self.app_update_archive:
            title = '查看已下载的新版'
        else:
            title = f"下载新版 {self.app_release['version']}"
        self.app_update_button.setText(title)
        self.app_update_button.setEnabled(self.app_download_progress is None)
        self.app_update_button.show()
        self.dismiss_app_update_button.setVisible(self.app_download_progress is None)

    def finish_app_update(self, result):
        self.app_update_request = None
        status = result.get('status')
        if status == 'available' and result.get('version'):
            if self.app_release != result:
                self.app_update_archive = None
            self.app_release = result
            self.show_app_update()
            return
        if status == 'up-to-date':
            self.app_release = None
            self.app_update_archive = None
            self.dismiss_app_update()
        if self.manual_app_update:
            messages = {
                'up-to-date': '当前已是最新应用版本。',
                'offline': '无法连接 GitHub，请检查网络后重试。',
                'rate-limited': 'GitHub 暂时限制了查询，请稍后重试。',
                'checking': '正在检查应用更新，请稍候。',
            }
            QMessageBox.information(self, '应用更新', messages.get(status, '暂时无法检查应用更新，请稍后重试。'))

    def dismiss_app_update(self):
        self.app_update_button.hide()
        self.dismiss_app_update_button.hide()

    def show_failure_feedback(self):
        if self.state.get('busy') or self.inflight:
            return
        if self.failure_dialog is not None and self.failure_dialog.isVisible():
            return
        dialog = FailureFeedbackDialog(self)
        self.failure_dialog = dialog
        dialog.finished.connect(lambda _: self.clear_failure_feedback(dialog))
        dialog.show()
        self.dismiss_feedback_toast()
        if not self.command('preview_failure_feedback'):
            self.failure_dialog.service_exited()

    def clear_failure_feedback(self, dialog):
        if self.failure_dialog is dialog:
            self.failure_dialog = None

    def show_feedback_toast(self, receipt):
        self.dismiss_feedback_toast()
        bubble = QFrame(self)
        bubble.setObjectName('feedbackToast')
        bubble.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bubble.setStyleSheet('QFrame#feedbackToast {background:#f1f8f5;border:1px solid #bbd8cc;border-radius:10px;}')
        layout = QHBoxLayout(bubble)
        layout.setContentsMargins(13, 9, 13, 9)
        layout.addWidget(Symbol('success', 20, ACCENT))
        messages = QVBoxLayout()
        messages.setSpacing(3)
        messages.addWidget(label('异常日志已上传', 'heading', False))
        if receipt:
            messages.addWidget(label(f'反馈编号 {receipt}', 'muted', False))
        layout.addLayout(messages)
        self.feedback_toast = bubble
        bubble.adjustSize()
        self.place_feedback_toast()
        bubble.show()
        bubble.raise_()
        self.feedback_toast_timer.start(5000)

    def place_feedback_toast(self):
        if getattr(self, 'feedback_toast', None) is not None:
            self.feedback_toast.move(max(12, self.width() - self.feedback_toast.width() - 24), 72)

    def dismiss_feedback_toast(self):
        if self.feedback_toast is not None:
            self.feedback_toast.hide()
            self.feedback_toast.deleteLater()
            self.feedback_toast = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_feedback_toast()

    def show_feedback(self):
        if self.feedback_dialog is None:
            self.feedback_dialog = FeedbackDialog(self)
        self.feedback_dialog.show()

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
        if self.app_download_progress is not None:
            self.finish_app_download({'status': 'download-failed'})
        self.app_update_request = None
        if self.failure_dialog is not None:self.failure_dialog.service_exited()
        uncertain = self.inflight in ('unlock', 'close', 'update_fl23', 'update_ftp') or (self.state.get('busy') and self.state.get('action') in ('unlock', 'close'))
        self.render_interrupted('结果待核实：服务在功能操作中退出。请保留本机记录，先重新检查，不要盲目重复写入。'
                                if uncertain else '本机服务已退出。重新启动服务后，重新准备并连接相机。')

    def on_event(self, generation, event):
        if generation != self.client.generation or self.transitioning or self.closing:
            return
        if self.feedback_dialog is not None:
            self.feedback_dialog.handle_event(event)
        if self.failure_dialog is not None and self.failure_dialog.isVisible():
            if self.failure_dialog.handle_event(event):
                return
        if event.get('type') == 'state':
            self.state = event['state']
            if self.state.get('phase') != 'result':
                self.dismiss_feedback_toast()
            if self.state.get('busy'):
                self.operation_started = True
                self.inflight = self.state.get('action')
            elif self.operation_started:
                self.inflight = None
            self.scroll.setEnabled(not self.inflight or self.operation_started)
            self.render()
        elif event.get('type') == 'app-update-result':
            if self.app_update_request and event.get('request_id') == self.app_update_request:
                self.finish_app_update(event.get('result', {}))
        elif event.get('type') == 'app-update-download-progress':
            if self.app_update_request and event.get('request_id') == self.app_update_request:
                self.app_download_progress = event['percent']
                self.show_app_update()
        elif event.get('type') == 'app-update-download-result':
            if self.app_update_request and event.get('request_id') == self.app_update_request:
                self.finish_app_download(event.get('result', {}))
        elif event.get('type') == 'update-result':
            result = event.get('result', {})
            status = result.get('status', 'error')
            sequence = result.get('sequence')
            labels = {
                'activated': '相机功能包已更新',
                'unchanged': '相机功能包已是最新',
                'not-modified': '相机功能包已是最新',
                'unconfigured': '更新暂不可用',
                'offline': '网络不可用',
                'unavailable': '更新暂不可用',
                'artifact-unavailable': '更新暂不可用',
                'rejected': '更新失败',
            }
            self.update_status = labels.get(status, None)
            if status == 'activated':
                self.update_available = True
            self.inflight = None
            self.scroll.setEnabled(True)
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
    window = Window(args.demo or args.offline_self_test, enable_usage_session=not args.offline_self_test)
    if args.offline_self_test:
        window.checked_app_at_startup = True
    window.show()
    if args.offline_self_test:
        from smoke import run
        return run(window, args.report)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
