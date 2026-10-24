say "Phase 9: export and restore (one signed tarball with your whole setup)"
need_venv
apply_manifest 9
"$VENV_PY" "$TORUS_HOME/bin/torus_export.py" docs >/dev/null && ok "restore guide: ~/.torus/docs/RESTORE.md"
if [ -d "$HOME/storage/downloads" ]; then ok "exports go to ~/storage/downloads (visible in Material Files)"; else warn "no ~/storage/downloads: run termux-setup-storage, or exports go to ~/.torus/exports"; fi
info "make a backup now:      torus export        (add --with-keys to include API keys)"
info "on a new phone:         torus restore ~/storage/downloads/torus-<date>.tar.gz"
phase_end 9
