import unittest

from a6700_unlock.audio_import import (
    AUDIO_IMPORT_BATCH_SCHEMA, AUDIO_IMPORT_PLAN_SCHEMA, EXECUTION_STAGES,
    AudioImportError, AudioImportTransactionError,
    build_audio_import_plan, confirm_audio_import_plan,
    build_audio_import_batch_plan, validate_audio_import_batch_plan,
    confirm_audio_import_batch_plan, gate_audio_import_batch_execution,
    validate_audio_import_batch_confirmation,
    gate_audio_import_execution, simulate_audio_import_transaction,
    validate_audio_import_plan)
from a6700_unlock.audio_packages import inspect_pcm_wav


def pcm_wav():
    payload = b"\x01\x00" * 2
    return (b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00" +
            b"data" + len(payload).to_bytes(4, "little") + payload)


def setting_preflight(**overrides):
    value = dict(cluster_bytes=16384, free_clusters=100,
                 directory_exists=False, existing_record_bytes=[None] * 10)
    value.update(overrides)
    return value


class AudioImportPlanTests(unittest.TestCase):
    def setUp(self):
        self.candidate = inspect_pcm_wav(pcm_wav(), "camera-shutter.wav")

    def test_current_plan_is_blocked_until_capability_and_executor_exist(self):
        plan = build_audio_import_plan(self.candidate, dict(type="local"))
        self.assertEqual(plan["schema"], AUDIO_IMPORT_PLAN_SCHEMA)
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["can_execute"])
        self.assertEqual(plan["reasons"][0]["id"],
                         "custom-shutter-sound-capability-unavailable")
        self.assertEqual(plan["evidence"]["camera_access"], "not-run")
        self.assertFalse(plan["evidence"]["write_attempted"])
        validate_audio_import_plan(plan)

    def test_future_ready_plan_still_requires_user_and_device_stages(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="market", package_id="community",
                                 package_version="1.0.0", package_digest="a" * 64,
                                 sound_id="camera", author="Morris", license="CC-BY-4.0"),
            capability_available=True, implementation_ready=True)
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["can_execute"])
        self.assertEqual(plan["requirements"][2]["id"], "user-confirmation")
        self.assertEqual(plan["requirements"][3]["id"], "fresh-device-preflight")
        validate_audio_import_plan(plan)

    def test_plan_binds_one_of_ten_target_slots(self):
        plan1 = build_audio_import_plan(
            self.candidate, dict(type="local"), target_slot=1,
            capability_available=True, implementation_ready=True)
        plan2 = build_audio_import_plan(
            self.candidate, dict(type="local"), target_slot=10,
            capability_available=True, implementation_ready=True)
        self.assertEqual(plan1["target"], dict(
            slot_count=10, slot=1, label="slot1", factory_fallback="KASHA"))
        self.assertEqual(plan2["target"]["slot"], 10)
        self.assertEqual(plan2["target"]["label"], "slot10")
        self.assertNotEqual(plan1["digest"], plan2["digest"])
        with self.assertRaisesRegex(AudioImportError, "音频导入槽位无效"):
            build_audio_import_plan(self.candidate, dict(type="local"), target_slot=11)

    def test_batch_plan_maps_ten_candidates_to_ten_slots(self):
        candidates = [dict(self.candidate, filename=f"sound-{index}.wav")
                      for index in range(1, 11)]
        batch = build_audio_import_batch_plan(
            candidates, dict(type="local"),
            capability_available=True, implementation_ready=True)
        self.assertEqual(batch["schema"], AUDIO_IMPORT_BATCH_SCHEMA)
        self.assertEqual(batch["slot_count"], 10)
        self.assertEqual([plan["target"]["slot"] for plan in batch["plans"]],
                         list(range(1, 11)))
        self.assertTrue(batch["can_execute"])
        validate_audio_import_batch_plan(batch)

        with self.assertRaisesRegex(AudioImportError, "条目数量"):
            build_audio_import_batch_plan(candidates + [self.candidate],
                                          dict(type="local"))

        tampered = dict(batch)
        tampered["slot_count"] = 9
        with self.assertRaisesRegex(AudioImportError, "摘要不匹配"):
            validate_audio_import_batch_plan(tampered)

    def test_invalid_candidates_and_digest_tampering_are_rejected(self):
        invalid = dict(self.candidate, peak_amplitude=0.1, rms_amplitude=0.2)
        with self.assertRaises(AudioImportError):
            build_audio_import_plan(invalid, dict(type="local"))
        with self.assertRaises(AudioImportError):
            build_audio_import_plan(self.candidate, dict(type="unknown"))
        plan = build_audio_import_plan(self.candidate, dict(type="local"))
        plan["candidate"]["title"] = "Changed"
        with self.assertRaisesRegex(AudioImportError, "摘要不匹配"):
            validate_audio_import_plan(plan)

    def test_wav_candidate_is_limited_to_512kib(self):
        too_large = dict(self.candidate, bytes=512 * 1024 + 1)
        with self.assertRaisesRegex(AudioImportError, "512KiB"):
            build_audio_import_plan(too_large, dict(type="local"))

    def test_batch_confirmation_and_gate_aggregate_all_slot_boundaries(self):
        candidates = [dict(self.candidate, filename=f"sound-{index}.wav")
                      for index in range(1, 4)]
        batch = build_audio_import_batch_plan(
            candidates, dict(type="local"),
            capability_available=True, implementation_ready=True)
        declined = confirm_audio_import_batch_plan(batch, False)
        self.assertEqual(declined["status"], "cancelled")
        self.assertEqual(declined["user_confirmation"], "declined")
        self.assertEqual(len(declined["results"]), 3)
        validate_audio_import_batch_confirmation(declined, batch=batch)
        declined_gate = gate_audio_import_batch_execution(
            batch, declined, [pcm_wav()] * 3)
        self.assertEqual(declined_gate["status"], "rejected")
        self.assertEqual(declined_gate["slot_statuses"][0]["status"], "rejected")

        confirmed = confirm_audio_import_batch_plan(batch, True)
        self.assertEqual(confirmed["status"], "blocked")
        self.assertEqual(confirmed["user_confirmation"], "confirmed")
        self.assertEqual(confirmed["camera_access"], "not-run")
        self.assertFalse(confirmed["write_attempted"])
        validate_audio_import_batch_confirmation(confirmed, batch=batch)
        gate = gate_audio_import_batch_execution(batch, confirmed, [pcm_wav()] * 3)
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["slot_statuses"][1]["stages"]
                         ["fresh-device-preflight"], "blocked")
        self.assertEqual(gate["camera_access"], "not-run")
        self.assertFalse(gate["write_attempted"])

        preflight = dict(fresh=True, compatible=True, capability_installed=True,
                         profile_sha256="profile", selection=0,
                         slot_generations=[0x10000006, 0x20000003, 0x30000001] + [0] * 7,
                         setting=setting_preflight())
        authorized = gate_audio_import_batch_execution(
            batch, confirmed, [pcm_wav()] * 3, preflight=preflight,
            lock=dict(held=True, owner="core"), executor_available=True)
        self.assertEqual(authorized["status"], "authorized")
        self.assertEqual([item["status"] for item in authorized["slot_statuses"]],
                         ["authorized"] * 3)

        insufficient = gate_audio_import_batch_execution(
            batch, confirmed, [pcm_wav()] * 3,
            preflight=dict(preflight, setting=setting_preflight(free_clusters=3)),
            lock=dict(held=True, owner="core"), executor_available=True)
        self.assertEqual(insufficient["status"], "blocked")
        self.assertEqual([item["status"] for item in insufficient["slot_statuses"]],
                         ["blocked"] * 3)
        self.assertEqual(insufficient["reasons"][0]["id"],
                         "setting-capacity-insufficient")
        self.assertEqual(insufficient["capacity"]["required_peak_clusters"], 4)

        tampered = dict(confirmed, batch_digest="0" * 64)
        with self.assertRaisesRegex(AudioImportError, "不匹配"):
            validate_audio_import_batch_confirmation(tampered, batch=batch)

    def test_confirmation_never_grants_device_access_in_current_implementation(self):
        plan = build_audio_import_plan(self.candidate, dict(type="local"))
        declined = confirm_audio_import_plan(plan, False)
        self.assertEqual(declined["status"], "cancelled")
        self.assertEqual(declined["user_confirmation"], "declined")
        self.assertEqual(declined["camera_access"], "not-run")
        self.assertFalse(declined["write_attempted"])

        confirmed = confirm_audio_import_plan(plan, True)
        self.assertEqual(confirmed["status"], "blocked")
        self.assertEqual(confirmed["user_confirmation"], "confirmed")
        self.assertEqual(confirmed["reasons"], plan["reasons"])
        self.assertEqual(confirmed["camera_access"], "not-run")
        self.assertFalse(confirmed["write_attempted"])

    def test_execution_gate_requires_confirmation_preflight_lock_and_executor(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="local"),
            capability_available=True, implementation_ready=True)
        declined = confirm_audio_import_plan(plan, False)
        result = gate_audio_import_execution(plan, declined, pcm_wav())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["stages"]["user-confirmation"], "rejected")
        self.assertEqual(result["camera_access"], "not-run")
        self.assertFalse(result["write_attempted"])

        confirmed = confirm_audio_import_plan(plan, True)
        result = gate_audio_import_execution(plan, confirmed, pcm_wav())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"]["fresh-device-preflight"], "blocked")

        preflight = dict(fresh=True, compatible=True, capability_installed=True,
                         profile_sha256="profile", selection=2,
                         slot_generations=[0x10000006, 0x20000003] + [0] * 8,
                         setting=setting_preflight())
        result = gate_audio_import_execution(plan, confirmed, pcm_wav(),
                                             preflight=preflight)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"]["shared-device-lock"], "blocked")

        lock = dict(held=True, owner="core")
        result = gate_audio_import_execution(plan, confirmed, pcm_wav(),
                                             preflight=preflight, lock=lock)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"]["import-executor"], "blocked")
        self.assertEqual(result["reasons"][0]["id"],
                         "custom-shutter-sound-executor-unavailable")
        self.assertEqual(result["camera_access"], "not-run")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(result["target_slot"], 1)
        self.assertEqual(result["current_selection"], 2)
        self.assertEqual(result["slot_generations"],
                         [0x10000006, 0x20000003] + [0] * 8)

        authorized = gate_audio_import_execution(
            plan, confirmed, pcm_wav(), preflight=preflight, lock=lock,
            executor_available=True)
        self.assertEqual(authorized["status"], "authorized")
        for stage in EXECUTION_STAGES:
            self.assertEqual(authorized["stages"][stage], "satisfied")
        self.assertEqual(authorized["camera_access"], "not-run")
        self.assertFalse(authorized["write_attempted"])
        self.assertEqual(authorized["target_slot"], 1)

        with self.assertRaisesRegex(AudioImportError, "音频候选"):
            gate_audio_import_execution(plan, confirmed, b"not-the-candidate",
                                        preflight=preflight, lock=lock)

    def test_execution_gate_requires_setting_fat_capacity(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="local"),
            capability_available=True, implementation_ready=True)
        confirmed = confirm_audio_import_plan(plan, True)
        base = dict(fresh=True, compatible=True, capability_installed=True,
                    profile_sha256="profile", selection=0,
                    slot_generations=[0] * 10)
        insufficient = gate_audio_import_execution(
            plan, confirmed, pcm_wav(),
            preflight=dict(base, setting=setting_preflight(free_clusters=1)),
            lock=dict(held=True, owner="core"), executor_available=True)
        self.assertEqual(insufficient["status"], "blocked")
        self.assertEqual(insufficient["stages"]["fresh-device-preflight"], "blocked")
        self.assertEqual(insufficient["reasons"][0]["id"], "setting-capacity-insufficient")
        self.assertFalse(insufficient["capacity"]["sufficient"])

        sufficient = gate_audio_import_execution(
            plan, confirmed, pcm_wav(),
            preflight=dict(base, setting=setting_preflight(free_clusters=2)),
            lock=dict(held=True, owner="core"), executor_available=True)
        self.assertEqual(sufficient["status"], "authorized")
        self.assertTrue(sufficient["capacity"]["sufficient"])

        replacement = gate_audio_import_execution(
            plan, confirmed, pcm_wav(),
            preflight=dict(base, setting=setting_preflight(
                free_clusters=2, directory_exists=True,
                existing_record_bytes=[200] + [None] * 9)),
            lock=dict(held=True, owner="core"), executor_available=True)
        self.assertEqual(replacement["status"], "authorized")
        self.assertEqual(replacement["capacity"]["required_peak_clusters"], 2)

    def test_transaction_model_success_and_failure_boundaries(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="local"),
            capability_available=True, implementation_ready=True)
        confirmation = confirm_audio_import_plan(plan, True)
        authorization = gate_audio_import_execution(
            plan, confirmation, pcm_wav(),
            preflight=dict(fresh=True, compatible=True, capability_installed=True,
                           profile_sha256="profile", selection=2,
                           slot_generations=[0x10000006, 0x20000003] + [0] * 8,
                           setting=setting_preflight()),
            lock=dict(held=True, owner="core"), executor_available=True)

        class Backend:
            def __init__(self, fail_at=None, readback=True):
                self.fail_at = fail_at
                self.readback_result = readback
                self.aborted = False

            def prepare(self, candidate, generation, slot):
                if self.fail_at == "prepare":
                    raise AudioImportTransactionError("prepare failed")
                return dict(candidate=candidate, generation=generation)

            def persist(self, resource, candidate, generation, slot):
                if self.fail_at == "persist":
                    raise AudioImportTransactionError("persist failed")
                return dict(resource=resource, candidate=candidate,
                            generation=generation)

            def readback(self, record):
                return self.readback_result

            def activate(self, record):
                if self.fail_at == "activate":
                    raise AudioImportTransactionError("activate failed")

            def abort(self, resource):
                self.aborted = True

        tampered = dict(authorization, plan_digest="0" * 64)
        with self.assertRaisesRegex(AudioImportTransactionError, "授权无效"):
            simulate_audio_import_transaction(
                plan, pcm_wav(), Backend(), 0x10000007, tampered)
        incomplete = dict(authorization, stages={
            key: value for key, value in authorization["stages"].items()
            if key != "shared-device-lock"
        })
        with self.assertRaisesRegex(AudioImportTransactionError, "阶段不完整"):
            simulate_audio_import_transaction(
                plan, pcm_wav(), Backend(), 0x10000007, incomplete)
        with self.assertRaisesRegex(AudioImportError, "音频候选"):
            simulate_audio_import_transaction(
                plan, b"not-the-candidate", Backend(), 0x10000007, authorization)
        with self.assertRaisesRegex(AudioImportTransactionError, "导入generation无效"):
            simulate_audio_import_transaction(
                plan, pcm_wav(), Backend(), 0x20000001, authorization)
        cross_slot = dict(authorization, target_slot=2)
        with self.assertRaisesRegex(AudioImportTransactionError, "授权无效"):
            simulate_audio_import_transaction(
                plan, pcm_wav(), Backend(), 0x10000007, cross_slot)

        backend = Backend()
        result = simulate_audio_import_transaction(
            plan, pcm_wav(), backend, 0x10000007, authorization)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phases"], ["prepare", "persist", "readback", "activate", "complete"])
        self.assertEqual(result["target_slot"], 1)
        self.assertTrue(result["selection_unchanged"])
        self.assertEqual(result["active_generation"], 0x20000003)
        self.assertFalse(result["old_sound_retained"])
        self.assertTrue(result["simulation"])
        self.assertEqual(result["camera_access"], "not-run")
        self.assertFalse(result["write_attempted"])

        backend = Backend(readback=False)
        result = simulate_audio_import_transaction(
            plan, pcm_wav(), backend, 0x10000008, authorization)
        self.assertEqual(result["status"], "verification-failed")
        self.assertTrue(result["candidate_saved"])
        self.assertTrue(result["old_sound_retained"])
        self.assertTrue(backend.aborted)
        self.assertNotIn("activate", result["phases"])

        for phase, saved in (("prepare", False), ("persist", False), ("activate", True)):
            backend = Backend(fail_at=phase)
            result = simulate_audio_import_transaction(
                plan, pcm_wav(), backend, 0x10000009, authorization)
            expected = "saved-not-playing" if phase == "activate" else "failed"
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["candidate_saved"], saved)
            self.assertTrue(result["old_sound_retained"])
            self.assertTrue(result["simulation"])
            self.assertFalse(result["write_attempted"])

    def test_importing_selected_slot_keeps_selection_but_updates_playback(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="local"), target_slot=1,
            capability_available=True, implementation_ready=True)
        confirmation = confirm_audio_import_plan(plan, True)
        authorization = gate_audio_import_execution(
            plan, confirmation, pcm_wav(),
            preflight=dict(fresh=True, compatible=True, capability_installed=True,
                           profile_sha256="profile", selection=1,
                           slot_generations=[0x10000006, 0x20000003] + [0] * 8,
                           setting=setting_preflight()),
            lock=dict(held=True, owner="core"), executor_available=True)

        class Backend:
            def prepare(self, candidate, generation, slot):
                return {}

            def persist(self, resource, candidate, generation, slot):
                return {}

            def readback(self, record):
                return True

            def activate(self, record):
                return None

        result = simulate_audio_import_transaction(
            plan, pcm_wav(), Backend(), 0x10000007, authorization)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["selection_unchanged"])
        self.assertEqual(result["active_generation"], 0x10000007)

        with self.assertRaisesRegex(AudioImportTransactionError, "新版本"):
            simulate_audio_import_transaction(
                plan, pcm_wav(), Backend(), 0x10000006, authorization)

    def test_full_scale_int16_minimum_sample_builds_valid_import_plan(self):
        import struct
        sample_rate = 24000
        frames = 2400
        payload = struct.pack("<hh", -32768, 32767) * (frames // 2)
        wav = (
            b"RIFF"
            + struct.pack("<I", 36 + len(payload))
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
            + b"data"
            + struct.pack("<I", len(payload))
            + payload
        )
        candidate = inspect_pcm_wav(wav, "full_scale.wav")
        self.assertLessEqual(candidate["peak_amplitude"], 1.0)
        self.assertLessEqual(candidate["rms_amplitude"], candidate["peak_amplitude"])
        plan = build_audio_import_plan(
            candidate, dict(type="local"), target_slot=5,
            capability_available=True, implementation_ready=True)
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["can_execute"])


if __name__ == "__main__":
    unittest.main()
