from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field


def _read_dotenv_value(name: str, path: str = ".env") -> str | None:
    try:
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip("\"'")
    except FileNotFoundError:
        return None
    return None


def _getenv_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None:
        value = _read_dotenv_value(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        logging.warning("Ignoring invalid integer for %s: %r", name, value)
        return default


def _getenv_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = _read_dotenv_value(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _getenv_bool(name: str, default: bool = False) -> bool:
    value = _getenv_str(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    logging.warning("Ignoring invalid boolean for %s: %r", name, value)
    return default


def _default_riva_server() -> str:
    server = _getenv_str("NIM_ASR_RIVA_SERVER")
    if server is not None:
        return server
    grpc_port = _getenv_int("NIM_GRPC_API_PORT", 50051)
    return f"localhost:{grpc_port}"


@dataclass
class DictationConfig:
    # --- Audio recording ---
    sample_rate: int = 16000
    capture_sample_rate: int | None = field(
        default_factory=lambda: _getenv_int("NIM_ASR_CAPTURE_SAMPLE_RATE")
    )
    channels: int = 1
    sample_width: int = 2  # 16-bit
    input_device_index: int | None = field(
        default_factory=lambda: _getenv_int("NIM_ASR_INPUT_DEVICE_INDEX")
    )
    chunk_duration_ms: int = 100

    # --- Riva / NIM ASR ---
    riva_server: str = field(default_factory=_default_riva_server)
    language_code: str = "en-US"
    profanity_filter: bool = False
    automatic_punctuation: bool = True
    verbatim_transcripts: bool = False
    endpointing_stop_history_ms: int = 800
    asr_mode: str = field(
        default_factory=lambda: _getenv_str("NIM_ASR_MODE", "stream") or "stream"
    )

    # Keep this small and limited to terms that repeatedly fail without boosting.
    # Broad/common words create false positives in otherwise normal dictation.
    boosted_words: list[str] = field(default_factory=list)
    boost_score: float = 20.0  # Range 20-100 per NVIDIA docs; higher = stronger bias

    # Debugging: retain the temporary WAV when explicitly requested.
    keep_audio: bool = field(
        default_factory=lambda: _getenv_bool("NIM_ASR_KEEP_AUDIO")
    )

    # --- Text insertion ---
    inserter: str = "xdotool"

    # --- Logging ---
    log_file: str = "dictation.log"
    log_level: str = "INFO"

    # --- Post-processing replacements ---
    replacements: dict[str, str] = field(
        default_factory=lambda: {
            # --- File extensions ---
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
            # Leading-edge variants
            "dot py ": ".py ",
            "dot yaml ": ".yaml ",
            "dot json ": ".json ",
            # --- Technical terms ---
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
        if self.capture_sample_rate is None:
            self.capture_sample_rate = self.sample_rate
        self.asr_mode = self.asr_mode.strip().lower()
        if self.asr_mode not in {"stream", "offline"}:
            logging.warning(
                "Ignoring invalid NIM_ASR_MODE=%r; using stream", self.asr_mode
            )
            self.asr_mode = "stream"
        if self.log_level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.log_level = self.log_level.upper()
        else:
            self.log_level = "INFO"
