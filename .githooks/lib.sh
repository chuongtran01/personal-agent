# Shared by the hooks. Sourced, not run.
#
# CHANGED holds the paths a hook is about (staged files, or the files in the commits being
# pushed), one per line. touches_backend and touches_frontend say whether any is on that side.

ROOT="$(git rev-parse --show-toplevel)"
BACKEND_BIN="$ROOT/backend/.venv/bin"

touches_backend() { printf '%s\n' "$CHANGED" | grep -qE '^(backend|data|evals)/'; }
touches_frontend() { printf '%s\n' "$CHANGED" | grep -qE '^frontend/'; }

# run "what it checks" command...   Prints the command's output only if it fails.
run() {
  label="$1"
  shift
  printf '  %s ... ' "$label"
  if output="$("$@" 2>&1)"; then
    echo "ok"
  else
    echo "FAILED"
    printf '%s\n' "$output" | tail -n 40
    echo
    echo "$HOOK blocked this. Fix it, or skip the hook once with --no-verify."
    exit 1
  fi
}

need_backend() {
  if [ ! -x "$BACKEND_BIN/ruff" ]; then
    echo "backend/.venv is missing: run 'make install' first."
    exit 1
  fi
}

# The frontend checks need its dependencies. Without them they are skipped, and the hook says so
# instead of blocking someone who only works on the backend.
have_frontend_deps() {
  if [ -d "$ROOT/frontend/node_modules" ]; then
    return 0
  fi
  echo "frontend changed, but frontend/node_modules is missing: skipped (run 'make web-install')"
  return 1
}
