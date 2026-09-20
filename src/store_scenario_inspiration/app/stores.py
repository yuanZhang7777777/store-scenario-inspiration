"""On-disk layout for one uploaded store.

The app writes the same directory shape the command-line batch already reads, so
both drive one set of files rather than two parallel worlds. A store directory
is self-describing: whatever stages have run are simply the artifacts present.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from direction import write_json


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NON_SLUG = re.compile(r"[^a-z0-9]+")
CHUNK = 1 << 20


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def stores_dir(self) -> Path:
        return self.root / "stores"

    def dir(self, store_id: str) -> Path:
        if not ID_PATTERN.fullmatch(store_id):
            raise ValueError(f"invalid store id: {store_id!r}")
        return self.stores_dir / store_id

    def path(self, store_id: str, name: str) -> Path:
        return self.dir(store_id) / name

    def images_dir(self, store_id: str) -> Path:
        return self.dir(store_id) / "images"

    def entry(self, store_id: str) -> dict:
        return read_json(self.path(store_id, "store.json"))

    def create(self, store_name: str, country: str, uploads: list[tuple[str, object]]) -> dict:
        """Write one store's screenshots and remember where they came from.

        Uploads arrive as ``(filename, file object)`` and are copied in chunks so
        a batch of screenshots never exists twice in memory.
        """
        if not store_name.strip():
            raise ValueError("store_name is required")
        if not uploads:
            raise ValueError("at least one screenshot is required")

        store_id = self._new_id(store_name)
        images = self.images_dir(store_id)
        images.mkdir(parents=True)
        kept = []
        taken: set[str] = set()
        for filename, handle in uploads:
            name = safe_filename(filename, taken)
            taken.add(name)
            kept.append(save_image(handle, images / name))
        entry = {"store_name": store_name.strip(), "country": country.strip().upper(),
                 "images": kept}
        write_json(self.path(store_id, "store.json"), entry)
        return {"id": store_id, **entry}

    def listing(self) -> list[dict]:
        if not self.stores_dir.is_dir():
            return []
        found = []
        for candidate in sorted(self.stores_dir.iterdir()):
            if not (candidate / "store.json").is_file():
                continue
            found.append({"id": candidate.name, "stages": self.stages(candidate.name),
                          **read_json(candidate / "store.json")})
        return found

    def stages(self, store_id: str) -> dict:
        base = self.dir(store_id)
        return {
            "uploaded": (base / "store.json").is_file(),
            "recognized": (base / "sample_store.json").is_file(),
            "direction": (base / "direction.json").is_file(),
            "scenes": (base / "deepseek_analysis.json").is_file(),
        }

    def _new_id(self, store_name: str) -> str:
        base = NON_SLUG.sub("-", store_name.strip().lower()).strip("-") or "store"
        candidate, suffix = base, 2
        while (self.stores_dir / candidate).exists():
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        return candidate


def save_image(handle, target: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as out:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return {"filename": target.name, "local_path": str(target),
            "sha256": digest.hexdigest(), "bytes": size}


def safe_filename(filename: str, taken: set[str]) -> str:
    """Keep the uploaded name where possible, since the vision pass echoes it back."""
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        name = "screenshot.png"
    if name not in taken:
        return name
    stem, suffix = Path(name).stem, Path(name).suffix or ".png"
    index = 2
    while f"{stem}-{index}{suffix}" in taken:
        index += 1
    return f"{stem}-{index}{suffix}"


def read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value
