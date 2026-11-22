"""Adapter that binds an audio plan to camera-owned slot records.

The transport is injected. This module never discovers USB, opens a camera,
acquires the device lock, or treats an offline backend as hardware validation.
"""
from __future__ import annotations
from contextlib import contextmanager

from .audio_import import AudioImportTransactionError
from .custom_sound_slots import SLOT_COUNT, valid_slot
from .custom_sound_records import (
    CustomSoundRecordError, build_slot_record, decode_slot_record)


class CustomSoundExecutorError(RuntimeError):
    pass


@contextmanager
def preserved_slot_backend(backend, directory):
    """Use the existing service-file backup/restore transaction for audio IO."""
    from .fl23_package import service_files
    from .custom_sound_native import (
        custom_sound_read_request, custom_sound_read_response, load_custom_sound_reader,
        custom_sound_request, custom_sound_response, load_custom_sound_payload, UNGUARDED)
    with service_files(backend, directory) as invoke:
        def exchange(binary, request):
            try:
                return invoke(binary, request)
            except BaseException:
                backend._fl23_transport_uncertain = True
                raise
        class SlotBackend:
            def read_custom_sound_slot(self, slot, nonce):
                return custom_sound_read_response(exchange(load_custom_sound_reader(),
                    custom_sound_read_request(slot, nonce)), nonce, slot)

            def run_custom_sound_import(self, plan, record, nonce, *, expected=UNGUARDED):
                return custom_sound_response(exchange(load_custom_sound_payload(),
                    custom_sound_request(plan, record, nonce, expected=expected)), nonce)

            def remove_custom_sound_slot(self, plan, record, nonce):
                return custom_sound_response(exchange(load_custom_sound_payload(),
                    custom_sound_request(plan, record, nonce, remove=True)), nonce)
        yield SlotBackend()


def prepare_slot_update(backend, plan, candidate):
    """Bind a candidate to the fresh target revision inside the caller's lock."""
    import uuid
    slot = plan.get('target', {}).get('slot') if isinstance(plan, dict) else None
    if not valid_slot(slot):
        raise CustomSoundExecutorError('默认声音不可覆盖；请选择 1–10 号槽。')
    reader = getattr(backend, 'read_custom_sound_slot', None)
    if not callable(reader):
        raise CustomSoundExecutorError('USB 后端缺少独立槽位读回能力。')
    previous = reader(slot, uuid.uuid4().bytes)
    if previous is None:
        revision = 0
    else:
        try:
            revision = decode_slot_record(previous, expected_slot=slot)['generation'] & 0x0FFFFFFF
        except (CustomSoundRecordError, TypeError, ValueError) as exc:
            raise CustomSoundExecutorError('目标槽记录异常，不能作为空槽覆盖。') from exc
    if revision == 0x0FFFFFFF:
        raise CustomSoundExecutorError('目标槽版本号已耗尽。')
    generation = (slot << 28) | (revision + 1)
    record = build_slot_record(plan['candidate'], candidate, slot, generation)
    return dict(slot=slot, generation=generation, previous=previous, record=record)


def read_slot_records(backend):
    """Read all fixed slots in an existing session; discard partial snapshots."""
    import uuid
    reader = getattr(backend, 'read_custom_sound_slot', None)
    if not callable(reader):
        raise CustomSoundExecutorError('USB 后端不支持机身音频槽读取。')
    records = {}
    for slot in range(1, SLOT_COUNT + 1):
        record = reader(slot, uuid.uuid4().bytes)
        if record is not None and not isinstance(record, bytes):
            raise CustomSoundExecutorError('音频槽读取器返回了无效记录。')
        records[slot] = record
    return records


def validate_slot_binding(binding, profile_sha256):
    from dataclasses import asdict
    from .package_installation import PackageBinding
    if (not isinstance(binding, PackageBinding)
            or binding.package_id != 'custom_shutter_sound'
            or binding.model != 'ILCE-6700' or binding.firmware != '2.00'
            or binding.adapter_sha256 != profile_sha256):
        raise CustomSoundExecutorError('缺少同机身、同加载器和同适配版本的声音绑定。')
    return asdict(binding)


