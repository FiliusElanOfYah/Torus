say "Phase 7: coderd (write, run and fix code) + oracled (keeps pinned topics fresh)"
need_venv
start=$CHANGED
apply_manifest 7
root="$(sed -n 's/^root=//p' "$TORUS_HOME/etc/coder.conf" 2>/dev/null)"
mkdir -p "${root:-$HOME/projects}" && ok "projects folder: ${root:-$HOME/projects}"
mkdir -p "$TORUS_HOME/vault/topics" && ok "topic digests: ~/.torus/vault/topics"
apply_services 7
restart_phase 7 "$start"
check_phase 7
info "code: web page Code tab or  torus code projects"
info "topics: edit ~/.torus/etc/pinned.yaml (or the Vault tab), then  torus oracle run   /  torus oracle status"
phase_end 7
