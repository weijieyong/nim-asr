#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import tempfile

from dictation import (
    AudioCapture,
    ConcurrentTranscriber,
    DictationConfig,
    PostProcessor,
    StreamingTranscriber,
    TextInserter,
)


def _setup_logging(config: DictationConfig) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.log_level))
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(config.log_file)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def _cleanup_temp(path: str) -> None:
    try:
        os.unlink(path)
    except OSError as exc:
        logging.warning("Could not remove temp file %s: %s", path, exc)


def _notify(title: str, message: str, icon: str = "dialog-information") -> None:
    try:
        subprocess.run(
            ["notify-send", title, message, f"--icon={icon}"],
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        logging.debug("notify-send failed: %s", exc)


def run_session(config: DictationConfig) -> int:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    capture = AudioCapture(config)
    transcriber = ConcurrentTranscriber(config)

    def _on_stop(signum: int, _frame: object) -> None:
        if not capture.is_stopped:
            logging.info("Received %s – stopping recording …", signal.Signals(signum).name)
            capture.stop()

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    transcriber.start()
    logging.info("Recording … (press the dictation shortcut again to stop)")
    _notify("Dictation", "Recording …", "audio-input-microphone")

    try:
        capture.record(tmp_path, chunk_callback=transcriber.feed)
    except (OSError, IOError) as exc:
        logging.error("Recording failed: %s", exc)
        _notify("Dictation", f"Recording failed: {exc}", "dialog-error")
        _cleanup_temp(tmp_path)
        return 1

    file_size = os.path.getsize(tmp_path)
    if file_size <= 44:
        logging.info("No audio captured – exiting.")
        _cleanup_temp(tmp_path)
        return 0

    logging.info("Saved %d bytes to %s", file_size, tmp_path)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    transcript = ""
    try:
        transcript = transcriber.stop()
    except RuntimeError:
        logging.warning("Concurrent ASR failed — falling back to two-phase replay.")
        try:
            transcript = StreamingTranscriber(config).transcribe(tmp_path)
        except Exception as exc:
            logging.error("Fallback ASR also failed: %s", exc)

    _cleanup_temp(tmp_path)

    if not transcript:
        logging.info("Empty transcript – nothing to insert.")
        _notify("Dictation", "No speech detected", "dialog-information")
        return 0

    logging.info("Raw transcript (%d chars): %s", len(transcript), transcript)

    cleaned = PostProcessor(config).process(transcript)
    if cleaned != transcript:
        logging.info("Cleaned  (%d chars): %s", len(cleaned), cleaned)

    TextInserter(config.inserter).insert(cleaned)
    _notify("Dictation", f"Inserted {len(cleaned)} characters", "accessories-text-editor")
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
