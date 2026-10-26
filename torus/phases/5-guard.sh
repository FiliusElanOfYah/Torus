say "Phase 5: airgapd (keys), sentineld (redaction + audit), llmd (Claude via httpx)"
need_venv
mkdir -p "$TORUS_HOME/airgap" && chmod 700 "$TORUS_HOME/airgap"
for k in "$TORUS_HOME"/airgap/*.key; do [ -f "$k" ] && chmod 600 "$k"; done
start=$CHANGED
apply_manifest 5
apply_services 5
restart_phase 5 "$start"
check_phase 5
if [ -f "$TORUS_HOME/airgap/anthropic.key" ]; then ok "Anthropic key stored"; else info "no Anthropic key yet: torus key set anthropic"; fi
info "LLM: $(grep -E '^(enabled|model)=' "$TORUS_HOME/etc/llm.toggle" | tr '\n' ' ')  (torus llm on / torus llm model opus)"
phase_end 5
