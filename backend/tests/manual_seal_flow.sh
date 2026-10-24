#!/bin/bash
# Phase 8 seal/tamper manual flow against the local mesh (TORUS_HOME=/tmp/torus-test)
export TORUS_HOME=/tmp/torus-test
PY=/tmp/tv/bin/python
B=/tmp/torus-test/bin
KEY=/tmp/torus-test/airgap/anthropic.key
ask() { $PY -c "import socket;s=socket.socket(socket.AF_UNIX);s.connect('/tmp/torus-test/airgap/airgap.sock');s.sendall(b'$1\n');print(repr(s.recv(4096).decode()))"; }
echo "== seal"; $PY $B/torus_seal.py seal; echo "exit $?"
echo "== verify"; $PY $B/torus_seal.py verify; echo "exit $?"
echo "== fingerprint"; $PY $B/torus_seal.py fingerprint
echo "== ask anthropic (sealed, ok)"; ask anthropic
echo "== ask bogus"; ask bogus
echo "== tamper"; printf 'sk-ant-EVIL\n' > $KEY
echo "== ask anthropic (tampered)"; ask anthropic
echo "== verify (tampered)"; $PY $B/torus_seal.py verify; echo "exit $?"
echo "== reseal"; $PY $B/torus_seal.py seal --quiet; echo "exit $?"
echo "== ask anthropic (resealed)"; ask anthropic
echo "== restore key"; printf 'sk-ant-test-1234567890abcdef\n' > $KEY; chmod 600 $KEY
$PY $B/torus_seal.py seal; $PY $B/torus_seal.py verify; echo "exit $?"
echo "== ask anthropic (final)"; ask anthropic
echo "== tamper manifest sig"; cp /tmp/torus-test/airgap/MANIFEST.json /tmp/man.bak
sed -i 's/"version":1/"version":2/' /tmp/torus-test/airgap/MANIFEST.json
$PY $B/torus_seal.py verify; echo "exit $?"; ask anthropic
cp /tmp/man.bak /tmp/torus-test/airgap/MANIFEST.json; touch /tmp/torus-test/airgap/MANIFEST.json
$PY $B/torus_seal.py verify; echo "exit $?"; ask anthropic
echo "== daemon tamper"; echo "# x" >> $B/vaultd.py; $PY $B/torus_seal.py verify; echo "exit $?"
sed -i '$ d' $B/vaultd.py; $PY $B/torus_seal.py verify; echo "exit $?"
echo "== sign / verify-file"; echo hello > /tmp/sigtest.txt
$PY $B/torus_seal.py sign /tmp/sigtest.txt; $PY $B/torus_seal.py verify-file /tmp/sigtest.txt; echo "exit $?"
echo bye >> /tmp/sigtest.txt; $PY $B/torus_seal.py verify-file /tmp/sigtest.txt; echo "exit $?"
rm -f /tmp/sigtest.txt.sig; $PY $B/torus_seal.py verify-file /tmp/sigtest.txt; echo "exit $?"
$PY $B/torus_seal.py bogus; echo "exit $?"
ls -la /tmp/torus-test/airgap/
