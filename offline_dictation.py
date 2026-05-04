#!/usr/bin/env python3
"""
Toggle dictation for vibe coding.
Record → Stop → Transcribe → Post-process → Insert.

Designed to be run from a global hotkey (via toggle_dictation.sh):
  First press: start recording
  Second press (SIGINT): stop recording → transcribe → insert text

Audio is fed to Riva streaming ASR **during** recording in a background thread
so most of the GPU work completes before you press stop.  No interim/partial
results are ever typed.  Only the final concatenated transcript is inserted.

If the concurrent stream fails, the saved WAV file is replayed through
streaming ASR as a fallback — you never lose audio.

Dependencies: pyaudio, nvidia-riva-client, xdotool (system)

Modular structure (extract to separate files later):
  - AudioCapture       → dictation/audio.py
  - ConcurrentTranscriber → dictation/asr.py
  - PostProcessor      → dictation/post.py
  - TextInserter       → dictation/insert.py
  - Config             → dictation/config.py
"""

from __future__ import annotations

import collections.abc
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field

import pyaudio
import riva.client


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class DictationConfig:
    """Central configuration for a dictation session.

    Extract to dictation/config.py when modularizing.
    """

    # --- Audio recording ---
    sample_rate: int = 16000
    """Hz. 16 kHz mono PCM ― Riva standard."""

    channels: int = 1
    sample_width: int = 2  # 16-bit
    chunk_duration_ms: int = 100
    """Duration of each audio chunk read from the mic.
    This bounds the latency between pressing stop and the recording loop exiting."""

    # --- Riva / NIM ASR ---
    riva_server: str = "localhost:50051"
    language_code: str = "en-US"
    profanity_filter: bool = False
    automatic_punctuation: bool = True
    verbatim_transcripts: bool = False
    """False = inverse text normalisation (dates → "May 4th", numbers → "42", etc.)."""

    boosted_words: list[str] = field(
        default_factory=lambda: [
            # Programming languages & tools
            "Python",
            "JavaScript",
            "TypeScript",
            "Rust",
            "GoLang",
            "CUDA",
            "PyTorch",
            "TensorFlow",
            "FastAPI",
            "React",
            "Node",
            "Docker",
            "Kubernetes",
            "gRPC",
            "protobuf",
            "REST",
            "GraphQL",
            "GitHub",
            "Copilot",
            "OpenCV",
            "ROS",
            # Common coding terms
            "async",
            "await",
            "function",
            "variable",
            "const",
            "let",
            "kwargs",
            "args",
            "enum",
            "config",
            "middleware",
            "endpoint",
        ]
    )
    """Words to boost for better recognition of technical vocabulary."""

    boost_score: float = 10.0
    """Higher = stronger bias toward boosted words (typical range 4-20)."""

    # --- Text insertion ---
    inserter: str = "xdotool"
    """Method for text insertion: 'xdotool' (X11), or add 'ydotool' (Wayland) later."""

    # --- Logging ---
    log_file: str = "dictation.log"
    log_level: str = "INFO"

    # --- Post-processing replacements ---
    # Extend this dict to add your own term → symbol mappings.
    # Keys are matched as plain substrings (case-insensitive planning).
    # For regex-level control, extend PostProcessor.process().
    replacements: dict[str, str] = field(
        default_factory=lambda: {
            # --- File extensions (context-aware: space-padded to avoid mid-word hits) ---
            " dot py ": " .py ",
            " dot yaml ": " .yaml ",
            " dot yml ": " .yml ",
            " dot json ": " .json ",
            " dot toml ": " .toml ",
            " dot md ": " .md ",
            " dot txt ": " .txt ",
            " dot csv ": " .csv ",
            " dot env ": " .env ",
            " dot lock ": " .lock ",
            " dot log ": " .log ",
            " dot cfg ": " .cfg ",
            " dot ini ": " .ini ",
            " dot conf ": " .conf ",
            # Leading-edge variants (".py" at start of utterance)
            "dot py ": ".py ",
            "dot yaml ": ".yaml ",
            "dot json ": ".json ",
            # --- Technical terms the ASR often mangles ---
            " R O S ": " ROS ",
            " R O S 2 ": " ROS 2 ",
            " R O S two ": " ROS 2 ",
            " c u d a ": " CUDA ",
            " C U D A ": " CUDA ",
            "cuda underscore visible underscore devices": "CUDA_VISIBLE_DEVICES",
            "CUDA underscore visible underscore devices": "CUDA_VISIBLE_DEVICES",
            "c u d a visible devices": "CUDA visible devices",
            "Fast API": "FastAPI",
            "fast api": "FastAPI",
            "open CV": "OpenCV",
            "open cv": "OpenCV",
            "type script": "TypeScript",
            "Type script": "TypeScript",
            "Java script": "JavaScript",
            "java script": "JavaScript",
            "git hub": "GitHub",
            "git lab": "GitLab",
            "postgres SQL": "PostgreSQL",
            "my SQL": "MySQL",
            "sql lite": "SQLite",
            "no SQL": "NoSQL",
            # --- Special tokens ---
            " star star ": " ** ",
            "star star": "**",
            " dot dot dot ": " ... ",
            "dot dot dot": "...",
            " right arrow ": " -> ",
            " fat arrow ": " => ",
            " fatarrow ": " => ",
            " left paren ": " (",
            " right paren ": ") ",
            " left bracket ": " [",
            " right bracket ": "] ",
            " left brace ": " {",
            " right brace ": "} ",
            # --- Whitespace words ---
            " new line": "\n",
            " newline": "\n",
            " new paragraph": "\n\n",
            " tab": "\t",
        }
    )

    def __post_init__(self) -> None:
        if self.log_level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.log_level = self.log_level.upper()
        else:
            self.log_level = "INFO"


