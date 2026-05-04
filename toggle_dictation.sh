#!/bin/bash
#
# Toggle dictation: press once to start recording, press again to stop,
# transcribe, and insert the result into the active text field.
#
# First press:  spawn offline_dictation.py (records until SIGINT)
# Second press: send SIGINT -> Python finalises audio -> ASR -> inserts text -> exits
#               (force-killed with SIGKILL only if it hangs beyond the timeout)

# === Configuration =========================================================

SCRIPT="offline_dictation.py"
PROJECT_DIR="/home/jie/03_Exp/nim-asr"
LOG_FILE="$PROJECT_DIR/dictation.log"
UV_BIN="$HOME/.local/bin/uv"

GRACEFUL_TIMEOUT=30  # seconds to wait for Python to finalise & transcribe
FORCE_KILL_TIMEOUT=5 # seconds between SIGTERM and SIGKILL

# === Determine state =======================================================

# Only match the python3 child process (not the `uv` parent) to get a single PID.
# pgrep returns 1 (no match) on first press — that's expected.
PID=$(pgrep -f "python3.*${SCRIPT}" 2>/dev/null || true)

if [ -n "$PID" ]; then
    # ── Session is running -> stop it gracefully ──────────────────────────
    notify-send "Dictation" "Stopping ... (transcribing)" --icon=audio-input-microphone-muted 2>/dev/null || true

    # 1. Send SIGINT - Python catches this, stops recording,
    #    runs offline ASR, post-processes, inserts text, then exits.
    kill -INT "$PID" 2>/dev/null || true

    # 2. Wait for a clean exit (up to GRACEFUL_TIMEOUT seconds).
    waited=0
    while kill -0 "$PID" 2>/dev/null; do
        if [ "$waited" -ge "$GRACEFUL_TIMEOUT" ]; then
            notify-send "Dictation" "Not responding - force killing ..." --icon=dialog-warning 2>/dev/null || true
            kill -TERM "$PID" 2>/dev/null || true
            sleep "$FORCE_KILL_TIMEOUT"
            if kill -0 "$PID" 2>/dev/null; then
                kill -KILL "$PID" 2>/dev/null || true
                notify-send "Dictation" "Force killed" --icon=dialog-error 2>/dev/null || true
            fi
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
else
    # ── No session running -> start recording ─────────────────────────────
    cd "$PROJECT_DIR" || exit 1

    # Launch offline_dictation in background; pipe all output (with
    # timestamps) to the log file.
    # shellcheck disable=SC2089
    ($UV_BIN run "$SCRIPT" 2>&1 \
        | awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0 }' \
        >> "$LOG_FILE" \
    ) &

    notify-send "Dictation" "Recording ... (press shortcut to stop)" --icon=audio-input-microphone 2>/dev/null || true
fi
