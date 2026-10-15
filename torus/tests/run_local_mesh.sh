#!/usr/bin/env bash
# Dev-only: run the whole TORUS mesh on a normal Linux box (no Termux/runit) for testing.
# usage: torus/tests/run_local_mesh.sh start|stop
set -u
T="${TORUS_HOME:-/tmp/torus-test}"
PY="${TORUS_PY:-/tmp/tv/bin/python}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/files"
export TORUS_HOME="$T"
DAEMONS="fetcherd parserd indexerd vaultd summarizerd airgapd sentineld llmd gatewayd coderd"
case "${1:-start}" in
  start)
    mkdir -p "$T"/{bin,etc,logs,var,web,vault/inbox,vault/answers,airgap}
    cp "$SRC"/bin/*.py "$T/bin/"; cp "$SRC/web/index.html" "$T/web/"
    [ -f "$T/etc/llm.toggle" ] || cp "$SRC/etc/llm.toggle" "$T/etc/"
    [ -f "$T/etc/search.conf" ] || cp "$SRC/etc/search.conf" "$T/etc/"
    [ -f "$T/etc/coder.conf" ] || printf 'root=%s/projects\nrun_timeout=60\n' "$T" > "$T/etc/coder.conf"
    pgrep -f "nats-server -a 127.0.0.1" >/dev/null || nohup nats-server -a 127.0.0.1 -p 4222 >"$T/logs/nats.log" 2>&1 </dev/null &
    sleep 1
    for d in $DAEMONS; do
      pkill -f "$T/bin/$d.py" 2>/dev/null
      PYTHONUNBUFFERED=1 nohup "$PY" "$T/bin/$d.py" >"$T/logs/$d.log" 2>&1 </dev/null &
    done
    sleep 3; echo "mesh started in $T (logs in $T/logs)";;
  stop)
    for d in $DAEMONS; do pkill -f "$T/bin/$d.py"; done; pkill -f "nats-server -a 127.0.0.1"; echo stopped;;
esac