# ============================================================================
# Audio Capture
# ============================================================================


class AudioCapture:
    """Records microphone audio to a WAV file.

    Call ``stop()`` from a signal handler to end recording gracefully.
    The recording loop checks the stop flag after every chunk read, so
    the stop latency is bounded by ``chunk_duration_ms``.
    """

    def __init__(self, config: DictationConfig) -> None:
        self.config: DictationConfig = config
        self._stopped: bool = False

    @property
    def is_stopped(self) -> bool:
        """True after stop() was called."""
        return self._stopped

    def stop(self) -> None:
        """Signal the recording loop to finish after the current chunk."""
        self._stopped = True

    def record(
        self,
        output_path: str,
        chunk_callback: collections.abc.Callable[[bytes], None] | None = None,
    ) -> str:
        """Record until ``stop()`` is called.

        If *chunk_callback* is given, each audio chunk is forwarded to it
        immediately after being read — used to feed a
        :class:`ConcurrentTranscriber` during recording.
        """
        chunk = int(self.config.sample_rate * self.config.chunk_duration_ms / 1000)
        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=p.get_format_from_width(self.config.sample_width),
                channels=self.config.channels,
                rate=self.config.sample_rate,
                input=True,
                frames_per_buffer=chunk,
            )
        except OSError as exc:
            logging.error("Failed to open microphone: %s", exc)
            p.terminate()
            raise

        frames: list[bytes] = []
        self._stopped = False

        try:
            while not self._stopped:
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                    frames.append(data)
                    if chunk_callback is not None:
                        chunk_callback(data)
                except OSError as exc:
                    logging.warning("Audio read glitch (continuing): %s", exc)
                    continue
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            p.terminate()

        duration = len(frames) * self.config.chunk_duration_ms / 1000.0
        logging.info("Recorded %.1f s of audio (%d chunks)", duration, len(frames))

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(
                p.get_sample_size(p.get_format_from_width(self.config.sample_width))
            )
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(b"".join(frames))

        return output_path


# ============================================================================
# ASR Transcriber  (Riva streaming, collect-only)
# ============================================================================


