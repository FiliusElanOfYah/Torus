# TORUS shared shell library. Sourced by `torus` and every phase script.
# Every write goes through put_file: whole file, CRLF stripped, old copy backed up,
# skipped when nothing changed. That makes every phase safe to run again and again.
# shellcheck shell=bash

: "${PREFIX:=/data/data/com.termux/files/usr}"
: "${HOME:=/data/data/com.termux/files/home}"
: "${TMPDIR:=$PREFIX/tmp}"
export PREFIX HOME TMPDIR
TORUS_HOME="${TORUS_HOME:-$HOME/.torus}"
TORUS_SRC="${TORUS_SRC:-$TORUS_HOME/src}"
TREE="$TORUS_SRC/torus"
FILES="$TREE/files"
SVDIR="${SVDIR:-$PREFIX/var/service}"
export SVDIR
BACKUP_ROOT="$TORUS_HOME/backups"
TORUS_STAMP="${TORUS_STAMP:-$(date +%Y%m%d-%H%M%S)}"
VENV_PY="$TORUS_HOME/venv/bin/python"
CHANGED=0
FAILS=0

say()  { printf '\033[1;33m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
info() { printf '  ..    %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILS=$((FAILS+1)); }
die()  { bad "$*"; exit 1; }

expand_dest() {
  case "$1" in
    H:*) printf '%s/%s' "$TORUS_HOME" "${1#H:}" ;;
    U:*) printf '%s/%s' "$HOME" "${1#U:}" ;;
    P:*) printf '%s/%s' "$PREFIX" "${1#P:}" ;;
    *)   printf '%s' "$1" ;;
  esac
}

backup() {  # backup <existing file>
  local f="$1" rel="${1#/}"
  mkdir -p "$BACKUP_ROOT/$TORUS_STAMP/$(dirname "$rel")"
  cp -p "$f" "$BACKUP_ROOT/$TORUS_STAMP/$rel"
  printf '%s\n' "$f" >> "$BACKUP_ROOT/$TORUS_STAMP/FILES"
}

# put_file <prepared temp file> <dest> <mode> <own|keep>
# The temp file is consumed. Dest is never appended to: it's replaced whole.
install_tmp() {
  local tmp="$1" dest="$2" mode="$3" policy="$4" real
  real="$(readlink -f "$dest" 2>/dev/null || printf '%s' "$dest")"
  if [ -e "$real" ]; then
    if [ "$policy" = keep ] && [ "${TORUS_FORCE:-0}" != 1 ]; then
      rm -f "$tmp"; ok "kept   $dest (yours)"; return 0
    fi
    if cmp -s "$tmp" "$real"; then
      rm -f "$tmp"; chmod "$mode" "$real"; ok "same   $dest"; return 0
    fi
    backup "$real"
  fi
  mkdir -p "$(dirname "$real")"
  chmod "$mode" "$tmp"
  mv -f "$tmp" "$real"
  CHANGED=$((CHANGED+1))
  ok "wrote  $dest"
}

new_tmp() { mkdir -p "$TMPDIR"; mktemp "$TMPDIR/torus.XXXXXX"; }

put_file() {  # put_file <src under files/> <dest> <mode> <policy>
  local src="$FILES/$1" tmp
  [ -f "$src" ] || die "missing in repo: torus/files/$1 (run: torus update)"
  tmp="$(new_tmp)"; tr -d '\r' < "$src" > "$tmp"
  install_tmp "$tmp" "$2" "$3" "$4"
}

put_tmpl() {  # put_tmpl <src under files/> <dest> <mode> <policy> <name>
  local src="$FILES/$1" tmp
  [ -f "$src" ] || die "missing in repo: torus/files/$1"
  tmp="$(new_tmp)"; tr -d '\r' < "$src" | sed "s/@NAME@/$5/g" > "$tmp"
  install_tmp "$tmp" "$2" "$3" "$4"
}

# iterate the manifest:  each_manifest <phase|all> <callback>   callback gets: phase policy mode src dest
each_manifest() {
  local want="$1" cb="$2" ph pol mode src dest
  while read -r ph pol mode src dest; do
    case "$ph" in ''|\#*) continue ;; esac
    [ "$want" = all ] || [ "$ph" = "$want" ] || continue
    "$cb" "$ph" "$pol" "$mode" "$src" "$(expand_dest "$dest")"
  done < "$TREE/manifest.txt"
}

