#!/usr/bin/env python3
"""Render and OCR-check the macOS SwiftUI UI without screen-capture permission."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a6700_unlock.desktop.controller import DesktopController


def build_state_directory(directory: Path) -> None:
    def save(name: str, state: dict) -> None:
        (directory / f"{name}.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def controller(scenario: str = "enabled") -> DesktopController:
        item = DesktopController(tempfile.mkdtemp(prefix="a6700-ui-render-"))
        if scenario != "enabled":
            item.reset(scenario)
        item.check_environment()
        return item

    def ready(scenario: str = "enabled") -> tuple[DesktopController, dict]:
        item = controller(scenario)
        item.acknowledge_notice(True)
        item.begin_preparation()
        item.confirm_preparation(True)
        item.reserve("inspect")
        return item, item.run_reserved()

    item = controller()
    save("notice", item.snapshot())
    item.acknowledge_notice(True)
    save("preparation", item.begin_preparation())
    item.confirm_preparation(True)
    item.reserve("inspect")
    save("home-enabled", item.run_reserved())

    item, state = ready("original")
    item.select_features("missing")
    save("home-original", item.snapshot())

    item, state = ready("partial")
    item.select_features("none")
    item.set_feature_selection("shutter", True)
    save("home-single-selection", item.snapshot())

    item, state = ready("enabled")
    item.select_features("all")
    item.reserve("unlock")
    save("result-success", item.run_reserved())

    item, state = ready("enabled")
    item.select_features("none")
    for fid in ("fl23", "creative_look_names", "custom_shutter_sound"):
        item.set_feature_selection(fid, True)
    item.reserve("close")
    save("result-close", item.run_reserved())

    for scenario, name in (
        ("communication", "result-uncertain"),
        ("verification", "result-verification-failed"),
        ("rollback", "result-rollback"),
    ):
        item, state = ready(scenario)
        item.select_features("all")
        item.reserve("unlock")
        save(name, item.run_reserved())

    # The auxiliary pages reuse a representative connected home state.
    item, state = ready("enabled")
    save("demo-settings", state)
    save("feedback", state)
    save("audio-market", state)
    save("audio-install", state)
    save("license", state)

    disconnected = controller("enabled")
    save("audio-market-disconnected", disconnected.snapshot())


def compile_renderer(work: Path) -> Path:
    sources = [
        path
        for path in sorted((ROOT / "desktop" / "macos").glob("*.swift"))
        if not path.name.startswith("._")
    ]
    renderer = ROOT / "scripts" / "render_macos_ui.swift"
    executable = work / "render_macos_ui"
    command = [
        "xcrun", "swiftc", "-parse-as-library", "-swift-version", "5",
        "-target", f"{platform.machine()}-apple-macosx13.0",
        "-D", "UI_RENDER", str(renderer), *map(str, sources), "-o", str(executable),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Swift UI renderer compilation failed:\n" + result.stderr[-8000:])
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The visual renderer requires macOS")
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="a6700-ui-render-") as temporary:
        work = Path(temporary)
        state_directory = work / "states"
        state_directory.mkdir()
        build_state_directory(state_directory)
        executable = compile_renderer(work)
        result = subprocess.run(
            [str(executable), str(state_directory), str(args.output)],
            check=False,
        )
        if result.returncode:
            return result.returncode

    report = json.loads((args.output / "visual-validation.json").read_text(encoding="utf-8"))
    failed = [page for page in report["pages"] if not page["passed"]]
    print(f"Visual UI validation: {len(report['pages']) - len(failed)}/{len(report['pages'])} pages passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
