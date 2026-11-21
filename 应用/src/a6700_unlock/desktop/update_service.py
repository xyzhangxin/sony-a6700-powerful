"""Desktop-scoped update configuration and user-triggered checks."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import os
import re
import uuid
import zipfile

from ..online_updates import API_BASE, UpdateClient
from ..audio_import import (
    build_audio_import_batch_plan, build_audio_import_plan,
    confirm_audio_import_batch_plan, confirm_audio_import_plan,
    gate_audio_import_batch_execution, gate_audio_import_execution,
    validate_audio_import_batch_confirmation, validate_audio_import_batch_plan,
    validate_audio_import_confirmation, validate_audio_import_plan,
)
from ..audio_packages import (
    build_audio_bundle, build_audio_package, inspect_pcm_wav,
    MAX_BUNDLE_BYTES, MAX_WAV_BYTES,
)
from ..capability_packages import custom_shutter_sound_available
from ..custom_sound_slots import valid_slot, slot_inventory
from ..host_io import open_exclusive, sync_directory
from ..operations import default_state_dir
from ..operations import make_directory
from ..package_updates import LocalUpdateCache


def load_trust_keys():
    from importlib.resources import files
    raw = files("a6700_unlock").joinpath("data/update-trust.json").read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1 or not isinstance(data.get("keys"), dict):
        raise ValueError("更新信任配置格式无效")
    if len(data["keys"]) > 16:
        raise ValueError("更新信任配置包含过多密钥")
    result = {}
    for key_id, encoded in data["keys"].items():
        if not isinstance(key_id, str) or not isinstance(encoded, str):
            raise ValueError("更新信任配置包含无效密钥")
        public = base64.b64decode(encoded, validate=True)
        if len(public) != 32 or base64.b64encode(public).decode() != encoded:
            raise ValueError("更新信任配置公钥无效")
        result[key_id] = encoded
    return result


class DesktopUpdateService:
    def __init__(self, state_dir=None, *, trusted_keys=None, transport=None,
                 api_base=API_BASE, adapter_set=None, sound_slot_reader=None,
                 audio_import_ready=False):
        root = Path(state_dir) if state_dir is not None else default_state_dir()
        self.cache = LocalUpdateCache(root / "update-cache")
        self.root = root
        self.audio_import_plans = root / "audio-import-plans"
        self.audio_import_batches = root / "audio-import-batches"
        self.audio_candidates = root / "audio-candidates"
        self.audio_import_confirmations = root / "audio-import-confirmations"
        self.audio_import_batch_confirmations = root / "audio-import-batch-confirmations"
        self.audio_exports = root / "audio-exports"
        self.trusted_keys = load_trust_keys() if trusted_keys is None else trusted_keys
        self.client = UpdateClient(self.cache, self.trusted_keys,
                                   transport=transport, api_base=api_base)
        self.adapter_set = adapter_set
        self.sound_slot_reader = sound_slot_reader
        self.audio_import_ready = audio_import_ready is True

    def audio_slots(self):
        """Use an adapter-owned snapshot; this service never opens USB."""
        records = self.sound_slot_reader() if self.sound_slot_reader else None
        return slot_inventory(records)

    @property
    def configured(self):
        return bool(self.trusted_keys)

    def _custom_shutter_sound_available(self):
        if self.audio_import_ready:
            return True
        if self.adapter_set is None:
            return False
        return custom_shutter_sound_available(self.adapter_set)

    def check(self, now=None):
        if not self.configured:
            return dict(status="unconfigured", cache_status="unavailable")
        return self.client.check("stable", now or datetime.now(timezone.utc))

    def audio_catalog(self, now=None):
        """Return a path-free catalog of verified, locally cached audio."""
        if not self.configured:
            return dict(status="unconfigured", packages=[])
        try:
            active = self.cache.active_audio_bundles(
                "stable", self.trusted_keys, now or datetime.now(timezone.utc), for_write=False)
        except Exception:
            return dict(status="unavailable", packages=[])
        packages = []
        for item in active["bundles"]:
            manifest = item["bundle"]["manifest"]
            packages.append(dict(
                package_id=manifest["package_id"], version=manifest["version"],
                title=manifest["title"], description=manifest["description"],
                author=manifest["author"], license=manifest["license"],
                digest=manifest["digest"],
                sounds=[dict(
                    sound_id=sound["sound_id"], title=sound["title"],
                    description=sound["description"], duration_ms=sound["duration_ms"],
                    sample_rate=sound["sample_rate"], channels=sound["channels"],
                    bit_depth=sound["bit_depth"], bytes=sound["bytes"],
                    tags=sound["tags"]) for sound in manifest["sounds"]],
            ))
        return dict(status=active["status"], sequence=active["sequence"], packages=packages)

    def _market_audio_package(self, package_id, now=None):
        active = self.cache.active_audio_bundles(
            "stable", self.trusted_keys, now or datetime.now(timezone.utc))
        selected_item = None
        for item in active["bundles"]:
            manifest = item["bundle"]["manifest"]
            if manifest["package_id"] == package_id:
                selected_item = (item, manifest)
                break
        if selected_item is None:
            raise ValueError("本机缓存中没有所选音频包。")
        item, manifest = selected_item
        artifact = item["artifact"]
        archive_path = self.cache.artifacts / artifact["sha256"]
        if not archive_path.is_file() or archive_path.is_symlink():
            raise ValueError("音频包缓存缺失或类型无效。")
        archive_data = archive_path.read_bytes()
        if (len(archive_data) != artifact["bytes"]
                or hashlib.sha256(archive_data).hexdigest() != artifact["sha256"]):
            raise ValueError("音频包缓存校验失败。")
        return item, manifest, archive_data

    def plan_audio_import(self, source, *, target_slot=1, path=None,
                          package_id=None, sound_id=None, filename=None,
                          now=None):
        """Validate a local or marketplace WAV and build a non-executing plan."""
        if filename is not None and not isinstance(filename, str):
            raise ValueError("音频写入名称无效。")
        if source == "local":
            if not isinstance(path, str) or not path:
                raise ValueError("本地音频路径无效。")
            selected = Path(path)
            if selected.is_symlink() or not selected.is_file():
                raise ValueError("本地音频必须是普通文件。")
            if selected.stat().st_size > MAX_WAV_BYTES:
                raise ValueError("本地音频超过512KiB。")
            data = selected.read_bytes()
            candidate = inspect_pcm_wav(data, filename if filename is not None else selected.name)
            origin = dict(type="local")
        elif source == "market":
            item, manifest, archive_data = self._market_audio_package(package_id, now)
            sound = next(
                (candidate for candidate in manifest["sounds"]
                 if candidate["sound_id"] == sound_id), None)
            if sound is None:
                raise ValueError("本机缓存中没有所选音频。")
            try:
                with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
                    data = archive.read(sound["filename"])
            except (OSError, KeyError, zipfile.BadZipFile) as exc:
                raise ValueError("音频包读取失败。") from exc
            candidate = inspect_pcm_wav(
                data, filename if filename is not None else sound["filename"],
                title=sound["title"],
                description=sound["description"], tags=sound["tags"])
            origin = dict(
                type="market", package_id=manifest["package_id"],
                package_version=manifest["version"], package_digest=manifest["digest"],
                sound_id=sound["sound_id"], author=manifest["author"],
                license=manifest["license"],
            )
        else:
            raise ValueError("音频来源无效。")
        if not valid_slot(target_slot):
            raise ValueError("音频导入槽位无效。")
        self._stage_audio_candidate(data, candidate["sha256"])
        plan = build_audio_import_plan(
            candidate, origin, target_slot=target_slot,
            capability_available=self._custom_shutter_sound_available(),
            implementation_ready=self.audio_import_ready)
        self._save_audio_import_plan(plan)
        return plan

    def plan_audio_import_batch(self, package_id, *, now=None):
        """Plan every sound in one marketplace package into sequential slots."""
        if not isinstance(package_id, str) or not package_id:
            raise ValueError("音频包 ID 无效。")
        item, manifest, archive_data = self._market_audio_package(package_id, now)
        sounds = manifest["sounds"]
        if not sounds or len(sounds) > 10:
            raise ValueError("音频包音频数量必须在1到10之间。")
        candidates = []
        origins = []
        try:
            with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
                for sound in sounds:
                    data = archive.read(sound["filename"])
                    candidate = inspect_pcm_wav(
                        data, sound["filename"], title=sound["title"],
                        description=sound["description"], tags=sound["tags"])
                    self._stage_audio_candidate(data, candidate["sha256"])
                    candidates.append(candidate)
                    origins.append(dict(
                        type="market", package_id=manifest["package_id"],
                        package_version=manifest["version"],
                        package_digest=manifest["digest"],
                        sound_id=sound["sound_id"], author=manifest["author"],
                        license=manifest["license"],
                    ))
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise ValueError("音频包读取失败。") from exc
        batch = build_audio_import_batch_plan(
            candidates, origins,
            capability_available=self._custom_shutter_sound_available())
        for plan in batch["plans"]:
            self._save_audio_import_plan(plan)
        self._save_audio_import_batch_plan(batch)
        return batch

    def export_audio_package(self, path, *, package_id, version, title,
                             description, author, license, sound_title=None):
        """Export one local WAV as a deterministic, data-only ZIP bundle."""
        if not isinstance(path, str) or not path:
            raise ValueError("本地音频路径无效。")
        selected = Path(path)
        if selected.is_symlink() or not selected.is_file():
            raise ValueError("本地音频必须是普通文件。")
        if selected.stat().st_size > MAX_WAV_BYTES:
            raise ValueError("本地音频超过512KiB。")
        data = selected.read_bytes()
        package = build_audio_package(
            [dict(filename=selected.name, data=data, title=sound_title)],
            package_id=package_id, version=version, title=title,
            description=description, author=author, license=license,
        )
        bundle = build_audio_bundle(
            package, [dict(filename=selected.name, data=data)])
        if len(bundle) > MAX_BUNDLE_BYTES:
            raise ValueError("音频包导出超过大小限制。")
        make_directory(self.audio_exports)
        filename = f"{package['package_id']}-{package['version']}.zip"
        destination = self.audio_exports / filename
        digest = hashlib.sha256(bundle).hexdigest()
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频包导出文件类型无效。")
            existing = destination.read_bytes()
            if existing == bundle:
                return self._audio_export_result(package, destination, digest, existing)
            raise ValueError("同名音频包导出已存在；请提高版本号或删除旧导出。")
        temporary = self.audio_exports / (filename + ".tmp-" + uuid.uuid4().hex)
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "wb") as output:
                output.write(bundle)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_exports)
        finally:
            if temporary.exists():
                temporary.unlink()
        return self._audio_export_result(package, destination, digest, bundle)

    @staticmethod
    def _audio_export_result(package, destination, digest, bundle):
        return dict(
            status="exported", package_id=package["package_id"],
            version=package["version"], package_digest=package["digest"],
            bundle_sha256=digest, bundle_bytes=len(bundle),
            sound_count=len(package["sounds"]),
            filename=destination.name, export_path=str(destination),
        )

    def _stage_audio_candidate(self, data, expected_sha256):
        """Persist the exact validated WAV bytes by content hash."""
        if (not isinstance(data, bytes) or not data
                or not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or len(data) > MAX_WAV_BYTES):
            raise ValueError("音频候选内容无效。")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise ValueError("音频候选哈希不匹配。")
        make_directory(self.audio_candidates)
        destination = self.audio_candidates / (digest + ".wav")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频候选缓存类型无效。")
            existing = destination.read_bytes()
            if existing != data:
                raise ValueError("音频候选缓存已损坏；不会覆盖。")
            return
        temporary = self.audio_candidates / (digest + ".tmp-" + uuid.uuid4().hex)
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_candidates)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load_audio_candidate(self, sha256):
        """Load a staged candidate for a future in-process executor."""
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("音频候选摘要无效。")
        path = self.audio_candidates / (sha256 + ".wav")
        if path.is_symlink() or not path.is_file():
            raise ValueError("本机没有对应的音频候选。")
        data = path.read_bytes()
        if not data or len(data) > MAX_WAV_BYTES or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("音频候选校验失败。")
        return data

    def _save_audio_import_plan(self, plan):
        make_directory(self.audio_import_plans)
        destination = self.audio_import_plans / (plan["digest"] + ".json")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频导入计划记录类型无效。")
            validate_audio_import_plan(json.loads(destination.read_text(encoding='utf-8')))
            return
        temporary = self.audio_import_plans / (plan["digest"] + ".json.tmp")
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(plan, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_import_plans)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _save_audio_import_batch_plan(self, batch):
        make_directory(self.audio_import_batches)
        destination = self.audio_import_batches / (batch["digest"] + ".json")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频批量导入计划记录类型无效。")
            validate_audio_import_batch_plan(json.loads(destination.read_text(encoding='utf-8')))
            return
        temporary = self.audio_import_batches / (
            batch["digest"] + ".json.tmp-" + uuid.uuid4().hex)
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(batch, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_import_batches)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load_audio_import_batch(self, batch_digest):
        if (not isinstance(batch_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", batch_digest)):
            raise ValueError("音频批量导入计划摘要无效。")
        path = self.audio_import_batches / (batch_digest + ".json")
        if path.is_symlink() or not path.is_file():
            raise ValueError("本机没有对应的音频批量导入计划。")
        try:
            batch = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError("音频批量导入计划记录无效。") from exc
        validate_audio_import_batch_plan(batch)
        if batch["digest"] != batch_digest:
            raise ValueError("音频批量导入计划摘要不匹配。")
        return batch

    def confirm_audio_import(self, plan_digest, confirmed):
        if (not isinstance(plan_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", plan_digest)):
            raise ValueError("音频导入计划摘要无效。")
        path = self.audio_import_plans / (plan_digest + ".json")
        if path.is_symlink() or not path.is_file():
            raise ValueError("本机没有对应的音频导入计划。")
        try:
            plan = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError("音频导入计划记录无效。") from exc
        validate_audio_import_plan(plan)
        if plan["digest"] != plan_digest:
            raise ValueError("音频导入计划摘要不匹配。")
        # A confirmed plan must remain executable from app-owned bytes even if
        # the original WAV or SD card has since been removed.
        self.load_audio_candidate(plan["candidate"]["sha256"])
        confirmation = confirm_audio_import_plan(plan, confirmed)
        validate_audio_import_confirmation(confirmation, plan=plan)
        self._save_audio_import_confirmation(confirmation)
        return confirmation

    def _save_audio_import_confirmation(self, confirmation):
        make_directory(self.audio_import_confirmations)
        destination = self.audio_import_confirmations / (
            confirmation["plan_digest"] + ".json")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频导入确认记录类型无效。")
            try:
                existing = json.loads(destination.read_text(encoding='utf-8'))
            except json.JSONDecodeError as exc:
                raise ValueError("音频导入确认记录无效。") from exc
            validate_audio_import_confirmation(existing)
            if existing == confirmation:
                return
        temporary = self.audio_import_confirmations / (
            confirmation["plan_digest"] + ".json.tmp-" + uuid.uuid4().hex)
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(confirmation, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_import_confirmations)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _save_audio_import_batch_confirmation(self, confirmation):
        make_directory(self.audio_import_batch_confirmations)
        destination = self.audio_import_batch_confirmations / (
            confirmation["batch_digest"] + ".json")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("音频批量导入确认记录类型无效。")
            try:
                existing = json.loads(destination.read_text(encoding='utf-8'))
            except json.JSONDecodeError as exc:
                raise ValueError("音频批量导入确认记录无效。") from exc
            validate_audio_import_batch_confirmation(existing)
            if existing == confirmation:
                return
        temporary = self.audio_import_batch_confirmations / (
            confirmation["batch_digest"] + ".json.tmp-" + uuid.uuid4().hex)
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(confirmation, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(self.audio_import_batch_confirmations)
        finally:
            if temporary.exists():
                temporary.unlink()

    def confirm_audio_import_batch(self, batch_digest, confirmed):
        """Confirm all slot plans after their app-owned bytes are verified."""
        batch = self._load_audio_import_batch(batch_digest)
        for plan in batch["plans"]:
            self.load_audio_candidate(plan["candidate"]["sha256"])
        confirmation = confirm_audio_import_batch_plan(batch, confirmed)
        validate_audio_import_batch_confirmation(confirmation, batch=batch)
        for slot_confirmation in confirmation["results"]:
            self._save_audio_import_confirmation(slot_confirmation)
        self._save_audio_import_batch_confirmation(confirmation)
        return confirmation

    def gate_audio_import(self, plan_digest):
        """Load exact plan/confirmation/candidate bytes and evaluate the gate."""
        if (not isinstance(plan_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", plan_digest)):
            raise ValueError("音频导入计划摘要无效。")
        plan_path = self.audio_import_plans / (plan_digest + ".json")
        confirmation_path = self.audio_import_confirmations / (plan_digest + ".json")
        if plan_path.is_symlink() or not plan_path.is_file():
            raise ValueError("本机没有对应的音频导入计划。")
        if confirmation_path.is_symlink() or not confirmation_path.is_file():
            raise ValueError("本机没有对应的音频导入确认。")
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            confirmation = json.loads(confirmation_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError("音频导入计划或确认记录无效。") from exc
        validate_audio_import_plan(plan)
        if plan["digest"] != plan_digest:
            raise ValueError("音频导入计划摘要不匹配。")
        validate_audio_import_confirmation(confirmation, plan=plan)
        candidate = self.load_audio_candidate(plan["candidate"]["sha256"])
        return gate_audio_import_execution(plan, confirmation, candidate)

    def prepare_audio_import_execution(self, plan_digest):
        """Load an explicitly confirmed plan and its exact staged candidate."""
        if not self.audio_import_ready:
            raise ValueError("自定义快门声能力或导入执行器未就绪；当前应用未开放导入。")
        if (not isinstance(plan_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", plan_digest)):
            raise ValueError("音频导入计划摘要无效。")
        plan_path = self.audio_import_plans / (plan_digest + ".json")
        confirmation_path = self.audio_import_confirmations / (plan_digest + ".json")
        if plan_path.is_symlink() or not plan_path.is_file():
            raise ValueError("本机没有对应的音频导入计划。")
        if confirmation_path.is_symlink() or not confirmation_path.is_file():
            raise ValueError("本机没有对应的音频导入确认。")
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            confirmation = json.loads(confirmation_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError("音频导入计划或确认记录无效。") from exc
        validate_audio_import_plan(plan)
        if plan["digest"] != plan_digest:
            raise ValueError("音频导入计划摘要不匹配。")
        validate_audio_import_confirmation(confirmation, plan=plan)
        if confirmation["user_confirmation"] != "confirmed":
            raise ValueError("音频导入尚未获得用户确认。")
        if (not plan["capability"]["available"]
                or not plan["capability"]["implementation_ready"]
                or not plan["can_execute"]):
            raise ValueError("自定义快门声能力或导入执行器未就绪；已确认计划不会进入设备阶段。")
        candidate = self.load_audio_candidate(plan["candidate"]["sha256"])
        return plan, candidate

    def gate_audio_import_batch(self, batch_digest):
        """Load exact batch records and evaluate every slot gate locally."""
        batch = self._load_audio_import_batch(batch_digest)
        aggregate_path = self.audio_import_batch_confirmations / (
            batch_digest + ".json")
        if aggregate_path.is_symlink() or not aggregate_path.is_file():
            raise ValueError("本机没有对应的音频批量导入确认。")
        try:
            aggregate = json.loads(aggregate_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError("音频批量导入确认记录无效。") from exc
        validate_audio_import_batch_confirmation(aggregate, batch=batch)
        for plan, expected in zip(batch["plans"], aggregate["results"]):
            path = self.audio_import_confirmations / (plan["digest"] + ".json")
            if path.is_symlink() or not path.is_file():
                raise ValueError("音频批量导入确认与子计划确认不匹配。")
            try:
                actual = json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as exc:
                raise ValueError("音频子计划确认记录无效。") from exc
            validate_audio_import_confirmation(actual, plan=plan)
            if actual != expected:
                raise ValueError("音频批量导入确认与子计划确认不匹配。")
        candidates = [
            self.load_audio_candidate(plan["candidate"]["sha256"])
            for plan in batch["plans"]
        ]
        return gate_audio_import_batch_execution(
            batch, aggregate, candidates)

    def audio_preview(self, package_id, sound_id, now=None, *, preview_dir=None):
        """Extract one verified WAV into the app-owned preview directory."""
        if not self.configured:
            return dict(status="unconfigured")
        if (not isinstance(package_id, str) or not package_id
                or not isinstance(sound_id, str) or not sound_id):
            raise ValueError("音频选择无效。")
        active = self.cache.active_audio_bundles(
            "stable", self.trusted_keys, now or datetime.now(timezone.utc))
        selected = None
        for item in active["bundles"]:
            manifest = item["bundle"]["manifest"]
            if manifest["package_id"] != package_id:
                continue
            for sound in manifest["sounds"]:
                if sound["sound_id"] == sound_id:
                    selected = (item, manifest, sound)
                    break
            if selected is not None:
                break
        if selected is None:
            raise ValueError("本机缓存中没有所选音频。")
        item, manifest, sound = selected
        artifact = item["artifact"]
        source = self.cache.artifacts / artifact["sha256"]
        if not source.is_file() or source.is_symlink():
            raise ValueError("音频包缓存缺失或类型无效。")
        data = source.read_bytes()
        if (len(data) != artifact["bytes"]
                or hashlib.sha256(data).hexdigest() != artifact["sha256"]):
            raise ValueError("音频包缓存校验失败。")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                content = archive.read(sound["filename"])
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise ValueError("音频包读取失败。") from exc
        if (len(content) != sound["bytes"]
                or hashlib.sha256(content).hexdigest() != sound["sha256"]):
            raise ValueError("音频文件校验失败。")

        preview_dir = self.root / "audio-previews" if preview_dir is None else Path(preview_dir)
        make_directory(preview_dir)
        for old in preview_dir.iterdir():
            if old.suffix in (".wav", ".tmp") and old.is_file() and not old.is_symlink():
                old.unlink()
        temporary = preview_dir / (sound["sound_id"] + ".wav.tmp")
        destination = preview_dir / (sound["sound_id"] + ".wav")
        try:
            descriptor = open_exclusive(temporary)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(preview_dir)
        finally:
            if temporary.exists():
                temporary.unlink()
        return dict(
            status="ready", path=str(destination), package_id=manifest["package_id"],
            package_version=manifest["version"], sound_id=sound["sound_id"],
            title=sound["title"], duration_ms=sound["duration_ms"],
            sample_rate=sound["sample_rate"], channels=sound["channels"],
            bit_depth=sound["bit_depth"], bytes=sound["bytes"],
        )
