"""Camera-free Qt + actual worker integration. Works from source and frozen app."""
import json
import sys
import time
from unittest.mock import patch
from PySide6.QtCore import QTimer, Qt, QEvent
from PySide6.QtGui import QKeyEvent
from dialogs import OperationConfirmation
from design import ActivityIndicator
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QDialog, QWidget, QVBoxLayout


def run(window, report):
    app = QApplication.instance()
    checks = []
    def until(predicate, timeout=40):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError('Timed out waiting for desktop state')
    def capture(name):
        for _ in range(6):app.processEvents()
        if report is not None:
            window.grab().save(str(report.with_name('Windows-'+name+'.png')))
    def choose_confirmation(action,accept,escape=False,enter=False):
        errors=[]
        def choose():
            dialog=app.activeModalWidget()
            try:
                assert isinstance(dialog,OperationConfirmation)
                assert dialog.cancel_button.isDefault()
                assert dialog.execute_button.isEnabled()
                for _ in range(5):app.processEvents()
                if report is not None:
                    dialog.grab().save(str(report.with_name('Windows-confirm-'+action+'.png')))
                if enter:
                    target=dialog.execute_button if accept else dialog.cancel_button
                    target.setFocus();app.processEvents()
                    app.sendEvent(dialog,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Return,Qt.KeyboardModifier.NoModifier))
                elif escape:
                    app.sendEvent(dialog,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier))
                else:
                    (dialog.execute_button if accept else dialog.cancel_button).click()
            except Exception as exc:
                errors.append(str(exc))
                if dialog:dialog.reject()
        QTimer.singleShot(120,choose)
        with patch.object(window.client,'send',wraps=window.client.send) as send:
            window.confirm_write(action)
            if not accept:send.assert_not_called()
            else:assert send.call_args.args[0]==action
        assert not errors,errors
    try:
        until(lambda: window.state.get('checked'))
        assert window.state['simulation'] and not window.state['device_access']
        assert window.state['environment']['ready']
        checks.append('simulation-startup')
        capture('notice')
        window.show_feedback()
        assert window.feedback_dialog is not None
        assert window.feedback_dialog.windowTitle() == '意见反馈'
        assert window.feedback_dialog.width() == 640
        assert window.feedback_dialog.height() == 680
        assert window.feedback_dialog.description.toPlainText() == ''
        assert not window.feedback_dialog.upload_button.isEnabled()
        window.feedback_dialog.description.setPlainText('测试反馈')
        window.feedback_dialog.refresh()
        assert window.feedback_dialog.upload_button.isEnabled()
        window.feedback_dialog.hide()
        checks.append('feedback-entry-and-disabled-state')
        with patch.object(QDialog,'exec',return_value=0):
            window.show_license()
        window.command('reset', scenario='original')
        until(lambda: window.state.get('scenario')=='original' and window.state.get('checked'))
        def prepare(expected_phase='home'):
            window.command('confirm_preparation', value=True)
            until(lambda: window.state.get('ready'))
            window.command('inspect')
            if 'animated-spinner-elapsed-and-live-details' not in checks:
                until(lambda: window.state.get('busy'))
                assert isinstance(window.spinner,ActivityIndicator)
                frame=window.spinner.frame
                until(lambda: window.spinner.frame!=frame,timeout=3)
                started=window.operation_started_at
                window.toggle_details('progress')
                assert window.operation_started_at==started
                until(lambda: bool(window.progress))
                assert window.progress_log_label.text()== '\n'.join(window.progress)
                assert '已用' in window.elapsed_label.text()
                capture('progress')
                checks.append('animated-spinner-elapsed-and-live-details')
            until(lambda: window.state.get('phase')==expected_phase and not window.inflight)
        window.command('acknowledge', value=True)
        until(lambda: window.state.get('notice_acknowledged'))
        window.command('prepare'); until(lambda: window.state.get('phase')=='preparation')
        capture('preparation')
        from PySide6.QtWidgets import QLabel
        assert any('自动准备' in item.text() for item in window.scroll.widget().findChildren(QLabel))
        checks.append('automatic-driver-preparation-presented')
        prepare()
        assert sum(row.get('available',True) for row in window.state['features'])==6
        assert {row['id'] for row in window.state['features'] if row.get('available',True)}=={'ftp','shutter','exposure','sound','fl23','creative_look_names'}
        checks.append('preparation-and-inspect-six-available')
        capture('features')
        left=window.scroll.widget().findChild(QWidget,'featureList')
        right=window.scroll.widget().findChild(QWidget,'actionPanel')
        assert left is not None and right is not None
        assert right.x()>left.x()+left.width()
        assert right.width()==270
        assert window.footer_host.isVisible()
        assert window.footer_host.geometry().bottom()<=window.centralWidget().height()
        checks.append('mac-layout-two-column-workspace-and-fixed-footer')
        # The native row action must expose the 0.4.1 repair update without
        # requiring a simulated legacy package or touching a real device.
        saved=window.state
        rows=[dict(row) for row in saved['features']]
        legacy=next(row for row in rows if row['id']=='fl23')
        legacy.update(state='enabled',selected=True,update_available=True,installed_revision=1,target_revision=2)
        window.state=dict(saved,features=rows);window.set_action('unlock');app.processEvents()
        update=window.scroll.widget().findChild(QPushButton,'fl23UpdateButton')
        assert update is not None and update.isEnabled()
        from PySide6.QtWidgets import QLabel
        assert any('已解锁 · 可更新' in item.text() for item in window.scroll.widget().findChildren(QLabel))
        capture('fl23-update')
        with patch.object(window.client,'send') as send:
            update.click();send.assert_called_once_with('update_fl23')
        assert window.inflight=='update_fl23'
        window.inflight=None;window.operation_started=False;window.state=saved;window.scroll.setEnabled(True);window.render()
        ftp_row = next(row for row in rows if row['id'] == 'ftp')
        ftp_row.update(state='enabled', selected=True, update_available=True, installed_revision=1, target_revision=2)
        window.state = dict(saved, features=rows)
        window.set_action('unlock'); app.processEvents()
        ftp_update = window.scroll.widget().findChild(QPushButton, 'ftpUpdateButton')
        assert ftp_update is not None and ftp_update.isEnabled()
        with patch.object(window.client, 'send') as send:
            ftp_update.click(); send.assert_called_once_with('update_ftp')
        assert window.inflight == 'update_ftp'
        window.inflight = None; window.operation_started = False
        window.state = saved; window.scroll.setEnabled(True); window.render()
        checks.append('ftp-status-and-exclusive-update-command')
        checks.append('legacy-fl23-status-and-exclusive-update-command')
        for action in ('unlock','close'):
            window.set_action(action)
            window.command('select', mode='all')
            until(lambda: all(row.get('selected') for row in window.state['features'] if row.get('available',True)))
            from PySide6.QtWidgets import QLabel
            notices=window.scroll.widget().findChildren(QLabel,'fl23StartupNotice')
            assert bool(notices)==(action=='unlock')
            if action=='unlock':assert '40 秒' in notices[0].text()
            if action=='unlock':
                choose_confirmation(action,False)
                choose_confirmation(action,False,escape=True)
                choose_confirmation(action,False,enter=True)
                assert window.state['phase']=='home' and not window.inflight
                checks.append('branded-confirm-cancel-sends-nothing')
            choose_confirmation(action,True,enter=action=='close')
            checks.append('branded-'+action+'-confirm-executes-once')
            until(lambda: window.state.get('phase')=='result' and not window.inflight)
            result = window.state['result']
            assert result['status']=='completed' and result['success'],result
            assert not result['device_access'] and not result['write_attempted']
            assert not window.state['compatible'] and not window.state['ready']
            assert [s['id'] for s in result['aftercare']['steps']]==['off','usb-out','battery-out','wait','battery-in','on']
            assert len(result['aftercare']['features'])==6
            notices=window.scroll.widget().findChildren(QLabel,'fl23StartupNotice')
            assert bool(notices)==(action=='unlock')
            if action=='unlock':
                assert '40 秒' in notices[0].text()
                assert '10 秒' in result['aftercare']['steps'][3]['title']
                assert '40 秒' in result['aftercare']['startup_notice']
            checks.append(action+'-result-six-steps-and-menu-checks')
            capture(action+'-result')
            window.command('restart_preparation')
            until(lambda: window.state.get('phase')=='preparation')
            assert not window.state['ready'] and not window.state['compatible']
            prepare()
        checks.append('fl23-startup-notice-before-and-after-unlock-only')
        # Native UI and worker must retain the other look option when one is closed.
        for feature,action,expected in (
                ('creative_look_names','unlock',{'creative_look_names'}),
                ('fl23','unlock',{'fl23','creative_look_names'}),
                ('fl23','close',{'creative_look_names'}),
                ('creative_look_names','close',set())):
            window.set_action(action);window.command('select',mode='none')
            until(lambda:not any(f.get('selected') for f in window.state['features']))
            window.command('select_feature',id=feature,value=True)
            until(lambda:{f['id'] for f in window.state['features'] if f.get('selected')}=={feature})
            assert bool(window.scroll.widget().findChildren(QLabel,'fl23StartupNotice'))==(feature=='fl23' and action=='unlock')
            choose_confirmation(action,True)
            until(lambda:window.state.get('phase')=='result' and not window.inflight)
            assert window.state['result']['success'],window.state['result']
            window.command('restart_preparation');until(lambda:window.state.get('phase')=='preparation');prepare()
            assert {f['id'] for f in window.state['features'] if f['state']=='enabled' and f['id'] in ('fl23','creative_look_names')}==expected
        checks.append('independent-name-fl23-combinations-preserve-unselected-option')
        # Active native settings are restored first, then firmware is closed.
        window.command('reset', scenario='close-active')
        until(lambda:window.state.get('scenario')=='close-active' and window.state.get('phase')=='notice')
        window.command('acknowledge', value=True);until(lambda:window.state.get('notice_acknowledged'))
        window.command('prepare');until(lambda:window.state.get('phase')=='preparation');prepare()
        window.set_action('close');window.command('select',mode='enabled')
        until(lambda:all(row.get('selected') for row in window.state['features'] if row.get('available',True) and row.get('state')!='disabled'))
        choose_confirmation('close',True)
        until(lambda:window.state.get('phase')=='result' and not window.inflight)
        result=window.state['result'];assert result['success'],result
        preparation=result['close_preparation']
        assert set(preparation['completed'])=={'ftp','sound','shutter','exposure'},preparation
        assert preparation['settings_write_attempted']
        assert not result['write_attempted'] and not result['device_access']
        from PySide6.QtWidgets import QLabel
        assert window.scroll.widget().findChild(QVBoxLayout,'closePreparationPanel') is not None
        assert window.scroll.widget().findChild(QVBoxLayout,'closeResultPanel') is not None
        labels=[item.text() for item in window.scroll.widget().findChildren(QLabel)]
        assert any('机身设置：已恢复目标状态并独立回读' in text for text in labels)
        assert any('固件功能：本次已关闭并独立回读' in text for text in labels)
        capture('close-active-result')
        checks.append('active-settings-restored-before-firmware-close-with-two-stage-result')
        # Busy is a host-tool conflict, not a request to unplug the camera.
        saved=window.state
        window.state=dict(saved,phase='result',result=dict(status='connection-busy',title='相机连接被占用',detail='无法连接',takeover=dict(available=False,detail='请先在原工具结束任务，再重新连接。')))
        window.render();app.processEvents()
        from PySide6.QtWidgets import QLabel
        assert any('原工具结束任务' in item.text() for item in window.scroll.widget().findChildren(QLabel))
        capture('busy');window.state=saved;window.render()
        checks.append('busy-shows-manual-release-without-force')
        # Reservation is local and immediate; an old queued state must not
        # allow a second operation or close the window before busy arrives.
        window.command('reset', scenario='original')
        until(lambda: window.state.get('scenario')=='original' and window.state.get('phase')=='notice')
        window.command('acknowledge', value=True)
        until(lambda: window.state.get('notice_acknowledged'))
        window.command('prepare'); until(lambda: window.state.get('phase')=='preparation')
        prepare()
        window.command('unlock')
        with patch.object(window.client, 'send') as send:
            window.command('close')
            send.assert_not_called()
        with patch.object(QMessageBox, 'information') as blocked:
            assert not window.close()
            blocked.assert_called_once()
        until(lambda: window.state.get('busy'))
        assert not window.mode.isEnabled()
        window.command('cancel')
        until(lambda: window.state.get('phase')=='result' and not window.inflight)
        assert window.state['result']['status']=='cancelled',window.state['result']
        assert not window.state['result']['write_attempted']
        assert window.mode.isEnabled()
        checks.append('reservation-double-click-close-guard-and-cancel')
        # Unknown firmware blocks both write buttons and offers reconnection.
        window.command('reset', scenario='incompatible')
        until(lambda: window.state.get('scenario')=='incompatible' and window.state.get('phase')=='notice')
        window.command('acknowledge', value=True)
        until(lambda: window.state.get('notice_acknowledged'))
        window.command('prepare'); until(lambda: window.state.get('phase')=='preparation')
        prepare('result')
        assert window.state['result']['status']=='blocked'
        assert not window.state['compatible'] and not window.state['ready']
        buttons={b.text(): b for b in window.scroll.widget().findChildren(QPushButton)}
        assert '解锁所选功能' not in buttons
        assert '关闭所选功能' not in buttons
        assert buttons['重新准备并检查'].isEnabled()
        checks.append('incompatible-inspect-blocks-both-writers')
        # Terminate only this synthetic child, while no operation is in flight.
        window.client.process.kill()
        until(lambda: not window.state and not window.client.alive())
        checks.append('simulation-service-interruption')
        window.start(); until(lambda: window.state.get('checked'))
        assert window.state['phase']=='notice' and not window.state['device_access']
        checks.append('restart-local-service-without-replay')
        # A separate physical worker receives environment only: no USB access.
        window.mode.setCurrentIndex(0)
        until(lambda: window.state.get('checked') and not window.state.get('simulation',True), timeout=90)
        assert not window.state['device_access'] and not window.state['write_attempted']
        if sys.platform=='win32':
            assert window.state['environment']['ready'],window.state['environment']
        checks.append('physical-environment-only-no-usb')
        value=dict(ok=True, checks=checks, device_access=False, write_attempted=False)
    except Exception as exc:
        value=dict(ok=False,checks=checks,error=str(exc),device_access=False)
    finally:
        window.close()
        until(lambda: not window.client.alive(), timeout=20)
    if report is not None:
        report.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if value['ok'] else 1
