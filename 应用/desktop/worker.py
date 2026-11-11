"""Fixed bundle entry point, invoked with -I -S -B -u (no external packages)."""
from pathlib import Path
import argparse
import runpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--physical', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parent / 'python'))
sys.argv = [sys.argv[0]]
runpy.run_module('a6700_unlock.desktop.physical_bridge' if args.physical
                 else 'a6700_unlock.desktop.bridge', run_name='__main__')
