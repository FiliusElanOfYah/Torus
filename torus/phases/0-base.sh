say "Phase 0: base packages, folders, Python venv, shared code"
missing=""
for p in python git curl openssl termux-services termux-tools proot-distro zip unzip; do
  dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then
  info "pkg install$missing"
  pkg install -y $missing || die "pkg install failed (try: pkg update, then run this again)"
fi
ok "packages present"
mkdir -p "$TORUS_HOME"/bin "$TORUS_HOME"/etc "$TORUS_HOME"/logs "$TORUS_HOME"/backups \
         "$TORUS_HOME"/cache "$TORUS_HOME"/var "$TORUS_HOME"/run "$TORUS_HOME"/web
ok "folders under ~/.torus"
if [ ! -x "$VENV_PY" ]; then
  info "creating venv ~/.torus/venv"
  python -m venv "$TORUS_HOME/venv" || die "python -m venv failed"
fi
ok "venv: $("$VENV_PY" --version 2>&1)"
pip_need nats nats-py
pip_need httpx httpx
start=$CHANGED
apply_manifest 0
[ "$CHANGED" -gt "$start" ] && touch "$TORUS_HOME/run/restart-all"
ln -sfn "$TREE/bin/torus" "$PREFIX/bin/torus" && ok "torus command linked"
phase_end 0