class StreamingTranscriber:
    """Replay a recorded WAV file through Riva streaming ASR.

    The Riva model available only supports ``type=online`` (streaming).
    We replay the WAV through the streaming endpoint with
    ``interim_results=False``, collect all final utterance transcripts,
    and concatenate them.

    No partial results are typed.  The audio is sent as fast as the
    server can process it (no real-time pacing).
    """

    _CHUNK_N_FRAMES: int = 1600  # PCM chunk size (100 ms at 16 kHz).

    def __init__(self, config: DictationConfig) -> None:
        self.config: DictationConfig = config
        self._service: riva.client.ASRService | None = None

    def _connect(self) -> riva.client.ASRService:
        if self._service is None:
            auth = riva.client.Auth(uri=self.config.riva_server)
            self._service = riva.client.ASRService(auth)
        return self._service

    def transcribe(self, wav_path: str) -> str:
        """Send a WAV file through streaming ASR and return the transcript."""
        service = self._connect()

        inner_config = riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            language_code=self.config.language_code,
            max_alternatives=1,
            profanity_filter=self.config.profanity_filter,
            enable_automatic_punctuation=self.config.automatic_punctuation,
            verbatim_transcripts=self.config.verbatim_transcripts,
            sample_rate_hertz=self.config.sample_rate,
            audio_channel_count=self.config.channels,
        )

        if self.config.boosted_words:
            riva.client.add_word_boosting_to_config(
                inner_config,
                self.config.boosted_words,
                self.config.boost_score,
            )

        streaming_config = riva.client.StreamingRecognitionConfig(
            config=inner_config,
            interim_results=False,
        )

        # Read raw PCM from the recorded WAV file.
        with wave.open(wav_path, "rb") as wf:
            raw_pcm = wf.readframes(wf.getnframes())

        chunk_bytes = (
            self._CHUNK_N_FRAMES * self.config.sample_width * self.config.channels
        )

        def audio_chunks():
            for i in range(0, len(raw_pcm), chunk_bytes):
                yield raw_pcm[i : i + chunk_bytes]

        logging.info("Replaying %d PCM bytes through streaming ASR ...", len(raw_pcm))
        t0 = time.monotonic()

        parts: list[str] = []
        try:
            responses = service.streaming_response_generator(
                audio_chunks=audio_chunks(),
                streaming_config=streaming_config,
            )
            for response in responses:
                for result in response.results:
                    if result.is_final and result.alternatives:
                        parts.append(result.alternatives[0].transcript)
        except Exception as exc:
            logging.error("Streaming ASR failed: %s", exc)
            return ""

        elapsed = time.monotonic() - t0
        logging.info(
            "Streaming ASR finished in %.1f s (%d utterance(s))", elapsed, len(parts)
        )

        return " ".join(parts).strip()


# ============================================================================
# Concurrent Transcriber  (live mic → Riva streaming in background thread)
# ============================================================================


