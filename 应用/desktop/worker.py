"""Fixed bundle entry point, invoked with -I -S -B -u (no external packages)."""
from pathlib import Path
import argparse
import runpy
import sys

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group()
mode.add_argument('--physical', action='store_true')
mode.add_argument('--audio-market', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parent / 'python'))
if args.audio_market:
    runpy.run_module('a6700_unlock.desktop.audio_market', run_name='__main__')
else:
    sys.argv = [sys.argv[0]]
    runpy.run_module('a6700_unlock.desktop.physical_bridge' if args.physical
                     else 'a6700_unlock.desktop.bridge', run_name='__main__')