def execute_slot_update(backend, plan, update, *, profile_sha256, binding):
    """Journal one fixed import in the caller's locked, authenticated session.

    A missing preimage is encoded separately from a present empty file. Native
    failures are closed only after observing the unchanged preimage. Uncertain
    execution never triggers another writer or a recovery guess.
    """
    import hashlib
    import json
    import uuid
    from .operations import save_json
    from .package_installation import create_installation_journal
    from .custom_sound_native import load_custom_sound_payload, load_custom_sound_reader
    bound = validate_slot_binding(binding, profile_sha256)
    slot, previous, record = update['slot'], update['previous'], update['record']
    definition = dict(schema='a6700.audio-slot-installation/v2', plan=plan, binding=bound,
                      profile_sha256=profile_sha256, slot=slot,
                      generation=update['generation'], previous_present=previous is not None,
                      before_sha256=hashlib.sha256(previous or b'').hexdigest(),
                      writer_sha256=hashlib.sha256(load_custom_sound_payload()).hexdigest(),
                      reader_sha256=hashlib.sha256(load_custom_sound_reader()).hexdigest(),
                      after_sha256=hashlib.sha256(record).hexdigest())
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True,
                           separators=(',', ':')).encode()).hexdigest()
    run = create_installation_journal(definition, digest,
                                     {f'slot{slot}': previous if previous is not None else b''})
    result = dict(plan_sha256=digest, run_directory=str(run), status='uncertain',
                  write_attempted=False, independent_readback=False,
                  execution_unconfirmed=False, functional_acceptance='pending',
                  target_slot=slot, generation=update['generation'], restart_required=False)
    try:
        # Recheck after durable journaling, immediately before the native call.
        if backend.read_custom_sound_slot(slot, uuid.uuid4().bytes) != previous:
            result.update(status='not_started', error='目标槽前态已变化，请重新读取并生成计划。')
            return result
        save_json(run / 'apply-0000.json', dict(part_id=f'slot{slot}'))
        result['write_attempted'] = True
        try:
            native = backend.run_custom_sound_import(plan, record, uuid.uuid4().bytes,
                                                    expected=previous)
        except BaseException:
            result['execution_unconfirmed'] = True
            raise
        result['native_result'] = native
        observed = backend.read_custom_sound_slot(slot, uuid.uuid4().bytes)
        result['observed_present'] = observed is not None
        result['observed_sha256'] = hashlib.sha256(observed or b'').hexdigest()
        if native.get('status') == 0 and not native.get('rolled_back') and observed == record:
            result.update(independent_readback=True, restart_required=True)
            if native.get('backup_cleanup_failed'):
                result['error'] = '音频已写入并回读，但旧备份清理未完成。'
            else:
                result['status'] = 'verified'
        elif native.get('status') not in (0, 8, 9) and observed == previous:
            result['status'] = 'rolled_back'
        else:
            result['error'] = '写入或回读状态未闭合；保留前态与记录，未自动重试。'
    except (Exception, KeyboardInterrupt) as exc:
        result['error'] = str(exc) or type(exc).__name__
    finally:
        save_json(run / 'result.json', result)
    return result


def restore_slot_update(backend, plan, update):
    """Restore one known completed import under the existing transaction lock.

    Never use after an unconfirmed execution. The caller owns identity checks,
    durable recovery intent, and transaction closure after this independent read.
    """
    import uuid
    slot, before, after = update['slot'], update['previous'], update['record']
    if not valid_slot(slot) or slot != plan.get('target', {}).get('slot'):
        raise CustomSoundExecutorError('恢复目标与导入计划不一致。')
    current = backend.read_custom_sound_slot(slot, uuid.uuid4().bytes)
    if current == before:
        return dict(status='already_restored', independent_readback=True, write_attempted=False)
    if current != after:
        raise CustomSoundExecutorError('目标槽已变化，拒绝覆盖后续修改。')
    if before is None:
        result = backend.remove_custom_sound_slot(plan, after, uuid.uuid4().bytes)
    else:
        result = backend.run_custom_sound_import(plan, before, uuid.uuid4().bytes, expected=after)
    if result.get('status') != 0 or result.get('backup_cleanup_failed'):
        raise CustomSoundExecutorError('恢复载荷未完成，需保留未闭合事务。')
    if backend.read_custom_sound_slot(slot, uuid.uuid4().bytes) != before:
        raise CustomSoundExecutorError('恢复后的独立回读不匹配。')
    return dict(status='restored', independent_readback=True, write_attempted=True)


