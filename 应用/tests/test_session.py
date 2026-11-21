"""Session lifecycle, real IPC and guarded core tests; synthetic sectors only."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from a6700_unlock import operations
from a6700_unlock.device_protocol import DeviceBusyError, DeviceError
from a6700_unlock.session import (CameraMscSession, PersistentUsbSession, SCHEMA,
                                  SessionServer, _receive, _send, descriptor_path, recover_descriptor)
from test_operations import sparse_fixture


class Backend:
    def __init__(self, sectors, directory):
        self.sectors = list(sectors)
        self.directory = directory
        self.reads = self.writes = self.opens = self.stops = self.abandons = 0
        self.cleanup_status = self.cleanup_warning = None
        self.failure = None
        self.channel = type("Channel", (), {"sequence": 1})()

    def prepare_access(self):
        pass

    def open_transport(self):
        self.opens += 1
        if self.failure == "auth":
            raise DeviceError("synthetic authentication failure")
        return self

    def read(self, nonce):
        self.reads += 1
        self.channel.sequence += 3
        if self.failure == "read":
            raise DeviceError("synthetic read failure")
        if self.failure == "readback" and self.writes:
            return [b"x" + self.sectors[0][1:]] + self.sectors[1:]
        return list(self.sectors)

    def write(self, before, after, nonce):
        self.writes += 1
        assert self.sectors == before
        runs = [p.parent for p in (self.directory / "runs").glob("*/write-intent.json")]
        assert runs, "writer executed without durable intent"
        for name in ("preimages.json", "targets.json"):
            assert (runs[-1] / name).exists()
        if self.failure == "rollback":
            return dict(status=5, error="synthetic failure", touched=1, rolled_back=True)
        self.sectors = list(after)
        if self.failure == "lost-reply":
            raise DeviceError("synthetic lost writer response")
        return dict(status=0, touched=1, rolled_back=False)

    def __exit__(self, *args):
        self.stops += 1
        self.cleanup_status = "stop-sent"

    def abandon_transport(self):
        self.abandons += 1
        self.cleanup_status = "unconfirmed"

    def observe_normal_mode(self):
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile, sectors = sparse_fixture()
        self.backend = Backend(sectors, self.directory)
        self.lock = patch("a6700_unlock.operations.host_lock_path", return_value=self.directory / "host.lock")
        self.lock.start()
        self.addCleanup(self.lock.stop)

    def owner(self, **kwargs):
        return PersistentUsbSession(profile=self.profile, state_dir=self.directory,
                                    backend_factory=lambda *a, **k: self.backend, **kwargs)

    def good(self, owner, method, params=None, **kwargs):
        reply = owner.request(method, params, **kwargs)
        self.assertTrue(reply["ok"], reply)
        return reply["result"]

    def plan(self, owner, features=None):
        return self.good(owner, "plan", dict(action="unlock", features=features or ["a"]))["plan_id"]

    def test_one_authentication_many_reads_and_lock_until_after_msc_observation(self):
        owner = self.owner()
        with owner:
            for _ in range(4):
                self.good(owner, "read")
            self.assertEqual(self.backend.opens, 1)
            self.assertEqual(self.backend.stops, 0)
            with self.assertRaises(DeviceBusyError):
                with operations.device_lock(self.directory / "other"):
                    pass
            def observe():
                with self.assertRaises(DeviceBusyError):
                    with operations.device_lock(self.directory / "other"):
                        pass
                return True
            self.backend.observe_normal_mode = observe
        self.assertEqual(self.backend.stops, 1)
        self.assertTrue(owner.cleanup["msc_observed"])
        with operations.device_lock(self.directory / "other"):
            pass
        owner.__exit__(None, None, None)
        self.assertEqual(self.backend.stops, 1)
        with self.assertRaisesRegex(DeviceError, "重复打开"):
            owner.__enter__()

    def test_real_device_owner_requires_bench_before_any_access(self):
        with patch.dict(os.environ, {}, clear=True):
            owner = PersistentUsbSession(profile=self.profile, state_dir=self.directory)
            with self.assertRaisesRegex(DeviceError, "共享调试台"):
                owner.__enter__()
        self.assertFalse(owner.evidence.exists())

    def test_scope_rejected_before_usb_and_no_raw_write_or_payload_method(self):
        with self.owner() as owner:
            for params in [dict(device="/dev/nflasha15", offset=0, size=512),
                           dict(device="/dev/nflasha15", offset=4096, size=513),
                           dict(device="/dev/nflasha8", offset=0, size=512),
                           dict(device="/dev/nflasha15", offset=4096, size=1024)]:
                self.assertFalse(owner.request("read", params)["ok"])
            for method in ("write", "exec", "payload"):
                with self.assertRaises(ValueError):
                    owner.request(method)
            self.assertEqual(self.backend.reads, 0)
            result = self.good(owner, "read", dict(device="/dev/nflasha15", offset=4096, size=512))
            self.assertEqual(result["sectors"], [bytes(512).hex()])
            self.assertEqual(result["native_scope"], "full-fixed-profile")

    def test_plan_apply_uses_fresh_read_existing_writer_and_independent_readback(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            self.assertEqual(self.backend.writes, 0)
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["verified"])
            self.assertEqual(self.backend.reads, 3)
            self.assertEqual(self.backend.writes, 1)
            self.assertEqual(self.backend.sectors[0][15], 0)  # unselected feature B
            self.assertFalse(owner.request("apply", dict(plan_id=plan, confirmed=True))["ok"])
            # Subsequent writes need a new fresh plan, but no new authentication.
            next_plan = self.plan(owner, ["b"])
            self.assertEqual(self.good(owner, "apply", dict(plan_id=next_plan, confirmed=True))["status"], "completed")
            self.assertEqual(self.backend.opens, 1)

    def test_changed_preimage_rejects_before_setting_or_firmware_writes(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            self.backend.sectors[3] = b"x" + bytes(511)
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "error")
            self.assertIn("前态已变化", result["error"])
            self.assertFalse(result["write_attempted"])
            self.assertEqual(self.backend.writes, 0)
            self.assertIsNone(owner.write_block_reason)

    def test_close_setting_drift_cannot_expand_approved_preparation(self):
        from a6700_unlock.desktop.simulation import MemoryDevice, fixture
        from a6700_unlock.device_profile import inspect_sectors
        profile, sectors = fixture()
        _, installed = inspect_sectors(profile, sectors, "unlock", ["sound"])
        backend = MemoryDevice(profile, installed, "close-active")
        backend.close_settings["sound"].update(current="on", saved="on")
        owner = PersistentUsbSession(profile=profile, state_dir=self.directory,
                                     backend_factory=lambda *a, **k: backend)
        with owner:
            result = self.good(owner, "plan", dict(action="close", features=["sound"]))
            self.assertEqual(result["close_preparation"]["planned"], [])
            backend.close_settings["sound"].update(current="electronic-only", saved="electronic-only")
            applied = self.good(owner, "apply", dict(plan_id=result["plan_id"], confirmed=True))
            self.assertEqual(applied["status"], "close-preparation-required")
            self.assertIn("计划中的关闭前设置已变化", applied["error"])
            self.assertFalse(applied["write_attempted"])
            self.assertFalse(applied["settings_write_attempted"])
            self.assertEqual(backend.close_setting_writes, [])
            self.assertEqual(backend.writes, 0)

    def test_fixed_session_does_not_require_unavailable_experimental_packages(self):
        from a6700_unlock.catalog import load_catalog
        from a6700_unlock.device_profile import load_device_profile
        from a6700_unlock.capability_packages import builtin_adapter_set, resolve_capability_selection
        profile = load_device_profile(load_catalog())
        with patch("a6700_unlock.capability_packages._custom_sound_package", side_effect=AssertionError("unrelated package")):
            adapter = builtin_adapter_set(profile, include_creative_looks=False, include_custom_sound=False)
            result = resolve_capability_selection(adapter, ["sound"], "unlock")
        self.assertEqual(result["selected_features"], ["sound"])

    def test_confirmation_and_feature_whitelist_are_required(self):
        with self.owner() as owner:
            for params in [dict(action="unlock", features=[]), dict(action="unlock", features=["unknown"]),
                           dict(action="restore", features=["a"]), dict(action="unlock", features=["a", "a"])]:
                self.assertFalse(owner.request("plan", params)["ok"])
            plan = self.plan(owner)
            for confirm in (False, 1, "true", None):
                self.assertFalse(owner.request("apply", dict(plan_id=plan, confirmed=confirm))["ok"])
            self.assertEqual(self.backend.writes, 0)
            self.assertEqual(self.good(owner, "apply", dict(plan_id=plan, confirmed=True))["status"], "completed")

    def test_request_id_replay_returns_record_without_writing_twice(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            ident = "a" * 32
            params = dict(plan_id=plan, confirmed=True)
            first = owner.request("apply", params, request_id=ident)
            second = owner.request("apply", params, request_id=ident)
            self.assertEqual(first, second)
            self.assertEqual(self.backend.writes, 1)
            self.assertEqual(self.good(owner, "result", dict(request_id=ident)), first)
            with self.assertRaisesRegex(DeviceError, "绑定其他请求"):
                owner.request("read", request_id=ident)

    def test_unfinished_request_never_replays(self):
        with self.owner() as owner:
            ident = "b" * 32
            owner.request("read", request_id=ident)
            (owner.evidence / "requests" / ident / "result.json").unlink()
            with self.assertRaisesRegex(DeviceError, "结果未知"):
                owner.request("read", request_id=ident)
            self.assertEqual(self.backend.reads, 1)

    def test_lost_writer_reply_poisoned_session_never_sends_stop(self):
        owner = self.owner()
        with owner:
            plan = self.plan(owner)
            self.backend.failure = "lost-reply"
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "uncertain")
            self.assertTrue(owner.uncertain)
            self.assertFalse(owner.request("read")["ok"])
        self.assertEqual(self.backend.stops, 0)
        self.assertEqual(self.backend.abandons, 1)
        self.assertFalse(owner.cleanup["msc_observed"])
        self.backend.failure = None
        with self.assertRaisesRegex(DeviceError, "未认证相机"):
            with self.owner():
                pass
        self.assertEqual(self.backend.opens, 1)
        self.assertEqual(self.backend.writes, 1)

    def test_verified_rollback_allows_replanning(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            self.backend.failure = "rollback"
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "rolled-back")
            self.assertFalse(owner.uncertain)
            self.assertIsNone(owner.write_block_reason)

    def test_readback_mismatch_blocks_next_write_and_does_not_claim_rollback(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            self.backend.failure = "readback"
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "verification-failed")
            self.assertTrue(owner.uncertain)
            self.assertEqual(self.backend.writes, 1)

    def test_audit_failure_after_writer_keeps_unknown_intent_and_blocks_restart(self):
        owner = self.owner()
        with owner:
            plan = self.plan(owner)
            save = operations.save_json
            def fail(path, data):
                if path.name == "result.json" and "requests" in path.parts:
                    raise OSError("synthetic journal failure")
                return save(path, data)
            with patch.object(operations, "save_json", side_effect=fail):
                with self.assertRaises(OSError):
                    owner.request("apply", dict(plan_id=plan, confirmed=True))
            self.assertTrue(owner.uncertain)
        with self.assertRaisesRegex(DeviceError, "未闭合写入"):
            with self.owner():
                pass

    def test_start_and_failed_auth_do_not_send_stop_or_leak_lock(self):
        self.backend.failure = "auth"
        with self.assertRaises(DeviceError):
            with self.owner():
                pass
        self.assertEqual(self.backend.stops, 0)
        with operations.device_lock(self.directory):
            pass

    def test_stop_acknowledgement_is_separate_from_observed_msc(self):
        self.backend.observe_normal_mode = lambda: False
        owner = self.owner()
        with owner:
            self.good(owner, "read")
        self.assertTrue(owner.cleanup["stop_sent"])
        self.assertTrue(owner.cleanup["stop_requested"])
        self.assertTrue(owner.cleanup["stop_acknowledged"])
        self.assertFalse(owner.cleanup["msc_observed"])

    def test_cleanup_failure_still_records_exit_and_releases_lock(self):
        owner = self.owner()
        with owner:
            def fail(*args, **kwargs):
                raise DeviceError("synthetic cleanup failure")
            owner.access.release_access = fail
        self.assertEqual(owner.cleanup["cleanup_status"], "unconfirmed")
        self.assertIn("cleanup failure", owner.cleanup["cleanup_warning"])
        self.assertTrue((owner.evidence / "exit.json").exists())
        with operations.device_lock(self.directory):
            pass

    def test_idle_lease_and_status_does_not_keep_session_alive(self):
        now = [0]
        with self.owner(clock=lambda: now[0], timeout_seconds=5) as owner:
            now[0] = 4
            self.good(owner, "status")
            now[0] = 5
            self.assertTrue(owner.expired())
            with self.assertRaisesRegex(DeviceError, "期限"):
                owner.request("read")
        self.assertEqual(self.backend.reads, 0)

    def test_maximum_duration_applies_even_with_frequent_requests(self):
        now = [0]
        with self.owner(clock=lambda: now[0], timeout_seconds=5, max_duration_seconds=10) as owner:
            for second in (4, 8):
                now[0] = second
                self.good(owner, "read")
            now[0] = 10
            self.assertTrue(owner.expired())

    def test_deadline_does_not_interrupt_inflight_transaction(self):
        now = [0]
        with self.owner(clock=lambda: now[0], timeout_seconds=5) as owner:
            plan = self.plan(owner)
            write = self.backend.write
            def late_write(*args):
                now[0] = 100
                self.assertEqual(self.backend.stops, 0)
                return write(*args)
            self.backend.write = late_write
            result = self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.backend.reads, 3)

    def test_sequence_budget_drains_without_wrap_or_authentication_retry(self):
        with self.owner() as owner:
            self.backend.channel.sequence = 60001
            self.assertFalse(owner.request("read")["ok"])
            self.assertTrue(owner.closing)
            self.assertEqual(self.backend.reads, 0)
            self.assertEqual(self.backend.opens, 1)

    def test_execution_resource_drift_rejects_before_new_device_read(self):
        with self.owner() as owner:
            plan = self.plan(owner)
            reads = self.backend.reads
            with patch("a6700_unlock.session.execution_resources_digest", return_value="changed"):
                result = owner.request("apply", dict(plan_id=plan, confirmed=True))
            self.assertFalse(result["ok"])
            self.assertIn("资源在计划后变化", result["error"])
            self.assertEqual(self.backend.reads, reads)
            self.assertEqual(self.backend.writes, 0)

    def test_replanning_identical_content_never_revives_old_approval(self):
        with self.owner() as owner:
            first = self.plan(owner)
            second = self.plan(owner)
            self.assertNotEqual(first, second)
            self.good(owner, "apply", dict(plan_id=second, confirmed=True))
            self.assertFalse(owner.request("apply", dict(plan_id=first, confirmed=True))["ok"])

    def test_confirmed_native_completion_can_exit_after_verification_failure(self):
        owner = self.owner()
        with owner:
            plan = self.plan(owner)
            self.backend.failure = "readback"
            self.good(owner, "apply", dict(plan_id=plan, confirmed=True))
            self.assertTrue(owner.uncertain)
            self.assertFalse(owner.execution_unconfirmed)
        self.assertEqual(self.backend.stops, 1)
        self.assertTrue(owner.cleanup["msc_observed"])

    def test_sigterm_during_write_requests_draining_after_independent_readback(self):
        import signal
        from a6700_unlock.msc_tool import main
        source = str(Path(__file__).resolve().parents[1] / "src")
        code = ("from a6700_unlock.session import CameraMscSession\n"
                "with CameraMscSession() as c:\n"
                " p=c.plan('unlock',['a'])\n"
                " r=c.apply(p['plan_id'],confirmed=True)\n"
                " assert r['verified']\n")
        owner = self.owner()
        write = self.backend.write
        def stop_during_write(*args):
            signal.raise_signal(signal.SIGTERM)
            self.assertEqual(self.backend.stops, 0)
            return write(*args)
        self.backend.write = stop_during_write
        with patch("a6700_unlock.msc_tool.PersistentUsbSession", return_value=owner), \
             patch.dict(os.environ, PYTHONPATH=source), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            # Child may still be exiting when the worker drains; no kill/retry.
            self.assertIn(main(["_serve", "--", sys.executable, "-B", "-c", code]), (0, 75))
        self.assertEqual(self.backend.writes, 1)
        self.assertEqual(self.backend.reads, 3)
        self.assertEqual(self.backend.stops, 1)

    def test_invalid_deadlines_rejected_without_device(self):
        for value in (0, -1, float("nan"), float("inf"), True, 901):
            with self.assertRaises(ValueError):
                self.owner(timeout_seconds=value)

    def start_ipc(self):
        server = SessionServer(self.owner())
        server.__enter__()
        def serve():
            while server.step():
                pass
        thread = threading.Thread(target=serve, daemon=True)
        def close():
            server.owner.closing = True
            thread.join(5)
            self.assertFalse(thread.is_alive())
            server.__exit__(None, None, None)
        try:
            thread.start()
        except BaseException:
            server.owner.closing = True
            server.__exit__(None, None, None)
            raise
        self.addCleanup(close)
        return server

    def test_real_ipc_sdk_and_client_exit_does_not_close_shared_worker(self):
        server = self.start_ipc()
        with CameraMscSession(state_dir=self.directory) as client:
            self.assertEqual(client.read(device="/dev/nflasha15", offset=4096), bytes(512))
        self.assertEqual(self.backend.stops, 0)
        with CameraMscSession(state_dir=self.directory) as client:
            self.assertEqual(len(client.read_profile()), 4)
            client.stop()
        self.assertTrue(server.owner.closing)
        self.assertEqual(self.backend.opens, 1)

    def test_bad_ipc_token_and_oversized_frame_never_touch_camera(self):
        server = self.start_ipc()
        info = server.info
        def connect():
            endpoint = info["endpoint"]
            conn = socket.socket(socket.AF_UNIX if endpoint["kind"] == "unix" else socket.AF_INET)
            conn.settimeout(3)
            conn.connect(endpoint.get("address") or ("127.0.0.1", endpoint["port"]))
            return conn
        with connect() as conn:
            _send(conn, dict(schema=SCHEMA, token="bad", request_id="a" * 32, method="read", params={}))
            self.assertFalse(_receive(conn)["ok"])
        with connect() as conn:
            _send(conn, dict(schema=SCHEMA, token="非ASCII" * 16, request_id="b" * 32, method="read", params={}))
            self.assertFalse(_receive(conn)["ok"])
        with connect() as conn:
            conn.sendall(struct.pack("!I", 1024 * 1024 + 1))
            self.assertFalse(_receive(conn)["ok"])
        self.assertEqual(self.backend.reads, 0)

    def test_existing_descriptor_is_never_deleted_or_overwritten(self):
        path = descriptor_path(self.directory)
        path.write_text("crashed worker evidence", encoding='utf-8')
        with self.assertRaises(FileExistsError):
            with SessionServer(self.owner()):
                pass
        self.assertEqual(path.read_text(encoding='utf-8'), "crashed worker evidence")
        self.assertEqual(self.backend.opens, 0)

    def test_explicit_endpoint_recovery_preserves_transaction_evidence(self):
        path = descriptor_path(self.directory)
        path.write_text(json.dumps(dict(pid=2147483647, session_id="dead", token="private-token")), encoding='utf-8')
        evidence = self.directory / "msc-sessions" / "old" / "requests" / ("a" * 32)
        evidence.mkdir(parents=True)
        operations.save_json(evidence / "intent.json", dict(method="apply"))
        with patch.dict(os.environ, A6700_BENCH_JOB="synthetic-recovery"), \
             patch("a6700_unlock.session._process_alive", return_value=False):
            result = recover_descriptor(self.directory)
        self.assertFalse(path.exists())
        self.assertTrue((evidence / "intent.json").exists())
        self.assertFalse(result["camera_access"])
        self.assertNotIn("private-token", json.dumps(result))
        with self.assertRaisesRegex(DeviceError, "未闭合写入"):
            with self.owner():
                pass

    def test_endpoint_recovery_refuses_live_owner_and_occupied_lock(self):
        path = descriptor_path(self.directory)
        path.write_text(json.dumps(dict(pid=os.getpid())), encoding='utf-8')
        with patch.dict(os.environ, A6700_BENCH_JOB="synthetic-recovery"):
            with self.assertRaisesRegex(DeviceError, "进程仍存在"):
                recover_descriptor(self.directory)
            with self.owner():
                with self.assertRaises(DeviceBusyError):
                    recover_descriptor(self.directory)
        self.assertTrue(path.exists())

    def test_run_wrapper_executes_real_child_sdk_then_exits_service(self):
        from a6700_unlock.msc_tool import main
        source = str(Path(__file__).resolve().parents[1] / "src")
        code = "from a6700_unlock.session import CameraMscSession\nwith CameraMscSession() as c:\n for i in range(3): assert len(c.read_profile()) == 4\n"
        owner = self.owner()
        with patch("a6700_unlock.msc_tool.PersistentUsbSession", return_value=owner), \
             patch.dict(os.environ, PYTHONPATH=source), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["_serve", "--", sys.executable, "-B", "-c", code]), 0)
        self.assertEqual(self.backend.opens, 1)
        self.assertEqual(self.backend.reads, 3)
        self.assertEqual(self.backend.stops, 1)
        self.assertFalse(descriptor_path(self.directory).exists())

    def test_submit_is_one_bench_job_with_expect_last_and_no_spawned_daemon(self):
        from a6700_unlock.msc_tool import main
        with patch.dict(os.environ, {"A6700_MSC_BENCH": "/synthetic/bench.py"}, clear=True), \
             patch("a6700_unlock.msc_tool.subprocess.check_output", return_value="last-job\n"), \
             patch("a6700_unlock.msc_tool.subprocess.call", return_value=0) as call:
            self.assertEqual(main(["session", "start", "--detach"]), 0)
        argv = call.call_args.args[0]
        self.assertIn("--detach", argv)
        self.assertEqual(argv[argv.index("--expect-last") + 1], "last-job")
        self.assertIn("_serve", argv)


if __name__ == "__main__":
    unittest.main()
