say "Phase 7: coderd (write, run and fix code in your projects)"
need_venv
start=$CHANGED
apply_manifest 7
root="$(sed -n 's/^root=//p' "$TORUS_HOME/etc/coder.conf" 2>/dev/null)"
mkdir -p "${root:-$HOME/projects}" && ok "projects folder: ${root:-$HOME/projects}"
apply_services 7
restart_phase 7 "$start"
check_phase 7
info "use it on the web page (Code tab) or: torus code projects"
phase_end 7
