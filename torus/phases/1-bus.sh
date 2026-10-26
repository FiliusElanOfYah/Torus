say "Phase 1: NATS bus, runit (termux-services), Termux:Boot"
start=$CHANGED
if [ -x "$TORUS_HOME/bin/nats-server" ]; then
  ok "nats-server: $("$TORUS_HOME/bin/nats-server" --version 2>&1)"
elif command -v nats-server >/dev/null 2>&1; then
  ln -sfn "$(command -v nats-server)" "$TORUS_HOME/bin/nats-server"
  ok "nats-server from pkg: $(nats-server --version 2>&1)"
else
  info "downloading nats-server (linux-arm64)"
  tag="$(curl -fsSL https://api.github.com/repos/nats-io/nats-server/releases/latest | sed -n 's/.*"tag_name": *"\(v[^"]*\)".*/\1/p' | head -n 1)"
  [ -n "$tag" ] || die "could not read the latest nats-server version from GitHub"
  curl -fL -o "$TMPDIR/nats.tgz" "https://github.com/nats-io/nats-server/releases/download/$tag/nats-server-$tag-linux-arm64.tar.gz" || die "download failed"
  tar -xzf "$TMPDIR/nats.tgz" -C "$TMPDIR" && install -m 0755 "$TMPDIR/nats-server-$tag-linux-arm64/nats-server" "$TORUS_HOME/bin/nats-server"
  rm -rf "$TMPDIR/nats.tgz" "$TMPDIR/nats-server-$tag-linux-arm64"
  ok "nats-server $tag installed"
fi
mkdir -p "$TORUS_HOME/jetstream" "$HOME/.termux/boot"
apply_manifest 1
apply_services 1
if ! pgrep -f runsvdir >/dev/null 2>&1; then
  info "runit is not running; starting it"
  service-daemon start >/dev/null 2>&1 || warn "could not start runit. Close Termux completely, open it again, then rerun this"
  sleep 2
fi
restart_phase 1 "$start"
sleep 2
if port_open 4222; then ok "NATS answering on 127.0.0.1:4222"; else bad "NATS not answering. Look at: torus logs nats-server"; fi
info "Termux:Boot: open the Termux:Boot app once so Android lets it run at boot"
phase_end 1
