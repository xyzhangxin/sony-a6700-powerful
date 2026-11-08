"""Fixed console worker for the Windows shell; same bridges as macOS."""
import argparse
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--physical', action='store_true')
    parser.add_argument('--driver-worker', type=Path)
    args = parser.parse_args()
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
    if args.driver_worker:
        from a6700_unlock.windows_driver import run_worker
        return run_worker(args.driver_worker)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    sys.argv = [sys.argv[0]]
    runpy.run_module('a6700_unlock.desktop.physical_bridge' if args.physical
                    else 'a6700_unlock.desktop.bridge', run_name='__main__')


if __name__ == '__main__':
    raise SystemExit(main())
