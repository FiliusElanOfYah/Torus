say "Phase 10: operatord (Claude works inside Termux in phases you approve) + Termux:API tools"
need_venv
if command -v termux-clipboard-get >/dev/null 2>&1; then
  ok "termux-api package present"
else
  info "pkg install termux-api  (phone tools: clipboard, notifications, camera, mic, SMS, location, contacts)"
  pkg install -y termux-api || warn "termux-api did not install; the Operator still works without phone tools"
fi
if [ -d "/data/data/com.termux.api" ] || pm list packages 2>/dev/null | grep -q com.termux.api; then
  ok "Termux:API app installed"
else
  warn "Termux:API app not found. Install it from F-Droid (same source as Termux) or phone tools will fail with 'permission denied'"
fi
mkdir -p "$TORUS_HOME/var/operator/snapshots" && chmod 700 "$TORUS_HOME/var/operator" "$TORUS_HOME/var/operator/snapshots" && ok "snapshots folder: ~/.torus/var/operator/snapshots (mode 700, kept 24 h)"
start=$CHANGED
apply_manifest 10
apply_services 10
restart_phase 10 "$start"
check_phase 10
info "try:  torus op \"list the five biggest folders under ~/storage/downloads\"   or the Operator tab on the web page"
info "every change asks first?  torus op strict on      per-task budget:  torus op budget 1"
phase_end 10
