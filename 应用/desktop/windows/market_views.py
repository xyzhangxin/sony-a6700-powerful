"""Windows PySide6 Shutter Sound Market UI mirroring Mac ShutterSoundMarketView."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from audio_market import (
    CAMERA_CLIP_DURATION,
    AudioMarketError,
    AudioMarketSession,
    AudioTrimRequest,
    MarketSound,
    camera_slot_name_error,
    default_camera_target_name,
    trim_waveform,
    validated_camera_target_name,
)
from design import ACCENT, AMBER, INK, MUTED, Symbol, button, column, label, line, panel, row, symbol_icon


class AudioTrimTimeline(QWidget):
    """Interactive 64-bar waveform selector with <=350ms trim window."""

    def __init__(self, duration: float, peaks: list[float], on_change, parent=None):
        super().__init__(parent)
        self.duration = max(0.05, duration)
        self.peaks = peaks or [0.2] * 64
        self.start = 0.0
        self.end = min(self.duration, CAMERA_CLIP_DURATION)
        self._on_change = on_change
        self._drag_mode: str | None = None
        self._drag_origin_x = 0.0
        self._drag_origin_start = self.start
        self._drag_origin_end = self.end
        self.setMinimumHeight(88)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _x_to_time(self, x: float) -> float:
        w = max(1.0, float(self.width() - 16))
        frac = max(0.0, min(1.0, (x - 8.0) / w))
        return frac * self.duration

    def _time_to_x(self, t: float) -> float:
        w = max(1.0, float(self.width() - 16))
        return 8.0 + (t / self.duration) * w

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = float(event.position().x())
        sx = self._time_to_x(self.start)
        ex = self._time_to_x(self.end)
        self._drag_origin_x = x
        self._drag_origin_start = self.start
        self._drag_origin_end = self.end
        if abs(x - sx) <= 10:
            self._drag_mode = 'start'
        elif abs(x - ex) <= 10:
            self._drag_mode = 'end'
        elif sx <= x <= ex:
            self._drag_mode = 'window'
        else:
            # Jump window center to click position.
            length = self.end - self.start
            t = self._x_to_time(x)
            ns = max(0.0, min(self.duration - length, t - length / 2.0))
            self.start = ns
            self.end = ns + length
            self._drag_mode = 'window'
            self._drag_origin_start = self.start
            self._drag_origin_end = self.end
            self.update()
            self._on_change(self.start, self.end)

    def mouseMoveEvent(self, event):
        if not self._drag_mode:
            return
        x = float(event.position().x())
        dx_t = ((x - self._drag_origin_x) / max(1.0, float(self.width() - 16))) * self.duration
        min_len = min(0.01, self.duration)
        if self._drag_mode == 'start':
            lo = max(0.0, self._drag_origin_end - CAMERA_CLIP_DURATION)
            hi = self._drag_origin_end - min_len
            self.start = max(lo, min(hi, self._drag_origin_start + dx_t))
        elif self._drag_mode == 'end':
            lo = self._drag_origin_start + min_len
            hi = min(self.duration, self._drag_origin_start + CAMERA_CLIP_DURATION)
            self.end = max(lo, min(hi, self._drag_origin_end + dx_t))
        else:
            length = self._drag_origin_end - self._drag_origin_start
            self.start = max(0.0, min(self.duration - length, self._drag_origin_start + dx_t))
            self.end = self.start + length
        self.update()
        self._on_change(self.start, self.end)

    def mouseReleaseEvent(self, event):
        self._drag_mode = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        p.setPen(QPen(QColor('#e6e9e8'), 1))
        p.setBrush(QColor('#ffffff'))
        p.drawRoundedRect(QRectF(0.5, 0.5, rect.width() - 1, rect.height() - 1), 8, 8)

        sx = self._time_to_x(self.start)
        ex = self._time_to_x(self.end)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 94, 79, 26))
        p.drawRect(QRectF(sx, 6, max(4.0, ex - sx), rect.height() - 12))

        count = len(self.peaks)
        usable_w = max(1.0, float(rect.width() - 16))
        bar_w = max(2.0, usable_w / count - 2.0)
        mid_y = rect.height() / 2.0
        max_h = rect.height() - 24.0
        for idx, amp in enumerate(self.peaks):
            bx = 8.0 + (idx / count) * usable_w
            bh = max(4.0, amp * max_h)
            inside = sx <= bx + bar_w / 2.0 <= ex
            p.setBrush(QColor(ACCENT if inside else '#c5ceca'))
            p.drawRoundedRect(QRectF(bx, mid_y - bh / 2.0, bar_w, bh), 1.5, 1.5)

        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawLine(QPointF(sx, 6), QPointF(sx, rect.height() - 6))
        p.drawLine(QPointF(ex, 6), QPointF(ex, rect.height() - 6))
        p.end()


class AudioTrimDialog(QDialog):
    """Modal audio clipper for files longer than 350 ms."""

    def __init__(self, session: AudioMarketSession, request: AudioTrimRequest, parent=None):
        super().__init__(parent)
        self.session = session
        self.request = request
        self.saved_sound: MarketSound | None = None
        self.setWindowTitle('裁剪音频片段')
        self.setModal(True)
        self.setFixedWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        layout.addWidget(label('裁剪音频片段', 'title'))
        layout.addWidget(label(f'相机槽位单段最长 350 毫秒；「{request.title}」总长 {request.duration:.2f} 秒，请在下方拖动选择区间。', 'muted'))

        peaks = trim_waveform(request.pcm_samples, bins=64)
        self.timeline = AudioTrimTimeline(request.duration, peaks, self._on_range_changed, self)
        layout.addWidget(self.timeline)

        self.range_label = label('', 'muted', False)
        layout.addWidget(self.range_label)
        self.err_label = label('', 'amber')
        self.err_label.hide()
        layout.addWidget(self.err_label)

        actions = row(layout, 10)
        self.preview_btn = button(actions, '试听片段', self._toggle_preview, 'secondary')
        actions.addStretch()
        button(actions, '取消', self.reject, 'secondary')
        self.save_btn = button(actions, '保存并导入', self._save, 'primary')

        self.session.changed.connect(self._sync_preview_button)
        self._on_range_changed(self.timeline.start, self.timeline.end)

    def _on_range_changed(self, start: float, end: float):
        if self.session.playing_id == self.request.id:
            self.session.stop_preview()
        self.range_label.setText(f'起点 {start:.3f} 秒 · 终点 {end:.3f} 秒 · 时长 {(end - start):.3f} 秒')

    def _sync_preview_button(self):
        playing = self.session.playing_id == self.request.id
        self.preview_btn.setText('停止试听' if playing else '试听片段')

    def _toggle_preview(self):
        try:
            self.err_label.hide()
            self.session.preview_trim_range(self.request, self.timeline.start, self.timeline.end)
        except AudioMarketError as exc:
            self.err_label.setText(str(exc))
            self.err_label.show()

    def _save(self):
        try:
            self.err_label.hide()
            self.saved_sound = self.session.save_trim(self.request, self.timeline.start, self.timeline.end)
            self.accept()
        except AudioMarketError as exc:
            self.err_label.setText(str(exc))
            self.err_label.show()

    def done(self, result):
        self.session.stop_preview()
        super().done(result)


class RenameAudioDialog(QDialog):
    """Rename a locally imported personal sound with camera slot UTF-8 validation."""

    def __init__(self, session: AudioMarketSession, sound: MarketSound, parent=None):
        super().__init__(parent)
        self.session = session
        self.sound = sound
        self.setWindowTitle('重命名音频')
        self.setModal(True)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(label('重命名音频', 'heading'))
        layout.addWidget(label('修改「我的音频」中的显示名称；写入相机时将直接使用该名称（支持中文与英文，无需 .wav 后缀）。', 'muted'))

        self.input = QLineEdit(sound.title)
        self.input.setPlaceholderText('输入音频名称（支持中文、英文、数字、空格及 ._-）')
        self.input.textChanged.connect(self._validate)
        layout.addWidget(self.input)

        self.hint = label('', 'accent')
        layout.addWidget(self.hint)

        btns = row(layout, 10)
        btns.addStretch()
        button(btns, '取消', self.reject, 'secondary')
        self.save_btn = button(btns, '保存', self._save, 'primary')
        self._validate()

    def _validate(self):
        trimmed = self.input.text().strip()
        err = camera_slot_name_error(trimmed)
        if err and trimmed:
            self.hint.setText(err)
            self.hint.setStyleSheet(f'color:{AMBER};font-size:11px;')
            self.save_btn.setEnabled(False)
        elif not trimmed:
            self.hint.setText('写入相机槽位名称：（未输入）')
            self.hint.setStyleSheet(f'color:{MUTED};font-size:11px;')
            self.save_btn.setEnabled(False)
        else:
            self.hint.setText(f'写入相机槽位名称：{trimmed}')
            self.hint.setStyleSheet(f'color:{ACCENT};font-size:11px;')
            self.save_btn.setEnabled(True)

    def _save(self):
        try:
            self.session.rename_personal_sound(self.sound, self.input.text())
            self.accept()
        except AudioMarketError as exc:
            self.hint.setText(str(exc))
            self.hint.setStyleSheet(f'color:{AMBER};font-size:11px;')


class ShutterSoundInstallDialog(QDialog):
    """Choose slot 1..10, validate target name, warn on overwrite, and execute or simulate import."""

    def __init__(self, session: AudioMarketSession, sound: MarketSound, controller, parent=None):
        super().__init__(parent)
        self.session = session
        self.sound = sound
        self.ctrl = controller
        self.selected_slot = self._initial_slot()
        self.user_selected = False
        self.demo_installed_slot: int | None = None
        self.prepare_error = ''
        self.slot_buttons: dict[int, QPushButton] = {}

        self.setWindowTitle('安装到相机槽位')
        self.setModal(True)
        self.setFixedWidth(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        head = row(layout, 12)
        titles = column(head, 4, 1)
        titles.addWidget(label('安装到相机槽位', 'title'))
        titles.addWidget(label(f'{sound.title} · {sound.duration_ms} 毫秒 · {sound.author}', 'muted'))
        self.preview_btn = button(head, '试听', lambda: self.ctrl['preview_sound'](sound), 'compactSecondary')
        head.setAlignment(self.preview_btn, Qt.AlignmentFlag.AlignTop)

        self.can_rename = sound.source_kind != 'catalog'
        if self.can_rename:
            name_row = row(layout, 8)
            name_row.addWidget(label('写入名称', 'heading', False))
            self.name_input = QLineEdit(default_camera_target_name(sound))
            self.name_input.setPlaceholderText('输入槽位名称（支持中文、英文、数字，无需 .wav）')
            self.name_input.textChanged.connect(lambda _: self.refresh())
            name_row.addWidget(self.name_input, 1)
            self.name_hint = label('', 'small')
            layout.addWidget(self.name_hint)
        else:
            self.name_input = None
            self.name_hint = None

        line(layout)

        slot_head = row(layout, 8)
        slot_head.addWidget(label('选择目标槽位（1～10）', 'heading', False))
        badge = label('默认 · 原厂（不可写入）', 'small', False)
        badge.setStyleSheet('font-size:10px;color:#6e787a;background:#eceeed;border-radius:9px;padding:2px 8px;')
        slot_head.addWidget(badge)
        slot_head.addStretch()
        self.refresh_slots_btn = button(slot_head, '刷新槽位', self.ctrl['refresh_slots'], 'compactSecondary')

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for slot in range(1, 11):
            btn = QPushButton()
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(52)
            btn.clicked.connect(lambda _=False, s=slot: self._select_slot(s))
            self.slot_buttons[slot] = btn
            grid.addWidget(btn, (slot - 1) // 5, (slot - 1) % 5)
        layout.addWidget(grid_host)

        self.status_box = QFrame()
        self.status_box.setStyleSheet('background:#f0f2f1;border-radius:8px;')
        sb_layout = QVBoxLayout(self.status_box)
        sb_layout.setContentsMargins(12, 10, 12, 10)
        self.status_label = label('', 'body')
        sb_layout.addWidget(self.status_label)
        layout.addWidget(self.status_box)

        footer = row(layout, 10)
        self.cancel_btn = button(footer, '取消', self.reject, 'secondary')
        footer.addStretch()
        self.confirm_btn = button(footer, '确认导入', self._perform_install, 'primary')
        self.done_btn = button(footer, '完成', self.accept, 'primary')

        self.session.changed.connect(self.refresh)
        self.ctrl['ensure_slots_loaded']()
        self.refresh()

    def _initial_slot(self) -> int:
        if self.ctrl['is_physical']():
            slots = self.ctrl['get_camera_slots']() or []
            for row_item in slots:
                if row_item.get('status') == 'empty':
                    return int(row_item['slot'])
            return 1
        return self.session.suggested_slot or 1

    def _select_slot(self, slot: int):
        if self._in_flight():
            return
        self.user_selected = True
        if self.selected_slot != slot:
            self.selected_slot = slot
            self.prepare_error = ''
            self.demo_installed_slot = None
            self.ctrl['reset_import_flow']()
        self.refresh()

    def _in_flight(self) -> bool:
        return bool(self.ctrl['audio_import_busy']())

    def _slot_info(self) -> tuple[str, bool, bool]:
        if self.ctrl['is_physical']():
            slots = self.ctrl['get_camera_slots']() or []
            for item in slots:
                if int(item.get('slot', 0)) == self.selected_slot:
                    st = item.get('status')
                    if st == 'empty':
                        return ('[空]', False, False)
                    if st == 'unknown':
                        return ('[未读取]', False, True)
                    return (str(item.get('name', '已占用')), True, False)
            return ('[未读取]', False, True)
        existing = self.session.slots[self.selected_slot - 1].sound
        return (existing.title, True, False) if existing else ('[空]', False, False)

    def _physical_succeeded(self) -> bool:
        ex = self.ctrl['get_audio_execute']()
        return self.ctrl['is_physical']() and bool(ex and ex.get('status') == 'completed' and ex.get('independent_readback') is True)

    def _succeeded(self) -> bool:
        return self._physical_succeeded() if self.ctrl['is_physical']() else (self.demo_installed_slot is not None)

    def refresh(self):
        physical = self.ctrl['is_physical']()
        slots = self.ctrl['get_camera_slots']() or []
        if physical and not self.user_selected:
            for item in slots:
                if item.get('status') == 'empty':
                    self.selected_slot = int(item['slot'])
                    break

        is_playing = self.session.playing_id == self.sound.id or self.session.preparing_id == self.sound.id
        self.preview_btn.setText('停止试听' if is_playing else '试听')

        in_flight = self._in_flight()
        succeeded = self._succeeded()
        if self.name_input is not None:
            self.name_input.setEnabled(not in_flight and not succeeded)

        trimmed = self.name_input.text().strip() if self.name_input is not None else ''
        name_err = camera_slot_name_error(trimmed) if self.can_rename else None
        if self.name_hint is not None:
            if name_err and trimmed:
                self.name_hint.setText(name_err)
                self.name_hint.setStyleSheet(f'color:{AMBER};font-size:10px;')
            else:
                preview_name = trimmed or default_camera_target_name(self.sound)
                self.name_hint.setText(f'相机槽位将显示：{preview_name}（支持中文 UTF-8，无需 .wav 后缀）')
                self.name_hint.setStyleSheet(f'color:{MUTED};font-size:10px;')

        self.refresh_slots_btn.setVisible(physical)
        self.refresh_slots_btn.setText('读取中…' if self.ctrl['audio_slots_busy']() else '刷新槽位')
        self.refresh_slots_btn.setEnabled(not self.ctrl['is_busy']() and not in_flight)

        slot_map = {int(item.get('slot', 0)): item for item in slots}
        for slot, btn in self.slot_buttons.items():
            if physical:
                row_item = slot_map.get(slot)
                slot_lbl = '[空]' if (row_item and row_item.get('status') == 'empty') else (str(row_item.get('name', '[未读取]')) if row_item else '[未读取]')
            else:
                s = self.session.slots[slot - 1].sound
                slot_lbl = s.title if s else '[空]'
            short = slot_lbl if len(slot_lbl) <= 9 else slot_lbl[:8] + '…'
            btn.setText(f'槽位 {slot}\n{short}')
            btn.setEnabled(not in_flight)
            selected = slot == self.selected_slot
            border = f'1.5px solid {ACCENT}' if selected else '1px solid #d9dfdc'
            bg = '#edf3f1' if selected else '#f8faf9'
            btn.setStyleSheet(f'text-align:left;padding:6px 8px;border-radius:8px;border:{border};background:{bg};font-size:11px;font-weight:{"600" if selected else "400"};color:{INK};')

        slot_lbl, occupied, unknown = self._slot_info()
        needs_overwrite = occupied or (physical and unknown)
        slots_known = bool(slots and any(item.get('status') != 'unknown' for item in slots))
        can_install = self.ctrl['can_install_audio']()

        err_text = self.prepare_error or self.ctrl['get_audio_error']()
        if in_flight:
            msg = self.ctrl['get_flow_message']() or '正在执行声音导入…'
            self.status_box.setStyleSheet('background:#edf3f1;border-radius:8px;')
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f'color:{INK};font-size:12px;')
        elif self._physical_succeeded():
            ex = self.ctrl['get_audio_execute']()
            target_s = ex.get('target_slot', self.selected_slot)
            fname = trimmed or default_camera_target_name(self.sound)
            msg = f'槽位 {target_s} 已写入「{fname}」并通过独立回读验证。'
            if ex.get('restart_notice'):
                msg += '\n' + ex['restart_notice']
            if ex.get('backup_cleanup_failed'):
                msg += f"\n{ex.get('cleanup_warning') or '注意：临时备份槽位清理未确认。'}"
            self.status_box.setStyleSheet('background:#eaf5f0;border-radius:8px;')
            self.status_label.setText(msg)
            self.status_label.setStyleSheet(f'color:{ACCENT};font-size:12px;font-weight:600;')
        elif self.demo_installed_slot is not None and not physical:
            display = trimmed or self.sound.title
            self.status_box.setStyleSheet('background:#eaf5f0;border-radius:8px;')
            self.status_label.setText(f'已演示安装「{display}」到槽位 {self.demo_installed_slot}。')
            self.status_label.setStyleSheet(f'color:{ACCENT};font-size:12px;font-weight:600;')
        elif err_text:
            self.status_box.setStyleSheet('background:#faf4ed;border-radius:8px;')
            self.status_label.setText(err_text)
            self.status_label.setStyleSheet(f'color:{AMBER};font-size:12px;')
        elif not can_install:
            self.status_box.setStyleSheet('background:#faf4ed;border-radius:8px;')
            self.status_label.setText('相机未连接或自定义快门声未解锁，当前无法导入。')
            self.status_label.setStyleSheet(f'color:{AMBER};font-size:12px;')
        elif physical and self.ctrl['audio_slots_busy']() and not slots_known:
            self.status_box.setStyleSheet('background:#f0f2f1;border-radius:8px;')
            self.status_label.setText('正在读取相机 10 个槽位，请稍候…')
            self.status_label.setStyleSheet(f'color:{MUTED};font-size:12px;')
        elif physical and not slots_known:
            self.status_box.setStyleSheet('background:#faf4ed;border-radius:8px;')
            self.status_label.setText(f'尚未读取相机槽位内容，点击下方按钮将直接写入槽位 {self.selected_slot}。')
            self.status_label.setStyleSheet(f'color:{AMBER};font-size:12px;')
        elif occupied:
            self.status_box.setStyleSheet('background:#faf4ed;border-radius:8px;')
            self.status_label.setText(f'槽位 {self.selected_slot} 当前已有「{slot_lbl}」，点击确认将直接覆盖原声音。')
            self.status_label.setStyleSheet(f'color:{AMBER};font-size:12px;')
        else:
            self.status_box.setStyleSheet('background:#edf3f1;border-radius:8px;')
            self.status_label.setText(f'槽位 {self.selected_slot} 当前空闲，可直接导入。')
            self.status_label.setStyleSheet(f'color:{ACCENT};font-size:12px;')

        self.cancel_btn.setVisible(not succeeded)
        self.cancel_btn.setEnabled(not in_flight)
        self.confirm_btn.setVisible(not succeeded)
        self.done_btn.setVisible(succeeded)
        self.confirm_btn.setText('确认覆盖并导入' if needs_overwrite else '确认导入')
        self.confirm_btn.setObjectName('destructive' if needs_overwrite else 'primary')
        self.confirm_btn.setStyle(self.confirm_btn.style())
        valid_name = (not self.can_rename) or (bool(trimmed) and name_err is None)
        self.confirm_btn.setEnabled(can_install and not self.ctrl['is_busy']() and not in_flight and valid_name)

    def _perform_install(self):
        self.session.stop_preview()
        self.demo_installed_slot = None
        self.prepare_error = ''
        if self.ctrl['is_physical']():
            try:
                target_name = validated_camera_target_name(self.name_input.text()) if self.can_rename else None
                self.ctrl['start_import'](self.sound, self.selected_slot, target_name)
                self.refresh()
            except AudioMarketError as exc:
                self.prepare_error = str(exc)
                self.refresh()
        else:
            existing = self.session.slots[self.selected_slot - 1].sound
            expected_id = existing.id if existing else None
            trimmed = self.name_input.text().strip() if self.name_input is not None else ''
            display = trimmed or self.sound.title
            demo_sound = MarketSound(
                id=self.sound.id,
                title=display,
                subtitle=self.sound.subtitle,
                author=self.sound.author,
                duration_ms=self.sound.duration_ms,
                tags=list(self.sound.tags),
                source_kind=self.sound.source_kind,
                file_path=self.sound.file_path,
                package_id=self.sound.package_id,
                sound_id=self.sound.sound_id,
            )
            if self.session.install_demo(demo_sound, self.selected_slot, replacing=expected_id):
                self.demo_installed_slot = self.selected_slot
            self.refresh()

    def done(self, result):
        self.session.stop_preview()
        super().done(result)


class ShutterSoundMarketWidget(QWidget):
    """Main shutter sound market page integrated into Windows Powerful6700 shell."""

    def __init__(self, session: AudioMarketSession, controller, parent=None):
        super().__init__(parent)
        self.session = session
        self.ctrl = controller
        self.install_dialog: ShutterSoundInstallDialog | None = None
        self._tab_buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 14)
        outer.setSpacing(10)

        head = row(outer, 10)
        head.addWidget(label('快门声市场', 'title', False))
        head.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索声音名称或关键词')
        self.search_input.setMaximumWidth(340)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet('QLineEdit { background:white; border:1px solid #d8dedc; border-radius:8px; padding:6px 10px; font-size:12px; }')
        self.search_input.textChanged.connect(self._on_query_changed)
        head.addWidget(self.search_input, 1)
        self.import_btn = button(head, '导入音频', self._on_import_clicked, 'primary')
        self.import_btn.setIcon(symbol_icon('download', 14, 'white', 4))
        self.import_btn.setIconSize(QSize(18, 14))
        self.import_btn.setToolTip('支持 WAV、MP3、M4A、AAC、AIFF、FLAC 和 WMA。超过 350 毫秒时可裁剪后保存。')

        _, self.status_card = panel(outer, padding=12, spacing=6)
        self.status_top = row(self.status_card, 8)
        self.status_reason = label('', 'amber')
        self.status_card.addWidget(self.status_reason)

        tabs = row(outer, 8)
        for cat in ('全部声音', '收藏', '我的音频'):
            b = button(tabs, cat, lambda c=cat: self._select_category(c), 'navTab')
            b.setCheckable(True)
            self._tab_buttons[cat] = b
        tabs.addStretch()
        self.count_label = label('0 个声音', 'small', False)
        tabs.addWidget(self.count_label)
        self.refresh_cat_btn = button(tabs, '刷新目录', self.ctrl['refresh_catalog'], 'compactSecondary')

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 4, 0, 12)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setColumnStretch(0, 1)
        self.grid_layout.setColumnStretch(1, 1)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_scroll.setWidget(self.grid_container)
        outer.addWidget(self.grid_scroll, 1)

        self.banner_label = label('', 'muted')
        outer.addWidget(self.banner_label)

        self.session.changed.connect(self.refresh)
        self.refresh()

    def _on_query_changed(self, text: str):
        self.session.query = text
        self._refresh_cards()

    def _select_category(self, category: str):
        self.session.category = category
        self.refresh()

    def _on_import_clicked(self):
        filt = '音频文件 (*.wav *.mp3 *.m4a *.aac *.aiff *.aif *.flac *.wma);;所有文件 (*.*)'
        path_str, _ = QFileDialog.getOpenFileName(self, '选择快门声音频', '', filt)
        if not path_str:
            return
        self.import_file(Path(path_str))

    def import_file(self, path: Path) -> MarketSound | None:
        try:
            res = self.session.import_local(path)
            if isinstance(res, AudioTrimRequest):
                dlg = AudioTrimDialog(self.session, res, self.window())
                if dlg.exec():
                    return dlg.saved_sound
                return None
            return res
        except AudioMarketError as exc:
            self.session.error = str(exc)
            self.session.message = ''
            self.refresh()
            return None

    def _open_rename(self, sound: MarketSound):
        dlg = RenameAudioDialog(self.session, sound, self.window())
        dlg.exec()

    def _open_delete(self, sound: MarketSound):
        answer = QMessageBox.question(
            self,
            '删除本地音频？',
            f'「{sound.title}」将从「我的音频」移除。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.session.trash_personal_sound(sound)
            except AudioMarketError as exc:
                self.session.error = str(exc)
                self.refresh()

    def _open_install(self, sound: MarketSound):
        self.ctrl['reset_import_flow']()
        dlg = ShutterSoundInstallDialog(self.session, sound, self.ctrl, self.window())
        self.install_dialog = dlg
        try:
            dlg.exec()
        finally:
            self.install_dialog = None

    def notify_install_state(self):
        if self.install_dialog is not None and self.install_dialog.isVisible():
            self.install_dialog.refresh()

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                ShutterSoundMarketWidget._clear_layout(item.layout())

    def _status_badge(self, text: str, fg: str, bg: str) -> QLabel:
        w = label(text, 'small', False)
        w.setStyleSheet(f'font-size:11px;font-weight:600;color:{fg};background:{bg};padding:4px 10px;border-radius:11px;')
        return w

    def _refresh_status_panel(self):
        self._clear_layout(self.status_top)
        physical = self.ctrl['is_physical']()
        connected = self.ctrl['is_camera_connected']()
        sound_state = self.ctrl['electronic_sound_state']()
        custom_state = self.ctrl['custom_sound_state']()
        sound_ok = sound_state == 'enabled'
        custom_ok = custom_state == 'enabled'
        state_map = {'enabled': '已解锁', 'disabled': '未解锁', 'partial': '部分解锁', 'error': '校验失败'}
        custom_map = {'enabled': '已解锁', 'disabled': '未安装', 'partial': '部分改动', 'error': '校验失败'}

        self.status_top.addWidget(self._status_badge('连接状态：已连接' if connected else '连接状态：未连接', ACCENT if connected else MUTED, '#e8f3ef' if connected else '#f0f2f1'))
        if connected:
            self.status_top.addWidget(self._status_badge('电子快门声音：' + state_map.get(sound_state, '未读取'), ACCENT if sound_ok else AMBER, '#e8f3ef' if sound_ok else '#faf4ed'))
            self.status_top.addWidget(self._status_badge('自定义快门声：' + custom_map.get(custom_state, '未读取'), ACCENT if custom_ok else (MUTED if custom_state == 'disabled' else AMBER), '#e8f3ef' if custom_ok else '#faf4ed'))
        else:
            self.status_top.addWidget(label('功能状态：连接后检查 · 未连接时仅支持试听、收藏和本地音频导入', 'small'))
        self.status_top.addStretch()

        if physical and connected:
            disc = button(self.status_top, '断开相机', self.ctrl['disconnect'], 'compactSecondary', not self.ctrl['is_busy']())
            disc.setObjectName('marketDisconnectButton')
        if not connected:
            btn = button(self.status_top, '连接相机' if physical else '演示连接', self.ctrl['on_connect'], 'primary', not self.ctrl['is_busy']())
            btn.setObjectName('marketConnectButton')
        elif not sound_ok or not custom_ok:
            button(self.status_top, '去功能管理', self.ctrl['on_go_features'], 'primary', not self.ctrl['is_busy']())
        else:
            button(self.status_top, '功能管理', self.ctrl['on_go_features'], 'compactSecondary', not self.ctrl['is_busy']())

        cleanup = self.ctrl['camera_disconnect_result']() if physical and not connected else None
        if cleanup:
            warning = cleanup.get('cleanup_warning')
            confirmed = cleanup.get('cleanup_status') == 'msc-observed' and not warning
            self.status_reason.setText('已断开相机 · MSC 已恢复' if confirmed
                                       else warning or '相机断开后尚未确认 MSC 已恢复。')
            self.status_reason.show()
        elif connected and (not sound_ok or not custom_ok):
            if not sound_ok:
                self.status_reason.setText('当前相机尚未解锁「电子快门声音」；请先在「功能管理」解锁电子快门声音。')
            else:
                self.status_reason.setText(self.ctrl['custom_sound_reason']() or '当前相机未检测到十槽自定义快门声固件，暂不可写入声音槽位。')
            self.status_reason.show()
        else:
            self.status_reason.hide()

    def _refresh_cards(self):
        self._clear_layout(self.grid_layout)
        sounds = self.session.filtered_sounds()
        self.count_label.setText(f'{len(sounds)} 个声音')
        can_install = self.ctrl['can_install_audio']() and not self.ctrl['is_busy']()
        install_tip = self.ctrl['install_disabled_reason']()

        if not sounds:
            empty = QWidget()
            v = QVBoxLayout(empty)
            v.setContentsMargins(20, 48, 20, 48)
            v.setSpacing(8)
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(label('没有找到匹配的声音' if self.session.query.strip() else ('还没有收藏' if self.session.category == '收藏' else '这里还没有音频'), 'heading'))
            v.addWidget(label('试试其他名称，或清空搜索。' if self.session.query.strip() else '支持导入 WAV、MP3、M4A 等音频，或到全部声音中试听。', 'muted'))
            self.grid_layout.addWidget(empty, 0, 0, 1, 2)
            return

        for idx, sound in enumerate(sounds):
            card = QFrame()
            card.setObjectName('soundCard')
            selected = self.session.selected_sound_id == sound.id
            card.setStyleSheet(f'QFrame#soundCard {{ background:white; border:1px solid {ACCENT if selected else "#e6e9e8"}; border-radius:10px; }}')
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 10, 12, 10)
            cv.setSpacing(8)

            top = row(cv, 9)
            active = self.session.playing_id == sound.id or self.session.preparing_id == sound.id
            play_btn = button(top, '', lambda s=sound: self.ctrl['preview_sound'](s), 'compactPrimary' if active else 'compactSecondary')
            play_btn.setFixedSize(34, 34)
            play_btn.setIcon(symbol_icon('stop_fill' if active else 'play_fill', 13, 'white' if active else ACCENT))
            play_btn.setIconSize(QSize(13, 13))
            play_btn.setToolTip('停止试听' if active else '试听')

            info = column(top, 2, 1)
            tl = label(sound.title, 'body', False)
            tl.setStyleSheet('font-size:13px;font-weight:600;')
            info.addWidget(tl)
            info.addWidget(label(f'{sound.duration_ms} 毫秒', 'small', False))

            if sound.personal:
                ren = button(top, '', lambda s=sound: self._open_rename(s), 'iconButton')
                ren.setFixedSize(28, 28)
                ren.setIcon(symbol_icon('pencil', 13, MUTED))
                ren.setIconSize(QSize(13, 13))
                ren.setToolTip('重命名')
                tr = button(top, '', lambda s=sound: self._open_delete(s), 'iconButton')
                tr.setFixedSize(28, 28)
                tr.setIcon(symbol_icon('trash', 13, MUTED))
                tr.setIconSize(QSize(13, 13))
                tr.setToolTip('删除本地音频')

            fav = sound.id in self.session.favorites
            fav_btn = button(top, '', lambda s=sound: self.session.toggle_favorite(s), 'iconButton')
            fav_btn.setFixedSize(28, 28)
            fav_btn.setIcon(symbol_icon('bookmark.fill' if fav else 'bookmark', 14, ACCENT if fav else MUTED))
            fav_btn.setIconSize(QSize(14, 14))
            fav_btn.setToolTip('取消收藏' if fav else '收藏')

            bot = row(cv, 6)
            summary = sound.subtitle or ' · '.join(sound.tags)
            sum_lbl = label(summary, 'small', False)
            bot.addWidget(sum_lbl, 1)
            inst_btn = button(bot, '安装', lambda s=sound: self._open_install(s), 'compactSecondary', can_install)
            inst_btn.setToolTip(install_tip)

            self.grid_layout.addWidget(card, idx // 2, idx % 2)

    def refresh(self):
        for cat, btn in self._tab_buttons.items():
            btn.setChecked(self.session.category == cat)
        self.refresh_cat_btn.setEnabled(not self.session.loading_catalog)
        self._refresh_status_panel()
        self._refresh_cards()
        if self.session.error:
            self.banner_label.setText(self.session.error)
            self.banner_label.setStyleSheet(f'color:{AMBER};font-size:12px;')
            self.banner_label.show()
        elif self.session.message:
            self.banner_label.setText(self.session.message)
            self.banner_label.setStyleSheet(f'color:{ACCENT};font-size:12px;')
            self.banner_label.show()
        else:
            self.banner_label.hide()
