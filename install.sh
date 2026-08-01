#!/usr/bin/env bash
# RAPP Second Brain installer — one file, no dependencies.
#   curl -fsSL https://raw.githubusercontent.com/kody-w/rapp-secondbrain/main/install.sh | bash
set -euo pipefail

REPO="${RSB_REPO:-kody-w/rapp-secondbrain}"
REF="${RSB_REF:-main}"
PREFIX="${RSB_PREFIX:-$HOME/.local/bin}"
BRAIN_HOME="${RAPP_SECOND_BRAIN_HOME:-$HOME/.rapp-second-brain}"

say()  { printf '  %s\n' "$*"; }
fail() { printf '\n  error: %s\n\n' "$*" >&2; exit 1; }

printf '\n  RAPP Second Brain\n  -----------------\n\n'

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || fail "Python 3.9+ is required (that is the only requirement)."
say "python      $($PY -V 2>&1 | awk '{print $2}')"

mkdir -p "$PREFIX"

if [ -f "$(dirname "$0")/rsb" ]; then
  install -m 0755 "$(dirname "$0")/rsb" "$PREFIX/rsb"          # local checkout
  say "installed   $PREFIX/rsb (from checkout)"
else
  command -v curl >/dev/null 2>&1 || fail "curl is required to download rsb."
  curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/rsb" -o "$PREFIX/rsb.tmp" \
    || fail "download failed from $REPO@$REF"
  head -1 "$PREFIX/rsb.tmp" | grep -q python || fail "downloaded file does not look like rsb"
  chmod 0755 "$PREFIX/rsb.tmp"
  mv "$PREFIX/rsb.tmp" "$PREFIX/rsb"
  say "installed   $PREFIX/rsb"
fi

"$PREFIX/rsb" --version >/dev/null || fail "rsb did not run after install"

if [ ! -f "$BRAIN_HOME/events.jsonl" ]; then
  "$PREFIX/rsb" init --owner "${USER:-owner}" >/dev/null
  say "brain       $BRAIN_HOME (created)"
else
  say "brain       $BRAIN_HOME (existing, untouched)"
fi

printf '\n'
case ":$PATH:" in
  *":$PREFIX:"*) say "rsb is on your PATH." ;;
  *)
    say "Add $PREFIX to your PATH:"
    printf '\n      export PATH="%s:$PATH"\n\n' "$PREFIX"
    ;;
esac

say "Try:  rsb brief"
say "Docs: https://github.com/$REPO"
printf '\n'
