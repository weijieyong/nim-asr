# Agent Context: nim-asr — Global Hotkey Dictation

## Project Overview

A toggle-based dictation system that converts speech to text into any text field. Press a global hotkey to start recording, speak naturally, press again to stop. The recorded audio is transcribed via NVIDIA Riva/NIM ASR (local GPU), post-processed, and inserted into the currently focused text area — IDE, terminal, browser, chat app, or any other text input.

**ASR modes**: The Parakeet CTC NIM is deployed with `mode=all`, which exposes both streaming and offline inference. `NIM_ASR_MODE=stream` keeps the low-latency background path; `NIM_ASR_MODE=offline` records the complete WAV and sends it in one request after stopping. Neither mode exposes interim/partial results to the user.

## File Structure

```
nim-asr/
├── offline_dictation.py    # Entry point: session lifecycle, signal handling, logging
├── toggle_dictation.sh     # Shell launcher bound to global hotkey
├── mic_check.py            # Diagnostic utility: list input devices + find USB mic index
├── dictation/              # Core library package
│   ├── __init__.py         # Re-exports all public classes
│   ├── config.py           # DictationConfig dataclass + .env helpers
│   ├── audio.py            # AudioCapture + PCM resampler
│   ├── asr.py              # ConcurrentTranscriber + OfflineTranscriber + StreamingTranscriber (fallback)
│   ├── post.py             # PostProcessor (replacements + whitespace)
│   └── insert.py           # TextInserter (xdotool backend)
├── AGENTS.md               # This file — AI agent context
├── README.md               # Human-facing docs
├── pyproject.toml          # Python deps (pyaudio, nvidia-riva-client)
├── compose.yaml            # Docker Compose for Riva NIM server
└── dictation.log           # Timestamped session logs
```

## Architecture

### Component Diagram

```
toggle_dictation.sh  (global hotkey bind)
       │
       │  first press: spawns
       │  second press: sends SIGINT
       ▼
offline_dictation.py          ← session lifecycle, signal handlers, logging setup
  │
  └── dictation/              ← importable package
        ├── DictationConfig   ← all settings, boosted words, replacements
        ├── AudioCapture      ← pyaudio, writes WAV, feeds chunks to callback
        ├── ConcurrentTranscriber  ← bg thread, streams mic chunks to Riva live
        ├── OfflineTranscriber  ← sends the complete WAV through offline ASR
        │     └── streaming fallback: StreamingTranscriber  ← replay WAV through streaming
        ├── PostProcessor     ← string replacements + whitespace cleanup
        └── TextInserter      ← xdotool type (pluggable backend)
```

### Data Flow (happy path)

```
User presses hotkey (first press)
  └→ toggle_dictation.sh spawns: uv run offline_dictation.py
       └→ AudioCapture starts recording to temp WAV
       └→ stream mode: ConcurrentTranscriber starts bg ASR thread
            └→ each mic chunk → queue → Riva streaming ASR
       └→ offline mode: audio is only written to the temp WAV

User speaks naturally (may pause, think, continue)

User presses hotkey (second press)
  └→ toggle_dictation.sh sends SIGINT to python3 child
       └→ AudioCapture.stop() → recording loop exits → WAV saved
       └→ stream mode: ConcurrentTranscriber.stop()
            └→ sentinel → server finalizes trailing audio
            └→ join bg thread → collect all utterances
       └→ offline mode: OfflineTranscriber sends the complete WAV once
       └→ PostProcessor.process(transcript)
       └→ TextInserter.insert(cleaned_text)
       └→ process exits
```

## Module Reference

### `dictation/config.py` — `DictationConfig`
Central config dataclass. Key fields:
- **Audio**: `sample_rate` (16000), `capture_sample_rate` (env: `NIM_ASR_CAPTURE_SAMPLE_RATE`), `channels` (1), `chunk_duration_ms` (100)
- **Device**: `input_device_index` (env: `NIM_ASR_INPUT_DEVICE_INDEX`) — leave unset for system default
- **Riva**: `riva_server`, `language_code`, `profanity_filter`, `automatic_punctuation`, `verbatim_transcripts`
- **ASR mode**: `asr_mode` (env: `NIM_ASR_MODE`, `stream` or `offline`)
- **Boosting**: `boosted_words` (list of technical terms), `boost_score` (10.0)
- **Insertion**: `inserter` ("xdotool")
- **Post-processing**: `replacements` (term → symbol mapping dict)
- **Logging**: `log_file`, `log_level`

Private helpers `_read_dotenv_value` / `_getenv_int` read integer settings from env or local `.env`.

### `dictation/audio.py` — `AudioCapture`
Records mic to WAV file.
- `stop()` / `is_stopped` — called from SIGINT handler
- `record(output_path, chunk_callback=None)` — blocking loop, feeds each chunk to callback

`_resample_pcm16_mono(data, from_rate, to_rate)` — linear resampler for devices that reject 16 kHz input.

**Signal response latency**: bounded by `chunk_duration_ms` (100ms). The loop checks `_stopped` after each `stream.read()` returns.

### `dictation/asr.py` — `ConcurrentTranscriber` + `OfflineTranscriber` + `StreamingTranscriber`

`ConcurrentTranscriber` — background thread feeds live mic audio to Riva streaming ASR.
- `start()` — launches worker thread
- `feed(chunk)` — thread-safe queue put
- `stop()` → `str` — sends sentinel, joins thread, returns concatenated transcript. Raises `RuntimeError` on ASR failure.

