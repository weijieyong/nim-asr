# Agent Context: nim-asr — Global Hotkey Dictation

## Project Overview

A toggle-based dictation system that converts speech to text into any text field. Press a global hotkey to start recording, speak naturally, press again to stop. The recorded audio is transcribed via NVIDIA Riva/NIM ASR (local GPU), post-processed, and inserted into the currently focused text area — IDE, terminal, browser, chat app, or any other text input.

**Key constraint**: The Riva model (`parakeet-1.1b-en-US-asr-streaming`) supports **streaming only** (`type=online`, `offline=False`). There is no offline/batch ASR endpoint. The system uses streaming ASR internally but never exposes interim/partial results to the user.

## File Structure

```
nim-asr/
├── offline_dictation.py    # Main Python script (single file, modular classes)
├── toggle_dictation.sh     # Shell launcher bound to global hotkey
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
offline_dictation.py
  │
  ├── DictationConfig      ← all settings, boosted words, replacements
  ├── AudioCapture         ← pyaudio, writes WAV, feeds chunks to callback
  ├── ConcurrentTranscriber ← bg thread, streams mic chunks to Riva live
  │     └── fallback: StreamingTranscriber  ← replay WAV through streaming
  ├── PostProcessor        ← string replacements + whitespace cleanup
  └── TextInserter         ← xdotool type (pluggable backend)
```

### Data Flow (happy path)

```
User presses hotkey (first press)
  └→ toggle_dictation.sh spawns: uv run offline_dictation.py
       └→ AudioCapture starts recording to temp WAV
       └→ ConcurrentTranscriber starts bg ASR thread
            └→ each mic chunk → queue → Riva streaming ASR
                 └→ final utterances collected in thread-safe list

User speaks naturally (may pause, think, continue)

User presses hotkey (second press)
  └→ toggle_dictation.sh sends SIGINT to python3 child
       └→ AudioCapture.stop() → recording loop exits → WAV saved
       └→ ConcurrentTranscriber.stop()
            └→ sentinel → server finalizes trailing audio
            └→ join bg thread → collect all utterances
       └→ PostProcessor.process(transcript)
       └→ TextInserter.insert(cleaned_text)
       └→ process exits
```

## Class Reference

### `DictationConfig` (dataclass)
Central config. Fields documented inline. Key areas:
- **Audio**: `sample_rate` (16000), `channels` (1), `chunk_duration_ms` (100)
- **Riva**: `riva_server`, `language_code`, `profanity_filter`, `automatic_punctuation`, `verbatim_transcripts`
- **Boosting**: `boosted_words` (list of technical terms), `boost_score` (10.0)
- **Insertion**: `inserter` ("xdotool")
- **Post-processing**: `replacements` (term → symbol mapping dict)
- **Logging**: `log_file`, `log_level`

**Extraction path**: → `dictation/config.py`

### `AudioCapture`
Records mic to WAV file. Properties:
- `stop()` / `is_stopped` — signal from SIGINT handler
- `record(output_path, chunk_callback=None)` — blocking loop, feeds each chunk to callback

**Signal response latency**: bounded by `chunk_duration_ms` (100ms). The loop checks `_stopped` after each `stream.read()` returns. In CPython, signal handlers run between bytecode instructions, so the handler executes after `read()` returns.

**Extraction path**: → `dictation/audio.py`

### `ConcurrentTranscriber`
Background thread feeds mic chunks to Riva streaming ASR live.
- `start()` — launches worker thread
- `feed(chunk)` — thread-safe queue put
- `stop()` → `str` — sends sentinel, joins thread, returns concatenated transcript. Raises `RuntimeError` on ASR failure.

**Threading**: Uses `queue.Queue[bytes | None]`. The sentinel `None` signals end-of-stream to the gRPC generator. The worker thread holds `_results: list[str]` behind `_lock`.

**Why streaming with interim_results=False**: The Riva model doesn't support offline batch. We use streaming but never show partials. The `interim_results=False` config means the server only sends back utterances when endpointing detects a complete utterance.

**Extraction path**: → `dictation/asr.py`

### `StreamingTranscriber` (fallback)
Replays a saved WAV file through streaming ASR. Used as fallback when `ConcurrentTranscriber` fails. Reads raw PCM via `wave.readframes()`, chunks it, sends through `streaming_response_generator`.

### `PostProcessor`
Applies `DictationConfig.replacements` as sorted string replacements (longest keys first to avoid partial matches), then normalizes whitespace.

**Extraction path**: → `dictation/post.py`

### `TextInserter`
Currently `xdotool type --clearmodifiers`. Escapes `\`, `"`, `` ` ``, `$`. Pluggable — add methods for ydotool, clipboard paste, etc.

**Extraction path**: → `dictation/insert.py`

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
Edit `DictationConfig.boosted_words` for terms the ASR frequently misrecognizes. Scores can vary per word via `boost_score`.

### Post-Processing Replacements
Edit `DictationConfig.replacements` dict. Keys are matched as plain substrings. Sort order matters: longer keys match first (prevents `" dot py "` being shadowed by `" dot "`).

To extend with regex patterns, modify `PostProcessor.process()`.

## Future Extension Points

The code is designed for modular extraction:

| Current Location | Future Module |
|---|---|
| `DictationConfig` | `dictation/config.py` |
| `AudioCapture` | `dictation/audio.py` |
| `ConcurrentTranscriber` + `StreamingTranscriber` | `dictation/asr.py` |
| `PostProcessor` | `dictation/post.py` |
| `TextInserter` | `dictation/insert.py` |

Known planned extensions:
- **LLM cleanup**: Add a post-ASR LLM pass in PostProcessor
- **Command mode vs natural prompt**: Detect "command" speech pattern vs dictation
- **Wayland support**: Add `ydotool` backend to TextInserter
- **Clipboard paste mode**: Add `pyperclip` + simulated Ctrl+V inserter
- **Config file**: Load `DictationConfig` from YAML/TOML
- **Custom vocabulary**: Per-project or per-session word lists

## Error Handling Patterns

1. **Recording fails** (no mic, OSError) → notify error, exit 1
2. **Empty recording** (WAV ≤ 44 bytes) → notify "No speech", exit 0
3. **Concurrent ASR fails** → fall back to `StreamingTranscriber` replay
4. **Fallback ASR also fails** → log error, return empty transcript
5. **xdotool fails** → log error (non-fatal, text lost)
6. **Unhandled exception** → `main()` catches, logs, notifies, returns 1
7. **Shell wait timeout** → SIGTERM → 5s → SIGKILL

## GPU / Performance Notes

- Model runs at RTF ≈ 1.0 at 30W (battery), RTF ≈ 0.3-0.5 at full power (AC)
- Concurrent streaming processes audio during recording → post-stop latency is just ~0.5-1s (trailing finalization)
- Two-phase fallback replay adds full audio-duration latency
- No interim/partial results are ever typed at any power state

## Conventions

- Python 3.13+, `from __future__ import annotations`
- Type annotations throughout
- `| None` instead of `Optional[X]`
- Dataclasses for configuration
- Thread safety via `queue.Queue` and `threading.Lock`
- All Riva config built inline (no argparser)
- Signal handlers are simple — set a flag, no blocking calls
- Logging to both file (timestamps) and stderr
