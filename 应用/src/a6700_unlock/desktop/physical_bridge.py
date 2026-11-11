"""Line-delimited JSON worker for the real USB desktop mode.

This bridge intentionally exposes only the fixed preparation and device
commands.  It does not accept a backend, profile, arbitrary path, shell
command or simulation scenario.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
import uuid

from .controller import DesktopController
from .update_service import DesktopUpdateService
from .protocol import decode_request, event
from ..device_protocol import DeviceBusyError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=None,
                        help='本机操作记录目录；默认使用系统状态目录')
    parser.add_argument('--backup-dir', type=Path, default=None,
                        help='功能状态备份目录；默认 ~/Documents/A6700 Backups')
    args = parser.parse_args()

    output_lock = Lock()
    # Authentication suppresses raw legacy ProductInfo through a stdout
    # redirect. Keep protocol progress on the original JSON output stream.
    protocol_output = sys.stdout
    def emit(kind, request_id=None, **values):
        with output_lock:
            print(json.dumps(event(kind, request_id=request_id, **values), ensure_ascii=False), flush=True,
                  file=protocol_output)

    controller = DesktopController(None, mode='physical', physical_state_dir=args.state_dir,
                                   physical_backup_dir=args.backup_dir)
    thread = None
    update_thread = None
    update_service = DesktopUpdateService(args.state_dir)
    guided_thread = None
    guided_cancel = None
    guided_session = None
    last_diagnostic = None

    from .. import diagnostics as diagnostics_module
    recovered_state_dir = args.state_dir if args.state_dir is not None else controller.service.state_dir
    recovered_recorder = diagnostics_module.load_unfinished_recorder(recovered_state_dir)
    if recovered_recorder is not None:
        guided_session = dict(recovered_recorder, started=time.monotonic(), collecting=False,
                              recorder=dict(recovered_recorder))
    else:
        from .. import feedback as feedback_module
        last_diagnostic = feedback_module.recover_diagnostic(recovered_state_dir)
        if last_diagnostic is not None:
            emit('diagnostic-result', result=last_diagnostic)

    def run(request_id):
        def progress(message):
            emit('progress', message=message, request_id=request_id)
        try:
            emit('state', state=(controller.run_takeover(progress) if controller.action == 'takeover'
                                  else controller.run_reserved(progress)), request_id=request_id)
        except (Exception, KeyboardInterrupt) as exc:
            with controller.lock:
                controller.busy = False
                controller.compatible = False
                controller.confirmed.clear()
                controller.features = []
                controller.phase = 'result'
                controller.revision += 1
                detail = str(exc).strip() or '重新检查环境、准备状态和 USB 连接后再试。'
                cancelled = isinstance(exc, KeyboardInterrupt)
                controller.result = dict(status='cancelled' if cancelled else 'error',
                    title='连接检查已取消' if cancelled else '真实设备服务发生错误',
                    detail=detail, success=False,
                    error=detail, simulation=False, device_access=True, write_attempted=False)
                if isinstance(exc, DeviceBusyError):
                    try:
                        takeover = controller.takeover_manager.prepare(exc.lock_path)
                    except (OSError, ValueError):
                        takeover = dict(available=False, confirmation='', owner='', pid=0,
                                        detail='无法确认占用者，请先退出其他相机软件再连接。')
                    controller.device_access = False
                    controller.result.update(status='connection-busy', title='相机连接正被其他工具使用',
                        detail=takeover['detail'], device_access=False, takeover=takeover)
                elif controller.action == 'takeover':
                    controller.device_access = False
                    controller.result.update(title='未完成连接接管', device_access=False)
            emit('state', state=controller.snapshot(), request_id=request_id)

    def run_update(request_id):
        try:
            result = update_service.check()
        except Exception:
            result = dict(status="error", cache_status="unavailable")
        emit("update-result", result=result, request_id=request_id,
             operation_id=controller.operation_id)

    def run_diagnostic(camera, request_id):
        nonlocal last_diagnostic
        if controller.busy or (thread is not None and thread.is_alive()) or (guided_thread is not None and guided_thread.is_alive()):
            raise ValueError('请先等待当前相机操作结束。')
        if camera:
            # The diagnostic collector owns a fresh backend and device lock.
            # Close the app's authenticated session before it starts.
            emit('state', state=controller.end_session())
        from .. import diagnostics
        result = diagnostics.export(
            camera=camera,
            progress=lambda message: emit('diagnostic-progress', message=message, request_id=request_id),
        )
        last_diagnostic = result
        emit('diagnostic-result', result=result, request_id=request_id)

    def emit_guided(status, **values):
        payload = dict(status=status, request_id=guided_session.get('request_id') if guided_session else None)
        if guided_session is not None:
            payload['operation_id'] = guided_session['operation_id']
        payload.update(values)
        emit('guided-state', request_id=payload.pop('request_id', None), **payload)

    def guided_event(name, elapsed):
        definitions = {
            'waiting_msc': ('waiting_msc', 'wait_msc', 'wait', 'waiting', 'timer'),
            'unplug_required': ('unplug_required', 'usb_unplug', 'wait', 'waiting', 'user'),
            'user_action_required': ('waiting_user', 'user_switch', 'acceptance', 'waiting', 'user'),
            'disconnect_observed': ('disconnected', 'usb_disconnected', 'restart', 'observed', 'device'),
            'reconnect_observed': ('reconnected', 'msc_returned', 'restart', 'observed', 'device'),
        }
        status, step, phase, event_status, source = definitions[name]
        guided_session['events'].append(dict(step_id=step, phase=phase, status=event_status,
                                             source=source, elapsed_ms=elapsed))
        emit_guided(status)

    def guided_job_alive():
        return guided_thread is not None and guided_thread.is_alive()

    def _collect_guided():
        nonlocal last_diagnostic
        if guided_session.get('recorder') is None:
            # Never turn a recovered session with missing identity into a
            # successful camera-only report with no operation history.
            result = dict(status='error', camera=True, mode='guided',
                          operation_id=guided_session.get('operation_id'),
                          error='录制身份缺失，未读取或清理机内历史。')
            guided_session['collecting'] = False
            last_diagnostic = result
            emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
            emit_guided('awaiting_retrieval', error='录制身份恢复不完整，未读取或清理机内历史。请重启应用恢复后再取回。')
            return
        reusable = (guided_session.get('recorder') is not None
                    and getattr(controller.service, 'connected', False)
                    and controller.service._session_backend is not None)
        if reusable:
            emit('diagnostic-progress', message='正在复用当前相机服务连接，直接停止记录并取回日志…')
            try:
                result = controller.service.collect_operation_log(
                    operation_id=guided_session['operation_id'], recorder=guided_session['recorder'],
                    progress=lambda message: emit('diagnostic-progress', message=message,
                                                  request_id=guided_session.get('request_id')),
                    guided_events=guided_session['events'])
            except (Exception, KeyboardInterrupt) as exc:
                if guided_session.get('recorder', {}).get('stop_attempted'):
                    guided_session['collecting'] = False
                    result = dict(status='error', camera=True, mode='guided',
                        operation_id=guided_session['operation_id'],
                        recorder_stop_attempted=True,
                        error=str(exc).strip() or '停止请求结果未确认。')
                    last_diagnostic = result
                    emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                    emit_guided('awaiting_retrieval', error='停止请求已发起，但结果未确认。为防止重复停止，未自动重连或重试；请保留现场并联系维护者。')
                    return
                # Only a pre-STOP transport failure is safe to retry through a
                # fresh MSC session. The durable recorder marker above prevents
                # a lost STOP response from being sent again automatically.
                emit('diagnostic-progress', message='当前服务连接不可用，改为等待 MSC 后取回已保存日志…')
            else:
                last_diagnostic = result
                if result.get('status') == 'collected':
                    guided_session['recorder'] = None
                    emit('state', state=controller.end_session())
                    emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                    emit_guided('collected')
                elif result.get('recorder_stop_attempted'):
                    guided_session['collecting'] = False
                    emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                    emit_guided('awaiting_retrieval', error='停止请求已发起，但结果未确认。为防止重复停止，未自动重连或重试；请保留现场并联系维护者。')
                else:
                    # export() returns pre-STOP USB failures as incomplete
                    # instead of raising. Do not keep reusing that dead session.
                    emit('diagnostic-progress', message='本次尚未发起停止请求；关闭失效连接，改为等待稳定 MSC 后取回…')
                if result.get('status') == 'collected' or result.get('recorder_stop_attempted'):
                    return
        from ..guided_diagnostics import stable_msc_observed
        from .. import diagnostics
        for attempt in range(1, 4):
            emit('state', state=controller.end_session())
            if not stable_msc_observed(timeout_seconds=120):
                guided_session['collecting'] = False
                emit_guided('awaiting_retrieval', error='记录已保存在相机。请重新连接 USB；如已关机请先开机。等待 MSC 后再次取回，不需要重新复现。')
                return
            emit('diagnostic-progress', message='相机 MSC 已连续稳定出现，等待 USB 服务完成枚举…')
            time.sleep(2.0)
            emit('diagnostic-progress', message='正在重新建立采集服务并停止机内记录（第 %d/3 次连接）…' % attempt)
            try:
                result = diagnostics.export(
                    camera=True, operation_id=guided_session['operation_id'], mode='guided',
                    guided_events=guided_session['events'],
                    recorder=guided_session.get('recorder'),
                    progress=lambda message: emit('diagnostic-progress', message=message, request_id=guided_session.get('request_id')),
                )
            except (Exception, KeyboardInterrupt) as exc:
                if guided_session.get('recorder', {}).get('stop_attempted') or attempt == 3:
                    result = dict(status='error', camera=True, mode='guided',
                        operation_id=guided_session['operation_id'],
                        recorder_stop_attempted=bool(guided_session.get('recorder', {}).get('stop_attempted')),
                        error=str(exc).strip() or '诊断导出未完成。')
                    last_diagnostic = result
                    guided_session['collecting'] = False
                    emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                    detail = ('停止请求结果未确认。未自动重试；请保留现场并联系维护者。'
                              if result.get('recorder_stop_attempted') else
                              '取回中断：%s 机内记录仍保留，可在 MSC 稳定后重试，不需要重新录制。' % (
                                  str(exc).strip() or '相机连接不可用。'))
                    emit_guided('awaiting_retrieval', error=detail)
                    return
                emit('diagnostic-progress', message='第 %d 次连接未完成且停止请求尚未发起；释放会话，继续等待设备重枚举…' % attempt)
                continue
            last_diagnostic = result
            if result.get('status') == 'collected':
                guided_session['recorder'] = None
                emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                emit_guided('collected')
                return
            if result.get('recorder_stop_attempted') or attempt == 3:
                guided_session['collecting'] = False
                emit('diagnostic-result', result=result, request_id=guided_session.get('request_id'))
                detail = ('停止请求已发起，但结果未确认。未自动重试；请保留现场并联系维护者。'
                          if result.get('recorder_stop_attempted') else
                          '机内记录尚未完整取回。请确认相机仍连接且 MSC 稳定后重试；不需要重新复现。')
                emit_guided('awaiting_retrieval', error=detail)
                return
            emit('diagnostic-progress', message='第 %d 次连接未完成且停止请求尚未发起；继续等待设备重枚举后重试…' % attempt)

    def run_collect_guided():
        try:
            _collect_guided()
        except (Exception, KeyboardInterrupt) as exc:
            if guided_session is None:
                emit_guided('error', error=str(exc).strip() or '日志取回未完成。')
                return
            guided_session['collecting'] = False
            emit_guided('awaiting_retrieval', error='日志尚未完整取回：%s 原记录仍保留，可直接重试。' % (
                str(exc).strip() or '相机连接中断。'))

    def collect_guided():
        nonlocal guided_thread
        if guided_session is None:
            raise ValueError('请先开始引导复现。')
        if guided_job_alive():
            raise ValueError('当前采集步骤尚未结束，请稍候或先取消重新采集。')
        guided_session['collecting'] = True
        if guided_cancel is not None:
            guided_cancel.set()
        emit_guided('collecting')
        emit('diagnostic-progress', message='正在结束录制；记录已保存在相机。如刚重新接线或开机，请稍等。')
        guided_thread = Thread(target=run_collect_guided, daemon=True)
        guided_thread.start()

    def watch_guided():
        from ..guided_diagnostics import watch
        try:
            outcome = watch(guided_cancel, guided_event)
            if outcome == 'reconnected':
                emit_guided('awaiting_retrieval', error='USB 已稳定恢复。请在相机旁确认连接无误后，点击“结束录制”取回日志。')
            elif outcome == 'timeout':
                emit_guided('timeout')
            elif outcome == 'cancelled' and not guided_session.get('collecting'):
                emit_guided('cancelled')
        except (Exception, KeyboardInterrupt) as exc:
            emit_guided('error', error=str(exc).strip() or '复现监测未完成。')

    def emit_recording_ready(session):
        recorder = session.get('recorder') or {}
        if recorder.get('sampling_pending'):
            emit_guided('waiting_user', error='日志服务已启动；创意外观探针正在初始化，可继续操作。')
        elif recorder.get('sampling_degraded'):
            emit_guided('waiting_user', error='日志服务已启动；创意外观采样失败（ERR=%s），仍可继续操作，错误已保存。' %
                        recorder.get('sampling_error'))
        else:
            emit_guided('waiting_user')

    def start_guided(request_id):
        nonlocal guided_thread, guided_cancel, guided_session, last_diagnostic
        if controller.busy or (thread is not None and thread.is_alive()) or (guided_thread is not None and guided_thread.is_alive()):
            raise ValueError('请先等待当前相机操作或复现监测结束。')
        if guided_session is not None:
            raise ValueError('检测到上次未取回的机内记录；请先结束录制取回，不要开始新的录制。')
        if not controller.compatible or not getattr(controller.service, 'connected', False):
            raise ValueError('请先连接并检查相机，确认采集服务正常后再开始录制。')
        guided_cancel = Event()
        guided_session = dict(operation_id=str(uuid.uuid4()), request_id=request_id, started=time.monotonic(), events=[], collecting=False)
        last_diagnostic = None
        emit_guided('starting')
        guided_session['recorder'] = controller.service.start_operation_log(
            operation_id=guided_session['operation_id'],
            progress=lambda message: emit('diagnostic-progress', message=message))
        guided_session['events'].append(dict(step_id='operation_recorder', phase='connect',
                                             status='passed', source='device', elapsed_ms=0))
        # Keep the authenticated service session open. The camera remains usable
        # for on-screen operation in this state; collection reuses this backend.
        # If the user unplugs/reboots, collection falls back to persisted MSC.
        emit_recording_ready(guided_session)

    def run_restart_guided(session, cancel, request_id):
        nonlocal guided_session, last_diagnostic
        old_result = None
        old_log_cleared = False
        try:
            if not getattr(controller.service, 'connected', False):
                emit('diagnostic-progress', message='正在连接相机并恢复采集服务；此步骤不会开始新录制…',
                     request_id=request_id)
                controller.service.perform('inspect', progress=lambda message: emit(
                    'diagnostic-progress', message=message, request_id=request_id))
                with controller.lock:
                    controller.compatible = True
                    controller.phase = 'home'
                    controller.revision += 1
                emit('state', state=controller.snapshot(), request_id=request_id)
                emit('diagnostic-progress', message='相机服务已恢复，准备处理旧日志…', request_id=request_id)
            if cancel.is_set():
                emit_guided('awaiting_retrieval', error='已取消重新录制；原日志未清理，可取回上传或再次重新录制。')
                return
            recorder = session.get('recorder')
            if recorder is not None:
                emit('diagnostic-progress', message='正在停止旧记录并取回相机内日志；完成后才会清理…',
                     request_id=request_id)
                old_result = controller.service.collect_operation_log(
                    operation_id=session['operation_id'], recorder=recorder,
                    progress=lambda message: emit('diagnostic-progress', message=message,
                                                  request_id=request_id),
                    guided_events=session['events'])
                if old_result.get('status') != 'collected':
                    session['collecting'] = False
                    emit('diagnostic-result', result=old_result, request_id=request_id)
                    emit_guided('awaiting_retrieval', error='旧日志尚未完整取回，未开始新录制。请先重试取回。')
                    return
                session['recorder'] = None
                old_log_cleared = True
                last_diagnostic = old_result
                emit('diagnostic-progress', message='旧日志已取回并清理，准备启动新录制…', request_id=request_id)
            if cancel.is_set():
                if old_result is not None:
                    emit('diagnostic-result', result=old_result, request_id=request_id)
                emit_guided('cancelled', error='已取消重新录制；旧日志已保留在本机，可直接上传。')
                if guided_session is session:
                    guided_session = None
                return

            session.update(operation_id=str(uuid.uuid4()), request_id=request_id,
                           started=time.monotonic(), events=[], collecting=False, recorder=None)
            last_diagnostic = None
            emit_guided('starting')
            emit('diagnostic-progress', message='正在启动新的机内记录，请稍候…', request_id=request_id)
            session['recorder'] = controller.service.start_operation_log(
                operation_id=session['operation_id'],
                progress=lambda message: emit('diagnostic-progress', message=message,
                                              request_id=request_id))
            session['events'].append(dict(step_id='operation_recorder', phase='connect',
                                          status='passed', source='device', elapsed_ms=0))
            if cancel.is_set():
                session['collecting'] = True
                emit_guided('collecting')
                emit('diagnostic-progress', message='已收到取消请求，正在正常停止新记录并取回日志…',
                     request_id=request_id)
                _collect_guided()
            else:
                emit_recording_ready(session)
        except (Exception, KeyboardInterrupt) as exc:
            session['collecting'] = False
            detail = str(exc).strip() or '重新采集未完成。'
            if session.get('recorder') is not None and not old_log_cleared:
                emit_guided('awaiting_retrieval', error='重新录制已停止，原日志未清理：%s' % detail)
            elif session.get('recorder') is not None:
                emit_guided('awaiting_retrieval', error='新记录已保留，可直接取回：%s' % detail)
            else:
                if old_result is not None:
                    emit('diagnostic-result', result=old_result, request_id=request_id)
                emit_guided('error', error='未开始新录制：%s' % detail)
                if guided_session is session:
                    guided_session = None
        finally:
            session['restarting'] = False

    def restart_guided(request_id):
        nonlocal guided_thread, guided_cancel, last_diagnostic
        if controller.busy or (thread is not None and thread.is_alive()) or (guided_thread is not None and guided_thread.is_alive()):
            raise ValueError('请先等待当前相机操作结束。')
        if guided_session is None:
            start_guided(request_id)
            return
        guided_cancel = Event()
        guided_session['restarting'] = True
        guided_session['collecting'] = False
        guided_session['request_id'] = request_id
        last_diagnostic = None
        emit_guided('restarting')
        emit('diagnostic-progress', message='正在准备重新录制；会先取回并清理旧日志，再启动新记录。',
             request_id=request_id)
        guided_thread = Thread(target=run_restart_guided,
                               args=(guided_session, guided_cancel, request_id), daemon=True)
        guided_thread.start()

    def cancel_guided():
        if guided_cancel is None or guided_session is None:
            raise ValueError('当前没有正在进行的复现监测。')
        if guided_job_alive():
            guided_cancel.set()
            guided_session['cancel_requested'] = True
            if guided_session.get('restarting'):
                emit_guided('cancelling')
                emit('diagnostic-progress', message='已请求取消；等待当前 USB 请求返回，不会强制断开相机或重发操作。')
            else:
                emit('diagnostic-progress', message='正在安全完成当前停止和取回步骤，请稍候；不会强制中断 USB。')
            return
        # Idle recording has no worker. Cancelling uses the normal joined stop,
        # retrieval and cleanup path instead of leaving the camera logger alive.
        collect_guided()

    def upload_feedback(description, images=None, request_id=None):
        nonlocal last_diagnostic
        # A unified collect-then-upload caller may already have queued its
        # upload command. Let the background collector publish the report first.
        if guided_job_alive() and guided_session is not None and (
                guided_session.get('collecting') or guided_session.get('restarting')):
            guided_thread.join()
        if images is None:
            images = []
        if not isinstance(images, list) or len(images) > 12:
            raise ValueError('一次最多上传 12 张图片。')
        for item in images:
            if not isinstance(item, str):
                raise ValueError('图片路径无效。')
            path = Path(item)
            if path.is_symlink() or not path.is_file():
                raise ValueError('图片必须是普通文件。')
        from .. import feedback
        if last_diagnostic is None:
            # Text/image-only feedback is a supported first-class flow. Keep a
            # synthetic local operation identity so network retries are idempotent.
            last_diagnostic = dict(status='not_requested', path=None,
                                   operation_id=str(uuid.uuid4()), mode='not_requested', events=[])
        include_artifact = bool(last_diagnostic.get('status') == 'collected' and last_diagnostic.get('path'))
        result = feedback.upload_feedback(last_diagnostic, description,
                                          include_artifact=include_artifact, images=images)
        emit('feedback-result', result=result, request_id=request_id)

    if guided_session is not None:
                emit_guided('awaiting_retrieval', error='检测到上次断电前已保存的机内记录。请连接并检查相机后，点击“结束录制”或“上传意见反馈”取回；不需要重新复现。')

    emit('state', state=controller.snapshot())
    for raw in sys.stdin:
        request_id = None
        try:
            request = decode_request(raw)
            request_id = request.get('request_id')
            command = request['command']
            if command == 'check_updates':
                if controller.busy or (thread is not None and thread.is_alive()) or (guided_thread is not None and guided_thread.is_alive()) or (update_thread is not None and update_thread.is_alive()):
                    raise ValueError('请等待当前相机操作或更新检查结束。')
                update_thread = Thread(target=run_update, args=(request_id,), daemon=True)
                update_thread.start()
                continue
            if command == 'guided_diagnostic':
                try:
                    action = request.get('action')
                    if action == 'start':
                        start_guided(request_id)
                    elif action == 'restart':
                        restart_guided(request_id)
                    elif action == 'collect':
                        collect_guided()
                    elif action == 'cancel':
                        cancel_guided()
                    else:
                        raise ValueError('未知复现操作。')
                except (Exception, KeyboardInterrupt) as exc:
                    emit_guided('error', error=str(exc).strip() or '复现操作未完成。')
                continue
            if command == 'upload_feedback':
                try:
                    upload_feedback(request.get('description', ''), request.get('images', []), request_id=request_id)
                except (Exception, KeyboardInterrupt) as exc:
                    emit('feedback-result', result=dict(status='error',
                        error=str(exc).strip() or '云端上传未完成。'), request_id=request_id)
                continue
            if command == 'diagnostic':
                if type(request.get('camera')) is not bool:
                    raise ValueError('诊断模式无效。')
                try:
                    run_diagnostic(request['camera'], request_id)
                except (Exception, KeyboardInterrupt) as exc:
                    emit('diagnostic-result', result=dict(
                        status='error', camera=request['camera'],
                        error=str(exc).strip() or '诊断导出未完成。'), request_id=request_id)
                continue
            if command in ('inspect', 'unlock', 'close', 'backup', 'restore', 'takeover', 'diagnostic', 'guided_diagnostic') and update_thread is not None and update_thread.is_alive():
                raise ValueError('正在检查更新，请等待完成后再操作相机。')
            if command == 'takeover':
                controller.reserve_takeover(request.get('confirmation'), request.get('confirmed'))
                emit('state', state=controller.snapshot(), request_id=request_id)
                thread = Thread(target=run, args=(request_id,), daemon=True)
                thread.start()
                continue
            if command in ('inspect', 'unlock', 'close', 'backup', 'restore', 'update_fl23', 'update_ftp'):
                if command=='update_fl23':controller.reserve_fl23_update()
                elif command=='update_ftp':controller.reserve_ftp_update()
                else:controller.reserve(command, request.get('backup_id') if command == 'restore' else None)
                emit('state', state=controller.snapshot(), request_id=request_id)
                thread = Thread(target=run, args=(request_id,), daemon=True)
                thread.start()
                continue
            if command == 'environment':
                state = controller.check_environment()
            elif command == 'acknowledge':
                state = controller.acknowledge_notice(request.get('value'))
            elif command == 'prepare':
                state = controller.begin_preparation()
            elif command == 'confirm':
                state = controller.confirm(request.get('id'), request.get('value'))
            elif command == 'confirm_preparation':
                state = controller.confirm_preparation(request.get('value'))
            elif command == 'restart_preparation':
                state = controller.restart_preparation()
            elif command == 'select_feature':
                state = controller.set_feature_selection(request.get('id'), request.get('value'))
            elif command == 'select':
                state = controller.select_features(request.get('mode'))
            elif command == 'acknowledge_check':
                state = controller.acknowledge_check()
            elif command == 'shutdown':
                if guided_job_alive():
                    guided_thread.join()
                state = controller.end_session()
                emit('state', state=state, request_id=request_id)
                break
            elif command == 'cancel':
                state = controller.cancel_operation()
            elif command == 'home':
                state = controller.home()
            else:
                raise ValueError('未知真实设备命令。')
            emit('state', state=state, request_id=request_id)
        except (ValueError, TypeError, KeyError):
            emit('error', message='当前操作不可用，请先完成前置步骤或等待当前操作结束。', request_id=request_id)
    controller.cancel.set()
    if guided_cancel is not None:
        guided_cancel.set()
    if thread is not None:
        thread.join(timeout=5)
    if guided_thread is not None:
        guided_thread.join(timeout=5)
    if update_thread is not None:
        update_thread.join(timeout=25)
    try:
        from ..usb.backend import close_capture_session
    except ImportError:
        close_capture_session = lambda: None
    close_capture_session()


if __name__ == '__main__':
    main()
