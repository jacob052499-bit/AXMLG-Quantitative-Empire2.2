"""Atomic progress/checkpoint writes tolerate short-lived Windows reader locks."""
import json
import time
from pathlib import Path


def atomic_json(destination, value):
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')), encoding='utf-8')
    for attempt in range(12):
        try:
            temporary.replace(destination)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(.025 * (attempt + 1))