**Threading**: `queue.Queue[bytes | None]`. Sentinel `None` signals end-of-stream to the gRPC generator. Worker thread holds `_results: list[str]` behind `_lock`.

`StreamingTranscriber` — fallback: replays a saved WAV through streaming ASR when `ConcurrentTranscriber` fails. Reads raw PCM via `wave.readframes()`, chunks it, sends through `streaming_response_generator`.

`OfflineTranscriber` — reads the saved WAV and sends all raw PCM in one `offline_recognize` request. Requires the NIM server's `mode=all` or offline profile.

`_build_recognition_config(config)` — shared helper that constructs `RecognitionConfig` with word boosting for both transcriber classes.

**Why `interim_results=False`**: Partial text is never shown or typed. Streaming mode only collects final endpointed utterances; offline mode returns the final complete-file response.

### `dictation/post.py` — `PostProcessor`
Applies `DictationConfig.replacements` as sorted string replacements (longest keys first to avoid partial matches), then normalizes whitespace.

### `dictation/insert.py` — `TextInserter`
Currently `xdotool type --clearmodifiers`. Escapes `\`, `"`, `` ` ``, `$`. Pluggable — add methods for ydotool, clipboard paste, etc.

### `offline_dictation.py` — Entry point
Session-level glue: `_setup_logging`, `_notify`, `_cleanup_temp`, `run_session`, `main`.

### `mic_check.py` — Diagnostic utility
Lists all input devices (name, channels, supported sample rates via `sounddevice`) then prints the pyaudio device index of the first USB mic. Run with `uv run mic_check.py`. Not used by the main dictation flow.

## Signal Protocol

### toggle_dictation.sh

| Action | Behavior |
|---|---|
| First press (no PID) | `cd PROJECT_DIR`, spawn `uv run offline_dictation.py` in bg, `notify-send` |
| Second press (PID found) | `kill -INT $PID`, wait up to 30s, escalate to SIGTERM → SIGKILL |
| PID matching | `pgrep -f "python3.*offline_dictation.py"` — only matches `.venv/bin/python3` child, NOT `uv` parent |

**Important**: The script does NOT use `set -euo pipefail`. Every command that might fail gracefully (`pgrep`, `notify-send`, `kill`) uses `|| true`.

### offline_dictation.py

| Signal | Handler |
|---|---|
| `SIGINT` | `_on_stop` → `capture.stop()` → recording loop exits |
| `SIGTERM` | Same as SIGINT (fallback from shell timeout) |
| After recording | Handlers reset to `SIG_DFL` so Ctrl+C during ASR kills immediately |

## Configuration & Customization

### Word Boosting
Edit `DictationConfig.boosted_words` in `dictation/config.py` for terms the ASR frequently misrecognizes.

### Post-Processing Replacements
Edit `DictationConfig.replacements` in `dictation/config.py`. Keys are matched as plain substrings. Longer keys match first (prevents `" dot py "` being shadowed by `" dot "`).

To extend with regex patterns, modify `PostProcessor.process()` in `dictation/post.py`.

### Device Selection
Set `NIM_ASR_INPUT_DEVICE_INDEX` in `.env` to pin a specific mic. Run `uv run mic_check.py` to find the index.
Set `NIM_ASR_CAPTURE_SAMPLE_RATE` if your device rejects 16 kHz (e.g. `44100`); audio is resampled automatically.

## Error Handling Patterns

1. **Recording fails** (no mic, OSError) → notify error, exit 1
2. **Empty recording** (WAV ≤ 44 bytes) → notify "No speech", exit 0
3. **Concurrent ASR fails** → fall back to `StreamingTranscriber` replay
4. **Offline ASR fails** → fall back to `StreamingTranscriber` replay
5. **Streaming fallback also fails** → log error, return empty transcript
6. **xdotool fails** → log error (non-fatal, text lost)
7. **Unhandled exception** → `main()` catches, logs, notifies, returns 1
8. **Shell wait timeout** → SIGTERM → 5s → SIGKILL

## GPU / Performance Notes

- Model runs at RTF ≈ 1.0 at 30W (battery), RTF ≈ 0.3-0.5 at full power (AC)
- Concurrent streaming processes audio during recording → post-stop latency is just ~0.5-1s (trailing finalization)
- Offline mode processes the complete WAV after stopping, so the full transcription time is post-stop latency
- Two-phase fallback replay adds full audio-duration latency
- No interim/partial results are ever typed at any power state

## Known Planned Extensions

- **LLM cleanup**: Post-ASR LLM pass in `PostProcessor`
- **Command mode vs natural prompt**: Detect speech intent in `PostProcessor`
- **Wayland support**: `ydotool` backend in `TextInserter`
- **Clipboard paste mode**: `pyperclip` + simulated Ctrl+V inserter
- **Config file**: Load `DictationConfig` from YAML/TOML
- **Custom vocabulary**: Per-project or per-session word lists

## Conventions

- Python 3.13+, `from __future__ import annotations`
- Type annotations throughout
- `| None` instead of `Optional[X]`
- Dataclasses for configuration
- Thread safety via `queue.Queue` and `threading.Lock`
- All Riva config built inline (no argparser)
- Signal handlers are simple — set a flag, no blocking calls
- Logging to both file (timestamps) and stderr