def load_slot_recovery(directory, *, profile_sha256, binding):
    """Validate saved recovery material without opening a device.

    This is not device authorization. The caller must bind the live body and
    loader, hold the shared lock and journal recovery before using the result.
    Legacy journals without a bound preimage are deliberately refused.
    """
    import base64
    import hashlib
    import json
    from pathlib import Path
    from .custom_sound_native import load_custom_sound_payload, load_custom_sound_reader
    bound = validate_slot_binding(binding, profile_sha256)
    root = Path(directory)
    try:
        if root.is_symlink() or (root / 'service-restore-failure.json').exists():
            raise ValueError('服务恢复尚未闭合或记录目录无效')
        def read(name):
            path = root / name
            if path.is_symlink():
                raise ValueError('恢复记录不能是符号链接')
            return json.loads(path.read_text(encoding='utf-8'))
        intent, result, preimages = read('intent.json'), read('result.json'), read('preimages.json')
        definition = intent['plan']
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest()
        if (intent['plan_sha256'] != digest or result['plan_sha256'] != digest
                or definition['schema'] != 'a6700.audio-slot-installation/v2'
                or definition['binding'] != bound
                or definition['profile_sha256'] != profile_sha256
                or result['status'] != 'verified'
                or result.get('execution_unconfirmed') is not False
                or result.get('independent_readback') is not True):
            raise ValueError('导入事务未完成或适配摘要不匹配')
        for key, payload in (('writer_sha256', load_custom_sound_payload()),
                             ('reader_sha256', load_custom_sound_reader())):
            if definition[key] != hashlib.sha256(payload).hexdigest():
                raise ValueError('恢复执行资源版本已变化')
        slot, plan = definition['slot'], definition['plan']
        if not valid_slot(slot) or plan['target']['slot'] != slot or set(preimages) != {f'slot{slot}'}:
            raise ValueError('恢复槽位范围不匹配')
        before = base64.b64decode(preimages[f'slot{slot}'], validate=True)
        if hashlib.sha256(before).hexdigest() != definition['before_sha256']:
            raise ValueError('旧音频摘要不匹配')
        present = definition['previous_present']
        if type(present) is not bool or (not present and before):
            raise ValueError('旧槽位存在状态不匹配')
        if present:
            decode_slot_record(before, expected_slot=slot)
        return dict(plan=plan, slot=slot, previous=before if present else None, binding=bound,
                    after_sha256=definition['after_sha256'], generation=definition['generation'],
                    source_plan_sha256=digest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CustomSoundExecutorError(f'无法准备音频恢复：{exc}') from exc


def bind_slot_recovery(backend, recovery):
    """Freshly read the exact completed record under the caller's device lock."""
    import hashlib
    import uuid
    slot = recovery['slot']
    if not valid_slot(slot) or recovery['plan']['target']['slot'] != slot:
        raise CustomSoundExecutorError('恢复目标无效。')
    current = backend.read_custom_sound_slot(slot, uuid.uuid4().bytes)
    if current == recovery['previous']:
        return None
    if not isinstance(current, bytes) or hashlib.sha256(current).hexdigest() != recovery['after_sha256']:
        raise CustomSoundExecutorError('槽位已发生后续修改，拒绝恢复覆盖。')
    decode_slot_record(current, expected_slot=slot, expected_generation=recovery['generation'])
    return dict(slot=slot, previous=recovery['previous'], record=current)


def execute_slot_recovery(backend, recovery, *, binding):
    """Journal a recovery in an already identity-checked, locked session.

    No device discovery or UI authorization here. Service restoration and MSC
    remain the caller's responsibility, as for execute_slot_update.
    """
    import hashlib
    import json
    from .operations import save_json
    from .package_installation import create_installation_journal
    bound = validate_slot_binding(binding, getattr(binding, 'adapter_sha256', None))
    if recovery.get('binding') != bound:
        raise CustomSoundExecutorError('恢复准备之后机身或加载器绑定已变化。')
    update = bind_slot_recovery(backend, recovery)
    definition = dict(schema='a6700.audio-slot-recovery/v1', binding=bound,
                      source_plan_sha256=recovery['source_plan_sha256'],
                      slot=recovery['slot'], before_present=recovery['previous'] is not None,
                      before_sha256=hashlib.sha256(recovery['previous'] or b'').hexdigest(),
                      after_sha256=recovery['after_sha256'])
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True,
                           separators=(',', ':')).encode()).hexdigest()
    run = create_installation_journal(definition, digest,
        {f"slot{recovery['slot']}": update['record'] if update else recovery['previous'] or b''})
    result = dict(plan_sha256=digest, run_directory=str(run), status='uncertain',
                  write_attempted=False, independent_readback=False,
                  execution_unconfirmed=False, restart_required=False)
    try:
        if update is None:
            result.update(status='no_op', independent_readback=True)
        else:
            save_json(run / 'apply-0000.json', dict(part_id=f"slot{recovery['slot']}"))
            # From here a failure may occur after submission: retain the open
            # journal and never retry or automatically issue another payload.
            result['write_attempted'] = True
            restored = restore_slot_update(backend, recovery['plan'], update)
            result.update(status='rolled_back', independent_readback=True,
                          write_attempted=restored['write_attempted'],
                          restart_required=restored['write_attempted'])
    except (Exception, KeyboardInterrupt) as exc:
        result.update(error=str(exc) or type(exc).__name__,
                      execution_unconfirmed=result['write_attempted'])
    finally:
        save_json(run / 'result.json', result)
    return result


