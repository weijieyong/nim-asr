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
PROJECT_DIR="/home/simt-wj/02_Tools/nim-asr"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
UV_BIN="$HOME/.local/bin/uv"

GRACEFUL_TIMEOUT=30  # seconds to wait for Python to finalise & transcribe
FORCE_KILL_TIMEOUT=5 # seconds between SIGTERM and SIGKILL
TRAY_PID_FILE="$PROJECT_DIR/.dictation_tray.pid"
STATE_FILE="$PROJECT_DIR/.dictation_state"

# === Determine state =======================================================

# Only match the python3 child process (not the `uv` parent) to get a single PID.
# pgrep returns 1 (no match) on first press — that's expected.
PID=$(pgrep -n -f "python3.*${SCRIPT}" 2>/dev/null || true)

if [ -n "$PID" ]; then
    STATE=$(cat "$STATE_FILE" 2>/dev/null || true)

    if [ "$STATE" != "recording" ]; then
        if [ "$STATE" = "starting" ]; then
            notify-send "Dictation" "Starting microphone - wait for Recording" --icon=audio-input-microphone 2>/dev/null || true
        else
            notify-send "Dictation" "Finishing previous dictation" --icon=audio-input-microphone-muted 2>/dev/null || true
        fi
        exit 0
    fi

    # ── Session is running -> stop it gracefully ──────────────────────────
    # Remove tray icon
    if [ -f "$TRAY_PID_FILE" ]; then
        INDICATOR_TARGET=$(cat "$TRAY_PID_FILE" 2>/dev/null || true)
        case "$INDICATOR_TARGET" in
            pgid=*)
                kill -TERM -- "-${INDICATOR_TARGET#pgid=}" 2>/dev/null || true
                ;;
            *[!0-9]*|'')
                ;;
            *)
                # Compatibility with PID files created by older versions.
                kill -TERM "$INDICATOR_TARGET" 2>/dev/null || true
                ;;
        esac
        rm -f "$TRAY_PID_FILE"
    fi

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

    if [ -x "$PYTHON_BIN" ]; then
        "$PYTHON_BIN" "$SCRIPT" >/dev/null 2>&1 &
    else
        "$UV_BIN" run "$SCRIPT" >/dev/null 2>&1 &
    fi

fi
