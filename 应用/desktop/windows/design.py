"""Qt presentation tokens matching macOS DesignSystem.swift; no device logic."""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QPushButton, QHBoxLayout, QVBoxLayout, QSizePolicy

INK='#1f2b2b'
ACCENT='#145e4f'
MUTED='#6e787a'
BACKGROUND='#f5f7f7'
AMBER='#a65c1a'
LINE='#e6e9e8'
STYLE='''
QWidget { color:#1f2b2b; font-size:12px; background:transparent; }
QMainWindow, QWidget#shell, QScrollArea, QWidget#page { background:#f5f7f7; }
QFrame#header, QFrame#steps, QFrame#footer { background:white; }
QFrame#panel, QFrame#featureList, QFrame#actionPanel { background:white; border:1px solid #e6e9e8; border-radius:12px; }
QFrame#tint { background:#edf3f1; border:0; border-radius:10px; }
QFrame#warning { background:#faf4ed; border:0; border-radius:10px; }
QFrame#separator { background:#e6e9e8; min-height:1px; max-height:1px; border:0; }
QLabel#title { font-size:23px; font-weight:700; }
QLabel#heading { font-size:14px; font-weight:600; }
QLabel#body { font-size:12px; }
QLabel#muted { color:#6e787a; font-size:11px; }
QLabel#small { color:#6e787a; font-size:10px; }
QLabel#accent { color:#145e4f; }
QLabel#amber { color:#a65c1a; }
QLabel#pill { color:#145e4f; background:#edf3f1; padding:6px 10px; border-radius:13px; font-size:11px; font-weight:600; }
QPushButton { background:#f0f2f1; border:0; border-radius:8px; padding:10px 18px; font-size:13px; font-weight:600; }
QPushButton:hover { background:#e4e9e7; }
QPushButton:pressed { background:#d9e2de; }
QPushButton#primary { background:#145e4f; color:white; }
QPushButton#primary:hover { background:#1e6b5b; }
QPushButton#destructive { background:#ad3833; color:white; }
QPushButton#destructive:hover { background:#972e29; }
QPushButton:disabled { color:#aab1ae; background:#edf0ee; }
QPushButton#primary:disabled { color:#f8faf9; background:#a9c2bb; }
QPushButton#destructive:disabled { color:#fbf7f6; background:#ddb5b2; }
QPushButton#link, QPushButton#quiet { background:transparent; color:#145e4f; font-size:11px; font-weight:400; padding:3px 0; border-radius:3px; }
QPushButton#quiet { color:#6e787a; }
QPushButton#link:hover, QPushButton#quiet:hover { color:#0b4538; background:#edf3f1; }
QPushButton#link:disabled, QPushButton#quiet:disabled { color:#b1b8b5; }
QFrame#segments { background:#eceeed; border-radius:7px; }
QPushButton#segment { background:transparent; font-size:12px; padding:4px 12px; border-radius:5px; font-weight:400; }
QPushButton#segment:checked { background:#d5d8d6; font-weight:600; }
QPushButton#segment:disabled { color:#aab1ae; }
QPushButton#iconButton { padding:2px; background:transparent; border-radius:5px; }
QPushButton#iconButton:hover { background:#edf3f1; }
QCheckBox { spacing:9px; font-size:12px; }
QCheckBox:disabled { color:#a0a9a4; }
QCheckBox::indicator { width:15px; height:15px; border:1px solid #cbd1cd; border-radius:4px; background:#edf0ee; }
QCheckBox::indicator:checked { background:#145e4f; border:1px solid #145e4f; image:none; }
QCheckBox::indicator:disabled { background:#f0f2f1; border:1px solid #e6e9e8; }
QScrollArea { border:0; }
QScrollBar:vertical { background:transparent; width:7px; margin:2px 0; }
QScrollBar::handle:vertical { background:#c9d0cc; min-height:30px; border-radius:3px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:transparent; }
QMenu { background:white; border:1px solid #e1e6e3; padding:6px; }
QMenu::item { padding:8px 18px; border-radius:5px; }
QMenu::item:selected { background:#edf3f1; }
QDialog { background:#f5f7f7; }
'''

