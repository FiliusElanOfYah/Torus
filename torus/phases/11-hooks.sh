say "Phase 11: hookd (GitHub webhooks -> Operator tasks) + Cloudflare Tunnel (one public hostname -> 127.0.0.1:7778 only)"
need_venv
if command -v cloudflared >/dev/null 2>&1; then
  ok "cloudflared present ($(cloudflared --version 2>/dev/null | head -1))"
else
  info "pkg install cloudflared  (Cloudflare Tunnel client; free plan is enough)"
  pkg install -y cloudflared || warn "cloudflared did not install; hookd still runs locally, nothing is reachable from GitHub until it does"
fi
mkdir -p "$TORUS_HOME/var/hooks" && chmod 700 "$TORUS_HOME/var/hooks"
start=$CHANGED
apply_manifest 11
apply_services 11
restart_phase 11 "$start"
check_phase 11
if [ -f "$TORUS_HOME/airgap/webhook.key" ]; then ok "webhook secret stored"; else info "next:  torus hooks secret        (makes the shared secret GitHub signs deliveries with)"; fi
if [ -f "$TORUS_HOME/etc/tunnel.yml" ]; then ok "tunnel configured: $(grep -m1 hostname "$TORUS_HOME/etc/tunnel.yml" | awk '{print $2}')"; else
  info "then:  torus tunnel login       (opens a Cloudflare login page once; the zone must be on Cloudflare DNS)"
  info "       torus tunnel route hooks.guardiaios.app"
  info "       torus hooks register owner/repo        (adds the webhook on GitHub with the secret, via your PAT)"
fi
info "rules: ~/.torus/etc/hooks.yaml   status: torus hooks status   every webhook task still waits for your plan-approval tap"
phase_end 11
