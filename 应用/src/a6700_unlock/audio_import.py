"""Offline custom shutter-sound import plans.

This module validates a candidate and compiles a declarative plan. It never
opens a camera, constructs a writer, or treats user confirmation as authority
to bypass an unavailable capability.
"""
from __future__ import annotations

import hashlib
import json

from .audio_packages import AudioPackageError, MAX_WAV_BYTES, inspect_pcm_wav
from .custom_sound_records import HEADER_BYTES as SLOT_HEADER_BYTES, MAX_NAME_BYTES
from .custom_sound_slots import SLOT_COUNT, valid_selection, valid_slot


AUDIO_IMPORT_PLAN_SCHEMA = "a6700.audio-import-plan/v2"
AUDIO_IMPORT_BATCH_SCHEMA = "a6700.audio-import-batch/v1"
AUDIO_IMPORT_CONFIRMATION_SCHEMA = "a6700.audio-import-confirmation/v1"
AUDIO_IMPORT_EXECUTION_SCHEMA = "a6700.audio-import-execution/v1"
AUDIO_IMPORT_BATCH_CONFIRMATION_SCHEMA = "a6700.audio-import-batch-confirmation/v1"
AUDIO_IMPORT_BATCH_EXECUTION_SCHEMA = "a6700.audio-import-batch-execution/v1"
AUDIO_IMPORT_TRANSACTION_SCHEMA = "a6700.audio-import-transaction/v1"
PLAN_REVISION = 2
BATCH_REVISION = 1
CAPABILITY_ID = "custom_shutter_sound"
EXECUTION_STAGES = (
    "local-candidate", "user-confirmation", "capability",
    "fresh-device-preflight", "shared-device-lock", "import-executor",
)
MAX_EXISTING_RECORD_BYTES = SLOT_HEADER_BYTES + MAX_NAME_BYTES + 1024 * 1024

_CANDIDATE_FIELDS = {
    "title", "description", "filename", "bytes", "sha256", "duration_ms",
    "sample_rate", "channels", "bit_depth", "codec", "tags",
    "peak_amplitude", "rms_amplitude",
}


class AudioImportError(ValueError):
    pass