# Presentation metadata mirrors the Mac feature title / explanation / icon map.
FEATURES={
 'ftp':('FTP 传输','解锁原生 FTP 菜单与传输设置','transfer'),
 'shutter':('关机快门帘','关机闭帘与静音模式联动','aperture'),
 'exposure':('灵活曝光','视频快门、光圈、ISO 独立控制','sliders'),
 'sound':('电子快门声音','解锁电子快门声音与音量选项','sound'),
 'fl23':('FL2／FL3 创意外观','增加 FL2、FL3；关闭时完整卸载其专属依赖','layers'),
 'creative_look_names':('创意外观中文名称','主列表和编号 1～6 显示完整名称，可独立启用','layers'),
 'pixel_shift':('位移多帧合成','开发与验证尚未完成，暂不开放','layers'),
 'dynamic_active':('动态增强防抖','开发与验证尚未完成，暂不开放','viewfinder'),
}

def label(text, role='body', wrap=True):
    w=QLabel(str(text)); w.setObjectName(role); w.setWordWrap(wrap)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
    return w


def button(layout,text,callback,role='secondary',enabled=True):
    w=QPushButton(text); w.setObjectName(role); w.setEnabled(enabled)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.clicked.connect(lambda _=False: callback()); layout.addWidget(w)
    if role in ('link','quiet') and isinstance(layout,QVBoxLayout):
        layout.setAlignment(w,Qt.AlignmentFlag.AlignLeft)
    return w


def panel(layout,kind='panel',padding=20,spacing=10):
    frame=QFrame(); frame.setObjectName(kind)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
    box=QVBoxLayout(frame); box.setContentsMargins(padding,padding,padding,padding); box.setSpacing(spacing);box.setAlignment(Qt.AlignmentFlag.AlignTop)
    if layout is not None: layout.addWidget(frame)
    return frame,box


def line(layout):
    w=QFrame(); w.setObjectName('separator'); layout.addWidget(w); return w


def row(layout,spacing=10):
    box=QHBoxLayout(); box.setSpacing(spacing); layout.addLayout(box); return box


def column(layout,spacing=8,stretch=0):
    box=QVBoxLayout(); box.setSpacing(spacing);box.setAlignment(Qt.AlignmentFlag.AlignTop); layout.addLayout(box,stretch); return box


def pill(text): return label('✓  '+text,'pill',False)


class Segments(QFrame):
    def __init__(self,items,selected,callback,parent=None):
        super().__init__(parent); self.setObjectName('segments');self.setFixedHeight(30)
        self.buttons={}; box=QHBoxLayout(self);box.setContentsMargins(2,2,2,2);box.setSpacing(0)
        for value,title in items:
            b=button(box,title,lambda v=value:callback(v),'segment')
            b.setCheckable(True);self.buttons[value]=b
        self.select(selected)
    def select(self,value):
        for v,b in self.buttons.items(): b.setChecked(v==value)


