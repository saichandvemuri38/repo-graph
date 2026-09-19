#!/bin/sh
# Run the CodeAtlas engine with the project's virtualenv when there is one (falls back to python3).
#   sh scripts/codeatlas.sh index          build or refresh the graph
#   sh scripts/codeatlas.sh serve          MCP server on stdio
DIR="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
cd "$DIR" || exit 1
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m atlas_engine "$@"
