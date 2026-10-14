#!/bin/sh
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if command -v python3 >/dev/null 2>&1; then
    tool_python=python3
elif command -v python >/dev/null 2>&1; then
    tool_python=python
else
    echo "Python 3 is required. See README.md for installation instructions." >&2
    exit 1
fi
exec "$tool_python" "$script_dir/launch_layout.pyw" "$@"
