"""Run reproducible, camera-free contributor checks from any working directory.

Default: all tests except optional native emulation. --native includes the
actual ARM64 ELF emulator tests. --strict treats missing/skipped tests as a
failure; use it in CI after installing the test extras.
"""
import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    discovered = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    selected = unittest.TestSuite(test for test in cases(discovered)
                                 if args.native or not test.id().startswith(('test_native.', 'test_fl23_package.FileNativeTests.')))
    print('Device access: none. Native emulation: ' + ('included' if args.native else 'excluded'), flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    if args.strict and result.skipped:
        print('Strict checks failed: skipped tests are not accepted as passed.', file=sys.stderr)
    return 0 if result.wasSuccessful() and not (args.strict and result.skipped) else 1


if __name__ == '__main__':
    raise SystemExit(main())