class AudioImportTransactionError(AudioImportError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _candidate(value):
    if not isinstance(value, dict) or not _CANDIDATE_FIELDS <= set(value):
        raise AudioImportError("音频候选信息不完整")
    if value["codec"] != "pcm-wav":
        raise AudioImportError("仅支持整数 PCM WAV")
    if not isinstance(value["title"], str) or not value["title"].strip():
        raise AudioImportError("音频标题无效")
    try:
        from .custom_sound_slots import encode_slot_name
        encode_slot_name(value["filename"])
    except ValueError as exc:
        raise AudioImportError(str(exc)) from exc
    if not isinstance(value["sha256"], str) or len(value["sha256"]) != 64:
        raise AudioImportError("音频哈希无效")
    integers = {
        "bytes": value["bytes"], "duration_ms": value["duration_ms"],
        "sample_rate": value["sample_rate"], "channels": value["channels"],
        "bit_depth": value["bit_depth"],
    }
    if any(type(item) is not int or item <= 0 for item in integers.values()):
        raise AudioImportError("音频数值字段无效")
    if value["channels"] not in (1, 2) or value["bit_depth"] not in (8, 16, 24, 32):
        raise AudioImportError("音频声道或位深无效")
    if not 8000 <= value["sample_rate"] <= 192000:
        raise AudioImportError("音频采样率无效")
    if value["duration_ms"] > 2000:
        raise AudioImportError("音频时长超过2秒")
    if value["bytes"] > MAX_WAV_BYTES:
        raise AudioImportError("音频文件超过512KiB")
    if (not isinstance(value["peak_amplitude"], (int, float))
            or not isinstance(value["rms_amplitude"], (int, float))
            or not 0 <= value["rms_amplitude"] <= value["peak_amplitude"] <= 1):
        raise AudioImportError("音频峰值或 RMS 无效")
    if not isinstance(value["tags"], list) or len(value["tags"]) > 8:
        raise AudioImportError("音频标签无效")
    return {key: value[key] for key in sorted(_CANDIDATE_FIELDS)} | {
        "description": value.get("description", "")
    }


def _source(value):
    if not isinstance(value, dict) or value.get("type") not in ("local", "market"):
        raise AudioImportError("音频来源无效")
    result = dict(type=value["type"])
    if result["type"] == "market":
        required = ("package_id", "package_version", "package_digest", "sound_id", "author", "license")
        if any(not isinstance(value.get(key), str) or not value[key] for key in required):
            raise AudioImportError("市场音频来源信息不完整")
        result.update({key: value[key] for key in required})
    return result


def _slot(value):
    if not valid_slot(value):
        raise AudioImportError("音频导入槽位无效")
    return value


def build_audio_import_plan(candidate, source, *, target_slot=1,
                            capability_available=False,
                            implementation_ready=False):
    """Build a non-executing plan from a validated audio candidate."""
    candidate = _candidate(candidate)
    source = _source(source)
    target_slot = _slot(target_slot)
    if capability_available and implementation_ready:
        status = "ready"
        reasons = []
    elif not capability_available:
        status = "blocked"
        reasons = [dict(
            id="custom-shutter-sound-capability-unavailable",
            message="自定义快门声能力包尚未安装或尚未开放；本计划不会写相机。",
        )]
    else:
        status = "blocked"
        reasons = [dict(
            id="custom-shutter-sound-import-not-implemented",
            message="自定义快门声导入执行器尚未实现；本计划不会写相机。",
        )]

    body = dict(
        schema=AUDIO_IMPORT_PLAN_SCHEMA, plan_revision=PLAN_REVISION,
        status=status, can_execute=status == "ready",
        capability=dict(
            id=CAPABILITY_ID, available=capability_available,
            implementation_ready=implementation_ready,
        ),
        reasons=reasons,
        source=source,
        candidate=candidate,
        target=dict(slot_count=SLOT_COUNT, slot=target_slot,
                    label=f"slot{target_slot}", factory_fallback="KASHA"),
        requirements=[
            dict(id="validated-audio", status="satisfied", evidence="local-core"),
            dict(id="user-audition", status="optional", evidence="local-playback"),
            dict(id="user-confirmation", status="pending", evidence="user_attestation"),
            dict(id="fresh-device-preflight", status="pending", evidence="device"),
            dict(id="shared-device-lock", status="pending", evidence="core"),
            dict(id="custom-sound-capability", status="satisfied" if capability_available else "blocked",
                 evidence="capability-package"),
            dict(id="import-executor", status="satisfied" if implementation_ready else "blocked",
                 evidence="core"),
        ],
        steps=[
            dict(id="local-validation", title="本机校验 WAV", detail="RIFF、PCM、时长、采样率、声道、位深、哈希和峰值校验。", status="completed"),
            dict(id="audition", title="本机试听", detail="用户可试听候选音频；试听不连接相机。", status="optional"),
            dict(id="confirm", title="确认导入", detail="展示来源、文件名、时长、峰值和哈希；用户确认前不进入设备阶段。", status="pending"),
            dict(id="preflight", title="新鲜设备预检", detail="连接相机后重新识别固件、能力和当前自定义快门声状态。", status="blocked" if status != "ready" else "pending"),
            dict(id="lock", title="取得共享设备锁", detail="从计划生成到独立读回一直持有同一共享锁。", status="blocked" if status != "ready" else "pending"),
            dict(id="commit", title=f"提交到槽位{target_slot}", detail=f"只写入目标槽 slot{target_slot} 记录并准备非活动播放资源；其他槽位不变。", status="blocked" if status != "ready" else "pending"),
            dict(id="readback", title="独立回读", detail=f"回读目标槽记录、WAV哈希和资源状态；失败保持旧声音。", status="blocked" if status != "ready" else "pending"),
        ],
        acceptance=[
            dict(id="sound-options-menu", path="设置、声音选项、电子快门声音", expected="显示原厂和已导入音频名，且目标槽位可选"),
            dict(id="electronic-shutter", path="电子快门拍摄", expected=f"选择槽位{target_slot}后播放该槽导入音频"),
        ],
        evidence=dict(
            local_validation="completed", camera_access="not-run",
            write_attempted=False, user_confirmation="pending",
        ),
    )
    return dict(body, digest=_digest(body))


def build_audio_import_batch_plan(candidates, source, *,
                                  capability_available=False,
                                  implementation_ready=False):
    """Build one offline batch containing up to ten slot-specific plans."""
    if not isinstance(candidates, list) or not candidates or len(candidates) > SLOT_COUNT:
        raise AudioImportError("音频批量导入条目数量无效")
    if isinstance(source, list):
        sources = source
    else:
        sources = [source] * len(candidates)
    if len(sources) != len(candidates):
        raise AudioImportError("音频批量导入来源数量无效")
    plans = []
    for index, (candidate, item_source) in enumerate(zip(candidates, sources), start=1):
        plans.append(build_audio_import_plan(
            candidate, item_source, target_slot=index,
            capability_available=capability_available,
            implementation_ready=implementation_ready))
    first = plans[0]
    body = dict(
        schema=AUDIO_IMPORT_BATCH_SCHEMA,
        batch_revision=BATCH_REVISION,
        status=first["status"],
        can_execute=first["can_execute"],
        capability=first["capability"],
        reasons=first["reasons"],
        slot_count=len(plans),
        source=source,
        plans=plans,
    )
    return dict(body, digest=_digest(body))


def validate_audio_import_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema") != AUDIO_IMPORT_PLAN_SCHEMA:
        raise AudioImportError("音频导入计划 schema 无效")
    if plan.get("plan_revision") != PLAN_REVISION:
        raise AudioImportError("音频导入计划版本无效")
    body = dict(plan)
    supplied = body.pop("digest", None)
    if supplied != _digest(body):
        raise AudioImportError("音频导入计划摘要不匹配")
    _candidate(plan["candidate"])
    _source(plan["source"])
    target = plan.get("target")
    if not isinstance(target, dict):
        raise AudioImportError("音频导入目标槽无效")
    if target.get("slot_count") != SLOT_COUNT:
        raise AudioImportError("音频导入槽位数量无效")
    _slot(target.get("slot"))
    return plan


def validate_audio_import_batch_plan(batch):
    if (not isinstance(batch, dict)
            or batch.get("schema") != AUDIO_IMPORT_BATCH_SCHEMA
            or batch.get("batch_revision") != BATCH_REVISION):
        raise AudioImportError("音频批量导入计划 schema 无效")
    body = dict(batch)
    supplied = body.pop("digest", None)
    if supplied != _digest(body):
        raise AudioImportError("音频批量导入计划摘要不匹配")
    plans = batch.get("plans")
    if (not isinstance(plans, list) or not plans
            or len(plans) != batch.get("slot_count")
            or len(plans) > SLOT_COUNT):
        raise AudioImportError("音频批量导入计划条目无效")
    for index, plan in enumerate(plans, start=1):
        validate_audio_import_plan(plan)
        if plan["target"]["slot"] != index:
            raise AudioImportError("音频批量导入计划槽位映射无效")
    return batch


def confirm_audio_import_plan(plan, confirmed):
    """Record a bounded confirmation without granting device access.

    Confirmation is bound to the exact plan digest. Until both the capability
    package and import executor are implemented, confirmation remains a local
    blocked result and never constructs a device session or writer.
    """
    validate_audio_import_plan(plan)
    if type(confirmed) is not bool:
        raise AudioImportError("音频导入确认值无效")
    if not confirmed:
        return dict(
            schema=AUDIO_IMPORT_CONFIRMATION_SCHEMA, status="cancelled",
            plan_digest=plan["digest"], user_confirmation="declined",
            camera_access="not-run", write_attempted=False,
        )
    if not plan["can_execute"]:
        return dict(
            schema=AUDIO_IMPORT_CONFIRMATION_SCHEMA, status="blocked",
            plan_digest=plan["digest"], user_confirmation="confirmed",
            reasons=plan["reasons"], camera_access="not-run",
            write_attempted=False,
        )
    # A future ready plan still cannot bypass this implementation boundary.
    return dict(
        schema=AUDIO_IMPORT_CONFIRMATION_SCHEMA, status="blocked",
        plan_digest=plan["digest"], user_confirmation="confirmed",
        reasons=[dict(
            id="custom-shutter-sound-executor-unavailable",
            message="相机导入执行器尚未实现；已停止在本地确认阶段。",
        )],
        camera_access="not-run", write_attempted=False,
    )


def validate_audio_import_confirmation(confirmation, *, plan=None):
    if (not isinstance(confirmation, dict)
            or confirmation.get("schema") != AUDIO_IMPORT_CONFIRMATION_SCHEMA
            or confirmation.get("status") not in ("cancelled", "blocked")
            or confirmation.get("user_confirmation") not in ("confirmed", "declined")
            or not isinstance(confirmation.get("plan_digest"), str)
            or confirmation.get("camera_access") != "not-run"
            or confirmation.get("write_attempted") is not False):
        raise AudioImportError("音频导入确认记录无效")
    if plan is not None and confirmation["plan_digest"] != plan["digest"]:
        raise AudioImportError("音频导入确认与计划不匹配")
    if ((confirmation["status"] == "cancelled") !=
            (confirmation["user_confirmation"] == "declined")):
        raise AudioImportError("音频导入确认状态不一致")
    return confirmation


def _unique_reasons(records):
    result = []
    seen = set()
    for record in records:
        for reason in record.get("reasons", []):
            if not isinstance(reason, dict) or not isinstance(reason.get("id"), str):
                raise AudioImportError("音频导入阻断原因无效")
            if reason["id"] in seen:
                continue
            seen.add(reason["id"])
            result.append(reason)
    return result


def confirm_audio_import_batch_plan(batch, confirmed):
    """Confirm every slot plan as one bounded, non-device operation."""
    validate_audio_import_batch_plan(batch)
    if type(confirmed) is not bool:
        raise AudioImportError("音频批量导入确认值无效")
    results = [confirm_audio_import_plan(plan, confirmed)
               for plan in batch["plans"]]
    for plan, confirmation in zip(batch["plans"], results):
        validate_audio_import_confirmation(confirmation, plan=plan)
    user_confirmation = "declined" if not confirmed else "confirmed"
    return dict(
        schema=AUDIO_IMPORT_BATCH_CONFIRMATION_SCHEMA,
        status="cancelled" if not confirmed else "blocked",
        batch_digest=batch["digest"], slot_count=batch["slot_count"],
        user_confirmation=user_confirmation, results=results,
        reasons=_unique_reasons(results), camera_access="not-run",
        write_attempted=False,
    )


def validate_audio_import_batch_confirmation(confirmation, *, batch=None):
    if (not isinstance(confirmation, dict)
            or confirmation.get("schema") != AUDIO_IMPORT_BATCH_CONFIRMATION_SCHEMA
            or confirmation.get("status") not in ("cancelled", "blocked")
            or confirmation.get("user_confirmation") not in ("confirmed", "declined")
            or not isinstance(confirmation.get("batch_digest"), str)
            or type(confirmation.get("slot_count")) is not int
            or not 1 <= confirmation["slot_count"] <= SLOT_COUNT
            or confirmation.get("camera_access") != "not-run"
            or confirmation.get("write_attempted") is not False):
        raise AudioImportError("音频批量导入确认记录无效")
    results = confirmation.get("results")
    if not isinstance(results, list) or len(results) != confirmation["slot_count"]:
        raise AudioImportError("音频批量导入确认条目无效")
    if ((confirmation["status"] == "cancelled")
            != (confirmation["user_confirmation"] == "declined")):
        raise AudioImportError("音频批量导入确认状态不一致")
    for result in results:
        validate_audio_import_confirmation(result)
        if result["user_confirmation"] != confirmation["user_confirmation"]:
            raise AudioImportError("音频批量导入确认与子计划不一致")
    if batch is not None:
        if (confirmation["batch_digest"] != batch["digest"]
                or confirmation["slot_count"] != batch["slot_count"]):
            raise AudioImportError("音频批量导入确认与计划不匹配")
        for plan, result in zip(batch["plans"], results):
            validate_audio_import_confirmation(result, plan=plan)
    return confirmation


def gate_audio_import_batch_execution(batch, confirmation, candidates, *,
                                      preflight=None, lock=None,
                                      executor_available=False):
    """Evaluate each slot gate and return one aggregate, non-device result."""
    validate_audio_import_batch_plan(batch)
    validate_audio_import_batch_confirmation(confirmation, batch=batch)
    if (not isinstance(candidates, list)
            or len(candidates) != batch["slot_count"]
            or any(not isinstance(candidate, bytes) for candidate in candidates)):
        raise AudioImportError("音频批量导入候选无效")
    device_state = _execution_preflight(preflight)
    capacity = _capacity_assessment(batch["plans"], device_state) if device_state else None
    executor_ready = bool(batch["capability"]["implementation_ready"]
                          and executor_available)
    if capacity is not None and not capacity["sufficient"]:
        results = [
            _capacity_blocked_execution_result(
                plan, capacity, slot_confirmation, executor_ready, lock)
            for plan, slot_confirmation in zip(batch["plans"], confirmation["results"])
        ]
    else:
        results = [
            gate_audio_import_execution(
                plan, slot_confirmation, candidate,
                preflight=preflight, lock=lock,
                executor_available=executor_available)
            for plan, slot_confirmation, candidate
            in zip(batch["plans"], confirmation["results"], candidates)
        ]
    if any(result["status"] == "rejected" for result in results):
        status = "rejected"
    elif all(result["status"] == "authorized" for result in results):
        status = "authorized"
    else:
        status = "blocked"
    aggregate = dict(
        schema=AUDIO_IMPORT_BATCH_EXECUTION_SCHEMA,
        status=status, batch_digest=batch["digest"],
        slot_count=batch["slot_count"],
        slot_statuses=[
            dict(slot=plan["target"]["slot"], plan_digest=plan["digest"],
                 status=result["status"], stages=result["stages"])
            for plan, result in zip(batch["plans"], results)
        ],
        results=results, reasons=_unique_reasons(results),
        camera_access="not-run", write_attempted=False,
    )
    if capacity is not None:
        aggregate["capacity"] = capacity
    return aggregate


def _validated_execution_candidate(plan, candidate):
    expected = plan["candidate"]
    if (not isinstance(candidate, bytes) or not candidate
            or len(candidate) != expected["bytes"]
            or hashlib.sha256(candidate).hexdigest() != expected["sha256"]):
        raise AudioImportError("音频候选与导入计划不匹配")
    inspected = inspect_pcm_wav(candidate, expected["filename"])
    compared = {
        "filename", "bytes", "sha256", "duration_ms", "sample_rate",
        "channels", "bit_depth", "codec",
    }
    if any(inspected[key] != expected[key] for key in compared):
        raise AudioImportError("音频候选参数与导入计划不匹配")


def _execution_preflight(value):
    if not isinstance(value, dict):
        return None
    if not (value.get("fresh") is True and value.get("compatible") is True
            and value.get("capability_installed") is True
            and isinstance(value.get("profile_sha256"), str)
            and value.get("profile_sha256")):
        return None
    selection = value.get("selection")
    generations = value.get("slot_generations")
    if not valid_selection(selection):
        return None
    if (not isinstance(generations, (list, tuple)) or len(generations) != SLOT_COUNT
            or any(not _valid_slot_generation(index + 1, item)
                   for index, item in enumerate(generations))):
        return None
    if selection and not generations[selection - 1]:
        return None
    setting = _setting_preflight(value.get("setting"))
    if setting is None:
        return None
    return dict(selection=selection, generations=tuple(generations), setting=setting)


def _setting_preflight(value):
    if not isinstance(value, dict):
        return None
    cluster_bytes = value.get("cluster_bytes")
    free_clusters = value.get("free_clusters")
    directory_exists = value.get("directory_exists")
    existing = value.get("existing_record_bytes")
    if (type(cluster_bytes) is not int or not 512 <= cluster_bytes <= 1024 * 1024
            or type(free_clusters) is not int or free_clusters < 0
            or type(directory_exists) is not bool
            or not isinstance(existing, list) or len(existing) != SLOT_COUNT):
        return None
    normalized = []
    for item in existing:
        if item is None:
            normalized.append(None)
            continue
        if (type(item) is not int
                or not SLOT_HEADER_BYTES <= item <= MAX_EXISTING_RECORD_BYTES):
            return None
        normalized.append(item)
    if not directory_exists and any(item is not None for item in normalized):
        return None
    return dict(cluster_bytes=cluster_bytes, free_clusters=free_clusters,
                directory_exists=directory_exists,
                existing_record_bytes=tuple(normalized))


def _valid_slot_generation(slot, value):
    if type(value) is not int or value < 0 or value > 0xFFFFFFFF:
        return False
    if value == 0:
        return True
    return ((value & 0xF0000000) == (slot << 28)
            and bool(value & 0x0FFFFFFF))


def _execution_lock(value):
    return (isinstance(value, dict) and value.get("held") is True
            and value.get("owner") == "core")


def _slot_record_bytes(plan):
    filename = plan["candidate"]["filename"].encode("utf-8")
    return SLOT_HEADER_BYTES + len(filename) + plan["candidate"]["bytes"]


def _clusters_for(bytes_value, cluster_bytes):
    return (bytes_value + cluster_bytes - 1) // cluster_bytes


def _capacity_assessment(plans, device_state):
    setting = device_state["setting"]
    cluster_bytes = setting["cluster_bytes"]
    required = 0 if setting["directory_exists"] else 1
    new_clusters = 0
    current_clusters = 0
    for plan in plans:
        new_bytes = _slot_record_bytes(plan)
        new_clusters += _clusters_for(new_bytes, cluster_bytes)
        existing = setting["existing_record_bytes"][plan["target"]["slot"] - 1]
        if existing is not None:
            current_clusters += _clusters_for(existing, cluster_bytes)
    required += new_clusters + current_clusters
    return dict(
        cluster_bytes=cluster_bytes,
        free_clusters=setting["free_clusters"],
        directory_exists=setting["directory_exists"],
        required_peak_clusters=required,
        new_record_clusters=new_clusters,
        current_primary_clusters=current_clusters,
        sufficient=required <= setting["free_clusters"],
        strategy="peak-new-plus-current-primary",
    )


def _capacity_reason(capacity):
    return dict(
        id="setting-capacity-insufficient",
        message=(f"/setting FAT12容量不足：峰值需要{capacity['required_peak_clusters']}簇，"
                 f"当前只有{capacity['free_clusters']}簇。"),
    )


def _capacity_blocked_execution_result(plan, capacity, confirmation,
                                       executor_ready, lock):
    stages = {
        "local-candidate": "satisfied",
        "user-confirmation": ("satisfied" if confirmation["user_confirmation"] == "confirmed"
                              else "rejected"),
        "capability": "satisfied" if plan["capability"]["available"] else "blocked",
        "import-executor": "satisfied" if executor_ready else "blocked",
        "fresh-device-preflight": "blocked",
        "shared-device-lock": "satisfied" if _execution_lock(lock) else "blocked",
    }
    return dict(
        schema=AUDIO_IMPORT_EXECUTION_SCHEMA, plan_digest=plan["digest"],
        candidate_sha256=plan["candidate"]["sha256"],
        target_slot=plan["target"]["slot"], stages=stages,
        status="blocked", reasons=[_capacity_reason(capacity)],
        capacity=capacity, camera_access="not-run", write_attempted=False,
    )


def gate_audio_import_execution(plan, confirmation, candidate, *,
                                preflight=None, lock=None,
                                executor_available=False):
    """Evaluate the sole import execution gate without opening a camera.

    This is deliberately a gate, not an executor. Even a future ready plan
    stops here until the real in-process executor is implemented and wired to
    the shared device lock.
    """
    validate_audio_import_plan(plan)
    validate_audio_import_confirmation(confirmation, plan=plan)
    _validated_execution_candidate(plan, candidate)
    executor_ready = bool(plan["capability"]["implementation_ready"]
                          and executor_available)
    stages = {
        "local-candidate": "satisfied",
        "user-confirmation": "satisfied" if confirmation["user_confirmation"] == "confirmed" else "rejected",
        "capability": "satisfied" if plan["capability"]["available"] else "blocked",
        "import-executor": "satisfied" if executor_ready else "blocked",
    }
    device_state = None
    result = dict(
        schema=AUDIO_IMPORT_EXECUTION_SCHEMA, plan_digest=plan["digest"],
        candidate_sha256=plan["candidate"]["sha256"],
        target_slot=plan["target"]["slot"], stages=stages,
        camera_access="not-run", write_attempted=False,
    )
    if confirmation["user_confirmation"] != "confirmed":
        return dict(result, status="rejected", reasons=[dict(
            id="user-confirmation-declined", message="用户已拒绝本次导入。")])
    if not plan["can_execute"]:
        return dict(result, status="blocked", reasons=plan["reasons"])

    device_state = _execution_preflight(preflight)
    result["current_selection"] = device_state["selection"] if device_state else None
    result["slot_generations"] = list(device_state["generations"]) if device_state else None
    capacity = _capacity_assessment([plan], device_state) if device_state else None
    if capacity is not None:
        result["capacity"] = capacity
    stages["fresh-device-preflight"] = "satisfied" if device_state else "blocked"
    if stages["fresh-device-preflight"] != "satisfied":
        return dict(result, status="blocked", reasons=[dict(
            id="fresh-device-preflight-required",
            message="缺少新鲜、兼容且确认自定义快门声能力已安装的设备预检。")])
    if not capacity["sufficient"]:
        stages["fresh-device-preflight"] = "blocked"
        return dict(result, status="blocked", reasons=[_capacity_reason(capacity)])
    stages["shared-device-lock"] = "satisfied" if _execution_lock(lock) else "blocked"
    if stages["shared-device-lock"] != "satisfied":
        return dict(result, status="blocked", reasons=[dict(
            id="shared-device-lock-required", message="必须持有核心共享设备锁后才能进入导入执行。")])
    if not executor_ready:
        # The real executor is not implemented. This final boundary prevents a
        # ready plan from being mistaken for write authority.
        return dict(result, status="blocked", reasons=[dict(
            id="custom-shutter-sound-executor-unavailable",
            message="相机导入执行器尚未实现；已停止在执行门禁，未访问相机。")])
    # Model-only path: this authorizes the offline transaction boundary, not a
    # camera write. The production caller leaves executor_available unset.
    return dict(result, status="authorized")


def _validated_execution_authorization(authorization, plan, candidate):
    if (not isinstance(authorization, dict)
            or authorization.get("schema") != AUDIO_IMPORT_EXECUTION_SCHEMA
            or authorization.get("status") != "authorized"
            or authorization.get("plan_digest") != plan["digest"]
            or authorization.get("candidate_sha256") != plan["candidate"]["sha256"]
            or authorization.get("target_slot") != plan["target"]["slot"]
            or type(authorization.get("current_selection")) is not int
            or not valid_selection(authorization["current_selection"])
            or not isinstance(authorization.get("slot_generations"), list)
            or len(authorization["slot_generations"]) != SLOT_COUNT
            or any(not _valid_slot_generation(index + 1, item)
                   for index, item in enumerate(authorization["slot_generations"]))
            or authorization.get("camera_access") != "not-run"
            or authorization.get("write_attempted") is not False):
        raise AudioImportTransactionError("音频导入执行授权无效")
    stages = authorization.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(EXECUTION_STAGES):
        raise AudioImportTransactionError("音频导入执行授权阶段不完整")
    if any(stages[stage] != "satisfied" for stage in EXECUTION_STAGES):
        raise AudioImportTransactionError("音频导入执行授权阶段未全部满足")
    _validated_execution_candidate(plan, candidate)
    return authorization


class AudioImportTransaction:
    """Host-side sequencing model for a future native import worker.

    The backend is injected and this class never opens USB, a camera session,
    or a writer. Results are explicitly marked simulated so a passing model
    test can never be reported as an on-camera import.
    """

    def __init__(self, plan, candidate, backend, generation, authorization):
        validate_audio_import_plan(plan)
        if not plan["can_execute"]:
            raise AudioImportTransactionError("只有ready计划可以进入导入事务模型。")
        slot = plan["target"]["slot"]
        if (not isinstance(generation, int) or generation <= 0
                or (generation & 0xF0000000) != (slot << 28)
                or not (generation & 0x0FFFFFFF)):
            raise AudioImportTransactionError("导入generation无效。")
        _validated_execution_candidate(plan, candidate)
        _validated_execution_authorization(authorization, plan, candidate)
        if not hasattr(backend, "prepare") or not hasattr(backend, "persist") \
                or not hasattr(backend, "readback") or not hasattr(backend, "activate"):
            raise AudioImportTransactionError("导入事务后端接口不完整。")
        self.plan = plan
        self.candidate = candidate
        self.backend = backend
        self.generation = generation
        self.target_slot = slot
        self.current_selection = authorization["current_selection"]
        self.slot_generations = tuple(authorization["slot_generations"])
        if generation <= self.slot_generations[slot - 1]:
            raise AudioImportTransactionError("导入generation不是目标槽的新版本。")
        self.calls = []

    def _record(self, name, detail=None):
        self.calls.append(name if detail is None else f"{name}:{detail}")

    def _result(self, status, phases, *, candidate_saved=False,
                old_sound_retained=True, error=None):
        return dict(
            schema=AUDIO_IMPORT_TRANSACTION_SCHEMA, status=status,
            simulation=True, camera_access="not-run", write_attempted=False,
            plan_digest=self.plan["digest"],
            candidate_sha256=self.plan["candidate"]["sha256"],
            target_slot=self.target_slot,
            generation=self.generation, phases=phases,
            candidate_saved=candidate_saved,
            old_sound_retained=old_sound_retained,
            selection_unchanged=True,
            active_generation=self._active_generation(old_sound_retained),
            error=error,
        )

    def _active_generation(self, old_sound_retained):
        if self.current_selection != self.target_slot:
            return (self.slot_generations[self.current_selection - 1]
                    if self.current_selection else None)
        return None if old_sound_retained else self.generation

    def run(self):
        phases = []
        resource = None
        record = None
        try:
            phases.append("prepare")
            self._record("prepare")
            resource = self.backend.prepare(self.candidate, self.generation,
                                             self.target_slot)
            phases.append("persist")
            self._record("persist")
            record = self.backend.persist(resource, self.candidate,
                                          self.generation, self.target_slot)
            phases.append("readback")
            self._record("readback")
            if not self.backend.readback(record):
                self._abort(resource)
                return self._result("verification-failed", phases,
                                   candidate_saved=True, error="独立回读与候选不一致。")
            phases.append("activate")
            self._record("activate")
            self.backend.activate(record)
            phases.append("complete")
            self._record("complete")
            return self._result("completed", phases, candidate_saved=True,
                                old_sound_retained=False)
        except AudioImportTransactionError as exc:
            failed_phase = phases[-1] if phases else "prepare"
            if failed_phase == "activate":
                # Persist succeeded but activation did not. This is the existing
                # saved-not-playing boundary; the previous active sound remains.
                return self._result("saved-not-playing", phases,
                                    candidate_saved=True,
                                    error=str(exc) or "候选资源激活失败。")
            self._abort(resource)
            return self._result("failed", phases, candidate_saved=record is not None,
                                error=str(exc) or "导入事务失败。")

    def _abort(self, resource):
        if resource is not None and hasattr(self.backend, "abort"):
            self._record("abort")
            self.backend.abort(resource)


def simulate_audio_import_transaction(plan, candidate, backend, generation,
                                      authorization):
    """Run the offline sequencing model and return path-free evidence."""
    return AudioImportTransaction(plan, candidate, backend, generation,
                                  authorization).run()
