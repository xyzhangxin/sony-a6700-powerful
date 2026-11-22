"""Windows transport shell; Mac-aligned views live in views.py/design.py."""
import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from PySide6.QtCore import QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QVBoxLayout
from audio_market import AudioMarketSession
from client import ServiceClient
from design import STYLE, ACCENT, FEATURES, Symbol, label
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
    def __init__(self,demo=False, *, enable_usage_session=True, audio_library=None, audio_favorites=None):
        super().__init__()
        self.setWindowTitle('Powerful6700')
        self.resize(1060,770);self.setMinimumSize(980,720)
        self.client=ServiceClient(self)
        self.client.event.connect(self.on_event);self.client.exited.connect(self.on_exit)
        self.state={};self.pending_mode=None;self.closing=False;self.update_status=None;self.update_available=False;self._auto_update_check=False
        self._catalog_update_inflight = False
        self._pending_audio_catalog = False
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
        self.audio_market = AudioMarketSession(library=audio_library, favorites_store=audio_favorites, parent=self)
        self.audio_slots = None
        self.audio_slots_busy = False
        self.audio_slots_loaded_op = None
        self.audio_import_flow_active = False
        self.audio_import_flow_slot = None
        self.audio_import_flow_message = ''
        self.audio_plan = None
        self.audio_confirmation = None
        self.audio_execute = None
        self.audio_error_text = ''
        self._pending_preview_request = None
        self.build_shell(demo)
        QTimer.singleShot(0,self.start)

    @property
    def physical(self):
        return self.mode.currentIndex() == 0

    def start(self):
        try:
            self.transitioning = False; self.inflight = None; self.state = {}; self.page_key = None
            self.operation_started = False; self.preparation_acknowledged=False; self.scroll.setEnabled(True)
            self._catalog_update_inflight = False; self._pending_audio_catalog = False
            self.reset_audio_import_flow()
            self.audio_slots = None
            self.audio_slots_busy = False
            self.audio_slots_loaded_op = None
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
                self.refresh_audio_catalog()
                self._auto_update_check=True
                self._catalog_update_inflight = True
                self.client.send('check_updates')
            else:
                self.audio_market.loading_catalog = False
        except Exception:
            self.render_interrupted('本机服务未能启动。请保留完整目录，重新解压应用。')

    def camera_operation_active(self):
        return bool(self.inflight or self.state.get('busy') or self.audio_import_flow_active)

    def _current_phase(self):
        return 'progress' if self.state.get('busy') else self.state.get('phase', 'notice')

    def _is_camera_connected(self):
        return self._current_phase() == 'home' and bool(self.state.get('compatible')) and bool(self.state.get('connected'))

    def _feature_state(self, feature_id):
        for item in self.state.get('features', []):
            if item.get('id') == feature_id:
                return item.get('state', 'unknown')
        return 'unknown'

    def _custom_sound_state(self):
        cs = self.state.get('custom_sound')
        runtime_state = cs.get('state', 'unknown') if isinstance(cs, dict) else 'unknown'
        if runtime_state == 'error':
            return runtime_state
        for item in self.state.get('features', []):
            if item.get('id') == 'custom_shutter_sound':
                return item.get('state', 'unknown')
        return runtime_state

    def _custom_sound_recoverable(self):
        cs = self.state.get('custom_sound') or {}
        return (
            self.physical
            and self._custom_sound_state() == 'error'
            and bool(cs.get('installable'))
            and cs.get('recoverable') is True
        )

    def _can_install_audio(self):
        return (
            self._is_camera_connected()
            and self._feature_state('sound') == 'enabled'
            and self._custom_sound_state() == 'enabled'
            and self.state.get('camera_disconnect') is None
        )

    def _install_disabled_reason(self):
        if self.state.get('camera_disconnect') is not None:
            return '相机已断开维护模式，请重新连接后再安装声音。'
        if not self._is_camera_connected():
            return '请先连接并检查相机，再将声音安装到槽位'
        if self._feature_state('sound') != 'enabled':
            return '请先在「功能管理」解锁电子快门声音'
        custom_state = self._custom_sound_state()
        if custom_state == 'error':
            return '自定义快门声加载校验失败，请查看快门声市场的状态说明'
        if custom_state != 'enabled':
            return '当前相机未解锁自定义快门声'
        return '选择相机槽位 1～10 写入当前声音'

    def open_market(self):
        if self.camera_operation_active():
            return
        self.return_to_market_after_connect = False
        self.viewing_market = True
        self.ensure_audio_slots_loaded()
        self.refresh_chrome(self._current_phase())

    def open_camera_workspace(self):
        self.return_to_market_after_connect = False
        self.viewing_market = False
        self.render()

    def _connect_from_market(self):
        if self.camera_operation_active():
            return
        self.return_to_market_after_connect = True
        self.viewing_market = False
        self.render()

    def refresh_audio_catalog(self):
        if not (self.physical and self.client.alive()) or self.inflight or self.state.get('busy'):
            return
        if self._catalog_update_inflight:
            self._pending_audio_catalog = True
            self.audio_market.loading_catalog = True
            self.audio_market.changed.emit()
            return
        self._pending_audio_catalog = False
        self.audio_market.loading_catalog = True
        self.audio_market.changed.emit()
        self.client.send('list_audio', request_id=uuid4().hex)

    def ensure_audio_slots_loaded(self):
        if not (self.physical and self._is_camera_connected() and self._custom_sound_state() == 'enabled' and self.state.get('camera_disconnect') is None):
            return
        op_id = self.state.get('operation_id') or 'connected'
        if self.audio_slots_loaded_op == op_id:
            return
        self.refresh_audio_slots()

    def refresh_audio_slots(self):
        if not self.physical or self.camera_operation_active() or self.state.get('camera_disconnect') is not None:
            return
        self.audio_slots_busy = True
        self.audio_slots = None
        self.reset_audio_import_flow()
        self.client.send('refresh_audio_slots', request_id=uuid4().hex)

    def reset_audio_import_flow(self):
        self.audio_import_flow_active = False
        self.audio_import_flow_slot = None
        self.audio_import_flow_message = ''
        self.audio_plan = None
        self.audio_confirmation = None
        self.audio_execute = None
        self.audio_error_text = ''

    def start_market_sound_import(self, sound, slot, target_name=None):
        if self.state.get('busy') or self.inflight or not (1 <= slot <= 10):
            return
        self.reset_audio_import_flow()
        self.audio_import_flow_active = True
        self.audio_import_flow_slot = slot
        self.audio_import_flow_message = f'正在校验音频并生成槽位 {slot} 导入计划…'
        extra = {'slot': slot, 'request_id': uuid4().hex}
        custom_name = (target_name or '').strip()
        if custom_name:
            extra['filename'] = custom_name
        if sound.source_kind in ('sample', 'personal') and sound.file_path is not None:
            extra.update(source='local', path=str(sound.file_path))
        elif sound.source_kind == 'catalog':
            extra.update(source='market', package_id=sound.package_id, sound_id=sound.sound_id)
        else:
            self.audio_import_flow_active = False
            self.audio_import_flow_message = ''
            self.audio_error_text = '音频来源无效。'
            return
        self.client.send('plan_audio_import', **extra)

    def _preview_market_sound(self, sound):
        def fetch_catalog(pkg_id, snd_id, full_id):
            if not self.physical or self.camera_operation_active():
                self.audio_market.preparing_id = None
                self.audio_market.error = '当前模式下请直接导入或试听内置声音。'
                self.audio_market.changed.emit()
                return
            request_id = uuid4().hex
            self._pending_preview_request = (request_id, full_id)
            self.client.send('preview_audio', package_id=pkg_id, sound_id=snd_id, request_id=request_id)

        self.audio_market.preview(sound, worker_preview_cb=fetch_catalog)

    def _market_controller(self):
        return {
            'is_physical': lambda: self.physical,
            'is_camera_connected': self._is_camera_connected,
            'is_busy': self.camera_operation_active,
            'electronic_sound_state': lambda: self._feature_state('sound'),
            'custom_sound_state': self._custom_sound_state,
            'custom_sound_recoverable': self._custom_sound_recoverable,
            'custom_sound_reason': lambda: (self.state.get('custom_sound') or {}).get('reason', ''),
            'recover_custom_sound': lambda: self.command('recover_custom_sound'),
            'camera_disconnect_result': lambda: self.state.get('camera_disconnect'),
            'can_install_audio': self._can_install_audio,
            'install_disabled_reason': self._install_disabled_reason,
            'get_camera_slots': lambda: self.audio_slots,
            'audio_slots_busy': lambda: self.audio_slots_busy,
            'audio_import_busy': lambda: self.audio_import_flow_active,
            'get_flow_message': lambda: self.audio_import_flow_message,
            'get_audio_execute': lambda: self.audio_execute,
            'get_audio_error': lambda: self.audio_error_text,
            'on_connect': self._connect_from_market,
            'on_go_features': self.open_camera_workspace,
            'disconnect': lambda: self.command('disconnect_camera'),
            'refresh_catalog': self.refresh_audio_catalog,
            'refresh_slots': self.refresh_audio_slots,
            'ensure_slots_loaded': self.ensure_audio_slots_loaded,
            'reset_import_flow': self.reset_audio_import_flow,
            'start_import': self.start_market_sound_import,
            'preview_sound': self._preview_market_sound,
        }

    def command(self, name, **values):
        if name in ('prepare_and_inspect', 'inspect', 'unlock', 'close', 'update_fl23', 'update_ftp', 'recover_custom_sound', 'check_updates', 'disconnect_camera'):
            if self.inflight or self.state.get('busy'):
                return
            if name == 'check_updates':
                self._catalog_update_inflight = True
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
        self._catalog_update_inflight = False
        self._pending_audio_catalog = False
        if self.app_download_progress is not None:
            self.finish_app_download({'status': 'download-failed'})
        self.app_update_request = None
        if self.failure_dialog is not None:self.failure_dialog.service_exited()
        uncertain = self.inflight in ('unlock', 'close', 'update_fl23', 'update_ftp', 'recover_custom_sound') or (self.state.get('busy') and self.state.get('action') in ('unlock', 'close', 'recover_custom_sound'))
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
            if (self.viewing_market
                    and self.state.get('phase') in ('running', 'result')
                    and self.state.get('action') == 'recover_custom_sound'):
                self.viewing_market = False
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
            self._catalog_update_inflight = False
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
            if self._pending_audio_catalog:
                self.refresh_audio_catalog()
        elif event.get('type') == 'progress':
            message=event.get('message', '正在等待结果…')
            self.progress.append(message)
            if getattr(self,'progress_log_label',None):
                self.progress_log_label.setText('\n'.join(self.progress))
            if getattr(self, 'progress_label', None) and not self.cancel_requested:
                self.progress_label.setText(message)
        elif event.get('type') == 'audio-result':
            result = event.get('result', {})
            incoming_slots = result.get('slots')
            if isinstance(incoming_slots, list) and (
                not self.audio_slots
                or not any(item.get('status') != 'unknown' for item in self.audio_slots)
                or any(item.get('status') != 'unknown' for item in incoming_slots)
            ):
                self.audio_slots = incoming_slots
            self.audio_market.set_catalog_from_worker(result.get('packages', []))
            if hasattr(self, 'market_widget') and self.viewing_market:
                self.market_widget.refresh()
        elif event.get('type') == 'audio-slots-result':
            result = event.get('result', {})
            self.audio_slots_busy = False
            if result.get('status') in ('observed', 'ready') and isinstance(result.get('slots'), list) and not result.get('error'):
                self.audio_slots = result.get('slots')
                self.audio_slots_loaded_op = self.state.get('operation_id') or 'connected'
                self.audio_error_text = ''
            else:
                self.audio_slots = None
                self.audio_slots_loaded_op = None
                self.audio_error_text = result.get('error') or '相机槽位读取失败。'
            if hasattr(self, 'market_widget'):
                self.market_widget.notify_install_state()
        elif event.get('type') == 'audio-preview-result':
            result = event.get('result', {})
            pending = self._pending_preview_request
            if pending is None or pending[0] != event.get('request_id'):
                return
            sid = pending[1]
            self._pending_preview_request = None
            if sid != self.audio_market.preparing_id:
                return
            if result.get('status') == 'ready' and result.get('path'):
                try:
                    raw = Path(result['path']).read_bytes()
                    self.audio_market._play_wav_bytes(raw, sid, result.get('duration_ms', 350))
                except OSError:
                    self.audio_market.preparing_id = None
                    self.audio_market.error = '音频试听文件读取失败。'
                    self.audio_market.changed.emit()
            else:
                self.audio_market.preparing_id = None
                self.audio_market.error = result.get('error') or '音频试听准备失败。'
                self.audio_market.changed.emit()
        elif event.get('type') == 'audio-import-plan-result':
            result = event.get('result', {})
            self.audio_plan = result
            if self.audio_import_flow_active:
                if result.get('status') == 'ready' and result.get('can_execute') and result.get('digest'):
                    slot = (result.get('target') or {}).get('slot', self.audio_import_flow_slot)
                    self.audio_import_flow_message = f'音频检查通过，正在确认导入槽位 {slot}…'
                    self.client.send('confirm_audio_import', plan_digest=result['digest'], confirmed=True, request_id=uuid4().hex)
                else:
                    self.audio_import_flow_active = False
                    self.audio_import_flow_message = ''
                    reasons = result.get('reasons') or []
                    self.audio_error_text = result.get('error') or (reasons[0].get('message') if reasons else '音频校验或导入计划生成未通过。')
            if hasattr(self, 'market_widget'):
                self.market_widget.notify_install_state()
        elif event.get('type') == 'audio-import-confirmation-result':
            result = event.get('result', {})
            self.audio_confirmation = result
            if self.audio_import_flow_active:
                digest = result.get('plan_digest')
                if result.get('user_confirmation') == 'confirmed' and digest and self.audio_plan and digest == self.audio_plan.get('digest'):
                    slot = (self.audio_plan.get('target') or {}).get('slot', self.audio_import_flow_slot)
                    self.audio_import_flow_message = f'正在写入相机槽位 {slot} 并执行独立回读…'
                    self.client.send('execute_audio_import', plan_digest=digest, request_id=uuid4().hex)
                else:
                    self.audio_import_flow_active = False
                    self.audio_import_flow_message = ''
                    self.audio_error_text = result.get('error') or '未能确认导入计划。'
            if hasattr(self, 'market_widget'):
                self.market_widget.notify_install_state()
        elif event.get('type') == 'audio-import-execute-progress':
            if self.audio_import_flow_active:
                self.audio_import_flow_message = event.get('message') or '正在写入相机槽位…'
                if hasattr(self, 'market_widget'):
                    self.market_widget.notify_install_state()
        elif event.get('type') == 'audio-import-execute-result':
            result = event.get('result', {})
            self.audio_execute = result
            self.audio_import_flow_active = False
            self.audio_import_flow_message = ''
            if result.get('status') == 'completed' and result.get('independent_readback') is True:
                self.audio_error_text = ''
                slot_num = result.get('target_slot', self.audio_import_flow_slot)
                if slot_num is not None:
                    slot_int = int(slot_num)
                    fname = ((self.audio_plan or {}).get('candidate') or {}).get('filename') or result.get('filename') or '已写入'
                    base_slots = self.audio_slots or [
                        dict(slot=i, name='[未读取]', label=f'{i} [未读取]', status='unknown', generation=None, playable=False)
                        for i in range(1, 11)
                    ]
                    self.audio_slots = [
                        (dict(
                            item,
                            name=fname,
                            label=f'{slot_int} {fname}',
                            status='imported',
                            generation=result.get('generation', item.get('generation')),
                            playable=True,
                        ) if int(item.get('slot', 0)) == slot_int else item)
                        for item in base_slots
                    ]
            else:
                self.audio_error_text = result.get('error') or '声音槽位写入未完成。'
                if result.get('write_attempted'):
                    self.audio_slots = None
                    self.audio_slots_loaded_op = None
            if hasattr(self, 'market_widget'):
                self.market_widget.notify_install_state()
        elif event.get('type') == 'error':
            self._catalog_update_inflight = False
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
        if self.state.get('busy') or self.inflight or self.audio_import_flow_active:
            QMessageBox.information(self, '请等待当前操作', '正在操作，暂不能关闭。可请求取消并等待明确结果。')
            event.ignore(); return
        if self.client.alive():
            self.closing = True; self.mode.setEnabled(False); self.command('shutdown'); event.ignore(); return
        self.audio_market.cleanup()
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
    smoke_dir = TemporaryDirectory(prefix='powerful-win-smoke-') if args.offline_self_test else None
    try:
        window = Window(
            args.demo or args.offline_self_test,
            enable_usage_session=not args.offline_self_test,
            audio_library=(Path(smoke_dir.name) / 'audio-library') if smoke_dir else None,
            audio_favorites={} if args.offline_self_test else None,
        )
        if args.offline_self_test:
            window.checked_app_at_startup = True
        window.show()
        if args.offline_self_test:
            from smoke import run
            return run(window, args.report)
        return app.exec()
    finally:
        if smoke_dir is not None:
            smoke_dir.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
