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


class PhysicalService:
    """Execute real USB operations while exposing the desktop service shape."""

    def __init__(self, state_dir=None, backend_factory=None, backup_dir=None):
        self.profile = load_device_profile(load_catalog())
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.backup_dir = (Path(backup_dir) if backup_dir is not None
                           else default_backup_dir())
        self.backend_factory = backend_factory
        from ..fl23_package import available, names_available
        self.fl23_available = backend_factory is None and available()
        self.names_available = backend_factory is None and names_available()
        self.connected = False
        self.simulation = False
        # Keep one authenticated USB session open for this app process. The
        # first operation asks for authorization; inspection and the chosen
        # write reuse it. Unlock/close then end the session before power-off.
        self._session_backend = None
        self._session_lock = None
        self._last_run_directory = None

    def list_backups(self):
        return list_backups(self.backup_dir)

    def reset_capture(self):
        self.close_session()

    def close_session(self):
        backend, self._session_backend = self._session_backend, None
        lock, self._session_lock = self._session_lock, None
        self.connected = False
        exit_error = None
        try:
            if backend is not None:
                backend.__exit__(None, None, None)
        except BaseException as exc:
            exit_error = str(exc) or type(exc).__name__
            raise
        finally:
            try:
                if backend is not None and self._last_run_directory is not None:
                    from ..operations import save_json
                    try:
                        save_json(self._last_run_directory / 'session-exit.json', dict(
                            cleanup_status=('unconfirmed' if exit_error else getattr(backend, 'cleanup_status', None)),
                            cleanup_warning=exit_error or getattr(backend, 'cleanup_warning', None)))
                    except OSError:
                        pass
            finally:
                self._last_run_directory = None
                if lock is not None:
                    lock.__exit__(None, None, None)
                try:
                    from ..usb.legacy import reset_libusb_backend
                    reset_libusb_backend()
                except Exception:
                    pass

    def _backend(self, progress, capture):
        if self.backend_factory is not None:
            try:
                return self.backend_factory(progress, capture=capture)
            except TypeError:
                return self.backend_factory(progress)
        from ..usb.backend import UsbBackend
        return UsbBackend(self.profile, progress, capture=capture)

    def perform(self, action, progress=lambda _message: None, backup_id=None, features=None):
        if action not in ("inspect", "unlock", "close", "backup", "restore"):
            raise ValueError("未知设备操作；当前支持检查、解锁、关闭、功能状态备份与恢复")
        # Reject package feature injection before acquiring/authenticating USB.
        # The fixed-sector backend cannot install startup packages.
        package_ids={'fl23'} if self.fl23_available else set()
        if self.names_available:package_ids.add('creative_look_names')
        supported=set(self.profile.catalog.available_features)|package_ids
        if features is not None and not set(features) <= supported:
            raise ValueError('所选功能暂未开放实机写入。')
        if features is not None and set(features)&package_ids and action not in ('unlock','close'):
            raise ValueError('创意外观选项当前仅支持解锁或关闭。')
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
                backend = self._backend(progress, capture=True)
                backend.__enter__()
                self._session_backend = backend
                from ..close_setting import verify_pending_settings
                verify_pending_settings(backend,directory)
            except BaseException:
                self.close_session()
                raise
        backend = self._session_backend
        backend.progress = progress
        try:
            if features is not None and set(features)&package_ids and set(features)-package_ids:
                result=self._perform_combined(backend,action,directory,progress,features)
            elif features is not None and set(features)&package_ids:
                from ..fl23_package import perform
                result=perform(backend,action,directory,progress,features=features)
            elif action == "backup":
                result = _backup_session(self.profile, backend, self.backup_dir,
                                         directory, progress, manage_backend=False)
            elif action == "restore":
                source = load_backup(find_backup(self.backup_dir, backup_id), self.profile)
                result = _restore_session(self.profile, backend, source, directory,
                                          progress, features=features, manage_backend=False)
            else:
                result = _execute_session(self.profile, backend, action, directory,
                                          progress, features=features, manage_backend=False)
                if action=='inspect' and result.get('status')=='inspected' and self.fl23_available:
                    from ..fl23_package import perform
                    package_result=perform(backend,'inspect',directory,progress)
                    result['fl23_inspection']=package_result
                    result['plan']['features'].extend(package_result.get('plan',{}).get('features',[]))
                    if package_result.get('status')!='inspected':
                        result['plan']['compatible']=False
                        result['error']=package_result.get('error','FL23 实机预检未通过')
        except BaseException:
            self.close_session()
            raise
        self._last_run_directory = Path(result['run_directory'])
        if action in ('unlock', 'close') or result.get('status') in (
                'error', 'uncertain', 'close-preparation-required',
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
        self.connected = self._session_backend is not None and result.get('status') in (
            'inspected', 'dry-run', 'completed', 'no-op', 'backup-completed', 'restored', 'settings-prepared')
        return result

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
        package_ids=[f for f in ('fl23','creative_look_names') if f in features]
        ordinary=sorted(set(features)-set(package_ids))
        selected=ordinary+package_ids
        names={r['id']:r['name'] for r in self.profile.catalog.data['features']}
        names['fl23']='FL2／FL3'
        names['creative_look_names']='创意外观中文名称'
        describe=lambda ids:'、'.join(names.get(i,i) for i in ids)
        run=Path(directory)/'runs'/('combined-'+uuid.uuid4().hex)
        make_directory(run)
        result=dict(action=action,status='error',device_access=True,write_attempted=False,settings_write_attempted=False,
                    verified=False,full_backup=False,run_directory=str(run),
                    selected_features=selected,completed_features=[],remaining_features=selected[:],
                    components=[],plan=dict(compatible=True,features=[],selected_features=selected,
                                            read_sectors=0,read_bytes=0,changed_bytes=0))
        save_json(run/'intent.json',dict(action=action,selected_features=selected,order=['ordinary','fl23']))
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
                                  error=package_check.get('error','创意外观关闭前检查未通过'))
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