class ConcurrentTranscriber:
    """Feeds live mic audio to Riva streaming ASR in a background thread.

    Call ``start()`` before recording, ``feed(chunk)`` for each mic chunk,
    and ``stop()`` after recording ends to get the final transcript.

    Final utterances are collected as they arrive from the server, so by
    the time the user presses stop, most audio is already processed.
    Only the trailing edge needs server finalization, which typically
    takes 1–3 s instead of replaying the entire audio.
    """

    def __init__(self, config: DictationConfig) -> None:
        self.config: DictationConfig = config
        self._results: list[str] = []
        self._lock: threading.Lock = threading.Lock()
        self._chunks: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def start(self) -> None:
        """Launch the background ASR worker thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, chunk: bytes) -> None:
        """Forward a raw PCM chunk to the ASR stream (thread-safe)."""
        self._chunks.put(chunk)

    def stop(self) -> str:
        """Signal end-of-audio and wait for the transcript.

        Returns the concatenated final transcript, or raises on ASR failure.
        """
        self._chunks.put(None)  # sentinel — signals end of audio
        assert self._thread is not None  # must call start() before stop()
        self._thread.join(timeout=30)
        if self._error is not None:
            raise RuntimeError("concurrent ASR failed") from self._error
        with self._lock:
            return " ".join(self._results).strip()

    def _run(self) -> None:
        """Background thread: consume chunks, send to Riva, collect utterances."""
        try:
            auth = riva.client.Auth(uri=self.config.riva_server)
            service = riva.client.ASRService(auth)

            inner = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                language_code=self.config.language_code,
                max_alternatives=1,
                profanity_filter=self.config.profanity_filter,
                enable_automatic_punctuation=self.config.automatic_punctuation,
                verbatim_transcripts=self.config.verbatim_transcripts,
                sample_rate_hertz=self.config.sample_rate,
                audio_channel_count=self.config.channels,
            )

            if self.config.boosted_words:
                riva.client.add_word_boosting_to_config(
                    inner,
                    self.config.boosted_words,
                    self.config.boost_score,
                )

            streaming_config = riva.client.StreamingRecognitionConfig(
                config=inner,
                interim_results=False,
            )

            def chunk_gen():
                while True:
                    chunk = self._chunks.get()
                    if chunk is None:
                        break
                    yield chunk

            for response in service.streaming_response_generator(
                chunk_gen(),
                streaming_config,
            ):
                for result in response.results:
                    if result.is_final and result.alternatives:
                        with self._lock:
                            self._results.append(result.alternatives[0].transcript)

        except Exception as exc:
            self._error = exc
            logging.error("Concurrent ASR worker failed: %s", exc)


# ============================================================================
# Post-Processor
# ============================================================================


class PostProcessor:
    """Clean and transform a raw ASR transcript for coding/terminal use.

    Apply a set of customisable string replacements, then run optional
    normalisation passes.  Extend by adding entries to the *replacements*
    dict in :class:`DictationConfig` or by subclassing ``process()``.

    Future enhancements:
      - LLM-based cleanup
      - Command-mode vs natural-prompt-mode detection
      - Regex-based patterns
    """

    def __init__(self, config: DictationConfig) -> None:
        self.config: DictationConfig = config
        # Longer keys first, so "dot py " beats " dot py ".
        self._replacements: list[tuple[str, str]] = sorted(
            config.replacements.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )

    def process(self, text: str) -> str:
        """Return the cleaned transcript."""
        if not text:
            return text

        result = text

        # 1. Simple string replacements
        for old, new in self._replacements:
            result = result.replace(old, new)

        # 2. Collapse repeated whitespace (but preserve intentional \n, \t)
        result = re.sub(r"[ \t]+", lambda m: m.group(0)[0] * len(m.group(0)), result)
        # Replace 2+ spaces with single (unless preceded by \n for indentation)
        lines = result.split("\n")
        lines = [re.sub(r" {2,}", " ", line) for line in lines]
        result = "\n".join(lines)

        # 3. Strip leading/trailing whitespace per line
        result = "\n".join(line.strip() for line in result.split("\n"))

        return result.strip()


# ============================================================================
# Text Inserter
# ============================================================================


class TextInserter:
    """Insert final text into the currently focused text field / terminal.

    Swappable backend:
      - ``xdotool`` (X11) – current default
      - Add ``ydotool`` or ``wl-paste`` (Wayland) later
      - Or add ``pyperclip`` + simulated paste (``Ctrl+V``) for clipboard mode
    """

    def __init__(self, method: str = "xdotool") -> None:
        self.method: str = method

    def insert(self, text: str) -> None:
        """Type *text* into the active window."""
        if not text:
            return

        if self.method == "xdotool":
            self._insert_xdotool(text)
        else:
            raise ValueError(f"Unknown inserter: {self.method!r}")

    @staticmethod
    def _insert_xdotool(text: str) -> None:
        """Type via ``xdotool type``. Quotes and backslashes are escaped."""
        safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
        safe = safe.replace("$", "\\$")
        cmd = [
            "xdotool",
            "type",
            "--clearmodifiers",
            "--delay",
            "0",
            "--",
            safe,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logging.error(
                "xdotool failed (rc=%d): %s", result.returncode, result.stderr.strip()
            )
        else:
            logging.info("Inserted %d characters via xdotool", len(text))


# ============================================================================
# Logging
# ============================================================================


def _setup_logging(config: DictationConfig) -> None:
    """Configure logging to both file (with timestamps) and stderr."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.log_level))

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler
    fh = logging.FileHandler(config.log_file)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Stderr handler (for when running in terminal / debugging)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)


