say "Phase 8: hardening (ML-DSA-65 seal on keys + daemons, OPSEC audit, Claude off at boot, log pruning)"
need_venv
pip_optional dilithium_py dilithium-py
start=$CHANGED
apply_manifest 8
# airgapd (phase 5) and the boot script (phase 1) gained the seal check and the boot hook; refresh them here too
put_file bin/airgapd.py "$TORUS_HOME/bin/airgapd.py" 0755 own
put_file boot/00-torus-start "$HOME/.termux/boot/00-torus-start" 0755 own
mkdir -p "$TORUS_HOME/var" "$TORUS_HOME/docs"
if "$VENV_PY" "$TORUS_HOME/bin/torus_seal.py" seal; then ok "sealed (torus seal verify checks it; airgapd refuses keys that do not match)"; else bad "sealing failed"; fi
if [ "$CHANGED" -gt "$start" ] && [ -f "$SVDIR/airgapd/run" ]; then info "restarting airgapd"; sv_bounce airgapd; sleep 3; fi
say "OPSEC audit"
if "$VENV_PY" "$TORUS_HOME/bin/torus_audit.py" run; then ok "audit: no failures"; else warn "audit found failures. Each line above says how to fix it; rerun: torus audit"; fi
info "switches live in ~/.torus/etc/hardening.conf (llm_off_at_boot, prune_logs_days)"
phase_end 8
