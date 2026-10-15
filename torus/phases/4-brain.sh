say "Phase 4: summarizerd (pure-Python TextRank)"
need_venv
start=$CHANGED
apply_manifest 4
apply_services 4
restart_phase 4 "$start"
check_phase 4
phase_end 4
