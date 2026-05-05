from __future__ import annotations

import logging
import queue
import threading
import time
import wave

import riva.client

from .config import DictationConfig


def _build_recognition_config(config: DictationConfig) -> riva.client.RecognitionConfig:
    inner = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        language_code=config.language_code,
        max_alternatives=1,
        profanity_filter=config.profanity_filter,
        enable_automatic_punctuation=config.automatic_punctuation,
        verbatim_transcripts=config.verbatim_transcripts,
        sample_rate_hertz=config.sample_rate,
        audio_channel_count=config.channels,
    )
    if config.boosted_words:
        riva.client.add_word_boosting_to_config(
            inner, config.boosted_words, config.boost_score
        )
    return inner


class StreamingTranscriber:
    _CHUNK_N_FRAMES: int = 1600

    def __init__(self, config: DictationConfig) -> None:
        self.config = config
        self._service: riva.client.ASRService | None = None

    def _connect(self) -> riva.client.ASRService:
        if self._service is None:
            auth = riva.client.Auth(uri=self.config.riva_server)
            self._service = riva.client.ASRService(auth)
        return self._service

    def transcribe(self, wav_path: str) -> str:
        service = self._connect()
        streaming_config = riva.client.StreamingRecognitionConfig(
            config=_build_recognition_config(self.config),
            interim_results=False,
        )

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
            for response in service.streaming_response_generator(
                audio_chunks=audio_chunks(),
                streaming_config=streaming_config,
            ):
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


class ConcurrentTranscriber:
    def __init__(self, config: DictationConfig) -> None:
        self.config = config
        self._results: list[str] = []
        self._lock = threading.Lock()
        self._chunks: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, chunk: bytes) -> None:
        self._chunks.put(chunk)

    def stop(self) -> str:
        self._chunks.put(None)
        assert self._thread is not None
        self._thread.join(timeout=30)
        if self._error is not None:
            raise RuntimeError("concurrent ASR failed") from self._error
        with self._lock:
            return " ".join(self._results).strip()

    def _run(self) -> None:
        try:
            auth = riva.client.Auth(uri=self.config.riva_server)
            service = riva.client.ASRService(auth)
            streaming_config = riva.client.StreamingRecognitionConfig(
                config=_build_recognition_config(self.config),
                interim_results=False,
            )

            def chunk_gen():
                while True:
                    chunk = self._chunks.get()
                    if chunk is None:
                        break
                    yield chunk

            for response in service.streaming_response_generator(chunk_gen(), streaming_config):
                for result in response.results:
                    if result.is_final and result.alternatives:
                        with self._lock:
                            self._results.append(result.alternatives[0].transcript)

        except Exception as exc:
            self._error = exc
            logging.error("Concurrent ASR worker failed: %s", exc)