class UsbSlotRecordTransport:
    """One-shot USB transport adapter for the fixed native slot payload."""

    def __init__(self, backend, plan):
        if not callable(getattr(backend, "run_custom_sound_import", None)):
            raise CustomSoundExecutorError("USB 后端不支持自定义快门声导入。")
        if not callable(getattr(backend, 'read_custom_sound_slot', None)):
            raise CustomSoundExecutorError('USB 后端缺少独立槽位读回能力。')
        self.backend = backend
        self.plan = plan
        self.saved = {}

    def write_slot(self, slot, record):
        import uuid
        if not valid_slot(slot) or slot != self.plan['target']['slot']:
            raise CustomSoundExecutorError('音频槽写入目标与计划不匹配。')
        result = self.backend.run_custom_sound_import(
            self.plan, record, uuid.uuid4().bytes)
        if result.get("status"):
            raise AudioImportTransactionError(
                f"自定义快门声槽写入失败：{result.get('error')}")
        self.saved[slot] = record

    def read_slot(self, slot):
        import uuid
        if not valid_slot(slot) or slot != self.plan['target']['slot']:
            raise CustomSoundExecutorError('音频槽读回目标与计划不匹配。')
        reader = getattr(self.backend, 'read_custom_sound_slot', None)
        if not callable(reader):
            raise CustomSoundExecutorError('USB 后端缺少独立槽位读回能力。')
        return reader(slot, uuid.uuid4().bytes)

    def activate_slot(self, slot, generation):
        # File persistence is complete. Native resource ownership and menu
        # activation are handled by the installed camera worker, not this USB
        # service payload.
        return None


class SlotRecordBackend:
    """AudioImportTransaction backend for one exact plan and target slot."""

    def __init__(self, plan, transport, name_publisher=None):
        if not isinstance(plan, dict) or not valid_slot(plan.get("target", {}).get("slot")):
            raise CustomSoundExecutorError("音频导入计划没有有效目标槽。")
        required = ("write_slot", "read_slot", "activate_slot")
        if any(not callable(getattr(transport, name, None)) for name in required):
            raise CustomSoundExecutorError("自定义快门声执行传输接口不完整。")
        if name_publisher is not None and not callable(name_publisher):
            raise CustomSoundExecutorError("自定义快门声菜单名发布接口无效。")
        self.plan = plan
        self.transport = transport
        self.target_slot = plan["target"]["slot"]
        self.name_publisher = name_publisher

    def prepare(self, candidate, generation, slot):
        if slot != self.target_slot:
            raise AudioImportTransactionError("执行器目标槽与计划不匹配。")
        try:
            record = build_slot_record(self.plan["candidate"], candidate,
                                       slot, generation)
        except CustomSoundRecordError as exc:
            raise AudioImportTransactionError(str(exc)) from exc
        return dict(slot=slot, generation=generation, record=record)

    def persist(self, resource, candidate, generation, slot):
        if slot != self.target_slot or generation != resource["generation"]:
            raise AudioImportTransactionError("执行器持久化身份不匹配。")
        self.transport.write_slot(slot, resource["record"])
        return resource

    def readback(self, record):
        observed = self.transport.read_slot(record["slot"])
        try:
            decode_slot_record(
                observed, expected_slot=record["slot"],
                expected_generation=record["generation"])
        except (CustomSoundRecordError, TypeError, ValueError):
            return False
        return observed == record["record"]

    def activate(self, record):
        self.transport.activate_slot(record["slot"], record["generation"])
        if self.name_publisher is not None:
            status = self.name_publisher(
                record["slot"], self.plan["candidate"]["filename"])
            if status:
                raise AudioImportTransactionError("自定义快门声菜单名发布失败。")

    def abort(self, resource):
        discard = getattr(self.transport, "discard_slot", None)
        if callable(discard):
            discard(resource["slot"], resource["generation"])
