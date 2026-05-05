from __future__ import annotations

import logging
import subprocess


class TextInserter:
    def __init__(self, method: str = "xdotool") -> None:
        self.method = method

    def insert(self, text: str) -> None:
        if not text:
            return
        if self.method == "xdotool":
            self._insert_xdotool(text)
        else:
            raise ValueError(f"Unknown inserter: {self.method!r}")

    @staticmethod
    def _insert_xdotool(text: str) -> None:
        safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
        safe = safe.replace("$", "\\$")
        cmd = ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", safe]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logging.error(
                "xdotool failed (rc=%d): %s", result.returncode, result.stderr.strip()
            )
        else:
            logging.info("Inserted %d characters via xdotool", len(text))
