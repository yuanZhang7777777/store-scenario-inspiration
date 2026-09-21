"""On-disk layout for one uploaded store.

The app writes the same directory shape the command-line batch already reads, so
both drive one set of files rather than two parallel worlds. A store directory
is self-describing: whatever stages have run are simply the artifacts present.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ..pipeline.business import normalize_metrics
from ..pipeline import artifacts
from ..pipeline.artifacts import write_json

from store_scenario_inspiration.reliability import json_digest


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

    def create(self, store_name: str, country: str, uploads: list[tuple[str, object]], *, business_metrics: dict | None = None) -> dict:
        """Write one store's screenshots and remember where they came from.

        Uploads arrive as ``(filename, file object)`` and are copied in chunks so
        a batch of screenshots never exists twice in memory.
        """
        if not store_name.strip():
            raise ValueError("store_name is required")
        if not uploads:
            raise ValueError("at least one screenshot is required")

        metrics = normalize_metrics(business_metrics)
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
                 "images": kept, "business_metrics": metrics, **metrics}
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
        """Which artifacts exist. Order matches the pipeline, not the alphabet.

        Rerank's verdicts live inside the retrieval payload, so its marker file
        is removed whenever the recall is recomputed: verdicts about a list that
        no longer exists are worse than none.

        The products stage is a directory rather than a file, and what makes it
        count as done is the manifest agreeing with the scenes and the filtered
        source it was built from — a half-written batch is not a finished stage.
        """
        base = self.dir(store_id)
        products_ready = (base / 'products').is_dir()
        manifest = base / 'products' / 'manifest.json'
        if manifest.is_file():
            try:
                data = read_json(manifest)
                expected = json_digest({'scenes': read_json(base / 'deepseek_scenes.json'),
                                        'source': read_json(base / 'analysis_input.json')})
                products_ready = data.get('status') == 'ready' and data.get('source_digest') == expected
            except (ValueError, TypeError, KeyError, OSError):
                products_ready = False
        return {
            'uploaded': (base / 'store.json').is_file(),
            'recognized': (base / 'sample_store.json').is_file(),
            'clues': (base / 'clues.json').is_file(),
            'scenes': (base / 'deepseek_scenes.json').is_file(),
            'products': products_ready,
            # True here means an assembled result is readable, not that the optional
            # prose stage succeeded. Partial results explicitly label their status.
            'synthesis': (base / 'deepseek_analysis.json').is_file(),
            'expansions': (base / 'expansions.json').is_file(),
            'retrieval': (base / 'retrieval.json').is_file(),
            'rerank': (base / 'rerank.json').is_file(),
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
    value = artifacts.read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value
