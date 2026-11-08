#!/usr/bin/env bash
# Opt-in foreground systemd scope for CueWire. Not a daemon or persistent service.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/target/release/cuewire"
SCOPE="cuewire-hotkeys.scope"
LABEL="CueWire Hotkeys Scoped Foreground"
ACTION="${1:-}"
if (( $# > 0 )); then shift; fi

usage() {
  cat <<'EOF'
CueWire scoped foreground:
  bash tools/cuewire_scope.sh check
  bash tools/cuewire_scope.sh run -- --mic NODE --monitor SINK --cue 0=FILE ...
  bash tools/cuewire_scope.sh emergency

RUN requires a manually configured input mic, monitor, cue WAV(s) and
a compiled target/release/cuewire. It creates a collectible transient
user systemd SCOPE (not an autostarting service).
EMERGENCY SIGKILL targets ONLY the CueWire-marked scope cgroup.
No pkill, killall, forcekillactive or changes to system audio defaults.
EOF
}
scope_property() {
  systemctl --user show "$SCOPE" --property="$1" --value 2>/dev/null || true
}
scope_owned() {
  [[ "$(scope_property Description)" == "$LABEL" ]] \
    && [[ "$(scope_property Transient)" == "yes" ]]
}
runtime_socket() {
  if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -d "$XDG_RUNTIME_DIR" || ! -O "$XDG_RUNTIME_DIR" ]]; then
    echo "XDG_RUNTIME_DIR missing, invalid, or not owned by user." >&2
    return 1
  fi
  printf '%s/cuewire-v0.1.sock\n' "$XDG_RUNTIME_DIR"
}
cleanup_if_stale() {
  if systemctl --user is-active --quiet "$SCOPE" 2>/dev/null; then
    echo '[cuewire/scope] Scope remains active; socket left untouched.' >&2
    return 0
  fi
  local socket_file
  socket_file="$(runtime_socket)" || return 1
  python3 - "$socket_file" <<'PY'
import errno,os,socket,stat,sys
p=sys.argv[1]
try: before=os.lstat(p)
except FileNotFoundError: sys.exit(0)
if not stat.S_ISSOCK(before.st_mode) or before.st_uid!=os.getuid():
    print("CueWire socket is not an owned socket; not removing",file=sys.stderr)
    sys.exit(1)
probe=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
probe.settimeout(.15)
try: probe.connect(p)
except OSError as exc:
    if exc.errno not in (errno.ECONNREFUSED,errno.ENOENT):
        print("Socket listener uncertain; refusing cleanup:",exc,file=sys.stderr)
        sys.exit(1)
else:
    print("Socket listener still alive; refusing cleanup",file=sys.stderr)
    sys.exit(1)
finally: probe.close()
try: now=os.lstat(p)
except FileNotFoundError: sys.exit(0)
if (before.st_dev,before.st_ino)!=(now.st_dev,now.st_ino):
    print("Socket inode changed; refusing cleanup",file=sys.stderr)
    sys.exit(1)
os.unlink(p)
print("[cuewire/scope] Removed stale CueWire socket from XDG_RUNTIME_DIR")
PY
}
case "$ACTION" in
  help|-h|--help) usage ;;
  check)
    for cmd in systemd-run systemctl python3; do
      command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 2; }
    done
    runtime_socket >/dev/null
    if systemctl --user is-active --quiet "$SCOPE"; then
      if scope_owned; then echo "ACTIVE: marked CueWire scope"
      else echo "CONFLICT: active unrecognized scope" >&2; exit 3; fi
    else echo "INACTIVE: CueWire scope not running"; fi
    ;;
  run)
    [[ "${1:-}" == "--" ]] && shift
    [[ -x "$BIN" ]] || { echo "Missing release binary. Run cargo build --release --locked." >&2; exit 2; }
    for cmd in systemd-run systemctl python3; do
      command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 2; }
    done
    sock="$(runtime_socket)" || exit 2
    if [[ -e "$sock" || -L "$sock" ]]; then
      echo "Socket $sock already exists. Never removing or hijacking it." >&2
      exit 2
    fi
    if systemctl --user is-active --quiet "$SCOPE"; then
      echo "Refusing to launch when scope is already active." >&2
      exit 2
    fi
    # The invoking shell stays outside the unit and handles safe socket
    # cleanup after systemd-run returns. Emergency cgroup kill never targets
    # this wrapper, another app, or the system PipeWire process.
    trap 'cleanup_if_stale || true' EXIT
    systemd-run --user --scope --collect --unit="$SCOPE" \
      --description="$LABEL" -- "$BIN" run "$@"
    ;;
  emergency)
    command -v systemctl >/dev/null || { echo 'Missing systemctl' >&2; exit 2; }
    if ! systemctl --user is-active --quiet "$SCOPE"; then
      echo 'No active scoped CueWire to terminate.' >&2
      exit 3
    fi
    if ! scope_owned; then
      echo 'REFUSED: scope marker does not match transient CueWire.' >&2
      exit 3
    fi
    systemctl --user kill --kill-whom=cgroup --signal=SIGKILL "$SCOPE"
    echo '[cuewire/scope] Emergency SIGKILL sent to CueWire cgroup only.'
    ;;
  *) usage; exit 2 ;;
esac
