from __future__ import annotations

import re

from .config import DictationConfig


class PostProcessor:
    def __init__(self, config: DictationConfig) -> None:
        self.config = config
        self._replacements: list[tuple[str, str]] = sorted(
            config.replacements.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )

    def process(self, text: str) -> str:
        if not text:
            return text

        result = text

        for old, new in self._replacements:
            result = result.replace(old, new)

        lines = result.split("\n")
        lines = [re.sub(r" {2,}", " ", line) for line in lines]
        result = "\n".join(lines)

        result = "\n".join(line.strip() for line in result.split("\n"))

        return result.strip()
