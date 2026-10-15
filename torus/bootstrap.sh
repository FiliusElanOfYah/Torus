#!/data/data/com.termux/files/usr/bin/bash
# TORUS bootstrap. Paste this line in Termux (safe to run again any time):
#   curl -fsSL https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh | bash
set -eu
REPO="${TORUS_REPO:-https://github.com/FiliusElanOfYah/Torus.git}"
BRANCH="${TORUS_BRANCH:-main}"
SRC="$HOME/.torus/src"
command -v git >/dev/null 2>&1 || pkg install -y git
mkdir -p "$HOME/.torus"
if [ -d "$SRC/.git" ]; then
  echo "== updating $SRC"
  git -C "$SRC" pull --ff-only
else
  echo "== downloading TORUS into $SRC"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$SRC"
fi
chmod +x "$SRC/torus/bin/torus"
ln -sfn "$SRC/torus/bin/torus" "$PREFIX/bin/torus"
echo
echo "== done. Next, run:  torus doctor"
