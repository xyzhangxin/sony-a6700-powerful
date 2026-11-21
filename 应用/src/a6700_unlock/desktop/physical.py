"""Physical USB service adapter for the desktop controller.

The controller remains presentation/state only.  This adapter constructs the
real catalog/profile, delegates to :func:`a6700_unlock.operations.execute`,
and therefore inherits the physical device lock, sparse preflight, journal,
writer and independent readback workflow.
"""
from __future__ import annotations

from pathlib import Path

from ..backup import default_backup_dir, find_backup, list_backups, load_backup
from ..catalog import load_catalog
from ..device_profile import load_device_profile


def _build_custom_sound_row(matched_guards, package_result=None, *, installable=False):
    """Describe inspected custom-shutter-sound firmware state separately from sound."""
    mode = 'not-installed'
    state = 'disabled'
    pkg_row = None
    if isinstance(package_result, dict):
        for row in package_result.get('plan', {}).get('features', ()):
            if row.get('id') == 'custom_shutter_sound':
                pkg_row = row
                break
    if isinstance(package_result, dict) and package_result.get('mode') == 'custom-sound-combined-stage':
        state = 'enabled'
        mode = 'custom-sound-combined-stage'
        installable = False
        reason = '已检测到旧研究底座十槽自定义快门声；请通过共享调试台迁移至统一 /setting/fl23。'
    elif pkg_row is not None and isinstance(package_result, dict) and package_result.get('status') == 'inspected':
        state = pkg_row.get('state', 'unknown')
        mode = 'fl23-unified-carrier' if installable or state in ('enabled', 'partial') else 'not-installed'
        if state == 'enabled':
            runtime = package_result.get('custom_sound_runtime', {})
            if runtime.get('status') == 'verified':
                reason = '十槽自定义快门声已安装并通过本次启动的加载校验，可在快门声市场管理音频。'
            else:
                state = 'error'
                reason = runtime.get('error') or '十槽自定义快门声已安装，当前启动的加载校验尚未通过。'
        elif state == 'partial':
            reason = '检测到部分自定义快门声状态或菜单改动，可直接重新解锁补全或关闭彻底清除。'
        elif state == 'error':
            reason = (package_result.get('custom_sound_reason') or pkg_row.get('reason')
                      or '十槽自定义快门声状态异常，可直接关闭以彻底清理。')
        else:
            reason = '尚未启用自定义快门声；解锁时将自动带上「电子快门声音」并写入 23 扇区 10 槽菜单。'
    elif isinstance(package_result, dict) and {9, 12}.issubset(matched_guards) and package_result.get('status') == 'error':
        state = 'error'
        mode = 'binding-error'
        reason = package_result.get('error') or '十槽自定义快门声加载器校验失败。'
    elif {9, 12} & matched_guards:
        state = 'partial'
        mode = 'partial'
        reason = '检测到部分自定义快门声菜单改动，请检查或关闭以彻底恢复。'
    else:
        reason = ('尚未启用自定义快门声；勾选解锁将自动包含「电子快门声音」。'
                  if installable
                  else '尚未安装十槽自定义快门声固件；需先解锁「电子快门声音」。')
    return dict(
        id='custom_shutter_sound',
        name='自定义快门声',
        state=state,
        requires=['sound'],
        available=bool(installable),
        installable=bool(installable),
        mode=mode,
        reason=reason,
    )


def _verified_custom_sound_binding(backend, profile_digest, device_identity):
    from ..custom_sound_executor import validate_slot_binding
    read_binding = getattr(backend, 'read_custom_sound_binding', None)
    if not callable(read_binding):
        raise ValueError('当前适配器尚不支持机身／加载器绑定校验。')
    if not device_identity:
        raise ValueError('当前会话缺少认证前机身身份，请重新连接相机。')
    binding = read_binding()
    validated = validate_slot_binding(binding, profile_digest)
    if binding.device_identity_sha256 != device_identity:
        raise ValueError('加载器绑定与认证前机身身份不一致。')
    return binding, validated


