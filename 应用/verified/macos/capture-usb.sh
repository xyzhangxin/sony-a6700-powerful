#!/bin/sh
# Wrapper around the unchanged, historically verified IOUSBHost helper.
# Compiles on explicit invocation only; does not install a persistent helper.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/a6700-usb-capture.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
clang -fobjc-arc -framework Foundation -framework IOUSBHost -framework IOKit \
  -o "$WORK/mac_capture" "$HERE/mac_capture.m"
sudo "$WORK/mac_capture"