# ============================================================================
# Session lifecycle helpers
# ============================================================================


def _cleanup_temp(path: str) -> None:
    """Remove a temp file, logging any error."""
    try:
        os.unlink(path)
    except OSError as exc:
        logging.warning("Could not remove temp file %s: %s", path, exc)


def _notify(title: str, message: str, icon: str = "dialog-information") -> None:
    """Show a desktop notification via ``notify-send`` (best-effort)."""
    try:
        _ = subprocess.run(
            ["notify-send", title, message, f"--icon={icon}"],
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        logging.debug("notify-send failed: %s", exc)


# ============================================================================
# Main workflow
# ============================================================================


def run_session(config: DictationConfig) -> int:
    """Run one dictation session: record (concurrent ASR) → post-process → insert.

    Returns an exit code (0 = success).
    """

    # ── Prepare temp file for audio (always saved for fallback) ─────────
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    # ── Record + concurrent ASR ──────────────────────────────────────────
    capture = AudioCapture(config)
    transcriber = ConcurrentTranscriber(config)

    def _on_stop(signum: int, _frame: object) -> None:
        if not capture.is_stopped:
            sig_name = signal.Signals(signum).name
            logging.info("Received %s – stopping recording …", sig_name)
            capture.stop()

    _ = signal.signal(signal.SIGINT, _on_stop)
    _ = signal.signal(signal.SIGTERM, _on_stop)

    transcriber.start()
    logging.info("Recording … (press the dictation shortcut again to stop)")
    _notify("Dictation", "Recording …", "audio-input-microphone")

    try:
        _ = capture.record(tmp_path, chunk_callback=transcriber.feed)
    except (OSError, IOError) as exc:
        logging.error("Recording failed: %s", exc)
        _notify("Dictation", f"Recording failed: {exc}", "dialog-error")
        _cleanup_temp(tmp_path)
        return 1

    file_size = os.path.getsize(tmp_path)
    if file_size <= 44:  # WAV header only = empty recording
        logging.info("No audio captured – exiting.")
        _cleanup_temp(tmp_path)
        return 0

    logging.info("Saved %d bytes to %s", file_size, tmp_path)

    # ── Transcribe ───────────────────────────────────────────────────────
    # Restore default signal handlers in case anything blocks below.
    _ = signal.signal(signal.SIGINT, signal.SIG_DFL)
    _ = signal.signal(signal.SIGTERM, signal.SIG_DFL)

    # Primary path: collect results from the concurrent stream.
    transcript = ""
    try:
        transcript = transcriber.stop()
    except RuntimeError:
        logging.warning("Concurrent ASR failed — falling back to two-phase replay.")
        try:
            fallback = StreamingTranscriber(config)
            transcript = fallback.transcribe(tmp_path)
        except Exception as exc:
            logging.error("Fallback ASR also failed: %s", exc)

    _cleanup_temp(tmp_path)

    if not transcript:
        logging.info("Empty transcript – nothing to insert.")
        _notify("Dictation", "No speech detected", "dialog-information")
        return 0

    logging.info("Raw transcript (%d chars): %s", len(transcript), transcript)

    # ── Post-process ─────────────────────────────────────────────────────
    processor = PostProcessor(config)
    cleaned = processor.process(transcript)

    if cleaned != transcript:
        logging.info("Cleaned  (%d chars): %s", len(cleaned), cleaned)

    # ── Insert ───────────────────────────────────────────────────────────
    inserter = TextInserter(config.inserter)
    inserter.insert(cleaned)

    _notify(
        "Dictation",
        f"Inserted {len(cleaned)} characters",
        "accessories-text-editor",
    )
    return 0


def main() -> int:
    config = DictationConfig()
    _setup_logging(config)

    logging.info("=== dictation session start ===")
    try:
        return run_session(config)
    except Exception as exc:
        logging.exception("Unhandled error: %s", exc)
        _notify("Dictation", f"Error: {exc}", "dialog-error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
