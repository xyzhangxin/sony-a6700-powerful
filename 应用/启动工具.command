#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$TASK_ROOT/start.sh" "$@"