each_service() {  # each_service <phase|all> <callback>   callback gets: phase name template policy
  local want="$1" cb="$2" ph name tmpl pol
  while read -r ph name tmpl pol; do
    case "$ph" in ''|\#*) continue ;; esac
    [ "$want" = all ] || [ "$ph" = "$want" ] || continue
    "$cb" "$ph" "$name" "$tmpl" "$pol"
  done < "$TREE/services.txt"
}

_apply_file() { put_file "$4" "$5" "$3" "$2"; }
apply_manifest() { each_manifest "$1" _apply_file; }

_apply_service() {
  local name="$2" tmpl="$3" pol="$4" dir="$SVDIR/$2"
  if [ "$pol" = keep ] && [ -f "$dir/run" ] && [ "${TORUS_FORCE:-0}" != 1 ]; then
    ok "kept   service $name (yours)"; return 0
  fi
  mkdir -p "$dir/log" "$TORUS_HOME/logs/$name"
  put_tmpl "service/$tmpl" "$dir/run" 0755 own "$name"
  put_tmpl "service/log.run" "$dir/log/run" 0755 own "$name"
  put_file "service/svlogd.config" "$TORUS_HOME/logs/$name/config" 0644 own
}
apply_services() { each_service "$1" _apply_service; }

phase_services() {  # names of services in a phase
  local want="$1" ph name rest
  while read -r ph name rest; do
    case "$ph" in ''|\#*) continue ;; esac
    [ "$ph" = "$want" ] && printf '%s ' "$name"
  done < "$TREE/services.txt"
}

sv_uptime() { sv status "$1" 2>/dev/null | sed -n 's/^run: [^(]*(pid [0-9]*) \([0-9]*\)s.*/\1/p'; }

sv_bounce() {  # (re)start a service once runsv has picked it up
  local name="$1" i=0
  while [ ! -p "$SVDIR/$name/supervise/control" ] && [ $i -lt 15 ]; do sleep 1; i=$((i+1)); done
  rm -f "$SVDIR/$name/down"
  command -v sv-enable >/dev/null 2>&1 && sv-enable "$name" >/dev/null 2>&1
  sv restart "$name" >/dev/null 2>&1 || sv up "$name" >/dev/null 2>&1 || warn "could not start $name"
}

# restart_phase <phase> <changed-count-at-start>
# Restarts the phase's services if anything changed, if shared code changed, or if they're not running.
restart_phase() {
  local ph="$1" start="$2" s up
  for s in $(phase_services "$ph"); do
    [ -f "$SVDIR/$s/run" ] || continue
    up="$(sv_uptime "$s")"
    if [ "$CHANGED" -gt "$start" ] || [ -f "$TORUS_HOME/run/restart-all" ] || [ -z "$up" ]; then
      info "restarting $s"; sv_bounce "$s"
    fi
  done
}

check_phase() {  # a service passes when it has stayed up for 3+ seconds
  local s up
  sleep 5
  for s in $(phase_services "$1"); do
    [ -f "$SVDIR/$s/run" ] || { warn "$s has no service folder"; continue; }
    up="$(sv_uptime "$s")"
    if [ -n "$up" ] && [ "$up" -ge 3 ]; then ok "$s running (${up}s)"; else bad "$s is not staying up. Look at: torus logs $s"; fi
  done
}

need_venv() { [ -x "$VENV_PY" ] || die "no Python venv at ~/.torus/venv. Run: torus install 0"; }
py_has() { "$VENV_PY" -c "import $1" >/dev/null 2>&1; }
pip_need() {  # pip_need <import-name> <pip-name>
  if py_has "$1"; then ok "python: $1"; return 0; fi
  info "pip install $2"
  "$TORUS_HOME/venv/bin/pip" install --quiet "$2" && py_has "$1" && ok "python: $1" || bad "pip install $2 failed"
}
pip_optional() {
  if py_has "$1"; then ok "python: $1"; return 0; fi
  info "pip install $2 (optional)"
  "$TORUS_HOME/venv/bin/pip" install --quiet "$2" >/dev/null 2>&1 && ok "python: $1" || warn "$2 did not install: TORUS uses its built-in fallback"
}

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

phase_end() {
  if [ "$FAILS" -eq 0 ]; then say "phase $1 OK ($CHANGED files changed)"; else say "phase $1 finished with $FAILS problem(s). Fix them, then run: torus install $1"; exit 1; fi
}
