#!/bin/bash

# Configuration
SCRIPT_PATH="/home/jie/03_Exp/nim-asr/direct_dictation.py"
PROJECT_DIR="/home/jie/03_Exp/nim-asr"
LOG_FILE="$PROJECT_DIR/dictation.log"
UV_BIN="$HOME/.local/bin/uv"

# Environment for ydotool
export YDOTOOL_SOCKET="/tmp/ydotool/socket"

# Find the PID of the running script
PID=$(pgrep -f "python.*direct_dictation.py")

if [ -n "$PID" ]; then
    # Script is running, kill it
    kill "$PID"
    notify-send "Dictation" "Microphone OFF" --icon=audio-input-microphone-muted
else
    # Script is not running, start it
    cd "$PROJECT_DIR" || exit
    
    # Start script, append timestamps to every line, and log to file
    # Uses awk to prepend [YYYY-MM-DD HH:MM:SS] to each line
    ($UV_BIN run "$SCRIPT_PATH" 2>&1 | awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0 }' >> "$LOG_FILE") &
    
    notify-send "Dictation" "Microphone ON" --icon=audio-input-microphone
fi