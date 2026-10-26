#!/data/data/com.termux/files/usr/bin/bash
# TORUS one-paste installer for Termux. Safe to run again any time (it updates and repairs).
#
#   curl -fsSL https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh | bash
#
# What it does, in order:
#   1. downloads (or updates) the TORUS source into ~/.torus/src and links the `torus` command
#   2. runs `torus install all`  - Phases 0-9: packages, NATS, Mongo, every daemon, web page, seal, export
#   3. prints `torus status` and the three commands that connect Claude
# Only download + link, no install:   TORUS_NO_INSTALL=1 bash bootstrap.sh
set -eu
REPO="${TORUS_REPO:-https://github.com/FiliusElanOfYah/Torus.git}"
BRANCH="${TORUS_BRANCH:-main}"
SRC="$HOME/.torus/src"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"

say() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }

# Commands that may ask questions get the terminal (or nothing) as input, never the piped script:
# with `curl ... | bash` the script itself is on stdin and a prompt would swallow the rest of it.
if (exec </dev/tty) 2>/dev/null; then IN=/dev/tty; else IN=/dev/null; fi

main() {
  if [ ! -d "$PREFIX" ]; then
    echo "This installer is for Termux on Android (F-Droid build). PREFIX=$PREFIX not found." >&2
    exit 1
  fi
  command -v git >/dev/null 2>&1 || { say "installing git"; pkg install -y git <"$IN"; }
  mkdir -p "$HOME/.torus"
  if [ -d "$SRC" ] && [ ! -d "$SRC/.git" ]; then
    local aside="$HOME/.torus/backups/src-not-git-$(date +%Y%m%d-%H%M%S)"
    say "$SRC exists but is not a TORUS checkout; moving it to $aside"
    mkdir -p "$HOME/.torus/backups" && mv "$SRC" "$aside"
  fi
  if [ -d "$SRC/.git" ]; then
    say "updating $SRC"
    git -C "$SRC" config core.fileMode false
    # tracked files you edited are kept (untracked files are left in place and ignored)
    local keep="" f
    while IFS= read -r -d '' f; do
      [ -n "$keep" ] || { keep="$HOME/.torus/backups/src-edits-$(date +%Y%m%d-%H%M%S)"; echo "you edited files in $SRC; copies are kept in $keep before the update:"; }
      echo "   $f"; mkdir -p "$keep/$(dirname "$f")"; cp -a "$SRC/$f" "$keep/$f" 2>/dev/null || true
    done < <(git -C "$SRC" status --porcelain -z --untracked-files=no 2>/dev/null | awk 'BEGIN{RS="\0"; ORS="\0"} {print substr($0, 4)}')
    git -C "$SRC" fetch --depth 1 origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
    git -C "$SRC" reset --hard "origin/$BRANCH"
  else
    say "downloading TORUS into $SRC"
    git clone --depth 1 -b "$BRANCH" "$REPO" "$SRC"
    git -C "$SRC" config core.fileMode false
  fi
  chmod +x "$SRC/torus/bin/torus"
  mkdir -p "$PREFIX/bin"
  ln -sfn "$SRC/torus/bin/torus" "$PREFIX/bin/torus"
  say "torus command linked: $(git -C "$SRC" log -1 --format='%h %cd' --date=short)"

  if [ "${TORUS_NO_INSTALL:-0}" = 1 ]; then
    echo "skipping install (TORUS_NO_INSTALL=1). Next:  torus doctor   then   torus install all"
    return 0
  fi

  say "installing everything (Phases 0-9). Re-run this same line any time to repair or update."
  torus install all <"$IN"

  say "status"
  torus status <"$IN" || true

  cat <<'EOF'

== TORUS is installed. Three more lines connect Claude (your own Anthropic key):

   torus key set anthropic     # paste the key from console.anthropic.com -> API Keys
   torus llm verify            # point sonnet / opus / haiku at the model ids your key can use
   torus llm on && torus llm test

   torus seal && torus audit   # post-quantum seal + 13 OPSEC checks (expect 0 FAIL)
   torus open                  # the phone page: Ask / Jobs / Code / Vault / Settings

Reboot once so Termux:Boot starts the mesh by itself. Full guide: ~/.torus/src/torus/INSTALL.md
EOF
}

main "$@"
