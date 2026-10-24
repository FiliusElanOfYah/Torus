#!/bin/bash
# Phase 8 audit CLI flow (run/json/boot/prune) against TORUS_HOME=/tmp/torus-test
export TORUS_HOME=/tmp/torus-test
PY=/tmp/tv/bin/python
B=/tmp/torus-test/bin
L=/tmp/torus-test/logs
echo "== run"; $PY $B/torus_audit.py run | sed 's/\x1b\[[0-9;]*m//g'; echo "exit ${PIPESTATUS[0]}"
echo "== json"; $PY $B/torus_audit.py json > /tmp/audit.json; echo "exit $?"
$PY -c "import json;d=json.load(open('/tmp/audit.json'));print(len(d['items']),d['counts'],d['ok'],[ (i['id'],i['status']) for i in d['items']])"
echo "== hardening.conf / llm.toggle before"; cat /tmp/torus-test/etc/hardening.conf; cat /tmp/torus-test/etc/llm.toggle
echo "== prepare old rotated logs"
mkdir -p $L/svc
touch $L/old.jsonl.1 $L/svc/@4000000000000000.s $L/fresh.jsonl.2 $L/keep.log $L/old2.log
touch -d '10 days ago' $L/old.jsonl.1 $L/svc/@4000000000000000.s $L/keep.log $L/old2.log
echo "== boot"; $PY $B/torus_audit.py boot; echo "exit $?"
cat /tmp/torus-test/etc/llm.toggle
ls $L/old.jsonl.1 $L/svc/@4000000000000000.s $L/fresh.jsonl.2 $L/keep.log 2>&1
echo "== boot again (nothing to do)"; $PY $B/torus_audit.py boot
echo "== prune"; touch $L/x.jsonl.3; touch -d '9 days ago' $L/x.jsonl.3; $PY $B/torus_audit.py prune; ls $L/x.jsonl.3 2>&1
echo "== llm_off_at_boot=false"; cp /tmp/torus-test/etc/hardening.conf /tmp/hard.bak
sed -i 's/llm_off_at_boot.*/llm_off_at_boot = false/' /tmp/torus-test/etc/hardening.conf
$PY -c "
import sys;sys.path.insert(0,'$B');import torus_common as c;c.write_conf(c.LLM_TOGGLE,{'enabled':'true'})"
$PY $B/torus_audit.py boot; cat /tmp/torus-test/etc/llm.toggle
cp /tmp/hard.bak /tmp/torus-test/etc/hardening.conf
rm -f $L/fresh.jsonl.2 $L/keep.log $L/old2.log; rmdir $L/svc 2>/dev/null
echo "== bogus"; $PY $B/torus_audit.py bogus; echo "exit $?"
cat /tmp/torus-test/etc/llm.toggle
