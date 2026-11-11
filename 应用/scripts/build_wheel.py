"""Build from a clean temporary source tree, excluding filesystem metadata.

On some macOS removable/shared volumes, an in-place setuptools build creates
AppleDouble ._* companions even inside .dist-info. Stage only runtime sources
on the temporary filesystem and reject any such archive entries before export.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        # 仓库路径含中文；Windows 控制台默认 ANSI 代码页无法输出。
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--offline", action="store_true", help="使用已有构建依赖；禁止索引和构建隔离下载")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="a6700-clean-build-") as temp:
        work = Path(temp)
        source = work / "source"
        source.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(ROOT / name, source / name)
        shutil.copytree(ROOT / "src/a6700_unlock", source / "src/a6700_unlock",
                        copy_function=shutil.copyfile,
                        ignore=shutil.ignore_patterns("._*", ".DS_Store", "__pycache__", "*.pyc"))
        for original, embedded in (("LICENSE", "LICENSE.txt"), ("NOTICE", "NOTICE.txt"),
                                   ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")):
            if (ROOT / original).read_bytes() != (source / "src/a6700_unlock/data/legal" / embedded).read_bytes():
                raise RuntimeError("Embedded license notice differs from source: " + original)
        command = [sys.executable, "-m", "pip", "--disable-pip-version-check", "wheel", "--no-deps"]
        if args.offline:
            command += ["--no-build-isolation", "--no-index"]
        subprocess.run(command + [str(source), "--wheel-dir", str(work / "wheels")], check=True)
        wheels = list((work / "wheels").glob("a6700_unlock-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected exactly one project wheel")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if any(part.startswith("._") or part == ".DS_Store" for n in names for part in Path(n).parts):
                raise RuntimeError("Filesystem metadata leaked into the wheel")
            if "a6700_unlock/data/a6700-2.00.json" not in names:
                raise RuntimeError("Runtime catalog missing from the wheel")
            for required in ("data/legal/LICENSE.txt", "data/legal/NOTICE.txt", "data/legal/THIRD_PARTY_NOTICES.md",
                             "data/device-profile.json", "data/native/reader.elf", "data/native/writer.elf",
                             "data/native/manifest.json", "native/mac_capture.m",
                             "usb/reference/sony_pmca/LICENSE.txt", "usb/reference/sony_pmca/LICENSE-python-sha1.txt",
                             "data/fl23/manifest.json", "data/fl23/recipe.json", "data/fl23/carrier.bin",
                             "data/fl23/reader.elf", "data/fl23/writer.elf", "data/fl23/files.elf",
                             "data/fl23/runtime/manifest.json"):
                if "a6700_unlock/" + required not in names:
                    raise RuntimeError("Missing device runtime resource: " + required)
            if not any(n.endswith(".dist-info/licenses/LICENSE") for n in names):
                raise RuntimeError("Distribution GPL license missing")
            if any(Path(n).suffix in (".bin", ".raw", ".so", ".plain", ".ext2")
                   and n not in {"a6700_unlock/data/fl23/carrier.bin",
                                 "a6700_unlock/data/fl23/variants/2/carrier.bin",
                                 "a6700_unlock/data/fl23/variants/3/carrier.bin"} for n in names):
                raise RuntimeError("Unexpected firmware/backup binary in the wheel")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output_dir / wheel.name
        with destination.open("xb") as out, wheel.open("rb") as source_file:
            shutil.copyfileobj(source_file, out)
        print(f"Built {destination}")
        print(f"SHA-256 {hashlib.sha256(destination.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
