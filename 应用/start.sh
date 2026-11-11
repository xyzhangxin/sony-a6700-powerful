#!/bin/sh
# Source-checkout launcher; no device operation occurs until selected in menu.
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$TASK_ROOT/.venv/bin/python" ]; then
  TASK_PYTHON="$TASK_ROOT/.venv/bin/python"
else
  TASK_PYTHON=python3
fi
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$TASK_PYTHON" -m a6700_unlock "$@"
