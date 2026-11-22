"""Script entry point for paired, bench-owned, bounded USB service sessions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from .device_protocol import DeviceError
from .session import CameraMscSession, PersistentUsbSession, SessionServer, recover_descriptor


def _show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def _serve(args):
    stopping = False
    owner = PersistentUsbSession(state_dir=args.state_dir, timeout_seconds=args.timeout,
                                 max_duration_seconds=args.max_duration,
                                 progress=lambda message: print(message, file=sys.stderr, flush=True))

    def stop(_signum, _frame):
        nonlocal stopping
        # Signals request draining. Never raise through native write/rollback.
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    child = None
    code = 0
    try:
        with SessionServer(owner) as server:
            _show(dict(ready=True, **owner.status()))
            if args.command:
                env = dict(os.environ, A6700_MSC_SESSION=str(server.path))
                # The child owns no USB or device lock. On expiry it receives
                # a closed IPC session, rather than being forcibly terminated.
                child = subprocess.Popen(args.command, env=env, stdin=subprocess.DEVNULL)
            while not stopping:
                if child is not None and child.poll() is not None:
                    code = child.returncode
                    break
                if not server.step():
                    break
            if child is not None and child.poll() is None:
                code = 75
                print("会话期限已到或收到停止请求；子脚本未被强杀，USB会话不再接单。", file=sys.stderr)
        _show(dict(closed=True, cleanup=owner.cleanup, evidence_directory=str(owner.evidence)))
        if owner.uncertain or not owner.cleanup.get("msc_observed") or owner.cleanup.get("cleanup_warning"):
            return 70
        return code if code >= 0 else 70
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _submit(args, *, detach=False, worker_action="_serve"):
    if os.environ.get("A6700_BENCH_JOB"):
        if worker_action == "_recover":
            _show(recover_descriptor(args.state_dir))
            return 0
        return _serve(args)
    bench = os.environ.get("A6700_MSC_BENCH")
    if not bench:
        raise DeviceError("请使用项目 scripts/msc-tool 入口，或将 worker 显式提交到共享调试台")
    expect = args.expect_last
    if expect is None:
        expect = subprocess.check_output([sys.executable, "-B", bench, "last"], text=True).strip()
    command = [sys.executable, "-B", bench, "run", "--expect-last", expect,
               "--note", "有界 USB MSC/Senser 会话"]
    if detach:
        command.append("--detach")
    command += ["--", sys.executable, "-B", "-m", "a6700_unlock.msc_tool", worker_action,
                "--timeout", str(args.timeout), "--max-duration", str(args.max_duration)]
    if args.state_dir is not None:
        command += ["--state-dir", str(args.state_dir)]
    if args.command:
        command += ["--", *args.command]
    return subprocess.call(command)


def _lifecycle(parser):
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-duration", type=float, default=900)
    parser.add_argument("--expect-last")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="msc-tool", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run", help="one bench job wraps a script and serves its SDK requests")
    _lifecycle(run)
    run.add_argument("command", nargs=argparse.REMAINDER)
    worker = commands.add_parser("_serve", help=argparse.SUPPRESS)
    _lifecycle(worker)
    worker.add_argument("command", nargs=argparse.REMAINDER)
    recover = commands.add_parser("_recover", help="retire a stale IPC endpoint inside a bench job")
    _lifecycle(recover)
    lifecycle = commands.add_parser("session").add_subparsers(dest="lifecycle", required=True)
    start = lifecycle.add_parser("start", help="keep a bounded bench job running; --detach prints the job ID")
    _lifecycle(start)
    start.add_argument("--detach", action="store_true")
    recovery = lifecycle.add_parser("recover", help="retire a dead IPC endpoint; does not recover camera data")
    _lifecycle(recovery)
    for name in ("status", "stop"):
        lifecycle.add_parser(name).add_argument("--state-dir", type=Path)
    result = commands.add_parser("result", help="query a recorded request; never re-execute it")
    result.add_argument("request_id")
    result.add_argument("--state-dir", type=Path)
    read = commands.add_parser("read", help="read aligned sectors already covered by the pinned profile")
    read.add_argument("--dev", required=True)
    read.add_argument("--offset", type=lambda s: int(s, 0), required=True)
    read.add_argument("--size", type=lambda s: int(s, 0), default=512)
    read.add_argument("--out", type=Path, required=True)
    read.add_argument("--state-dir", type=Path)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--state-dir", type=Path)
    plan = commands.add_parser("plan", help="fresh dry-run; no firmware or settings writes")
    plan.add_argument("operation", choices=("unlock", "close"))
    plan.add_argument("--feature", action="append", required=True)
    plan.add_argument("--state-dir", type=Path)
    apply = commands.add_parser("apply", help="execute an exact session plan through the existing guarded core")
    apply.add_argument("plan_id")
    apply.add_argument("--confirm", action="store_true", required=True)
    apply.add_argument("--state-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "_recover":
            _show(recover_descriptor(args.state_dir))
            return 0
        if args.action in {"run", "_serve"}:
            args.command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if args.action == "run" and not args.command:
                parser.error("run 需要 -- 后的脚本命令")
            return _serve(args) if args.action == "_serve" else _submit(args)
        if args.action == "session" and args.lifecycle == "start":
            args.command = []
            return _submit(args, detach=args.detach)
        if args.action == "session" and args.lifecycle == "recover":
            args.command = []
            return _submit(args, worker_action="_recover")
        if args.action == "read" and args.out.exists():
            raise DeviceError("输出文件已存在，不覆盖：" + str(args.out))
        with CameraMscSession(state_dir=args.state_dir) as camera:
            if args.action == "session":
                result = getattr(camera, args.lifecycle)()
                if args.lifecycle == "stop":
                    result["exit_record"] = str(Path(camera.info["evidence_directory"]) / "exit.json")
                    result["note"] = "已请求排空；以 exit.json 和 bench wait 的最终结果确认退出"
            elif args.action == "read":
                data = camera.read(device=args.dev, offset=args.offset, size=args.size)
                from .host_io import open_exclusive, sync_directory
                with os.fdopen(open_exclusive(args.out), "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                sync_directory(args.out.parent)
                result = dict(bytes=len(data), out=str(args.out))
            elif args.action == "plan":
                result = camera.plan(args.operation, args.feature)
            elif args.action == "result":
                result = camera.result(args.request_id)
            elif args.action == "apply":
                result = camera.apply(args.plan_id, confirmed=args.confirm)
            else:
                result = camera.inspect()
            _show(result)
            if args.action in {"apply", "inspect", "plan"}:
                return 0 if result.get("status") in {"completed", "no-op", "settings-prepared", "inspected", "dry-run"} else 2
        return 0
    except KeyboardInterrupt:
        print("已停止客户端；已提交的调试台任务不会取消。用 a6700-bench wait 接回，不要重复提交。", file=sys.stderr)
        return 130
    except (DeviceError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print("msc-tool: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
