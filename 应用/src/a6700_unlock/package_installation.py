"""Install and rollback orchestration for a trusted device adapter.

Part names are logical adapter identifiers. The installer owns the lock and
durable preimages; the adapter provides fresh device observations. Digests
identify data for comparison and do not authorize writes or store recovery data.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import base64
import hashlib
import json
from pathlib import Path
import re
import uuid

from . import operations


SCHEMA = "a6700.package-installation-plan/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class InstallationPlanError(ValueError):
    pass


class PackageWriteUnconfirmed(RuntimeError):
    """An adapter operation may still be running. Stop all device access."""


def _is_sha256(value):
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


@dataclass(frozen=True)
class PackageBinding:
    package_id: str
    package_version: str
    package_sha256: str
    adapter_sha256: str
    device_identity_sha256: str
    model: str
    firmware: str

    def __post_init__(self):
        labels = (self.package_id, self.package_version, self.model, self.firmware)
        hashes = (self.package_sha256, self.adapter_sha256, self.device_identity_sha256)
        if any(not isinstance(label, str) or not label.strip() for label in labels):
            raise InstallationPlanError("能力包、版本或机身信息缺失")
        if not all(map(_is_sha256, hashes)):
            raise InstallationPlanError("能力包、适配或机身身份摘要无效")


@dataclass(frozen=True)
class PackagePart:
    part_id: str
    before_sha256: str
    after_sha256: str

    def __post_init__(self):
        if not isinstance(self.part_id, str) or not self.part_id.strip():
            raise InstallationPlanError("能力包分项标识无效")
        if not _is_sha256(self.before_sha256) or not _is_sha256(self.after_sha256):
            raise InstallationPlanError("能力包分项摘要无效")

    def reversed(self):
        return PackagePart(self.part_id, self.after_sha256, self.before_sha256)


@dataclass(frozen=True)
class PackageInstallationPlan:
    binding: PackageBinding
    action: str
    parts: tuple[PackagePart, ...]

    def __post_init__(self):
        if self.action not in ("install", "withdraw"):
            raise InstallationPlanError("不支持的能力包操作")
        if not isinstance(self.binding, PackageBinding):
            raise InstallationPlanError("缺少已核对的能力包绑定")
        if not isinstance(self.parts, tuple) or not self.parts or not all(
            isinstance(part, PackagePart) for part in self.parts
        ):
            raise InstallationPlanError("缺少有序的能力包分项")
        if len({part.part_id for part in self.parts}) != len(self.parts):
            raise InstallationPlanError("能力包分项重复")

    @property
    def plan_sha256(self):
        raw = json.dumps(self.to_record(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_record(self):
        return dict(schema=SCHEMA, **asdict(self))

    @property
    def writes(self):
        return tuple(part for part in self.parts if part.before_sha256 != part.after_sha256)

    def _check_scope(self, binding, observed):
        if binding != self.binding:
            raise InstallationPlanError("能力包、适配或机身身份已变化")
        if not isinstance(observed, Mapping) or set(observed) != {
            part.part_id for part in self.parts
        }:
            raise InstallationPlanError("机身回读范围与计划不一致")

    def check_preimages(self, binding, observed):
        self._check_scope(binding, observed)
        for part in self.parts:
            if observed[part.part_id] != part.before_sha256:
                raise InstallationPlanError(f"分项前态不匹配：{part.part_id}")

    def verify_readback(self, binding, observed):
        self._check_scope(binding, observed)
        for part in self.parts:
            if observed[part.part_id] != part.after_sha256:
                raise InstallationPlanError(f"分项独立回读不匹配：{part.part_id}")

    def rollback_writes(self, binding, observed):
        """Return a verified prefix to its preimages, in reverse order.

        Call after execution has stopped and every part has been observed
        again. Unknown data, changed guards, or a non-prefix state need
        reconciliation. Confirm the result by checking every preimage.
        """
        self._check_scope(binding, observed)
        completed = []
        seen_before = False
        for part in self.parts:
            actual = observed[part.part_id]
            if part.before_sha256 == part.after_sha256:
                if actual != part.before_sha256:
                    raise InstallationPlanError(f"分项守卫已变化：{part.part_id}")
            elif actual == part.before_sha256:
                seen_before = True
            elif actual == part.after_sha256 and not seen_before:
                completed.append(part)
            else:
                raise InstallationPlanError(f"分项状态无法确认：{part.part_id}")
        return tuple(part.reversed() for part in reversed(completed))


def plan_installation(binding, parts, observed, *, action):
    """Bind an install or withdrawal to the adapter's current observations.

    The adapter supplies the supported transition for the selected versions.
    A withdrawal must explicitly describe its targets, which may differ from
    a failed installation's preimages when dependencies share resources.
    """
    plan = PackageInstallationPlan(binding, action, tuple(parts))
    plan.check_preimages(binding, observed)
    return plan


def _read_snapshot(plan, adapter):
    binding, snapshot = adapter.read_snapshot(tuple(part.part_id for part in plan.parts))
    if not isinstance(snapshot, Mapping) or any(type(data) is not bytes for data in snapshot.values()):
        raise InstallationPlanError("适配器未返回独立读取的分项数据")
    snapshot = dict(snapshot)
    digests = {part_id: hashlib.sha256(data).hexdigest() for part_id, data in snapshot.items()}
    return binding, snapshot, digests


_CLOSED_RESULTS = {"verified", "no_op", "rolled_back", "not_started"}


def pending_package_installation(directory):
    """Return the first journal that needs reconciliation, if any."""
    for intent in sorted(Path(directory).glob("*/intent.json")):
        if intent.with_name('service-restore-failure.json').exists():
            return intent.parent
        try:
            result = json.loads(intent.with_name("result.json").read_text(encoding="utf-8"))
            if isinstance(result, dict) and result.get("status") in _CLOSED_RESULTS:
                definition = json.loads(intent.read_text(encoding="utf-8"))
                if (isinstance(definition, dict)
                        and _is_sha256(definition.get("plan_sha256"))
                        and definition["plan_sha256"] == result.get("plan_sha256")):
                    continue
        except (OSError, ValueError, TypeError):
            pass
        return intent.parent
    return None


def _journal_directory():
    # Keep one journal location across all callers of the shared device lock.
    return operations.host_lock_path().parent / "package-installations"


def guard_pending_package_installation():
    pending = pending_package_installation(_journal_directory())
    if pending is not None:
        raise InstallationPlanError(f"存在未闭合的能力包事务：{pending}")


def create_installation_journal(plan_record, plan_sha256, preimages):
    """Persist intent/preimages under the caller's already-held device lock.

    Shared by package installation and fixed audio imports. This does not
    authorize device access or mark a transaction complete.
    """
    if (not isinstance(plan_record, dict) or not _is_sha256(plan_sha256)
            or not isinstance(preimages, Mapping)
            or any(type(data) is not bytes for data in preimages.values())):
        raise InstallationPlanError('安装事务记录无效')
    guard_pending_package_installation()
    run = _journal_directory() / uuid.uuid4().hex
    operations.make_directory(run)
    operations.save_json(run / 'preimages.json', {
        part_id: base64.b64encode(data).decode('ascii')
        for part_id, data in preimages.items()})
    operations.save_json(run / 'intent.json', dict(plan=plan_record, plan_sha256=plan_sha256))
    return run


def execute_package_installation(plan, adapter, *, state_dir=None):
    """Run a preapproved plan under the shared lock using a built-in adapter.

    The caller selects and verifies the adapter and package before invoking
    this function. The adapter reads from the device independently of its
    writer and checks the part's preimage immediately before each mutation.
    Both apply_part and restore_part must return only after execution stops,
    or raise PackageWriteUnconfirmed if that cannot be established.
    """
    directory = Path(state_dir) if state_dir is not None else operations.default_state_dir()
    with operations.device_lock(directory):
        from .session import pending_session_write
        for session_directory in dict.fromkeys((operations.default_state_dir(), directory)):
            blocker, _ = pending_session_write(session_directory)
            if blocker:
                raise InstallationPlanError(blocker)
        binding, preimages, observed = _read_snapshot(plan, adapter)
        plan.check_preimages(binding, observed)
        run = create_installation_journal(plan.to_record(), plan.plan_sha256, preimages)
        return _execute_journaled(plan, adapter, run, preimages)


def _execute_journaled(plan, adapter, run, preimages):
    result = dict(plan_sha256=plan.plan_sha256, run_directory=str(run), attempted_parts=[],
                  rollback_parts=[], status="no_op", functional_acceptance="not_run")
    try:
        for index, part in enumerate(plan.writes):
            operations.save_json(run / f"apply-{index:04}.json", {"part_id": part.part_id})
            result["attempted_parts"].append(part.part_id)
            adapter.apply_part(part)
        if result["attempted_parts"]:
            binding, _, observed = _read_snapshot(plan, adapter)
            result["readback_sha256"] = observed
            plan.verify_readback(binding, observed)
            result["status"] = "verified"
    except (Exception, KeyboardInterrupt) as failure:
        result["error"] = str(failure) or type(failure).__name__
        if not result["attempted_parts"]:
            result["status"] = "not_started"
        elif isinstance(failure, (PackageWriteUnconfirmed, KeyboardInterrupt)):
            result["status"] = "uncertain"
        else:
            _restore_preimages(plan, adapter, run, preimages, result)
    operations.save_json(run / "result.json", result)
    return result


def _restore_preimages(plan, adapter, run, preimages, result):
    result["status"] = "uncertain"
    try:
        binding, _, observed = _read_snapshot(plan, adapter)
        result["recovery_observed_sha256"] = observed
        rollback = plan.rollback_writes(binding, observed)
        if len(rollback) > len(result["attempted_parts"]):
            raise InstallationPlanError("回读显示尚未执行的分项已改变")
        for index, part in enumerate(rollback):
            operations.save_json(run / f"restore-{index:04}.json", {"part_id": part.part_id})
            result["rollback_parts"].append(part.part_id)
            adapter.restore_part(part, preimages[part.part_id])
        binding, _, observed = _read_snapshot(plan, adapter)
        result["recovery_readback_sha256"] = observed
        plan.check_preimages(binding, observed)
    except (Exception, KeyboardInterrupt) as failure:
        result["rollback_error"] = str(failure) or type(failure).__name__
    else:
        result["status"] = "rolled_back"
