#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading

from dictation import (
    AudioCapture,
    ConcurrentTranscriber,
    DictationConfig,
    OfflineTranscriber,
    PostProcessor,
    StreamingTranscriber,
    TextInserter,
)

TRAY_PID_FILE = ".dictation_tray.pid"
STATE_FILE = ".dictation_state"
_recording_indicator_process: subprocess.Popen[bytes] | None = None


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


def _notify(
    title: str,
    message: str,
    icon: str = "dialog-information",
    *,
    wait: bool = True,
) -> None:
    def _send() -> None:
        try:
            subprocess.run(
                ["notify-send", title, message, f"--icon={icon}"],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logging.debug("notify-send failed: %s", exc)

    if wait:
        _send()
        return

    try:
        threading.Thread(target=_send, daemon=True).start()
    except RuntimeError as exc:
        logging.debug("notify-send thread failed: %s", exc)


def _show_recording_indicator() -> None:
    global _recording_indicator_process

    if shutil.which("yad") is None:
        return

    _clear_recording_indicator()
    try:
        process = subprocess.Popen(
            [
                "yad",
                "--notification",
                "--image=microphone-sensitivity-high-symbolic",
                "--text=Microphone active — dictation is recording",
                "--no-middle",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logging.debug("yad notification failed: %s", exc)
        return

    _recording_indicator_process = process

    try:
        with open(TRAY_PID_FILE, "w", encoding="utf-8") as pid_file:
            pid_file.write(f"pgid={process.pid}\n")
    except OSError as exc:
        logging.debug("Could not write tray pid file: %s", exc)


def _write_state(state: str) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as state_file:
            state_file.write(f"{state}\n")
    except OSError as exc:
        logging.debug("Could not write dictation state: %s", exc)


def _clear_state() -> None:
    try:
        os.unlink(STATE_FILE)
    except FileNotFoundError:
        return
    except OSError as exc:
        logging.debug("Could not remove dictation state: %s", exc)


def _clear_recording_indicator() -> None:
    global _recording_indicator_process

    try:
        with open(TRAY_PID_FILE, encoding="utf-8") as pid_file:
            target = pid_file.read().strip()
    except (FileNotFoundError, OSError):
        target = ""

    process = _recording_indicator_process
    if not target and process is not None:
        target = f"pgid={process.pid}"

    process_group = target.startswith("pgid=")
    try:
        pid = int(target.removeprefix("pgid="))
    except ValueError:
        pid = 0

    if pid > 0:
        try:
            if process_group:
                os.killpg(pid, signal.SIGTERM)
            else:
                # Compatibility with PID files created by older versions.
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            logging.debug("Could not stop recording indicator: %s", exc)

    if process is not None and process.pid == pid:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
        _recording_indicator_process = None

    try:
        os.unlink(TRAY_PID_FILE)
    except FileNotFoundError:
        return
    except OSError as exc:
        logging.debug("Could not remove tray pid file: %s", exc)


def run_session(config: DictationConfig) -> int:
    _write_state("starting")
    try:
        return _run_session(config)
    finally:
        _clear_recording_indicator()
        _clear_state()


def _run_session(config: DictationConfig) -> int:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    capture = AudioCapture(config)
    transcriber: ConcurrentTranscriber | None = None
    if config.asr_mode == "stream":
        transcriber = ConcurrentTranscriber(config)

    def _on_stop(signum: int, _frame: object) -> None:
        if not capture.is_stopped:
            logging.info(
                "Received %s – stopping recording …", signal.Signals(signum).name
            )
            capture.stop()

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    if transcriber is not None:
        transcriber.start()

    def _on_start() -> None:
        _write_state("recording")
        logging.info("Recording … (press the dictation shortcut again to stop)")
        _show_recording_indicator()
        _notify("Dictation", "Recording …", "audio-input-microphone", wait=False)

    try:
        capture.record(
            tmp_path,
            chunk_callback=transcriber.feed if transcriber is not None else None,
            on_start=_on_start,
        )
    except (OSError, IOError) as exc:
        logging.error("Recording failed: %s", exc)
        _notify("Dictation", f"Recording failed: {exc}", "dialog-error")
        _cleanup_temp(tmp_path)
        return 1

    _clear_recording_indicator()

    file_size = os.path.getsize(tmp_path)
    if file_size <= 44:
        logging.info("No audio captured – exiting.")
        _cleanup_temp(tmp_path)
        return 0

    logging.info("Saved %d bytes to %s", file_size, tmp_path)
    _write_state("finishing")

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    transcript = ""
    if config.asr_mode == "offline":
        try:
            transcript = OfflineTranscriber(config).transcribe(tmp_path)
        except Exception as exc:
            logging.warning(
                "Offline ASR failed — falling back to streaming replay: %s", exc
            )
            try:
                transcript = StreamingTranscriber(config).transcribe(tmp_path)
            except Exception as fallback_exc:
                logging.error("Streaming fallback also failed: %s", fallback_exc)
    else:
        assert transcriber is not None
        try:
            transcript = transcriber.stop()
        except RuntimeError:
            logging.warning("Concurrent ASR failed — falling back to two-phase replay.")
            try:
                transcript = StreamingTranscriber(config).transcribe(tmp_path)
            except Exception as exc:
                logging.error("Fallback ASR also failed: %s", exc)

    if config.keep_audio:
        logging.info("Kept recorded audio for debugging: %s", tmp_path)
    else:
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
    _notify(
        "Dictation", f"Inserted {len(cleaned)} characters", "accessories-text-editor"
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
