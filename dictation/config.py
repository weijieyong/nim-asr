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
    riva_server: str = "localhost:50051"
    language_code: str = "en-US"
    profanity_filter: bool = False
    automatic_punctuation: bool = True
    verbatim_transcripts: bool = False

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
    boost_score: float = 10.0

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
        if self.log_level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.log_level = self.log_level.upper()
        else:
            self.log_level = "INFO"
