# nim-asr — Global Hotkey Dictation

Toggle-based dictation that converts speech to text into any text field. Press a global hotkey to start recording, speak naturally, press again to stop. The audio is transcribed via NVIDIA Riva/NIM ASR (local GPU), post-processed, and inserted into the currently focused text area — IDE, terminal, browser, chat app, or any other text input.

*Tested on NVIDIA GeForce RTX 5080.*

## How It Works

```
Hotkey press 1 → Start process → Wait for "Recording…"
     ↓  Microphone is ready; ASR mode is selected by NIM_ASR_MODE
     ↓  Speak naturally (pause, think, continue)
Hotkey press 2 → Stop recording → Finalize transcript → Insert text
```

In `stream` mode, audio is sent to Riva ASR **during** recording in a background thread, so most of the GPU work finishes before you press stop. In `offline` mode, the complete WAV is sent once after recording stops, which adds processing delay but gives the recognizer the full utterance. No partial/interim text is ever typed in either mode.

The first press starts a new Python process and opens the USB microphone. This creates a short startup window. Wait for the **Recording…** notification or tray indicator before speaking; audio spoken before the microphone is ready cannot be captured.

## Prerequisites

- Linux with X11
- [xdotool](https://www.semicomplete.com/projects/xdotool/)
- `notify-send` for status notifications
- `yad` for the optional persistent recording tray indicator
- NVIDIA GPU with Docker + NVIDIA Container Toolkit

```bash
sudo apt update
sudo apt install xdotool libnotify-bin yad portaudio19-dev

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

### 1. Riva NIM ASR Server

Create `.env` and configure the NGC key, model profile, cache paths, ports, and microphone:

```dotenv
NGC_API_KEY=<your-key>

CONTAINER_ID=parakeet-1-1b-ctc-en-us
# Keep both streaming and offline inference available.
NIM_TAGS_SELECTOR=mode=all,vad=default,diarizer=disabled
NIM_DISABLE_MODEL_DOWNLOAD=false

LOCAL_NIM_CACHE=/home/<user>/.cache/nim
RIVA_MODELS_PATH=/path/to/nim-asr/riva-models/models

NIM_HTTP_API_PORT=9000
NIM_GRPC_API_PORT=50501

NIM_ASR_INPUT_DEVICE_INDEX=<pyaudio-device-index>
NIM_ASR_CAPTURE_SAMPLE_RATE=44100
# Client path: stream (default) or offline.
NIM_ASR_MODE=stream
NIM_ASR_KEEP_AUDIO=false
```

On the first run, allow the model download. After the model is present in the mounted cache/model paths, `NIM_DISABLE_MODEL_DOWNLOAD=true` can prevent repeat downloads.

Start the server:

```bash
docker compose up -d
docker compose logs -f
```

With the configuration above, the server exposes HTTP on `localhost:9000` and gRPC on `localhost:50501`. The `mode=all` profile keeps both streaming and offline inference available; the client selects the path with `NIM_ASR_MODE`.
If the server was already running with `mode=str`, run `docker compose up -d` once after changing the selector so the combined profile is loaded.

### 2. Client Dependencies

```bash
uv sync
uv run mic_check.py
```

Set `NIM_ASR_INPUT_DEVICE_INDEX` to the desired PyAudio input index. If that device does not accept 16 kHz directly, set `NIM_ASR_CAPTURE_SAMPLE_RATE` to a supported rate; captured mono PCM is resampled to the model's 16 kHz input rate.

### 3. Bind the Toggle Script to a Global Hotkey

Edit your keyboard shortcut settings (GNOME Settings → Keyboard → Keyboard Shortcuts, or your WM equivalent). Add a custom shortcut:

| Field | Value |
|---|---|
| Name | Toggle Dictation |
| Command | `/home/simt-wj/02_Tools/nim-asr/toggle_dictation.sh` |
| Shortcut | Choose a key — e.g. `Ctrl+Super+D` |

If the repository is moved, update `PROJECT_DIR` in `toggle_dictation.sh` and the keyboard shortcut command.

## Usage

| Action | Result |
|---|---|
| Press shortcut (1st time) | Starts Python and opens the microphone |
| Notification and system-tray microphone appear | Recording is ready — begin speaking |
| Press shortcut while recording | Stops capture and finalizes the transcript |
| Press shortcut while starting/finishing | Shows current state; does not interrupt the session |
| Notification "Inserted N characters" | Done |

The text appears in whatever window or terminal is focused when transcription finishes. The launcher uses `.dictation_state` to distinguish `starting`, `recording`, and `finishing`, and sends `SIGINT` only while actively recording.

While the microphone stream is open, a persistent microphone icon appears in the system tray with the tooltip **Microphone active — dictation is recording**. It disappears as soon as audio capture stops, before transcript finalization. This uses `yad` and requires a desktop tray/AppIndicator implementation.

## Recognition Defaults

The client currently uses these quality-oriented defaults from `DictationConfig`:

| Setting | Default | Purpose |
|---|---:|---|
| Audio chunk duration | 100 ms | Low-latency transport; chunks remain one continuous ASR stream |
| ASR mode | `stream` | Set `NIM_ASR_MODE=offline` to send the complete WAV after stopping |
| Automatic punctuation | Enabled | Adds punctuation and capitalization |
| Verbatim transcripts | Disabled | Enables written-form normalization such as spoken numbers to digits |
| Endpoint stop history | 800 ms | Avoids prematurely finalizing speech during short pauses |
| Interim results | Disabled | Partial text is never exposed or typed |
| Word boosting | Disabled | Avoids false positives from broad context biasing |

## Post-Processing

Raw ASR output is cleaned up via a configurable replacement dictionary — useful for technical terms, special characters, file extensions, and common dictation patterns. Edit `DictationConfig.replacements` in `dictation/config.py` to add your own mappings.

### Default replacements include:

| Say | Get |
|---|---|
| "dot py" | `.py` |
| "dot json" | `.json` |
| "star star" | `**` |
| "new line" | `\n` |
| "tab" | `\t` |
| "R O S two" | `ROS 2` |
| "Fast API" | `FastAPI` |
| "right arrow" | `->` |
| "fat arrow" | `=>` |

Replacements are sorted by key length (longest first) so `"dot py "` matches before `"dot "`.

### Word Boosting

Word boosting is disabled by default because broad or common boosted words can
create false positives. Add only uncommon terms that repeatedly fail without
boosting to `DictationConfig.boosted_words` in `dictation/config.py`:

```python
boosted_words = [
    "FastAPI",
    "PyTorch",
    # Add only terms verified against retained recordings.
]
```

Keep the initial boost score at `20.0`. Do not boost common words such as
`let`, `function`, or `variable`.

### Debugging Cut-Off Transcripts

Set `NIM_ASR_KEEP_AUDIO=true` in `.env` to keep each session's temporary WAV. The retained path is written to `dictation.log`:

```text
Kept recorded audio for debugging: /tmp/tmpabcdef.wav
```

Listen to that file to determine whether missing words were lost during capture or transcription. Leave this disabled during normal use because recordings can contain sensitive audio.

## Power / Battery Notes

On battery (GPU capped at ~30W), the model runs at roughly **1× real-time** — 5 seconds of speech takes ~5 seconds to process. Streaming overlaps this work with recording; offline mode performs it after stopping, so the full processing time is added to the finalization wait.

On AC power (full GPU wattage), RTF drops to ~0.3-0.5×. Streaming processing often finishes before you press stop; offline mode still waits until the recording ends before submitting the WAV.

## Logging

All sessions are logged to `dictation.log` with timestamps:

```
[2026-07-16 15:19:52] INFO     === dictation session start ===
[2026-07-16 15:19:52] INFO     Recording … (press the dictation shortcut again to stop)
[2026-07-16 15:20:09] INFO     Received SIGINT – stopping recording …
[2026-07-16 15:20:09] INFO     Recorded 14.7 s of audio (147 chunks, capture=44100 Hz, asr=16000 Hz)
[2026-07-16 15:20:09] INFO     Raw transcript (36 chars): What is natural language processing?
[2026-07-16 15:20:09] INFO     Inserted 36 characters via xdotool
```

## Riva and Audio Notes

- The deployed profile is `mode=all,vad=default,diarizer=disabled`, which exposes both streaming and offline models.
- Do not switch this Parakeet 1.1B CTC streaming profile to unsupported VAD/diarizer combinations without checking the NIM support matrix for the installed image.
- The USB microphone can capture at 44.1 kHz while the ASR model receives 16 kHz mono PCM.
- The 100 ms buffers are transport chunks, not independently transcribed sentences.
- Live audio is queued while the background gRPC connection starts, so ASR connection startup does not discard captured microphone chunks.
- In streaming mode, if concurrent ASR fails, the saved WAV is replayed through the streaming endpoint as a fallback.
- In offline mode, the saved WAV is sent in one request through the offline endpoint.

## Maintenance

### Check Server Status

```bash
docker compose ps
docker compose logs --tail=200
```

### Inspect Models Inside Container

```bash
docker exec -it parakeet-1-1b-ctc-en-us ls -R /data/models
```

### Restart the Server

```bash
docker compose restart
```

Deleting the NIM cache or model directory forces a large model download/rebuild. Back up or verify the configured paths before removing either directory.

## Architecture (for Developers)

See [AGENTS.md](AGENTS.md) for the full architecture, module reference, and extension plans.

The core logic lives in the `dictation/` package; `offline_dictation.py` is a thin session entry point:

| Module | Contents |
|---|---|
| `dictation/config.py` | `DictationConfig` dataclass + `.env` helpers |
| `dictation/audio.py` | `AudioCapture` + PCM resampler |
| `dictation/asr.py` | `ConcurrentTranscriber` + `OfflineTranscriber` + `StreamingTranscriber` (fallback) |
| `dictation/post.py` | `PostProcessor` (replacements + whitespace) |
| `dictation/insert.py` | `TextInserter` (xdotool backend) |
| `offline_dictation.py` | Session lifecycle, signal handling, logging |
| `mic_check.py` | Diagnostic: list devices + find USB mic index |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| First words are missing | Speaking before microphone readiness | Wait for the **Recording…** notification/tray indicator before speaking |
| First words are missing even after waiting | Capture startup or ASR speech-start detection | Enable `NIM_ASR_KEEP_AUDIO=true`, reproduce once, and inspect the WAV |
| No notification on hotkey | Wrong shortcut path or missing `notify-send` | Verify the shortcut and install `libnotify-bin` |
| No persistent recording indicator | Missing `yad` or desktop tray/AppIndicator support | Install `yad` and enable the desktop's AppIndicator extension; notifications and recording still work without it |
| "Recording…" but nothing appears after stop | Riva server unavailable | Run `docker compose ps` and inspect `docker compose logs --tail=200` |
| Text appears 10s+ after stop | GPU on battery (30W) | Plug in AC power for faster processing |
| Poor accuracy or false technical words | Word boosting is too broad | Disable boosts; add only verified uncommon terms at score `20.0` |
| Sentence is cut off | Capture or ASR endpointing issue | Retain the WAV and compare it with the raw transcript in `dictation.log` |
| ASR returns "Unavailable model" | Wrong NIM profile or model cache | Check `CONTAINER_ID`, `NIM_TAGS_SELECTOR`, and server logs |
| `xdotool` fails | Wrong X11 display / Wayland | Run `echo $DISPLAY`; use X11 session (Wayland support TBD) |
| No audio recorded | Wrong mic selected | Run `uv run mic_check.py` to find the correct device index, set `NIM_ASR_INPUT_DEVICE_INDEX` in `.env` |
