say "Phase 2: vault (Downloads/vault) and Mongo"
start=$CHANGED
if [ -e "$TORUS_HOME/vault" ]; then
  ok "vault: $(readlink -f "$TORUS_HOME/vault")"
elif [ -d "$HOME/storage/downloads" ]; then
  mkdir -p "$HOME/storage/downloads/vault"
  ln -s "$HOME/storage/downloads/vault" "$TORUS_HOME/vault" && ok "vault linked to Downloads/vault (visible in Material Files)"
else
  mkdir -p "$TORUS_HOME/vault"
  warn "no ~/storage yet: vault is inside Termux only. Run termux-setup-storage, then move it if you want it in Downloads"
fi
mkdir -p "$TORUS_HOME/vault/inbox" "$TORUS_HOME/vault/answers"
ok "vault folders: inbox (pages), answers (your questions)"
apply_services 2
if [ -f "$SVDIR/torus-mongo/run" ]; then
  restart_phase 2 "$start"
  sleep 3
  if port_open 27017; then ok "Mongo answering on 127.0.0.1:27017"; else warn "Mongo not answering yet (it is optional for TORUS ask). Look at: torus logs torus-mongo"; fi
fi
phase_end 2
