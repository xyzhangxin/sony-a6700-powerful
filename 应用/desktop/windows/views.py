"""Mac-layout Qt views. Only presentation; commands/transactions remain shared."""
from pathlib import Path
import time
import sys
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QPixmap, QIcon, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QComboBox, QMenu, QDialog, QPlainTextEdit, QSizePolicy)
from a6700_unlock import __version__
from a6700_unlock.notice import operation_notice
from importlib.resources import files
from design import (label,button,panel,line,row,column,pill,Segments,Symbol,CheckBox,
                    ACCENT,INK,MUTED,AMBER,FEATURES,ActivityIndicator,symbol_icon)


def title(layout,text,subtitle=''):
    layout.addWidget(label(text,'title'))
    if subtitle: layout.addWidget(label(subtitle,'muted'))


def icon_text(layout,kind,text,role='heading',color=ACCENT,size=20):
    box=row(layout);box.addWidget(Symbol(kind,size,color));box.addWidget(label(text,role));return box


def disclosure(layout,text,items,opened,callback):
    button(layout,('⌄  ' if opened else '›  ')+text,callback,'quiet')
    if opened:
        for item in items:layout.addWidget(label(item,'muted'))


class Views:
    def build_shell(self,demo):
        self.action='unlock';self.disclosures=set();self.progress=[];self.cancel_requested=False
        self.operation_started_at=None
        self.elapsed_timer=QTimer(self);self.elapsed_timer.setInterval(1000);self.elapsed_timer.timeout.connect(self.update_elapsed);self.elapsed_timer.start()
        self.mode=QComboBox(self);self.mode.addItems(['相机','演示']);self.mode.setCurrentIndex(1 if demo else 0)
        self.mode.hide();self.mode.currentIndexChanged.connect(self.change_mode)
        shell=QWidget();shell.setObjectName('shell');self.setCentralWidget(shell)
        self.outer=QVBoxLayout(shell);self.outer.setContentsMargins(0,0,0,0);self.outer.setSpacing(0)
        self.header=QFrame();self.header.setObjectName('header');h=QHBoxLayout(self.header)
        h.setContentsMargins(28,16,28,16);h.setSpacing(12)
        self.mark=label('',wrap=False);self.mark.setFixedSize(40,40)
        icon=(Path(sys._MEIPASS)/'Powerful6700.png' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'品牌/Powerful6700.png')
        self.setWindowIcon(QIcon(str(icon)));self.mark.setPixmap(QPixmap(str(icon)).scaled(40,40,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));h.addWidget(self.mark)
        brand=column(h,3);brand.addWidget(label('Powerful6700','heading',False));brand.addWidget(label('固件 2.00 · 开发测试版','small',False))
        h.addStretch();self.demo_button=button(h,'',self.demo_settings,'iconButton');self.demo_button.setFixedSize(28,28)
        self.demo_button.setAccessibleName('演示设置');self.demo_button.setToolTip('演示场景与连接设置')
        demo_icon=Symbol('sliders',18);demo_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        d=QHBoxLayout(self.demo_button);d.setContentsMargins(5,5,5,5);d.addWidget(demo_icon)
        self.feedback_button=button(h,'意见反馈',self.show_feedback,'link');self.feedback_button.setAccessibleName('意见反馈');self.feedback_button.setToolTip('填写问题和图片，按需采集并上传相机日志');self.feedback_button.setIcon(symbol_icon('bubble',14,ACCENT));self.feedback_button.setIconSize(QSize(14,14))
        self.mode_symbol=Symbol('cable',15);h.addWidget(self.mode_symbol)
        self.mode_status=label('相机模式','accent',False);self.mode_status.setStyleSheet('font-size:11px; font-weight:600;');h.addWidget(self.mode_status)
        self.mode_segments=Segments([(1,'演示'),(0,'相机')],self.mode.currentIndex(),self.mode.setCurrentIndex);self.mode_segments.setFixedWidth(122);h.addWidget(self.mode_segments)
        help_button=button(h,'',self.help_menu,'iconButton');help_button.setFixedSize(28,28);help_button.setAccessibleName('帮助与版本')
        self.update_dot=QWidget(help_button);self.update_dot.setFixedSize(8,8)
        self.update_dot.setStyleSheet('background:#e5484d;border-radius:4px;')
        self.update_dot.move(18,2);self.update_dot.hide();self.update_dot.setToolTip('有可用更新，点击查看')
        i=QHBoxLayout(help_button);i.setContentsMargins(5,5,5,5);sym=Symbol('more',18,MUTED);sym.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);i.addWidget(sym)
        self.help_button=help_button;self.outer.addWidget(self.header)
        self.step_frame=QFrame();self.step_frame.setObjectName('steps');self.step_layout=QHBoxLayout(self.step_frame);self.step_layout.setContentsMargins(28,11,28,13);self.step_layout.setSpacing(20)
        self.outer.addWidget(self.step_frame);line(self.outer)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.outer.addWidget(self.scroll,1)
        self.footer_host=QWidget();f=QVBoxLayout(self.footer_host);f.setContentsMargins(0,0,0,0);f.setSpacing(0);self.footer_layout=f;self.outer.addWidget(self.footer_host)
        self.render_loading('正在检查本机组件…')

    @staticmethod
    def clear(box):
        while box.count():
            item=box.takeAt(0)
            if item.widget():item.widget().deleteLater()
            elif item.layout():Views.clear(item.layout())

    def page(self,text,subtitle=''):
        widget=QWidget();widget.setObjectName('page');layout=QVBoxLayout(widget)
        layout.setContentsMargins(28,20,28,20);layout.setSpacing(18)
        if text:title(layout,text,subtitle)
        old=self.scroll.takeWidget()
        if old:old.deleteLater()
        self.scroll.setWidget(widget);self.progress_label=None;self.elapsed_label=None;self.progress_log_label=None
        return layout

    def refresh_chrome(self,phase):
        s=self.state;stage=0 if phase=='notice' else 1 if phase=='preparation' or (phase in ('progress','result') and s.get('action')=='inspect') else 2
        self.mode_segments.select(self.mode.currentIndex());self.mode_segments.setEnabled(self.mode.isEnabled())
        self.demo_button.setVisible(not self.physical);self.demo_button.setEnabled(not self.inflight and not s.get('busy'))
        self.mode_symbol.kind='cable' if self.physical else 'play';self.mode_symbol.update()
        self.mode_status.setText(('相机已检查' if s.get('compatible') else '相机模式') if self.physical else '演示模式 · 不连接相机')
        self.clear(self.step_layout)
        for i,name in enumerate(['操作前须知','准备并连接','功能管理']):
            if i:self.step_layout.addWidget(label('›','small',False))
            group=QWidget();r=QHBoxLayout(group);r.setContentsMargins(0,0,0,0);r.setSpacing(7)
            badge=label('✓' if stage>i else str(i+1),'',False);badge.setFixedSize(19,19);badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet('border-radius:9px; font-size:11px; font-weight:600;'+('background:#145e4f;color:white;' if stage>=i else 'border:1px solid #a7afac;color:#929c96;'))
            r.addWidget(badge);t=label(name,'',False);t.setStyleSheet(f'font-size:12px;color:{ACCENT if stage>=i else "#929c96"};font-weight:{600 if stage==i else 400};');r.addWidget(t);self.step_layout.addWidget(group)
        self.step_layout.addStretch()
        if stage>0:button(self.step_layout,'查看须知',self.review_notice,'quiet')

    def toggle_details(self,key):
        if key in self.disclosures:self.disclosures.remove(key)
        else:self.disclosures.add(key)
        self.render()

    def help_menu(self):
        self.update_dot.hide()
        m=QMenu(self)
        if self.physical:
            m.addAction('检查更新',lambda:self.command('check_updates'))
            if getattr(self,'update_status',None):
                m.addSection(self.update_status)
            m.addSeparator()
        m.addAction('查看操作前须知',self.review_notice);m.addAction('Windows 驱动准备',self.driver_help)
        m.addAction('开源许可',self.show_license);m.addSeparator();a=m.addAction('Windows 预览 · '+__version__);a.setEnabled(False)
        m.exec(self.help_button.mapToGlobal(self.help_button.rect().bottomRight()))

    def demo_settings(self):
        m=QMenu(self);m.addSection('演示场景 · 不访问 USB')
        for item in self.state.get('scenarios',[]):
            a=m.addAction(item['name']);a.setCheckable(True);a.setChecked(self.state.get('scenario')==item['id'])
            a.triggered.connect(lambda _,ident=item['id']:self.command('reset',scenario=ident))
        m.addSeparator();connected=self.state.get('connected');m.addAction('模拟断开连接' if connected else '重新连接演示设备',lambda:self.command('disconnect' if connected else 'reconnect'))
        m.exec(self.demo_button.mapToGlobal(self.demo_button.rect().bottomLeft()))

    def dialog(self,name):
        d=QDialog(self);d.setWindowTitle(name);d.resize(780,620);d.setMinimumSize(680,500)
        outer=QVBoxLayout(d);outer.setContentsMargins(24,24,24,24);outer.setSpacing(18)
        return d,outer

    def show_license(self):
        d,l=self.dialog('开源许可');title(l,'开源许可','GPL-3.0-only · 无担保')
        text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(files('a6700_unlock').joinpath('data/legal/LICENSE.txt').read_text(encoding='utf-8'));text.setStyleSheet('background:white;border:1px solid #e6e9e8;border-radius:8px;padding:12px; font-size:11px;');l.addWidget(text)
        r=row(l);r.addStretch();button(r,'关闭',d.accept,'primary');d.exec()

    def review_notice(self):
        d,l=self.dialog('操作前须知');scroll=QScrollArea();scroll.setWidgetResizable(True);w=QWidget();content=QVBoxLayout(w);content.setSpacing(16)
        title(content,'操作前须知','查看须知不会改变当前连接或执行状态。')
        self.notice_sections(content,operation_notice().get('sections',[]),reviewing=True)
        scroll.setWidget(w);l.addWidget(scroll);r=row(l);r.addStretch();button(r,'返回当前操作',d.accept,'primary');d.exec()

    def render_loading(self,text):
        l=self.page('请稍候',text);l.addStretch();self.clear(self.footer_layout)

    def render_interrupted(self,text):
        self.transitioning=False;self.inflight=None;self.mode.setEnabled(True);self.state={};self.page_key=None;self.scroll.setEnabled(True)
        self.refresh_chrome('stopped');l=self.page('本地设备服务已停止','重新插拔 USB 不会重启已停止的本地服务。')
        _,card=panel(l);icon_text(card,'warning',text,'heading',AMBER)
        card.addWidget(label('下方按钮只重启本机服务和检查环境，不会自动连接相机或重放上次操作。','muted'))
        b=row(l);button(b,'重新启动本地服务',self.start,'primary');b.addStretch();l.addStretch();self.clear(self.footer_layout)

    def render(self):
        s=self.state;busy=bool(s.get('busy'));self.mode.setEnabled(not busy and not self.inflight)
        phase='progress' if busy else s.get('phase','notice');key=(phase,s.get('action'))
        old=self.page_key;position=self.scroll.verticalScrollBar().value() if old==key else 0
        if old!=key:
            self.disclosures.clear()
            if phase=='progress':self.progress=[];self.cancel_requested=False;self.operation_started_at=time.monotonic()
            if phase=='home':self.action='unlock'
        self.page_key=key;self.refresh_chrome(phase)
        if hasattr(self,'update_dot'):
            self.update_dot.setVisible(bool(getattr(self,'update_available',False)))
        if getattr(self,'update_available',False):
            self._ensure_update_banner()
        elif hasattr(self,'_update_banner') and self._update_banner is not None:
            self._update_banner.hide()
        l=self.page('')
        if phase=='notice':self.notice(l)
        elif phase=='preparation':self.preparation(l)
        elif phase=='home':self.workspace(l)
        elif phase=='progress':self.operation_progress(l)
        elif phase=='result':self.operation_result(l)
        l.addStretch();self.render_footer(phase)
        QTimer.singleShot(0,lambda:self.scroll.verticalScrollBar().setValue(position))

    def _ensure_update_banner(self):
        """Show a dismissable banner under the header when an adapter-set update arrives."""
        if not hasattr(self,'_update_banner') or self._update_banner is None:
            self._update_banner=QWidget();self._update_banner.setObjectName('updateBanner')
            self._update_banner.setStyleSheet('background:#ebf5ff;border-bottom:1px solid #d0e4f7;')
            layout=QHBoxLayout(self._update_banner);layout.setContentsMargins(28,6,28,6)
            layout.addWidget(label('已更新到最新版本','small'))
            layout.addStretch()
            button(self._update_banner,'知道了',self._dismiss_update_banner,'quiet')
            self.outer.insertWidget(self.outer.indexOf(self.step_frame),self._update_banner)
        self._update_banner.show()

    def _dismiss_update_banner(self):
        self.update_available=False
        if hasattr(self,'_update_banner') and self._update_banner is not None:
            self._update_banner.hide()

    def notice(self,l):
        s=self.state;title(l,'操作前须知','请先确认机型与操作风险，再准备相机。')
        _,box=panel(l,padding=13);r=row(box);ready=s.get('environment',{}).get('ready')
        r.addWidget(Symbol('check' if ready else 'warning',18,ACCENT if ready else AMBER))
        r.addWidget(label('本机组件已就绪' if ready else '本机组件检查未通过'));r.addStretch()
        button(r,'收起详情' if 'environment' in self.disclosures else '检查详情',lambda:self.toggle_details('environment'),'quiet')
        button(r,'重新检查',lambda:self.command('environment'),'link',s.get('checked',False))
        for item in s.get('environment',{}).get('checks',[]):
            if 'environment' in self.disclosures:box.addWidget(label(('✓ ' if item['ok'] else '○ ')+item['name']+' · '+str(item['detail']),'small'))
            if not item['ok']:box.addWidget(label(item['name']+'：'+item.get('remediation',''),'amber'))
        self.notice_sections(l,s.get('notice',{}).get('sections',[]))

    def notice_sections(self,l,sections,reviewing=False):
        scope=next((x for x in sections if x['id']=='scope'),None)
        if scope:
            _,card=panel(l,'tint',17);r=row(card,12);r.addWidget(Symbol('camera',24),0,Qt.AlignmentFlag.AlignTop);text=column(r,6,1)
            text.addWidget(label(scope['title'],'heading'));text.addWidget(label(scope['body'],'muted'))
        risks=row(l,14)
        for section in [x for x in sections if x['level']=='risk']:
            _,card=panel(risks,padding=16);icon_text(card,'warning' if section['id']=='device-risk' else 'sliders',section['title'],'heading',AMBER,17)
            card.addWidget(label(section['body'],'muted'))
            if section.get('source_url'):button(card,'Sony 服务政策 ↗',lambda url=section['source_url']:QDesktopServices.openUrl(QUrl(url)),'link')
        section=next((x for x in sections if x['id']=='disclaimer'),None)
        if section:
            _,card=panel(l,padding=16);icon_text(card,'document',section['title'],'heading',INK,17);card.addWidget(label(section['body'],'muted'))
            button(card,'查看开源许可',self.show_license,'link')
        validation=next((x for x in sections if x['id']=='validation'),None)
        if validation and reviewing:
            l.addWidget(label('当前验证范围','heading'));l.addWidget(label(validation['body'],'muted'))
        elif validation:
            disclosure(l,'当前验证范围',[validation['body'],'Windows 预览仍待本版实机连接与功能验收。'],'validation' in self.disclosures,lambda:self.toggle_details('validation'))

    def preparation(self,l):
        title(l,'准备相机','按下面四项完成设置，最后连接数据线。已经准备好的话，确认后即可检查。')
        _,box=panel(l,padding=0,spacing=0)
        summaries={
            'ftp_stopped':'没有 FTP 菜单可直接跳过；如已有且开启，关闭传输后重启相机。',
            'camera_msc':'选择海量存储器（MSC），不要选择 MTP、电脑遥控或 USB 流式传输。',
            'power_awake':'关闭自动关机，确认电量充足或供电可靠。',
            'usb_connection':'使用可传数据的 USB 线，只连接一台相机；普通使用不接 Pico。'}
        for i,step in enumerate(self.state.get('steps',[])):
            if i:line(box)
            w=QWidget();r=QHBoxLayout(w);r.setContentsMargins(18,17,18,17);r.setSpacing(16);box.addWidget(w)
            n=label(str(i+1),'',False);n.setAlignment(Qt.AlignmentFlag.AlignCenter);n.setFixedSize(27,27);n.setStyleSheet('background:#edf3f1;color:#145e4f;border-radius:8px;font-weight:600;');r.addWidget(n,0,Qt.AlignmentFlag.AlignTop)
            text=column(r,7,1);text.addWidget(label(step['title'],'heading'));text.addWidget(label(summaries.get(step['id'],step['instruction']),'muted'))
            guides=step.get('menu_guides',[])
            if guides:text.addWidget(label(' → '.join(guides[0]['path'])+' → '+guides[0]['target'],'small'))
            preview=QFrame();preview.setFixedWidth(214);v=QVBoxLayout(preview);v.setContentsMargins(10,10,10,10);v.setSpacing(7);r.addWidget(preview,0,Qt.AlignmentFlag.AlignTop)
            if guides:
                g=guides[0];preview.setStyleSheet('QFrame {background:#1a2633;border-radius:10px;} QLabel {color:#aab4bf;}')
                v.addWidget(label('相机界面 · 右上角                         示意','small',False));pr=row(v,8);pr.addWidget(Symbol('camera' if i==0 else 'cable' if i==1 else 'battery',25,'#ffa13d'))
                pc=column(pr,2,1);pc.addWidget(label(g['title'],'small'));target=label(g['target']);target.setStyleSheet('color:#ffa13d;font-size:13px;font-weight:600;');pc.addWidget(target)
            else:
                preview.setObjectName('tint');v.addWidget(label('连接方式 · 关键项                          示意','small',False));pr=row(v,7);pr.addWidget(Symbol('document',24));pr.addWidget(label('────','accent',False));pr.addWidget(Symbol('camera',24));pr.addWidget(label('MSC','pill',False));v.addWidget(label('只连一台 · 不接 Pico','small'))
        l.addWidget(label('右侧只展示相机界面右上角的关键项，不是实机截图。下方确认只代表人工声明，连接后仍会检查固件与功能状态。','muted'))
        _,box=panel(l,'tint',15);r=row(box,11);r.addWidget(Symbol('info',18),0,Qt.AlignmentFlag.AlignTop);text=column(r,6,1)
        text.addWidget(label('MSC 提示为什么会消失？Windows 需要怎样准备？','heading'))
        text.addWidget(label('连接后会切到维护服务，MSC 提示消失本身不等于断线；保持数据线连接，以应用结果为准。首次连接会由专用助手一次授权、自动准备 MSC 与维护服务共用的 WinUSB；主应用不提权，后续无需重复安装。','muted'))
        if self.physical:button(text,'驱动说明与恢复普通 SD 访问',self.driver_help,'link')

    def set_action(self,action):
        self.action=action;self.render()

    def dependency_issue(self,features,affected):
        selected={f['id'] for f in features if f.get('selected')};by_id={f['id']:f for f in features}
        if self.action=='unlock':
            for f in affected:
                for ident in f.get('requires',[]):
                    if by_id.get(ident,{}).get('state')!='enabled' and ident not in selected:return '所选功能有依赖，请同时选择相关功能。'
        else:
            removed={f['id'] for f in affected}
            if any(f.get('state')!='disabled' and f['id'] not in selected and removed.intersection(f.get('requires',[])) for f in features):return '其他已解锁功能依赖此项目，请同时选中关闭。'
        return None

    def workspace(self,l):
        s=self.state;features=s.get('features',[]);available=[f for f in features if f.get('available',True)];selected=[f for f in available if f.get('selected')]
        affected=[f for f in selected if f['state']!=('enabled' if self.action=='unlock' else 'disabled') or (self.action=='unlock' and f.get('update_available'))]
        has_updates=self.action=='unlock' and any(f.get('update_available') for f in affected)
        r=row(l);r.addWidget(label('功能管理','title'));r.addStretch();r.addWidget(pill(('A6700' if self.physical else '演示设备')+' · 检查通过'))
        l.addWidget(label(f"已解锁 {sum(f['state']=='enabled' for f in available)} / {len(available)} 项",'muted'))
        columns=row(l,18);left,listbox=panel(None,padding=0,spacing=0);left.setObjectName('panel');left.setObjectName('featureList');columns.addWidget(left,1,Qt.AlignmentFlag.AlignTop)
        toolbar=QWidget();t=QHBoxLayout(toolbar);t.setContentsMargins(14,13,14,13);t.setSpacing(14);t.addWidget(label('选择功能'));t.addStretch()
        button(t,'选择未解锁或可更新' if self.action=='unlock' else '选择可关闭',lambda:self.command('select',mode='missing' if self.action=='unlock' else 'enabled'),'link')
        button(t,'清空' if len(selected)==len(available) else '全选',lambda:self.command('select',mode='none' if len(selected)==len(available) else 'all'),'quiet');listbox.addWidget(toolbar);line(listbox)
        for i,f in enumerate(features):
            if i:line(listbox)
            w=QWidget();r=QHBoxLayout(w);r.setContentsMargins(16,12,16,12);r.setSpacing(12)
            check=CheckBox();check.setChecked(f.get('selected',False));check.setEnabled(f.get('available',True));check.setAccessibleName('选择功能：'+f['name'])
            check.toggled.connect(lambda v,ident=f['id']:self.command('select_feature',id=ident,value=v));r.addWidget(check)
            name,explanation,icon=FEATURES.get(f['id'],(f['name'],'按当前适配管理此功能','layers'))
            r.addWidget(Symbol(icon,34,ACCENT if f.get('available',True) else '#9cafa7',True));text=column(r,5,1)
            heading=label(name);heading.setStyleSheet('font-size:13px;font-weight:600;'+('color:#9da7a1;' if not f.get('available',True) else ''));text.addWidget(heading)
            text.addWidget(label(explanation if f.get('available',True) else f.get('unavailable_reason',explanation),'small'))
            state_text='暂未开放' if not f.get('available',True) else {'enabled':'已解锁','disabled':'未解锁','partial':'部分解锁','unknown':'待核实'}.get(f['state'],'待确认')
            if f.get('available',True) and f.get('update_available'):state_text+=' · 可更新'
            st=label(state_text,'small',False);st.setStyleSheet('font-size:10px;color:'+ (ACCENT if f.get('available',True) and f['state']=='enabled' else MUTED));r.addWidget(st)
            if f.get('update_available'):
                is_ftp = f['id'] == 'ftp'
                update=button(r,'更新 FTP' if is_ftp else '更新 FL2／FL3',
                              lambda is_ftp=is_ftp:self.command('update_ftp' if is_ftp else 'update_fl23'),
                              'secondary',not self.inflight and not s.get('busy'))
                update.setObjectName('ftpUpdateButton' if is_ftp else 'fl23UpdateButton')
                update.setToolTip('仅更新当前功能，保留其他功能和设置')
            listbox.addWidget(w)
        right,box=panel(None);right.setFixedWidth(270);right.setObjectName('actionPanel');columns.addWidget(right,0,Qt.AlignmentFlag.AlignTop)
        box.addWidget(label('调整所选功能','heading'));box.addSpacing(4)
        seg=Segments([('unlock','解锁'),('close','关闭')],self.action,self.set_action);seg.setAccessibleName('操作方式');box.addWidget(seg,0,Qt.AlignmentFlag.AlignLeft);box.addSpacing(10)
        count=label(('将解锁或更新 ' if has_updates else '将解锁 ' if self.action=='unlock' else '将关闭 ')+str(len(affected))+' 项');count.setStyleSheet('font-size:21px;font-weight:700;');box.addWidget(count)
        box.addWidget(label(f'已选 {len(selected)} 项 · 状态已符合的功能会跳过','small'));box.addSpacing(10)
        tasks=QWidget();tasks.setMinimumHeight(94);body=QVBoxLayout(tasks);body.setContentsMargins(0,0,0,0);body.setSpacing(10)
        if not affected:body.addWidget(label('本版可用功能都已解锁。如需撤销，切换到“关闭”并选择功能。' if self.action=='unlock' and all(f['state']=='enabled' for f in available) else '在左侧选择需要调整的功能。','muted'))
        for f in affected:body.addWidget(label(('⊕  ' if self.action=='unlock' else '⊖  ')+FEATURES.get(f['id'],(f['name'],))[0]+(' · 更新' if self.action=='unlock' and f.get('update_available') else '')))
        body.addStretch();box.addWidget(tasks)
        issue=self.dependency_issue(features,affected)
        if issue:box.addWidget(label(issue,'amber'))
        if self.action=='unlock':
            for feature in selected:
                if feature.get('unlock_notice'):
                    notice=label(feature['unlock_notice'],'amber');notice.setObjectName('fl23StartupNotice');box.addWidget(notice)
        if self.action=='close':box.addWidget(label('一次完成：先恢复相关机身默认设置，再关闭固件功能并独立回读。','muted'))
        execute=button(box,('' if self.physical else '演示：')+('解锁或更新 ' if has_updates else '解锁 ' if self.action=='unlock' else '恢复设置并关闭 ')+str(len(affected))+' 项',lambda:self.confirm_write(self.action),'primary' if self.action=='unlock' else 'destructive',bool(affected) and not issue and s.get('compatible',False))
        execute.setAccessibleName('解锁所选功能' if self.action=='unlock' else '关闭所选功能')
        box.addWidget(label('只修改所选功能，每次执行前会重新检查。' if self.physical else '演示不会连接或修改真实相机。','small'))

    def request_cancel(self):
        self.cancel_requested=True;self.command('cancel');self.render()

    def operation_progress(self,l):
        action=self.state.get('action');titles={'inspect':'正在连接并检查','close':'正在恢复设置并关闭','unlock':'正在解锁或更新所选功能'}
        title(l,titles.get(action,'正在处理'),'MSC 提示可能在维护模式下消失，请保持相机开机与 USB 连接，等待应用确认结果。' if self.physical else '正在运行演示流程，不会访问真实相机。')
        _,box=panel(l);r=row(box,18);self.spinner=ActivityIndicator();r.addWidget(self.spinner)
        text=column(r,8,1);self.progress_label=label('正在请求结束，请稍候…' if self.cancel_requested else self.progress[-1] if self.progress else '正在准备连接相机…' if self.physical else '正在准备演示数据…','heading');text.addWidget(self.progress_label)
        self.elapsed_label=label('','muted');text.addWidget(self.elapsed_label);self.update_elapsed()
        line(box);button(box,('⌄  ' if 'progress' in self.disclosures else '›  ')+'查看处理过程',lambda:self.toggle_details('progress'),'quiet')
        if 'progress' in self.disclosures:
            self.progress_log_label=label('\n'.join(self.progress) or '正在等待服务响应…','muted');box.addWidget(self.progress_log_label)
        r=row(l,14);button(r,'正在等待结果…' if self.cancel_requested else '请求取消',self.request_cancel,'secondary',not self.cancel_requested);r.addWidget(label('取消不会强制中断已发送的写入，请等待核验结果。','muted',False));r.addStretch()

    def update_elapsed(self):
        if getattr(self,'elapsed_label',None) is not None:
            elapsed=max(0,int(time.monotonic()-(self.operation_started_at or time.monotonic())))
            self.elapsed_label.setText(f'已用 {elapsed} 秒 · '+('请勿拔线或关机' if self.physical else '演示步骤会短暂停留'))

    def close_preparation_panel(self,l,preparation):
        if not preparation:return
        _,box=panel(l);box.setObjectName('closePreparationPanel')
        box.addWidget(label('关闭前机身状态' if self.physical else '模拟关闭前机身状态','heading'))
        completed=set(preparation.get('completed',[]))
        for check in preparation.get('checks',[]):
            if check['id'] in completed:status='已恢复为“'+check.get('target_label','')+'”并回读'
            elif check.get('status')=='safe':status='已确认，无需调整'
            else:status='尚未确认可安全关闭'
            box.addWidget(label(check.get('name',check['id'])+'：'+status))
            if check.get('reason'):box.addWidget(label(check['reason'],'muted'))
        if preparation.get('settings_write_attempted') and preparation.get('status')!='ready':
            box.addWidget(label('已发生设置调整或尝试，请核对完成范围；不会自动重新开启已停用的功能。','amber'))

    def operation_result(self,l):
        s=self.state;r=s.get('result') or {};after=r.get('aftercare')
        if r.get('success') and after:self.success(l,r,after)
        else:
            uncertain=r.get('status') in ('uncertain','verification-failed','recovery-required','close-preparation-uncertain')
            title(l,r.get('title','操作未完成'),'先核实当前状态，再决定下一步。' if uncertain else '处理下面的问题后，可以重新连接并检查。')
            self.close_preparation_panel(l,r.get('close_preparation'))
            _,box=panel(l);head=row(box,16);head.addWidget(Symbol('warning',31,AMBER),0,Qt.AlignmentFlag.AlignTop);t=column(head,10,1);t.addWidget(label(r.get('detail','当前状态无法确认，请重新检查。'),'heading'))
            if r.get('status')=='connection-busy':
                t.addWidget(label((r.get('takeover') or {}).get('detail','请在占用相机的原工具中结束任务，再重新连接。'),'amber'))
            if r.get('error') and r.get('error')!=r.get('detail'):
                t.addWidget(label(r['error'],'amber'))
            t.addWidget(label('保留本次记录，不要盲目重复写入。' if uncertain else '确认相机模式、驱动、供电与连接后，再主动重新检查。','muted'))
            if not self.physical:t.addWidget(label('这是演示场景，真实相机没有被访问或修改。','accent'))
            line(box);disclosure(box,'查看诊断详情',[r.get('error',''),'状态：'+r.get('status','error')]+self.progress,'diagnostic' in self.disclosures,lambda:self.toggle_details('diagnostic'))
            b=row(l);button(b,'重新准备并检查',lambda:self.command('restart_preparation'),'primary');b.addStretch()

    def success(self,l,r,after):
        text='已经是目标状态' if r.get('status')=='no-op' else ('固件已关闭，设置已恢复' if r.get('status')=='settings-prepared' else '关闭完成' if self.state.get('action')=='close' else '解锁或更新完成')
        if not self.physical and r.get('status')!='no-op':text='模拟'+text
        head=row(l,13);head.addWidget(Symbol('success',30),0,Qt.AlignmentFlag.AlignTop);titles=column(head,7,1)
        subtitle=('所选功能已符合目标，未重复写入。请在相机上确认实际表现。' if r.get('status')=='no-op' else '写入与独立回读通过，实际功能还需在相机上确认。') if self.physical else '合成数据演示已完成；真实相机没有被访问或修改。'
        title(titles,text,subtitle);head.addWidget(pill('待机身验收' if self.physical else '仅演示结果'),0,Qt.AlignmentFlag.AlignTop)
        self.close_preparation_panel(l,r.get('close_preparation'))
        if self.state.get('action')=='close':
            _,status_box=panel(l);status_box.setObjectName('closeResultPanel')
            status_box.addWidget(label('机身设置：已恢复目标状态并独立回读'))
            status_box.addWidget(label('固件功能：本次已关闭并独立回读' if r.get('status')=='completed' else '固件功能：此前已关闭，本次未重复写入'))
            status_box.addWidget(label('菜单不会在设置恢复后立即消失；必须完成下方完全断电重启。','amber'))
        if r.get('cleanup_warning'):
            _,warning=panel(l,'warning',14);icon_text(warning,'warning','USB 服务退出未确认。请按下方断电步骤重启，再重新连接。','body',AMBER)
        _,box=panel(l);h=row(box);h.addWidget(Symbol('power',20));h.addWidget(label('接下来：断电重启相机','heading'));h.addStretch();h.addWidget(label('关机 ≠ 完全断电','muted',False))
        box.addWidget(label('请完整执行以下步骤，再检查菜单是否生效。' if self.physical else '以下为流程示意，无需操作真实相机。','muted'));box.addSpacing(6)
        steps=row(box,12)
        for i,step in enumerate(after.get('steps',[])):
            w=QWidget();v=QVBoxLayout(w);v.setContentsMargins(0,0,0,0);v.setSpacing(9)
            number=label(str(i+1),'',False);number.setAlignment(Qt.AlignmentFlag.AlignCenter);number.setFixedSize(23,23);number.setStyleSheet('background:#edf3f1;color:#145e4f;border-radius:11px;font-size:11px;font-weight:700;');v.addWidget(number)
            heading=label(step['title']);heading.setStyleSheet(f'font-size:{18 if step["id"]=="wait" else 15}px;font-weight:600;color:{ACCENT if step["id"]=="wait" else INK};');v.addWidget(heading)
            v.addWidget(label(step['detail'],'muted'));v.addStretch();steps.addWidget(w,1)
        if after.get('startup_notice'):
            _,notice_box=panel(box,'warning',14)
            notice=label(after['startup_notice'],'amber');notice.setObjectName('fl23StartupNotice');notice_box.addWidget(notice)
        h=row(l);h.addWidget(label('开机后，检查本次功能','heading'));h.addStretch();h.addWidget(label(str(len(after.get('features',[])))+' 项 · 仅列出本次选择','muted',False))
        grid=QGridLayout();grid.setHorizontalSpacing(14);grid.setVerticalSpacing(14);grid.setColumnStretch(0,1);grid.setColumnStretch(1,1);l.addLayout(grid)
        for i,f in enumerate(after.get('features',[])):
            frame,card=panel(None);head=row(card);head.addWidget(label(f['name'],'heading'));head.addStretch();outcome=label(f['outcome'],'small');outcome.setStyleSheet('font-size:10px;color:#145e4f;');head.addWidget(outcome)
            path=label(f['path']);path.setStyleSheet('color:#145e4f;font-size:12px;font-weight:600;');card.addWidget(path)
            card.addWidget(label('成功标准','small'));card.addWidget(label(f['expected']));card.addWidget(label(f.get('note',''),'muted'));card.addStretch();grid.addWidget(frame,i//2,i%2)
        disclosure(l,'查看本次处理详情',[r.get('detail','')]+self.progress,'result' in self.disclosures,lambda:self.toggle_details('result'))

    def render_footer(self,phase):
        self.clear(self.footer_layout);line(self.footer_layout);frame=QFrame();frame.setObjectName('footer');h=QHBoxLayout(frame);h.setContentsMargins(28,16 if phase in ('notice','preparation','result') else 12,28,16 if phase in ('notice','preparation','result') else 12);h.setSpacing(16);self.footer_layout.addWidget(frame)
        s=self.state;success=phase=='result' and (s.get('result') or {}).get('success') and (s.get('result') or {}).get('aftercare')
        if phase=='notice':
            h.addStretch();ack=CheckBox('我已阅读并了解适用范围与操作风险');ack.setObjectName('noticeAcknowledgement');ack.setChecked(s.get('notice_acknowledged',False));ack.toggled.connect(lambda v:self.command('acknowledge',value=v));h.addWidget(ack)
            button(h,'开始准备相机',lambda:self.command('prepare'),'primary',s.get('checked',False) and s.get('environment',{}).get('ready',False) and s.get('notice_acknowledged',False))
        elif phase=='preparation':
            button(h,'返回',lambda:self.command('cancel'),'quiet');h.addStretch();ack=CheckBox('以上设置已完成，相机已连接并保持开机');ack.setObjectName('preparationAcknowledgement');ack.setChecked(s.get('ready',False));ack.toggled.connect(lambda v:self.command('confirm_preparation',value=v));h.addWidget(ack)
            button(h,'连接并自动准备驱动' if self.physical else '检查演示设备',lambda:self.command('inspect'),'primary',s.get('ready',False) and not self.inflight)
        elif success:
            t=column(h,4,1);t.addWidget(label('还想管理其他功能？'));t.addWidget(label('完成机身检查后，重新连接 MSC；应用会重新检查当前状态。' if self.physical else '返回准备页，重新检查演示设备后可继续体验。','muted'))
            button(h,'继续操作，重新连接',lambda:self.command('restart_preparation'),'primary')
        else:
            h.addWidget(Symbol('cable' if self.physical else 'info',13,MUTED));h.addWidget(label(('相机检查已通过 · MSC 提示消失不等于断线，请保持 USB 连接' if s.get('compatible') else '请保持相机开机和 USB 连接，等待应用确认结果') if self.physical else '合成数据演示 · 所有操作均不访问相机','small',False));h.addStretch()
            if phase=='home':button(h,'重新连接',lambda:self.command('restart_preparation'),'link')
