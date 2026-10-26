say "Phase 6: gatewayd web page at http://127.0.0.1:7777 + torus ask"
need_venv
start=$CHANGED
apply_manifest 6
apply_services 6
restart_phase 6 "$start"
check_phase 6
sleep 1
if curl -fsS http://127.0.0.1:7777/api/health >/dev/null 2>&1; then ok "web page up: torus open"; else bad "gateway not answering. Look at: torus logs gatewayd"; fi
phase_end 6
