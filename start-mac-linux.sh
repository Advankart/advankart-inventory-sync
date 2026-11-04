#!/bin/sh
cd "$(dirname "$0")"
[ -d venv ] || { python3 -m venv venv && venv/bin/pip install -r requirements.txt; }
export INVSYNC_LAUNCHER=1
ARGS=""
while true; do
  venv/bin/python app.py $ARGS
  [ $? -eq 3 ] || break
  ARGS="--no-browser --wait-port"
done
