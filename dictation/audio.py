from __future__ import annotations

import array
import collections.abc
import contextlib
import logging
import os
import sys
import wave

import pyaudio

from .config import DictationConfig


@contextlib.contextmanager
def ignore_stderr() -> collections.abc.Iterator[None]:
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    sys.stderr.flush()
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)


def _resample_pcm16_mono(data: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate or not data:
        return data

    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()

    in_count = len(samples)
    out_count = max(1, round(in_count * to_rate / from_rate))
    ratio = from_rate / to_rate
    resampled = array.array("h")

    for out_index in range(out_count):
        source = out_index * ratio
        left = int(source)
        right = min(left + 1, in_count - 1)
        fraction = source - left
        value = round(samples[left] + (samples[right] - samples[left]) * fraction)
        resampled.append(value)

    if sys.byteorder != "little":
        resampled.byteswap()
    return resampled.tobytes()


class AudioCapture:
    def __init__(self, config: DictationConfig) -> None:
        self.config = config
        self._stopped = False

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    def stop(self) -> None:
        self._stopped = True

    def record(
        self,
        output_path: str,
        chunk_callback: collections.abc.Callable[[bytes], None] | None = None,
        on_start: collections.abc.Callable[[], None] | None = None,
    ) -> str:
        capture_rate = self.config.capture_sample_rate or self.config.sample_rate
        chunk = int(capture_rate * self.config.chunk_duration_ms / 1000)
        with ignore_stderr():
            p = pyaudio.PyAudio()
        try:
            with ignore_stderr():
                stream = p.open(
                    format=p.get_format_from_width(self.config.sample_width),
                    channels=self.config.channels,
                    rate=capture_rate,
                    input=True,
                    input_device_index=self.config.input_device_index,
                    frames_per_buffer=chunk,
                )
        except OSError as exc:
            logging.error("Failed to open microphone: %s", exc)
            p.terminate()
            raise

        if on_start is not None:
            on_start()

        frames: list[bytes] = []
        self._stopped = False

        try:
            while not self._stopped:
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                    data = _resample_pcm16_mono(
                        data,
                        from_rate=capture_rate,
                        to_rate=self.config.sample_rate,
                    )
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
        logging.info(
            "Recorded %.1f s of audio (%d chunks, capture=%d Hz, asr=%d Hz)",
            duration,
            len(frames),
            capture_rate,
            self.config.sample_rate,
        )

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(
                p.get_sample_size(p.get_format_from_width(self.config.sample_width))
            )
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(b"".join(frames))

        return output_path
