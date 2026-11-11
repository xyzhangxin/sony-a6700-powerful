"""Windows feedback sheet aligned with the macOS DiagnosticsView."""
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from design import (ACCENT, AMBER, BACKGROUND, INK, MUTED, ActivityIndicator, Symbol,
                    SymbolInk, button, label, line, panel, symbol_icon)


IMAGE_SUFFIXES = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif', '.tif', '.tiff', '.gif',
    '.bmp', '.webp', '.arw', '.dng', '.cr2', '.cr3', '.nef', '.raf', '.orf',
    '.rw2',
}
MAX_IMAGES = 12
MAX_IMAGE_BYTES = 200 * 1024 * 1024
GUIDED_ACTIVE = {
    'starting', 'restarting', 'cancelling', 'waiting_msc', 'unplug_required',
    'waiting_user', 'disconnected', 'reconnected', 'collecting',
    'awaiting_retrieval', 'timeout',
}
ACTIVELY_RECORDING = {
    'starting', 'waiting_msc', 'unplug_required', 'waiting_user',
    'disconnected', 'reconnected',
}


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            widget = item.widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear_layout(item.layout())


def _format_size(size):
    if size >= 1024 * 1024:
        return f'{size / 1024 / 1024:.1f} MB'
    if size >= 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size} B'


class ImageTile(QWidget):
    """88×88 thumbnail matching the macOS feedback grid tile."""

    def __init__(self, path, remove_callback, parent=None):
        super().__init__(parent)
        self.path = path
        self.remove_callback = remove_callback
        self.setFixedSize(88, 88)
        self.setToolTip(path.name)
        self.setAccessibleName(f'图片 {path.name}')
        # macOS centres the 66pt preview + 4pt gap + 9pt caption inside the
        # 88pt frame, which leaves 3.5pt above the preview.
        self._preview = QRectF(0, 3.5, 88, 66)
        self._pixmap = QPixmap(str(path))

    def _size_text(self):
        try:
            return _format_size(self.path.stat().st_size)
        except OSError:
            return ''

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        preview = self._preview
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#eef1f0'))
        painter.drawRoundedRect(preview, 8, 8)
        if self._pixmap.isNull():
            font = QFont()
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            painter.setPen(QColor(MUTED))
            painter.drawText(preview, Qt.AlignmentFlag.AlignCenter,
                             self.path.suffix.upper()[1:] or 'IMG')
        else:
            scaled = self._pixmap.scaled(
                88, 66, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            clip = QPainterPath()
            clip.addRoundedRect(preview, 8, 8)
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(int((88 - scaled.width()) / 2),
                               int(preview.top() + (66 - scaled.height()) / 2),
                               scaled)
            painter.restore()
        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(0, 73.5, 88, 14),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self._size_text())
        painter.end()


class RemoveBadge(QWidget):
    """19pt xmark.circle.fill straddling the tile's top-right corner.

    macOS offsets the 16pt symbol by (5,-5) from the tile's top-trailing
    corner, so the disc is centred on the corner and extends above it. Qt
    clips a child to its parent, so the badge lives on the scroll content
    instead of inside the 88x88 tile.
    """

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setFixedSize(19, 19)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('移除图片')
        self.setToolTip('移除图片')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawEllipse(QRectF(0, 0, 19, 19))
        painter.setBrush(QColor('white'))
        painter.drawEllipse(QRectF(1, 1, 17, 17))
        painter.setPen(QPen(QColor('#8a908e'), 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(6.2, 6.2), QPointF(12.8, 12.8))
        painter.drawLine(QPointF(12.8, 6.2), QPointF(6.2, 12.8))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.callback()
            return
        super().mousePressEvent(event)


class AddImageTile(QWidget):
    """88×88 dashed tile: a 22pt plus over the label, as in the macOS VStack.

    QToolButton pins the icon to the top of the button and the text to the
    bottom, which pushed the plus 21pt above the macOS position, so the tile
    paints itself instead.
    """

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setFixedSize(88, 88)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('添加图片')
        self.setToolTip('添加图片')
        self._hover = False

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.callback()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QPen(QColor(20, 94, 79, 115), 1, Qt.PenStyle.CustomDashLine)
        border.setDashPattern([5, 4])
        painter.setPen(border)
        painter.setBrush(QColor(20, 94, 79, 22 if self._hover else 9))
        painter.drawRoundedRect(QRectF(0.5, 0.5, 87, 87), 9, 9)
        painter.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        # SF's 22pt light plus renders as an 18pt cross centred on the tile.
        painter.drawLine(QPointF(35.5, 35.0), QPointF(52.5, 35.0))
        painter.drawLine(QPointF(44.0, 26.5), QPointF(44.0, 43.5))
        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(ACCENT))
        painter.drawText(QRectF(0, 52, 88, 14),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         '添加图片')
        painter.end()


class FeedbackDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('意见反馈')
        self.setModal(False)
        self.setFixedSize(640, 680)
        self.setAccessibleName('意见反馈')

        self.state = {}
        self.images = []
        self.guided_state = None
        self.diagnostic_result = None
        self.upload_result = None
        self.upload_pending = False
        self.progress_lines = []
        self._badges = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._build_header(outer)
        line(outer)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea{border:0;}')
        outer.addWidget(scroll, 1)
        content = QWidget()
        self.content = content
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        # The image block grew by the hint's 2px leading correction; the
        # description block gives those two pixels back at its bottom so the
        # log card keeps the macOS y=410 edge.
        layout.setSpacing(12)
        scroll.setWidget(content)

        self._build_description(layout)
        self._build_images(layout)
        self._build_log(layout)
        layout.addStretch()

        line(outer)
        self._build_footer(outer)
        self.refresh()

    def _build_header(self, outer):
        header = QWidget()
        header.setObjectName('header')
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        outer.addWidget(header)

        # macOS draws the 24pt SF Symbol as a 34x27 image inside a ~40pt slot;
        # the front bubble is opaque so it hides the back one.
        layout.addWidget(SymbolInk('bubble', (32, 25), (40, 31), ACCENT,
                                   BACKGROUND), 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title = label('意见反馈', 'heading', False)
        title.setStyleSheet('font-size:20px;font-weight:700;')
        titles.addWidget(title)
        titles.addWidget(label('填写问题，按需录制错误日志并附图，最后统一上传。',
                               'muted', False))
        layout.addLayout(titles, 1)
        close_button = button(layout, '关闭', self.hide, 'quiet')
        close_button.setStyleSheet('font-size:13px;padding:0;')
        close_button.setAccessibleName('关闭意见反馈')
        layout.setAlignment(close_button, Qt.AlignmentFlag.AlignTop)

    def _build_description(self, layout):
        box = QVBoxLayout()
        box.setSpacing(6)
        box.setContentsMargins(0, 0, 0, 2)
        layout.addLayout(box)
        heading = label('问题描述', 'heading', False)
        heading.setStyleSheet('font-size:12px;font-weight:600;')
        box.addWidget(heading)
        self.description = QPlainTextEdit()
        # macOS uses TextEditor.frame(height: 86) plus 7pt padding on each side.
        self.description.setFixedHeight(100)
        # Qt adds a 4px document margin on top of the stylesheet padding; macOS
        # has only the 7pt padding, so drop it to keep the first line in place.
        self.description.document().setDocumentMargin(0)
        self.description.setStyleSheet(
            'QPlainTextEdit{background:white;border:1px solid #e6e9e8;'
            'border-radius:8px;padding:7px;font-size:12px;}')
        self.description.textChanged.connect(self.refresh)
        box.addWidget(self.description)
        box.addWidget(label('请写明现象、操作步骤和期望结果。', 'small', False))

    def _build_images(self, layout):
        box = QVBoxLayout()
        # macOS stacks these with 8pt; Qt labels carry extra leading, so the
        # spacing is trimmed to keep the grid on the same baseline.
        box.setSpacing(6)
        layout.addLayout(box)
        header = QHBoxLayout()
        # SF's Label puts the 16pt symbol and the text 11pt apart, and the
        # count another 8pt after the text; Qt's uniform 6px spacing put the
        # heading five pixels left of the macOS row.
        header.setSpacing(0)
        header.addSpacing(1)
        header.addWidget(SymbolInk('photo', (16, 13), (16, 15), INK, BACKGROUND))
        header.addSpacing(10)
        heading = label('可选图片', 'heading', False)
        heading.setStyleSheet('font-size:12px;font-weight:600;')
        header.addWidget(heading)
        header.addSpacing(8)
        self.image_count = label('', 'small', False)
        header.addWidget(self.image_count)
        header.addStretch()
        box.addLayout(header)
        hint = label('点击上传框添加图片，单张不超过 200MiB。', 'small', False)
        # Qt's 10px label carries two more pixels of leading than SF's 10pt
        # text; the extra top margin keeps the baseline on the macOS row.
        hint.setContentsMargins(0, 2, 0, 0)
        box.addWidget(hint)

        self.image_grid = QGridLayout()
        # macOS uses adaptive 88pt columns: at 600pt wide that is a 101.7pt
        # pitch with each tile centred, i.e. a 2pt inset and 14pt gaps.
        # The macOS grid sits 3pt lower than the hint text above it.
        self.image_grid.setContentsMargins(2, 3, 0, 0)
        self.image_grid.setHorizontalSpacing(14)
        self.image_grid.setVerticalSpacing(10)
        self.image_grid.setColumnStretch(6, 1)
        box.addLayout(self.image_grid)
        self.image_error = label('', 'amber')
        self.image_error.hide()
        box.addWidget(self.image_error)

    def _build_log(self, layout):
        _, box = panel(layout, padding=20, spacing=8)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.log_icon = Symbol('record', 25, '#ff3b30')
        # SF's record.circle carries a 2pt top and 1pt side bearing inside its
        # 25pt box; the owned vector fills the box, so a fixed slot restores
        # that bearing and keeps the text column at the macOS x=81.
        icon_slot = QWidget()
        icon_slot.setFixedSize(28, 27)
        self.log_icon.setParent(icon_slot)
        self.log_icon.move(1, 2)
        header.addWidget(icon_slot, 0, Qt.AlignmentFlag.AlignTop)
        content = QVBoxLayout()
        content.setSpacing(8)
        header.addLayout(content, 1)
        box.addLayout(header)

        self.log_title = label('', 'heading', False)
        self.log_title.setStyleSheet('font-size:14px;font-weight:600;')
        content.addWidget(self.log_title)
        self.log_message = label('', 'muted')
        content.addWidget(self.log_message)

        self.progress_container = QVBoxLayout()
        self.progress_container.setSpacing(5)
        content.addLayout(self.progress_container)
        self.log_actions = QHBoxLayout()
        self.log_actions.setSpacing(10)
        self.log_actions.setContentsMargins(0, 0, 0, 0)
        content.addLayout(self.log_actions)

    def _build_footer(self, outer):
        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(9)
        outer.addWidget(footer)

        self.result_container = QVBoxLayout()
        self.result_container.setSpacing(5)
        layout.addLayout(self.result_container)

        self.upload_button = QPushButton()
        self.upload_button.setObjectName('primary')
        self.upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # macOS frames the label at 24pt inside 11pt vertical padding.
        self.upload_button.setFixedHeight(46)
        self.upload_button.setStyleSheet('padding:0 18px;')
        self.upload_button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Fixed)
        self.upload_button.setIcon(symbol_icon('paperplane.fill', 16, '#ffffff', 7))
        self.upload_button.setIconSize(QSize(23, 16))
        self.upload_button.clicked.connect(self.submit_feedback)
        layout.addWidget(self.upload_button)
        self.upload_hint = label('', 'small', False)
        layout.addWidget(self.upload_hint)

    @property
    def physical(self):
        return self.window.physical

    @property
    def busy(self):
        return bool(self.window.state.get('busy') or self.window.inflight)

    @property
    def service_ready(self):
        return self.window.client.alive()

    @property
    def recording_ready(self):
        return (self.physical and self.service_ready
                and self.window.state.get('compatible') is True
                and self.window.state.get('connected') is True)

    @property
    def guided_active(self):
        return (self.guided_state or {}).get('status', '') in GUIDED_ACTIVE

    @property
    def awaiting_retrieval(self):
        return (self.guided_state or {}).get('status') == 'awaiting_retrieval'

    @property
    def actively_recording(self):
        return (self.guided_state or {}).get('status', '') in ACTIVELY_RECORDING

    @property
    def diagnostic_collected(self):
        return bool((self.diagnostic_result or {}).get('path'))

    def handle_state(self, state):
        self.state = state or {}
        self.refresh()

    def handle_event(self, event):
        kind = event.get('type')
        if kind == 'guided-state':
            self.guided_state = event
            self.refresh()
        elif kind == 'diagnostic-progress':
            self.progress_lines.append(event.get('message', '正在等待结果…'))
            self.progress_lines = self.progress_lines[-8:]
            self.refresh()
        elif kind == 'diagnostic-result':
            self.diagnostic_result = event.get('result', {})
            self.refresh()
        elif kind == 'feedback-result':
            self.upload_result = event.get('result', {})
            self.upload_pending = False
            self.refresh()

    def choose_images(self):
        filters = ('图片 (*.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff *.gif '
                   '*.bmp *.webp *.arw *.dng *.cr2 *.cr3 *.nef *.raf *.orf '
                   '*.rw2);;所有文件 (*)')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要上传的图片', '', filters)
        if not paths:
            return
        error = ''
        for raw in paths:
            if len(self.images) >= MAX_IMAGES:
                error = '一次最多上传 12 张图片。'
                break
            path = Path(raw)
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                error = f'{path.name} 不是支持的图片格式。'
                continue
            if any(existing == path for existing in self.images):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                error = f'无法读取图片大小：{path.name}'
                continue
            if size > MAX_IMAGE_BYTES:
                error = f'{path.name} 超过 200MiB，未加入。'
                continue
            self.images.append(path)
        self.image_error.setText(error)
        self.image_error.setVisible(bool(error))
        self.refresh()

    def remove_image(self, path):
        if path in self.images:
            self.images.remove(path)
            self.refresh()

    def submit_feedback(self):
        description = self.description.toPlainText().strip()
        if not description:
            QMessageBox.information(self, '暂时无法继续', '请先填写问题描述。')
            return
        if not self.service_ready:
            QMessageBox.information(self, '暂时无法继续',
                                    '本机服务未运行，请先重新启动服务。')
            return
        if self.guided_active:
            QMessageBox.information(self, '暂时无法继续',
                                    '请先结束录制并取回日志，再统一上传。')
            return
        self.upload_result = None
        self.upload_pending = True
        self.window.command('upload_feedback', description=description,
                            images=[str(path) for path in self.images])
        self.refresh()

    def refresh(self):
        self._refresh_images()
        self._refresh_log()
        self._refresh_footer()

    def _refresh_images(self):
        _clear_layout(self.image_grid)
        column = 0
        row = 0
        for path in self.images:
            self.image_grid.addWidget(ImageTile(path, self.remove_image, self),
                                      row, column)
            column += 1
            if column == 6:
                column = 0
                row += 1
        if len(self.images) < MAX_IMAGES:
            for _ in range(3 if not self.images else 1):
                self.image_grid.addWidget(AddImageTile(self.choose_images, self),
                                          row, column)
                column += 1
                if column == 6:
                    column = 0
                    row += 1
        self.image_count.setText(
            f'{len(self.images)}/{MAX_IMAGES}' if self.images else '')
        QTimer.singleShot(0, self._sync_badges)

    def _sync_badges(self):
        for badge in self._badges:
            badge.hide()
            badge.deleteLater()
        self._badges = []
        for index in range(self.image_grid.count()):
            tile = self.image_grid.itemAt(index).widget()
            if not isinstance(tile, ImageTile):
                continue
            badge = RemoveBadge(lambda path=tile.path: self.remove_image(path),
                                self.content)
            origin = tile.mapTo(self.content, QPoint(0, 0))
            badge.move(origin.x() + 74, origin.y() - 5)
            badge.show()
            badge.raise_()
            self._badges.append(badge)

    def _refresh_log(self):
        status = (self.guided_state or {}).get('status', '')
        error = (self.guided_state or {}).get('error', '')
        diagnostic = self.diagnostic_result or {}

        if self.guided_active:
            title = '相机内有未取回日志'
            if self.actively_recording:
                message = '正在持续记录。可直接操作相机；完成后点击“结束录制并上传”。'
            else:
                message = '记录已保存在相机。可取回并上传；重新录制会先清空这份日志。'
        elif self.diagnostic_collected:
            title = '诊断报告已生成'
            message = '日志已生成。填写问题和图片后，使用底部按钮统一上传。'
        else:
            title = '错误日志采集'
            message = ('开始后可直接操作相机；记录会持续写入并同步保存。'
                       if self.recording_ready else
                       '请先连接并检查相机，确认采集服务正常后再录制。')
        if error and not self.guided_active:
            message = error
        elif diagnostic.get('error') and not self.diagnostic_collected:
            message = str(diagnostic['error'])

        self.log_title.setText(title)
        self.log_message.setText(message)
        self.log_icon.kind = 'clock' if self.guided_active else 'record'
        self.log_icon.color = AMBER if self.guided_active else '#ff3b30'
        self.log_icon.update()

        _clear_layout(self.progress_container)
        _clear_layout(self.log_actions)
        if self.guided_active:
            progress_title = {
                'starting': '正在启动错误日志采集',
                'restarting': '正在重新采集',
                'cancelling': '正在取消操作',
                'collecting': '正在结束录制并取回日志',
            }.get(status, '正在与相机通信')
            progress_detail = {
                'cancelling': '已收到取消请求。正在等待当前 USB 请求安全返回，不会强制断开相机。',
                'restarting': '正在连接相机、取回旧日志并启动新记录；各阶段进度会显示在下方。',
                'collecting': '正在停止机内记录、读取日志和整理诊断报告，请保持 USB 连接。',
            }.get(status, '正在与相机通信，请保持连接。')
            header = QHBoxLayout()
            header.setSpacing(10)
            indicator = ActivityIndicator()
            indicator.setFixedSize(16, 16)
            header.addWidget(indicator)
            progress_heading = label(progress_title, 'accent', False)
            progress_heading.setStyleSheet('font-size:13px;font-weight:600;')
            header.addWidget(progress_heading)
            self.progress_container.addLayout(header)
            self.progress_container.addWidget(label(progress_detail, 'muted'))
            for message in self.progress_lines[-5:]:
                self.progress_container.addWidget(label(message, 'muted'))
            if status in {'starting', 'restarting'}:
                button(self.log_actions, '取消',
                       lambda: self.window.command(
                           'guided_diagnostic', action='cancel'),
                       'secondary', not self.busy)
            elif status == 'cancelling':
                self.log_actions.addWidget(
                    label('取消请求已发送，正在安全收尾…', 'amber'))
        else:
            if not self.diagnostic_collected:
                button(self.log_actions, '开始录制',
                       lambda: self.window.command(
                           'guided_diagnostic', action='start'),
                       'primary', self.recording_ready and not self.busy)
            else:
                ready = label('日志已就绪', 'accent', False)
                self.log_actions.addWidget(ready)
                button(self.log_actions, '重新录制',
                       lambda: self.window.command(
                           'guided_diagnostic', action='restart'),
                       'secondary', self.recording_ready and not self.busy)
            if self.awaiting_retrieval:
                button(self.log_actions, '取回日志',
                       lambda: self.window.command(
                           'guided_diagnostic', action='collect'),
                       'primary', not self.busy)
                button(self.log_actions, '重新录制（清空旧日志）',
                       lambda: self.window.command(
                           'guided_diagnostic', action='restart'),
                       'secondary', not self.busy)
        for index in range(self.log_actions.count()):
            widget = self.log_actions.itemAt(index).widget()
            if isinstance(widget, QPushButton):
                # macOS PrimaryStyle is 13pt + 11pt vertical padding = 38pt.
                widget.setFixedHeight(38)
        self.log_actions.addStretch()

    def _refresh_footer(self):
        _clear_layout(self.result_container)
        result = self.upload_result or {}
        if result:
            status = result.get('status', 'error')
            if status == 'received':
                title, color = '反馈和附件已上传', ACCENT
            elif status == 'partial':
                title, color = '反馈已上传，部分附件未接收', AMBER
            else:
                title, color = '上传未完成', AMBER
            title_label = label(title, 'accent' if color == ACCENT else 'amber', False)
            title_label.setStyleSheet(f'font-size:12px;font-weight:600;color:{color};')
            self.result_container.addWidget(title_label)
            if result.get('feedback_id'):
                self.result_container.addWidget(
                    label('feedback_id：' + str(result['feedback_id']),
                          'muted', False))
            if result.get('error'):
                self.result_container.addWidget(label(str(result['error']), 'muted'))
            elif status != 'received':
                self.result_container.addWidget(
                    label('报告仍保留在本机，可稍后重试；不需要重新录制。', 'muted'))

        description_ready = bool(self.description.toPlainText().strip())
        self.upload_button.setEnabled(
            self.service_ready and not self.busy and description_ready
            and not self.guided_active)
        self.upload_button.setText(
            '正在上传意见反馈…' if self.upload_pending else '上传意见反馈')
        if self.guided_active:
            hint = '请先结束录制并取回日志，再统一上传。'
        elif self.diagnostic_collected:
            hint = '将提交问题描述、日志 ZIP 和所选图片。'
        else:
            hint = '不录制日志也可以上传；将提交问题描述和所选图片。'
        self.upload_hint.setText(hint)
