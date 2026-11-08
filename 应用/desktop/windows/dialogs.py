"""Branded operation confirmation; no device access or transaction ownership."""
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFrame,
                              QGraphicsDropShadowEffect,QPushButton)
from design import label,button,panel,row,column,Symbol,FEATURES,ACCENT,AMBER,line


class OperationConfirmation(QDialog):
    def __init__(self,parent,action,features,physical,skipped=0):
        super().__init__(parent,Qt.WindowType.Dialog|Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        updating=action=='unlock' and any(f.get('update_available') for f in features)
        self.setWindowTitle('确认解锁或更新功能' if updating else '确认解锁功能' if action=='unlock' else '确认关闭功能')
        self.setModal(True);self.setFixedWidth(580)
        self.setAccessibleName(self.windowTitle())
        outer=QVBoxLayout(self);outer.setContentsMargins(20,20,20,20)
        card=QFrame();card.setObjectName('confirmationCard')
        card.setStyleSheet('QFrame#confirmationCard { background:white; border:1px solid #e6e9e8; border-radius:16px; }')
        shadow=QGraphicsDropShadowEffect(card);shadow.setBlurRadius(32);shadow.setOffset(0,6);shadow.setColor(QColor(20,40,32,40));card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        body=QVBoxLayout(card);body.setContentsMargins(28,26,28,24);body.setSpacing(17)
        head=row(body,12);head.addWidget(Symbol('sliders' if action=='unlock' else 'layers',36,ACCENT,True));titles=column(head,5,1)
        titles.addWidget(label('确认解锁或更新所选功能' if updating else '确认解锁所选功能' if action=='unlock' else '确认关闭所选功能','heading'))
        titles.addWidget(label('本次仅影响下列功能，其他项目保持现状。','muted'))
        verb='解锁或更新' if updating else '解锁' if action=='unlock' else '关闭'
        _,listing=panel(body,'tint',16,10)
        count=label(f'将{verb} {len(features)} 项');count.setStyleSheet('font-size:21px;font-weight:700;');listing.addWidget(count)
        for feature in features:
            name,_,kind=FEATURES.get(feature['id'],(feature['name'],'','layers'))
            r=row(listing,10);r.addWidget(Symbol(kind,17));r.addWidget(label(name+(' · 更新' if feature.get('update_available') else '')));r.addStretch()
        if skipped:listing.addWidget(label(f'另有 {skipped} 项已符合目标，将自动跳过。','small'))
        if physical:
            body.addWidget(label('将修改相机内部功能字段。执行前会再次检查；请保持供电和 USB 连接，等待写入及独立回读结果。','muted'))
        else:
            body.addWidget(label('当前为演示模式，不会连接或修改真实相机。','muted'))
        if action=='close':
            body.addWidget(label('将先恢复相关机身默认设置并独立回读，再关闭固件功能；设置结果不明时不会继续写固件。','muted'))
        if any(f['id']=='fl23' for f in features):
            body.addWidget(label('关闭 FL2／FL3 会完整卸载其专属支持，保留其他已解锁功能。完成后务必断电重启，再检查创意外观菜单。' if action=='close' else 'FL2／FL3 与其他功能依次执行、分别核验；若中途失败，已完成的部分不会自动全部撤回，请先检查结果。','muted'))
        if any(f['id']=='creative_look_names' for f in features):
            body.addWidget(label('中文名称可独立关闭；未选择关闭的 FL2／FL3 保持。' if action=='close' else '中文名称覆盖主列表及编号 1～6；未选择的 FL2／FL3 保持当前状态。','muted'))
        line(body)
        buttons=row(body,10);buttons.addStretch()
        self.cancel_button=button(buttons,'返回调整',self.reject,'secondary')
        self.cancel_button.setObjectName('confirmationCancel')
        self.execute_button=button(buttons,('确认' if physical else '演示')+verb+f' {len(features)} 项',self.accept,'primary' if action=='unlock' else 'destructive')
        self.execute_button.setAccessibleName('确认执行');self.execute_button.setProperty('testId','confirmationExecute')
        # Enter initially returns to review. Execution requires explicit choice.
        self.execute_button.setAutoDefault(True);self.cancel_button.setDefault(True)
        self.dim=QWidget(parent.centralWidget());self.dim.setStyleSheet('background:rgba(24,43,36,45);');self.dim.hide()
        self.finished.connect(lambda _:self.dim.hide())
    def showEvent(self,event):
        super().showEvent(event)
        self.dim.setGeometry(self.parentWidget().centralWidget().rect());self.dim.show();self.dim.raise_()
        center=self.parentWidget().mapToGlobal(self.parentWidget().rect().center())
        self.move(center-QPoint(self.width()//2,self.height()//2))
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
    def done(self,result):
        self.dim.hide();super().done(result)