class Symbol(QWidget):
    """Small owned line icons, drawn as vectors; no platform glyph/font dependency."""
    def __init__(self,kind,size=20,color=ACCENT,tile=False):
        super().__init__();self.kind=kind;self.color=color;self.tile=tile;self.setFixedSize(size,size)
        self.setAccessibleName(kind)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.tile:
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#f1f5f3'));p.drawRoundedRect(QRectF(self.rect()),8,8)
        margin=7 if self.tile else 1
        p.translate(margin,margin);n=self.width()-2*margin;p.scale(n/24,n/24)
        pen=QPen(QColor(self.color),1.65,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen);p.setBrush(Qt.BrushStyle.NoBrush)
        def ln(a,b,c,d):p.drawLine(QPointF(a,b),QPointF(c,d))
        def path(points,closed=False):
            q=QPainterPath(QPointF(*points[0]))
            for xy in points[1:]:q.lineTo(*xy)
            if closed:q.closeSubpath()
            p.drawPath(q)
        k=self.kind
        if k in ('check','success'):
            p.drawEllipse(QRectF(2,2,20,20));path([(7,12),(10.5,15.5),(17,8.5)])
        elif k=='transfer':
            path([(2,7),(7,2),(12,7)]);ln(7,2,7,22);path([(12,17),(17,22),(22,17)]);ln(17,2,17,22)
        elif k=='sliders':
            for y,x in [(5,8),(12,16),(19,9)]:
                ln(2,y,x-2.5,y);ln(x+2.5,y,22,y);p.drawRoundedRect(QRectF(x-2.5,y-2,5,4),1,1)
        elif k=='aperture':
            import math
            p.drawEllipse(QRectF(2,2,20,20))
            points=[(12+5*math.cos(i*math.pi/3),12+5*math.sin(i*math.pi/3)) for i in range(6)]
            path(points,True)
            for i,xy in enumerate(points):
                theta=(i+1)*math.pi/3;ln(*xy,12+9.5*math.cos(theta),12+9.5*math.sin(theta))
        elif k=='sound':
            path([(3,9),(7,9),(12,5),(12,19),(7,15),(3,15)],True)
            p.drawArc(QRectF(11,6,8,12),-65*16,130*16);p.drawArc(QRectF(10,2,14,20),-58*16,116*16)
        elif k=='layers':
            path([(2,8),(12,2),(22,8),(12,14)],True);path([(2,12),(12,18),(22,12)]);path([(2,16),(12,22),(22,16)])
        elif k=='viewfinder':
            for pts in [[(2,8),(2,2),(8,2)],[(16,2),(22,2),(22,8)],[(2,16),(2,22),(8,22)],[(16,22),(22,22),(22,16)]]:path(pts)
        elif k in ('power','battery'):
            if k=='power':
                p.drawArc(QRectF(3,3,18,18),130*16,280*16);ln(12,1,12,12)
            else:
                p.drawRoundedRect(QRectF(2,6,18,12),2,2);ln(22,10,22,14);ln(5,9,5,15);ln(9,9,9,15)
        elif k in ('warning','info'):
            if k=='warning':path([(12,2),(23,21),(1,21)],True)
            else:p.drawEllipse(QRectF(2,2,20,20))
            ln(12,8,12,13.5);p.drawPoint(QPointF(12,17))
        elif k=='camera':
            path([(2,7),(7,7),(9,4),(15,4),(17,7),(22,7),(22,20),(2,20)],True);p.drawEllipse(QRectF(8,9,8,8))
        elif k=='cable':
            p.drawRoundedRect(QRectF(8,5,8,9),2,2);ln(10,1,10,5);ln(14,1,14,5);ln(12,14,12,23)
        elif k=='play':
            p.drawEllipse(QRectF(2,2,20,20));path([(10,7),(17,12),(10,17)],True)
        elif k=='more':
            p.drawEllipse(QRectF(2,2,20,20))
            for x in (7,12,17):p.drawPoint(QPointF(x,12))
        else:
            p.drawRoundedRect(QRectF(3,3,18,18),3,3);ln(7,8,17,8);ln(7,12,17,12);ln(7,16,14,16)
        p.end()

from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton

class CheckBox(QCheckBox):
    def paintEvent(self,event):
        super().paintEvent(event)
        if self.isChecked():
            opt=QStyleOptionButton();self.initStyleOption(opt)
            r=self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator,opt,self)
            p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor('white'),1.6,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            q=QPainterPath(QPointF(r.x()+3.5,r.y()+8));q.lineTo(r.x()+6.5,r.y()+11);q.lineTo(r.x()+12,r.y()+4.5);p.drawPath(q)

from PySide6.QtCore import QTimer

class ActivityIndicator(QWidget):
    """Mac-style indeterminate activity; never implies a made-up percentage."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setFixedSize(28,28)
        self.setAccessibleName('正在处理')
        self.frame=0
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.advance)
    def advance(self):
        self.frame=(self.frame+1)%12;self.update()
    def showEvent(self,event):
        super().showEvent(event);self.timer.start()
    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width()/2,self.height()/2)
        for i in range(12):
            c=QColor(ACCENT);c.setAlphaF(.16+.84*((i-self.frame)%12)/11)
            p.setPen(QPen(c,2.3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(0,-7),QPointF(0,-11));p.rotate(30)
        p.end()
