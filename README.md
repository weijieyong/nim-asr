# nim-asr — Global Hotkey Dictation

Toggle-based dictation that converts speech to text into any text field. Press a global hotkey to start recording, speak naturally, press again to stop. The audio is transcribed via NVIDIA Riva/NIM ASR (local GPU), post-processed, and inserted into the currently focused text area — IDE, terminal, browser, chat app, or any other text input.

*Tested on NVIDIA GeForce RTX 5080.*

## How It Works

```
Hotkey press 1 → Start recording (mic + ASR run concurrently)
     ↓  Speak naturally (pause, think, continue)
Hotkey press 2 → Stop recording → Finalize transcript → Insert text
```

Audio is streamed to Riva ASR **during** recording in a background thread, so most of the GPU work finishes before you press stop. After stopping, only the trailing ~0.5-1s of audio needs finalization. No partial/interim text is ever typed.

## Prerequisites

- Linux with X11 (Wayland support planned)
- [xdotool](https://www.semicomplete.com/projects/xdotool/)
- NVIDIA GPU with Docker + NVIDIA Container Toolkit

```bash
sudo apt update
sudo apt install xdotool portaudio19-dev

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

### 1. Riva NIM ASR Server

Configure your `.env` file with an NGC API key, then start the server:

```bash
docker compose up
```

The server exposes a gRPC endpoint at `localhost:50051` (the model is `parakeet-1.1b-en-US-asr-streaming` — streaming-only).

### 2. Client Dependencies

```bash
uv sync
```

### 3. Bind the Toggle Script to a Global Hotkey

Edit your keyboard shortcut settings (GNOME Settings → Keyboard → Keyboard Shortcuts, or your WM equivalent). Add a custom shortcut:

| Field | Value |
|---|---|
| Name | Toggle Dictation |
| Command | `/home/jie/03_Exp/nim-asr/toggle_dictation.sh` |
| Shortcut | Choose a key — e.g. `Ctrl+Super+D` |

## Usage

| Action | Result |
|---|---|
| Press shortcut (1st time) | Notification "Recording…" — speak naturally |
| Press shortcut (2nd time) | Notification "Stopping…" — audio finalizes → text appears in active field |
| Notification "Inserted N characters" | Done |

The text appears in whatever window or terminal is currently focused (IDE, terminal, browser, etc.).

## Post-Processing

Raw ASR output is cleaned up via a configurable replacement dictionary — useful for technical terms, special characters, file extensions, and common dictation patterns. Edit `DictationConfig.replacements` in `offline_dictation.py` to add your own mappings.

### Default replacements include:

| Say | Get |
|---|---|
| "dot py" | `.py` |
| "dot json" | `.json` |
| "underscore" | `_` |
| "star star" | `**` |
| "new line" | `\n` |
| "tab" | `\t` |
| "R O S two" | `ROS 2` |
| "Fast API" | `FastAPI` |
| "right arrow" | `->` |
| "fat arrow" | `=>` |

Replacements are sorted by key length (longest first) so `"dot py "` matches before `"dot "`.

### Word Boosting

The ASR often misrecognizes domain-specific terms (technical jargon, names, unusual words). Add them to `DictationConfig.boosted_words`:

```python
boosted_words = [
    "Python", "TypeScript", "FastAPI", "CUDA",
    "async", "await", "kwargs", "middleware",
    # add your own domain terms
]
```

## Power / Battery Notes

On battery (GPU capped at ~30W), the model runs at roughly **1× real-time** — 5 seconds of speech takes ~5 seconds to process. With concurrent streaming, processing overlaps with recording, so you only wait **~1s** after pressing stop.

On AC power (full GPU wattage), RTF drops to ~0.3-0.5×. Processing finishes before you even press stop.

## Logging

All sessions are logged to `dictation.log` with timestamps:

```
[2026-05-04 18:23:36] Recording … (press the dictation shortcut again to stop)
[2026-05-04 18:23:36] Recorded 4.9 s of audio (49 chunks)
[2026-05-04 18:23:36] Saved 156844 bytes to /tmp/tmpco9onnge.wav
[2026-05-04 18:23:36] Replaying 156800 PCM bytes through streaming ASR ...
[2026-05-04 18:23:39] Streaming ASR finished in 5.4 s (1 utterance(s))
[2026-05-04 18:23:39] Raw transcript: hello world
[2026-05-04 18:23:39] Inserted 11 characters via xdotool
```

## Configuration Notes (Riva Server)

- **VAD & Diarization**: If background noise (fan) affects performance, try `vad=silero` with `diarizer=sortformer`
- **Model Type**: Avoid `model_type=prebuilt` (triggers DGX Spark version)
- This model (`parakeet-1.1b-en-US-asr-streaming`) is streaming-only — offline/batch ASR is not supported

## Maintenance

### Clear Cache & Models

```bash
rm -rf ~/03_Exp/nim-asr/riva-models/*
rm -rf ~/.cache/nim/*
```

### Inspect Models Inside Container

```bash
docker exec -it parakeet-1-1b-ctc-en-us ls -R /data/models
```

### Export Models to Host

```bash
sudo docker cp parakeet-1-1b-ctc-en-us:/data/models /home/jie/03_Exp/nim-asr/riva-models
sudo chown -R $USER:$USER /home/jie/03_Exp/nim-asr/riva-models
```

## Architecture (for Developers)

See [AGENTS.md](AGENTS.md) for the full architecture, class reference, and extension plans.

The code is organized as a single file (`offline_dictation.py`) with clearly separated classes designed for extraction into a package:

| Class | Future Module |
|---|---|
| `DictationConfig` | `dictation/config.py` |
| `AudioCapture` | `dictation/audio.py` |
| `ConcurrentTranscriber` | `dictation/asr.py` |
| `PostProcessor` | `dictation/post.py` |
| `TextInserter` | `dictation/insert.py` |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| No notification on hotkey | Hotkey binding wrong path | Check shortcut points to `toggle_dictation.sh` |
| "Recording…" but nothing appears after stop | Riva server not running | `docker compose up` |
| Text appears 10s+ after stop | GPU on battery (30W) | Plug in AC power for faster processing |
| ASR returns "Unavailable model" | Wrong model config | Check compose.yaml uses `parakeet-1.1b-en-US-asr-streaming` |
| `xdotool` fails | Wrong X11 display / Wayland | Run `echo $DISPLAY`; use X11 session (Wayland support TBD) |
| No audio recorded | Wrong mic selected | `arecord -D hw:1,0 -f dat -d 5 /tmp/test.wav` to test mic |
