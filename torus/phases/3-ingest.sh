say "Phase 3: ingest mesh (fetcherd, parserd, indexerd, vaultd)"
need_venv
pip_need nats nats-py
pip_need httpx httpx
if ! py_has trafilatura; then
  info "trafilatura needs libxml2/libxslt"
  pkg install -y libxml2 libxslt >/dev/null 2>&1
fi
pip_optional trafilatura trafilatura
pip_optional pdfminer pdfminer.six
start=$CHANGED
apply_manifest 3
apply_services 3
restart_phase 3 "$start"
check_phase 3
info "gate test: torus selftest ingest"
phase_end 3
