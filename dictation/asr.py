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


def _build_streaming_config(
    config: DictationConfig,
) -> riva.client.StreamingRecognitionConfig:
    streaming = riva.client.StreamingRecognitionConfig(
        config=_build_recognition_config(config),
        interim_results=False,
    )
    riva.client.add_endpoint_parameters_to_config(
        streaming,
        start_history=0,
        start_threshold=0.0,
        stop_history=config.endpointing_stop_history_ms,
        stop_history_eou=0,
        stop_threshold=0.0,
        stop_threshold_eou=0.0,
    )
    return streaming


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
        streaming_config = _build_streaming_config(self.config)

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


class OfflineTranscriber:
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
        with wave.open(wav_path, "rb") as wf:
            raw_pcm = wf.readframes(wf.getnframes())

        logging.info("Sending %d PCM bytes to offline ASR ...", len(raw_pcm))
        t0 = time.monotonic()
        response = service.offline_recognize(
            audio_bytes=raw_pcm,
            config=_build_recognition_config(self.config),
        )
        parts = [
            result.alternatives[0].transcript
            for result in response.results
            if result.alternatives
        ]
        elapsed = time.monotonic() - t0
        logging.info("Offline ASR finished in %.1f s", elapsed)
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
        if self._thread.is_alive():
            raise RuntimeError("concurrent ASR timed out before final transcript")
        if self._error is not None:
            raise RuntimeError("concurrent ASR failed") from self._error
        with self._lock:
            return " ".join(self._results).strip()

    def _run(self) -> None:
        try:
            auth = riva.client.Auth(uri=self.config.riva_server)
            service = riva.client.ASRService(auth)
            streaming_config = _build_streaming_config(self.config)

            def chunk_gen():
                while True:
                    chunk = self._chunks.get()
                    if chunk is None:
                        break
                    yield chunk

            for response in service.streaming_response_generator(
                chunk_gen(), streaming_config
            ):
                for result in response.results:
                    if result.is_final and result.alternatives:
                        with self._lock:
                            self._results.append(result.alternatives[0].transcript)

        except Exception as exc:
            self._error = exc
            logging.error("Concurrent ASR worker failed: %s", exc)