class PhysicalService:
    """Execute real USB operations while exposing the desktop service shape."""

    def __init__(self, state_dir=None, backend_factory=None, backup_dir=None):
        self.profile = load_device_profile(load_catalog())
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.backup_dir = (Path(backup_dir) if backup_dir is not None
                           else default_backup_dir())
        self.backend_factory = backend_factory
        from ..fl23_package import availability, custom_sound_availability
        if backend_factory is None:
            self.fl23_available, self.names_available = availability()
            self.custom_sound_available = custom_sound_availability()
        else:
            self.fl23_available, self.names_available, self.custom_sound_available = False, False, False
        from ..capability_packages import builtin_adapter_set
        self.adapter_set = builtin_adapter_set(
            self.profile,
            fl23_available=self.fl23_available,
            names_available=self.names_available,
            custom_sound_available=self.custom_sound_available,
            include_creative_looks=self.fl23_available or self.names_available,
            include_custom_sound=any(f['id'] == 'sound' for f in self.profile.catalog.data['features']))
        self.connected = False
        self.simulation = False
        # Keep one authenticated USB session open for this app process. The
        # first operation asks for authorization; inspection and the chosen
        # write reuse it. Unlock/close then end the session before power-off.
        self._session_backend = None
        self._session_access = None
        self._session_lock = None
        self._last_run_directory = None

    def list_backups(self):
        return list_backups(self.backup_dir)

    def reset_capture(self):
        self.close_session()

    def close_session(self, *, verify_msc=False):
        backend, self._session_backend = self._session_backend, None
        access, self._session_access = self._session_access, None
        lock, self._session_lock = self._session_lock, None
        self.connected = False
        exit_error = None
        cleanup_status = None
        cleanup_warning = None
        try:
            if access is not None:
                if getattr(backend, '_fl23_transport_uncertain', False) is True:
                    access.release_access(send_stop=False)
                else:
                    access.release_access()
            cleanup_status = getattr(backend, 'cleanup_status', None)
            cleanup_warning = getattr(backend, 'cleanup_warning', None)
            if verify_msc and cleanup_status != 'msc-observed' and not cleanup_warning:
                from ..usb.legacy import normal_mode_observed
                if normal_mode_observed():
                    cleanup_status = 'msc-observed'
                else:
                    cleanup_status = 'unconfirmed'
                    cleanup_warning = '未检测到相机恢复到 MSC。请检查 USB 连接和相机的 USB 模式。'
            return dict(cleanup_status=cleanup_status, cleanup_warning=cleanup_warning)
        except BaseException as exc:
            exit_error = str(exc) or type(exc).__name__
            raise
        finally:
            try:
                if backend is not None and self._last_run_directory is not None:
                    from ..operations import save_json
                    try:
                        save_json(self._last_run_directory / 'session-exit.json', dict(
                            cleanup_status='unconfirmed' if exit_error else cleanup_status,
                            cleanup_warning=exit_error or cleanup_warning))
                    except OSError:
                        pass
            finally:
                self._last_run_directory = None
                if lock is not None:
                    lock.__exit__(None, None, None)
                try:
                    import gc
                    from ..usb.legacy import reset_libusb_backend
                    reset_libusb_backend()
                    gc.collect()
                except Exception:
                    pass

    def start_operation_log(self, operation_id, progress):
        """Reuse the inspection session and its lock; never reauthenticate at Start."""
        if self._session_backend is None or not self.connected:
            raise ValueError('请先连接并检查相机，再开始录制。')
        from ..operations import default_state_dir
        from .. import diagnostics
        try:
            return diagnostics.start_operation_log(
                state_dir=self.state_dir if self.state_dir is not None else default_state_dir(),
                operation_id=operation_id, progress=progress, backend=self._session_backend)
        except BaseException:
            self.close_session()
            raise

    def collect_operation_log(self, operation_id, recorder, progress, guided_events=None):
        """Stop/recover the recorder over the still-open inspection session."""
        if self._session_backend is None or not self.connected:
            raise ValueError('当前没有可复用的相机服务连接。')
        from ..operations import default_state_dir
        from .. import diagnostics
        try:
            result = diagnostics.export(
                camera=True,
                state_dir=self.state_dir if self.state_dir is not None else default_state_dir(),
                operation_id=operation_id, mode='guided', guided_events=guided_events,
                recorder=recorder, progress=progress, backend=self._session_backend)
        except BaseException:
            self.close_session()
            raise
        return result

    def _refresh_adapter_set(self):
        from ..capability_packages import builtin_adapter_set
        self.adapter_set = builtin_adapter_set(
            self.profile, fl23_available=self.fl23_available,
            names_available=self.names_available,
            custom_sound_available=self.custom_sound_available,
            include_creative_looks=self.fl23_available or self.names_available,
            include_custom_sound=any(f['id'] == 'sound' for f in self.profile.catalog.data['features']))

    def perform(self, action, progress=lambda _message: None, backup_id=None, features=None):
        self._refresh_adapter_set()
        if action not in ("inspect", "unlock", "close", "backup", "restore"):
            raise ValueError("未知设备操作；当前支持检查、解锁、关闭、功能状态备份与恢复")
        # Reject package feature injection before acquiring/authenticating USB.
        # The fixed-sector backend cannot install startup packages.
        package_ids={'fl23'} if self.fl23_available else set()
        if self.names_available:package_ids.add('creative_look_names')
        if self.custom_sound_available:package_ids.add('custom_shutter_sound')
        supported=set(self.profile.catalog.available_features)|package_ids
        if features is not None and not set(features) <= supported:
            raise ValueError('所选功能暂未开放实机写入。')
        if features is not None and set(features)&package_ids and action not in ('unlock','close'):
            raise ValueError('创意外观／自定义快门声选项当前仅支持解锁或关闭。')
        capability_resolution = None
        effective_features = list(features) if features is not None else None
        if features is not None:
            from ..capability_packages import resolve_capability_selection
            try:
                capability_resolution = resolve_capability_selection(self.adapter_set, features, action)
            except ValueError as exc:
                raise ValueError('所选功能暂未开放实机写入。') from exc
            if action == 'unlock':
                effective_features = list(capability_resolution['resolved_features'])
        from ..operations import (_execute_session, _backup_session, _restore_session,
                                  default_state_dir, device_lock)
        directory = self.state_dir if self.state_dir is not None else default_state_dir()
        if self._session_backend is None:
            # Claim the shared lock before capture/auth and keep it until the
            # authenticated session is closed. Calls below must not lock twice.
            lock = device_lock(directory, observe_returned_settings=True)
            lock.__enter__()
            self._session_lock = lock
            try:
                from ..device_access import UsbDeviceAccess
                access = UsbDeviceAccess(self.profile, self.backend_factory)
                self._session_access = access
                access.prepare_access(progress, capture=True)
                identity_reader = getattr(access.backend, 'read_custom_sound_identity', None)
                if callable(identity_reader):
                    try:
                        identity_reader()
                    except Exception:
                        pass
                backend = access.open_transport(progress)
                self._session_backend = backend
                from ..close_setting import verify_pending_settings
                verify_pending_settings(backend,directory)
            except BaseException:
                self.close_session()
                raise
        backend = self._session_backend
        backend.progress = progress
        try:
            if effective_features is not None and set(effective_features)&package_ids and set(effective_features)-package_ids:
                result=self._perform_combined(backend,action,directory,progress,effective_features)
            elif effective_features is not None and set(effective_features)&package_ids:
                from ..fl23_package import perform
                result=perform(backend,action,directory,progress,features=effective_features)
            elif action == "backup":
                result = _backup_session(self.profile, backend, self.backup_dir,
                                         directory, progress, manage_backend=False)
            elif action == "restore":
                source = load_backup(find_backup(self.backup_dir, backup_id), self.profile)
                result = _restore_session(self.profile, backend, source, directory,
                                          progress, features=effective_features, manage_backend=False)
            else:
                blocked_close = None
                if (action == 'close' and effective_features is not None
                        and 'sound' in effective_features and self.custom_sound_available
                        and hasattr(backend, '_command')):
                    from ..fl23_package import perform
                    pkg_check = perform(backend, 'inspect', directory, progress)
                    cs_row = next((r for r in pkg_check.get('plan', {}).get('features', ())
                                   if r.get('id') == 'custom_shutter_sound'), None)
                    if pkg_check.get('status') != 'inspected' or (cs_row and cs_row.get('state') != 'disabled'):
                        blocked_close = dict(
                            action='close',
                            status='close-preparation-required',
                            device_access=True,
                            write_attempted=False,
                            settings_write_attempted=False,
                            verified=False,
                            full_backup=False,
                            run_directory=pkg_check.get('run_directory') or str(directory),
                            plan=pkg_check.get('plan') or dict(compatible=True, features=[], selected_features=list(effective_features)),
                            error='自定义快门声已启用；请先或同时关闭「自定义快门声」，再关闭「电子快门声音」。',
                        )
                if blocked_close is not None:
                    result = blocked_close
                else:
                    result = _execute_session(self.profile, backend, action, directory,
                                              progress, features=effective_features, manage_backend=False)
                if blocked_close is None and action=='inspect' and result.get('status')=='inspected' and self.fl23_available:
                    matched_guards = {row['index'] for row in result.get('plan', {}).get('guard_compatibility', ())}
                    read_binding = getattr(backend, 'read_custom_sound_binding', None)
                    device_identity = getattr(backend, '_custom_sound_identity', None)
                    package_result = None
                    if hasattr(backend, '_command'):
                        from ..fl23_package import perform
                        package_result = perform(backend, 'inspect', directory, progress)
                        if package_result.get('status') == 'inspected':
                            package_result.setdefault('mode', 'fl23-unified-carrier')
                            installed = any(
                                row.get('id') == 'custom_shutter_sound' and row.get('state') == 'enabled'
                                for row in package_result.get('plan', {}).get('features', ()))
                            if installed:
                                progress('正在核对本次开机的自定义快门声加载状态…')
                                try:
                                    _verified_custom_sound_binding(backend, self.profile.digest, device_identity)
                                except Exception as exc:
                                    error = str(exc).strip() or type(exc).__name__
                                    package_result['custom_sound_runtime'] = dict(
                                        status='error', error=f'十槽自定义快门声已安装，加载校验失败。{error}')
                                    if (getattr(backend, '_fl23_transport_uncertain', False) is True
                                            or getattr(backend, '_custom_sound_service_restore_unconfirmed', False) is True):
                                        result['status'] = 'blocked'
                                        result['plan']['compatible'] = False
                                        result['error'] = error
                                else:
                                    package_result['custom_sound_runtime'] = dict(status='verified')
                    if ((package_result is None or package_result.get('status') != 'inspected')
                            and {9, 12}.issubset(matched_guards) and callable(read_binding) and device_identity):
                        try:
                            from ..creative_looks import FEATURES
                            progress('正在核对十槽自定义快门声与创意外观联合加载器状态…')
                            _, validated = _verified_custom_sound_binding(
                                backend, self.profile.digest, device_identity)
                            fl23_features = [dict(id='fl23', name='FL2／FL3 创意外观', state='enabled', available=True,
                                                  installed_revision=2, target_revision=2,
                                                  update_available=False, update_state='current')]
                            if self.names_available:
                                fl23_features.append(dict(id='creative_look_names',
                                                          name=FEATURES[1]['name'], state='enabled', available=True))
                            package_result = dict(action='inspect', status='inspected',
                                                  mode='custom-sound-combined-stage',
                                                  binding=validated,
                                                  plan=dict(compatible=True, features=fl23_features))
                        except BaseException as exc:
                            package_result = dict(action='inspect', status='error',
                                                  error=str(exc) or type(exc).__name__,
                                                  plan=dict(compatible=False, features=[]))
                    if package_result is None:
                        from ..fl23_package import perform
                        package_result = perform(backend, 'inspect', directory, progress)
                    result['fl23_inspection']=package_result
                    result['plan']['features'].extend(package_result.get('plan',{}).get('features',[]))
                    if package_result.get('status')!='inspected':
                        result['status']='blocked'
                        result['plan']['compatible']=False
                        result['error']=package_result.get('error','FL23 实机预检未通过')
                    if result.get('run_directory') and Path(result['run_directory']).is_dir():
                        from ..operations import save_json
                        extra_path = Path(result['run_directory']) / 'fl23-inspect-result.json'
                        if not extra_path.exists():
                            save_json(extra_path, package_result)
                if action == 'inspect' and (result.get('status') == 'inspected'
                                            or result.get('fl23_inspection') is not None):
                    matched_guards = {row['index'] for row in result.get('plan', {}).get('guard_compatibility', ())}
                    pkg = result.get('fl23_inspection')
                    if pkg is None and {9, 12}.issubset(matched_guards):
                        read_binding = getattr(backend, 'read_custom_sound_binding', None)
                        device_identity = getattr(backend, '_custom_sound_identity', None)
                        if callable(read_binding) and device_identity:
                            try:
                                _verified_custom_sound_binding(backend, self.profile.digest, device_identity)
                                pkg = dict(status='inspected', mode='custom-sound-combined-stage')
                            except BaseException as exc:
                                pkg = dict(status='error', error=str(exc) or type(exc).__name__)
                    result['custom_sound'] = _build_custom_sound_row(
                        matched_guards, pkg, installable=self.custom_sound_available)
        except BaseException:
            self.close_session()
            raise
        self._last_run_directory = Path(result['run_directory'])
        if action in ('unlock', 'close') or result.get('status') in (
                'blocked', 'error', 'uncertain', 'close-preparation-required',
                'close-preparation-uncertain', 'close-preparation-cancelled', 'close-preparation-restart-required'):
            try:
                self.close_session()
            except Exception as exc:
                # A service-exit failure must not erase the recorded write
                # and independent readback result. The lock is released in
                # close_session's finally block, and the next run reconnects.
                result['cleanup_status'] = 'unconfirmed'
                result['cleanup_warning'] = str(exc) or type(exc).__name__
            else:
                result['cleanup_status'] = getattr(backend, 'cleanup_status', None)
                result['cleanup_warning'] = getattr(backend, 'cleanup_warning', None)
        if capability_resolution is not None:
            result['capability_resolution'] = capability_resolution
        self.connected = self._session_backend is not None and result.get('status') in (
            'inspected', 'dry-run', 'completed', 'no-op', 'backup-completed', 'restored', 'settings-prepared')
        return result

    def read_audio_slots(self, progress=lambda _message: None):
        """Explicit fixed observation, never triggered by catalog rendering."""
        from ..operations import default_state_dir, device_lock
        from ..device_access import UsbDeviceAccess
        from ..close_setting import verify_pending_settings
        from ..custom_sound_executor import read_slot_records
        from ..custom_sound_slots import slot_inventory
        from ..custom_sound_native import load_custom_sound_reader
        # Missing/mismatched packaged assets must fail before USB takeover.
        load_custom_sound_reader()
        directory = self.state_dir if self.state_dir is not None else default_state_dir()
        owns_session = self._session_backend is None
        if owns_session:
            lock = device_lock(directory, observe_returned_settings=True)
            lock.__enter__()
            self._session_lock = lock
        elif not self.connected:
            self.close_session()
            raise ValueError('当前相机会话未就绪；请先重新连接相机。')
        try:
            if owns_session:
                access = UsbDeviceAccess(self.profile, self.backend_factory)
                self._session_access = access
                access.prepare_access(progress, capture=True)
                identity_reader = getattr(access.backend, 'read_custom_sound_identity', None)
                if callable(identity_reader):
                    try:
                        identity_reader()
                    except Exception:
                        pass
                backend = access.open_transport(progress)
                self._session_backend = backend
                self.connected = True
                verify_pending_settings(backend, directory)
            else:
                backend = self._session_backend
                backend.progress = progress
            progress('正在读取相机的 10 个声音槽位…')
            import uuid
            from ..operations import make_directory, save_json
            from ..custom_sound_executor import preserved_slot_backend
            run = Path(directory) / 'runs' / ('audio-inventory-' + uuid.uuid4().hex)
            make_directory(run)
            self._last_run_directory = run
            with preserved_slot_backend(backend, run / 'service') as observer:
                slots = slot_inventory(read_slot_records(observer))
            save_json(run / 'result.json', dict(status='observed', slots=slots,
                                               persistent_write_attempted=False))
        except BaseException:
            self.close_session()
            raise
        if owns_session:
            self.close_session()
        return dict(status='observed', slots=slots, device_access=True,
                    persistent_write_attempted=False,
                    cleanup_status=getattr(backend, 'cleanup_status', None))

    def execute_audio_import(self, plan, candidate, progress=lambda _message: None):
        """Run one exact custom-shutter-sound slot import using an active or fresh session."""
        if (not isinstance(plan, dict)
                or not plan.get("can_execute")
                or not plan.get("capability", {}).get("available")
                or not plan.get("capability", {}).get("implementation_ready")):
            raise ValueError("自定义快门声能力或导入执行器未就绪；拒绝进入相机执行。")
        from ..operations import default_state_dir, device_lock
        from ..device_access import UsbDeviceAccess
        from ..close_setting import verify_pending_settings
        import uuid

        directory = self.state_dir if self.state_dir is not None else default_state_dir()
        owns_session = self._session_backend is None
        if owns_session:
            lock = device_lock(directory, observe_returned_settings=True)
            lock.__enter__()
            self._session_lock = lock
        elif not self.connected:
            self.close_session()
            raise ValueError("当前相机会话未就绪；请先重新连接相机。")
        try:
            if owns_session:
                access = UsbDeviceAccess(self.profile, self.backend_factory)
                self._session_access = access
                access.prepare_access(progress, capture=True)
                # Capture identity while USB still advertises the normal body;
                # service-mode descriptors must never stand in for this identity.
                identity_reader = getattr(access.backend, 'read_custom_sound_identity', None)
                if not callable(identity_reader):
                    raise ValueError('当前适配器缺少认证前机身身份读取。')
                device_identity = identity_reader()
                backend = access.open_transport(progress)
                self._session_backend = backend
                self.connected = True
                verify_pending_settings(backend, directory)
            else:
                access = self._session_access
                backend = self._session_backend
                device_identity = getattr(backend, '_custom_sound_identity', None)
                if not isinstance(device_identity, str) or not device_identity:
                    raise ValueError('当前会话缺少认证前机身身份，请重新连接相机后再导入。')
            backend.progress = progress
            from ..custom_sound_executor import prepare_slot_update, execute_slot_update, preserved_slot_backend
            from ..custom_sound_executor import validate_slot_binding
            read_binding = getattr(backend, 'read_custom_sound_binding', None)
            if not callable(read_binding):
                raise ValueError('当前适配器尚不支持新鲜机身／加载器绑定，不能导入声音。')
            binding = read_binding()
            validate_slot_binding(binding, self.profile.digest)
            if binding.device_identity_sha256 != device_identity:
                raise ValueError('加载器绑定与认证前机身身份不一致。')
            from ..operations import make_directory
            service_run = Path(directory) / 'runs' / ('audio-import-service-' + uuid.uuid4().hex)
            make_directory(service_run)
            self._last_run_directory = service_run
            result = None
            try:
                if read_binding() != binding:
                    raise ValueError('机身或加载器已变化，请重新准备声音导入。')
                with preserved_slot_backend(backend, service_run / 'service') as transport:
                    update = prepare_slot_update(transport, plan, candidate)
                    result = execute_slot_update(transport, plan, update,
                        profile_sha256=self.profile.digest, binding=binding)
            except BaseException as exc:
                if result is not None:
                    from ..operations import save_json
                    result.update(status='uncertain', service_restore_verified=False,
                                  cleanup_error=str(exc) or type(exc).__name__)
                    save_json(Path(result['run_directory']) / 'service-restore-failure.json', result)
                raise
            self._last_run_directory = Path(result['run_directory'])
            if result.get('execution_unconfirmed'):
                # A timed-out native writer may still run; do not send STOP.
                if access is not None:
                    access.release_access(send_stop=False)
                self._session_access = None
            journal_status = result['status']
            output = dict(result, journal_status=journal_status,
                          status=('completed' if journal_status == 'verified' else
                                  'failed' if journal_status in ('not_started', 'rolled_back') else 'uncertain'),
                          device_access=True)
        except BaseException:
            self.close_session()
            raise
        if owns_session or journal_status != 'verified' or result.get('execution_unconfirmed'):
            self.close_session()
        return output

    def _perform_combined(self, backend, action, directory, progress, features):
        """Serialize existing installers under the same session lock.

        Each installer reads fresh state and independently verifies its own
        writes. A failed or unknown result stops the batch without replay.
        Successful earlier work is reported explicitly, never called rolled back.
        """
        import uuid
        from ..operations import _execute_session, make_directory, save_json
        from ..fl23_package import perform as package_perform
        from ..close_preparation import prepare_close, attach_preparation
        package_ids=[f for f in ('fl23','creative_look_names','custom_shutter_sound') if f in features]
        ordinary=sorted(set(features)-set(package_ids))
        selected=ordinary+package_ids
        names={r['id']:r['name'] for r in self.profile.catalog.data['features']}
        names['fl23']='FL2／FL3'
        names['creative_look_names']='创意外观中文名称'
        names['custom_shutter_sound']='自定义快门声'
        describe=lambda ids:'、'.join(names.get(i,i) for i in ids)
        run=Path(directory)/'runs'/('combined-'+uuid.uuid4().hex)
        make_directory(run)
        result=dict(action=action,status='error',device_access=True,write_attempted=False,settings_write_attempted=False,
                    verified=False,full_backup=False,run_directory=str(run),
                    selected_features=selected,completed_features=[],remaining_features=selected[:],
                    components=[],plan=dict(compatible=True,features=[],selected_features=selected,
                                            read_sectors=0,read_bytes=0,changed_bytes=0))
        save_json(run/'intent.json',dict(action=action,selected_features=selected,order=['ordinary','fl23']))
        if action=='unlock' and self.custom_sound_available:
            # The dependency (including basic shutter sound) goes first, so
            # check the target carrier before applying any ordinary changes.
            package_check=package_perform(backend,'inspect',directory,progress,
                                           features=package_ids,validate_unlock=True)
            result['package_unlock_preflight']=package_check
            if package_check.get('status')!='inspected':
                result['plan']['compatible']=False
                result['error']=package_check.get('error','功能包解锁前检查未通过')
                save_json(run/'result.json',result)
                return result
        if action=='close':
            # Validate BOTH installers before changing any setting or firmware.
            # Their ordinary execution still re-reads and re-plans afterwards.
            try:
                ordinary_check=_execute_session(self.profile,backend,'close',directory,progress,
                    dry_run=True,features=ordinary,manage_backend=False)
                result['ordinary_close_preflight']=ordinary_check
                if ordinary_check.get('close_preparation') is not None:
                    result['close_preparation']=ordinary_check['close_preparation']
                if ordinary_check.get('status')!='dry-run':
                    result.update(status=ordinary_check.get('status','error'),
                                  error=ordinary_check.get('error','普通功能关闭前检查未通过'))
                    save_json(run/'result.json',result)
                    return result
                package_check=package_perform(backend,'inspect',directory,progress,
                                               features=package_ids,validate_close=True)
                result['package_close_preflight']=package_check
                if package_check.get('status')!='inspected' or not package_check.get('close_ready'):
                    result.update(status='close-preparation-required',
                                  error=package_check.get('error','功能包关闭前检查未通过'))
                    save_json(run/'result.json',result)
                    return result
                if 'sound' in ordinary and 'custom_shutter_sound' not in package_ids and self.custom_sound_available:
                    cs_row = next((r for r in package_check.get('plan', {}).get('features', ())
                                   if r.get('id') == 'custom_shutter_sound'), None)
                    if cs_row and cs_row.get('state') != 'disabled':
                        result.update(status='close-preparation-required',
                                      error='自定义快门声已启用；请先或同时关闭「自定义快门声」，再关闭「电子快门声音」。')
                        save_json(run/'result.json',result)
                        return result
                if not attach_preparation(result,prepare_close(backend,ordinary,run,progress)):
                    save_json(run/'result.json',result)
                    return result
            except (Exception,KeyboardInterrupt) as exc:
                result.update(status='close-preparation-required',error='关闭前检查未完成：'+str(exc))
                save_json(run/'result.json',result)
                return result
        for name, ids in (('ordinary',ordinary),('fl23',package_ids)):
            progress('正在处理所选功能：'+describe(ids))
            try:
                part=(package_perform(backend,action,directory,progress,**({'features':ids} if ids!=['fl23'] else {})) if name=='fl23' else
                      _execute_session(self.profile,backend,action,directory,progress,
                                       features=ids,manage_backend=False))
            except BaseException as exc:
                # An unexpected exception cannot establish whether this stage wrote.
                part=dict(status='uncertain',verified=False,write_attempted=True,
                          error=str(exc) or type(exc).__name__)
            result['components'].append(dict(name=name,features=ids,result=part))
            result['write_attempted'] |= bool(part.get('write_attempted'))
            result['settings_write_attempted'] |= bool(part.get('settings_write_attempted'))
            plan=part.get('plan',{})
            result['plan']['features'].extend(plan.get('features',[]))
            for key in ('read_sectors','read_bytes','changed_bytes'):
                result['plan'][key]+=plan.get(key,0)
            if part.get('status') not in ('completed','no-op','settings-prepared') or not part.get('verified'):
                result['status']='partial' if result['completed_features'] else part.get('status','error')
                result['error']=('已确认完成：'+describe(result['completed_features'])+'；' if result['completed_features'] else '')+\
                    '未完成：'+describe(result['remaining_features'])+'。'+part.get('error',part.get('status','操作未确认'))
                break
            result['completed_features'].extend(ids)
            result['remaining_features']=[i for i in selected if i not in result['completed_features']]
            save_json(run/('completed-'+name+'.json'),result)
        else:
            # The second installer may share sectors with the first. Re-read the
            # ordinary feature state after FL23, using its original reader.
            check=_execute_session(self.profile,backend,'inspect',directory,progress,
                                   features=ordinary,manage_backend=False)
            result['preservation_check']=check
            target='enabled' if action=='unlock' else 'disabled'
            rows={r['id']:r for r in check.get('plan',{}).get('features',[])}
            if (check.get('status')=='inspected' and check.get('plan',{}).get('compatible') and
                    all(rows.get(i,{}).get('state')==target for i in ordinary)):
                result['verified']=True
                result['status']=('completed' if result['write_attempted'] else
                                  'settings-prepared' if result['settings_write_attempted'] else 'no-op')
                result['final_features']=list(rows.values())+result['components'][-1]['result'].get('final_features',[])
            else:
                result['status']='uncertain'
                result['error']='分步操作已完成，但最终原有功能保持检查未通过，请重新读取状态；未自动重发或回滚。'
        save_json(run/'result.json',result)
        return result
