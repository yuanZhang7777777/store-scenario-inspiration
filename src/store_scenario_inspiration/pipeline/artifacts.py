"""Reading and writing the JSON files one stage hands to the next.

Those files are the pipeline's contract, so both the stages and the web app go
through here rather than each rolling their own. A write goes to a temporary
file in the destination directory and is then renamed: a stage that dies
mid-write leaves the previous artifact intact instead of a half-written one.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile


def read_json(path: Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)
