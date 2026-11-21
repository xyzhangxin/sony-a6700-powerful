"""Build the separate Windows console repair tool; no device access."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libusb-source", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or args.output.exists():
        parser.error("Build on Windows into a new output directory")
    import libusb_package
    from a6700_unlock.ftp_migration import load_bundle
    load_bundle()
    for name, version in (("pyinstaller", "6.22.3"), ("pyusb", "1.2.1"), ("libusb-package", "1.0.30.0")):
        if importlib.metadata.version(name) != version:
            raise RuntimeError("Build dependency differs: " + name)
    pin = json.loads((ROOT / "desktop/runtime-dependencies.json").read_bytes())["libusb"]
    if hashlib.sha256(args.libusb_source.read_bytes()).hexdigest() != pin["sha256"]:
        raise RuntimeError("libusb corresponding source differs")
    library = Path(libusb_package.get_library_path())
    relative = "libusb_package/" + library.relative_to(Path(libusb_package.__file__).parent).as_posix()
    with tempfile.TemporaryDirectory(prefix="Powerful6700-FTP-Repair-") as folder:
        work = Path(folder)
        (work / "entry.py").write_text("from a6700_unlock.ftp_migration import entrypoint\nraise SystemExit(entrypoint())\n", encoding='utf-8')
        (work / "runtime.json").write_text(json.dumps(dict(kind="bundled", platform="windows-x64", version="FTP-Repair-1")), encoding='utf-8')
        (work / "bundle-manifest.json").write_text(json.dumps(dict(schema=1, resources=dict(
            libusb=dict(path=relative, sha256=hashlib.sha256(library.read_bytes()).hexdigest())))), encoding='utf-8')
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--console",
            "--name", "Powerful6700-FTP-Repair", "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
            "--specpath", str(work / "spec"), "--paths", str(ROOT / "src"),
            "--add-data", str(ROOT / "src/a6700_unlock") + ":a6700_unlock",
            "--add-data", str(work / "runtime.json") + ":.",
            "--add-data", str(work / "bundle-manifest.json") + ":.",
            "--add-binary", str(library) + ":" + str(Path(relative).parent),
            "--collect-submodules", "a6700_unlock", "--collect-all", "usb", "--collect-all", "libusb_package",
            "--hidden-import", "plistlib", "--exclude-module", "PySide6", str(work / "entry.py")], check=True)
        shutil.copytree(work / "dist/Powerful6700-FTP-Repair", args.output)
    # Keep the console open to read results; never auto-confirm the FIX prompt.
    (args.output / "启动FTP迁移修复.cmd").write_bytes(
        b'@echo off\r\nchcp 65001 >nul\r\nset PYTHONUTF8=1\r\ncd /d "%~dp0"\r\n'
        b'"%~dp0Powerful6700-FTP-Repair.exe"\r\necho.\r\npause\r\n')
    licenses = args.output / "licenses"
    licenses.mkdir()
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, licenses / name)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    for name in ("pyusb", "libusb-package", "pyinstaller", "cryptography", "cffi", "pycparser"):
        dist = importlib.metadata.distribution(name)
        for entry in dist.files or ():
            if "license" in str(entry).lower() or "copying" in str(entry).lower():
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    destination = licenses / name / str(entry).replace("..", "_")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    dep_sources = args.output / "dependency-sources"
    dep_sources.mkdir()
    shutil.copyfile(args.libusb_source, dep_sources / pin["filename"])
    print(json.dumps(dict(built=str(args.output), device_access=False, write_attempted=False)))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    main()
