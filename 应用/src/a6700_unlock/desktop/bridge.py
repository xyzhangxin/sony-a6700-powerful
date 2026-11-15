"""Line-delimited JSON worker for the native desktop preview.

Only a fixed simulator can be selected. No request can specify a backend,
profile, pathname, shell command or physical-device mode.
"""
import json
import sys
import tempfile
from threading import Lock, Thread

from .controller import DesktopController
from .protocol import decode_request, event


def main():
    output_lock = Lock()
    def emit(kind, request_id=None, **values):
        with output_lock:
            print(json.dumps(event(kind, request_id=request_id, **values), ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix='a6700-desktop-simulation-') as directory:
        controller = DesktopController(directory)
        thread = None
        def run(request_id):
            def progress(message):
                emit('progress', message=message, request_id=request_id)
                # Pace the synthetic workflow so progress and cancellation can
                # be exercised by a person. This is not device timing.
                controller.cancel.wait(2.5)
            try:
                emit('state', state=controller.run_reserved(progress), request_id=request_id)
            except Exception:
                # Unexpected application faults must not strand a busy screen.
                with controller.lock:
                    controller.busy = False
                    controller.compatible = False
                    controller.confirmed.clear()
                    controller.features = []
                    controller.phase = 'result'
                    controller.revision += 1
                    controller.result = dict(status='error', title='模拟服务发生错误',
                        detail='重新检查环境或切换场景后再试。', success=False,
                        error='', simulation=True, device_access=False, write_attempted=False)
                emit('state', state=controller.snapshot(), request_id=request_id)

        emit('state', state=controller.snapshot())
        for raw in sys.stdin:
            request_id = None
            try:
                request = decode_request(raw)
                request_id = request.get('request_id')
                command = request['command']
                if command == 'prepare_and_inspect':
                    controller.prepare_and_reserve(request.get('acknowledged'), request.get('prepared'))
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
                if command == 'reset':
                    controller.reset(request.get('scenario', 'enabled'))
                    state = controller.check_environment()
                elif command == 'environment':
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
                    state = controller.end_session()
                    emit('state', state=state, request_id=request_id)
                    break
                elif command == 'cancel':
                    state = controller.cancel_operation()
                elif command == 'disconnect':
                    state = controller.disconnect()
                elif command == 'reconnect':
                    state = controller.reconnect()
                elif command == 'home':
                    state = controller.home()
                else:
                    raise ValueError('未知预览命令。')
                emit('state', state=state, request_id=request_id)
            except (ValueError, TypeError, KeyError):
                emit('error', message='当前操作不可用，请先完成前置步骤或等待当前操作结束。', request_id=request_id)
        controller.cancel.set()
        if thread is not None:
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
