"""On-disk layout for one uploaded store.

The app writes the same directory shape the command-line batch already reads, so
both drive one set of files rather than two parallel worlds. A store directory
is self-describing: whatever stages have run are simply the artifacts present.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from ..pipeline import artifacts
from ..pipeline.artifacts import write_json
from ..pipeline.recognize import unread_images

from store_scenario_inspiration.reliability import json_digest


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NON_SLUG = re.compile(r"[^a-z0-9]+")
CHUNK = 1 << 20

RUN_STATE_FILE = "run_state.json"
SCHEMA_RUN_STATE = "store-run-state-v1"

# Which setting moves which step. Changing one moves no file, so without this a
# saved setting that had not been applied yet would look exactly like one that
# had, and the only way to tell would be to remember.
STAGE_SETTINGS = {
    "synthesis": ("temperature",),
    "scenes": ("scene_count", "temperature"),
    "products": ("products_per_scene", "temperature"),
    "expand": ("expansion_terms", "temperature"),
    "retrieval": ("recall_limit", "stock_filter"),
    "rerank": ("rerank_provider",),
}

# name, the flag in stages() that says it finished, what it reads, what it writes.
# Each step reads the one before it, so once a step is behind, everything after
# it is behind as well: a scene list built from product roles that are about to
# change is not a current scene list either.
STAGE_CHAIN = (
    ("synthesis", "synthesis", "analysis_input.json", "deepseek_conclusion.json"),
    ("scenes", "scenes", "analysis_input.json", "deepseek_scenes.json"),
    ("products", "products", "deepseek_scenes.json", "products/manifest.json"),
    ("expand", "expansions", "products/manifest.json", "expansions.json"),
    ("retrieval", "retrieval", "expansions.json", "retrieval.json"),
    ("rerank", "rerank", "retrieval.json", "rerank.json"),
)


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
        a batch of screenshots never exists twice in memory. They are optional:
        an operator who knows what the shop sells can type the products instead,
        and gets the same reading from them. What a store cannot be is empty, and
        that is judged where the products are known, not here.
        """
        if not store_name.strip():
            raise ValueError("store_name is required")

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

    def add_images(self, store_id: str, uploads: list[tuple[str, object]]) -> list[dict]:
        """Append screenshots to a store, keeping the ones already there.

        A store is read once and then worked on for a while; a picture that
        should have been in the first upload is the ordinary case, not a reason
        to start over.
        """
        if not uploads:
            raise ValueError("at least one screenshot is required")
        entry = self.entry(store_id)
        taken = {image["filename"] for image in entry.get("images") or []}
        images = self.images_dir(store_id)
        images.mkdir(parents=True, exist_ok=True)
        added = []
        for filename, handle in uploads:
            name = safe_filename(filename, taken)
            taken.add(name)
            added.append(save_image(handle, images / name))
        entry["images"] = [*(entry.get("images") or []), *added]
        write_json(self.path(store_id, "store.json"), entry)
        return added

    def remove_image(self, store_id: str, filename: str) -> dict:
        """Drop one screenshot, and the file behind it.

        The last one may go as well: a store read from typed products alone is a
        store, and the screenshots were only ever one way to describe one.
        """
        entry = self.entry(store_id)
        current = entry.get("images") or []
        kept = [image for image in current if image["filename"] != Path(filename).name]
        if len(kept) == len(current):
            raise ValueError(f"没有这张截图：{filename}")
        (self.images_dir(store_id) / Path(filename).name).unlink(missing_ok=True)
        entry["images"] = kept
        write_json(self.path(store_id, "store.json"), entry)
        return entry

    def outdated(self, store_id: str, params: dict) -> list[str]:
        """Every step that no longer describes what is on disk, in run order.

        A step is behind in one of two ways. Its input has been written since it
        ran — the operator excluded a product, added one by hand, or added a
        screenshot. Or a setting it reads has been changed since it ran, which
        moves no file at all and is why the values each step ran with are kept
        beside it.

        A step the app never ran has no recorded values and is judged by the
        files alone: the command line writes the same artifacts without writing
        down what they were run with, and reporting a store as needing a paid
        rerun on the strength of a guess is worse than saying nothing.
        """
        base = self.dir(store_id)
        staged = self.stages(store_id)
        ran = stage_runs(base)
        behind: list[str] = []
        # The reading comes before the chain: a screenshot that no vision pass
        # has covered makes everything downstream describe a store that is not
        # the one on disk. A store with no screenshots has nothing to cover, and
        # the steps below are judged by their files alone.
        downstream = True
        try:
            entry = self.entry(store_id)
            if not entry.get("images"):
                # No screenshots: the reading is still a step, and it is behind
                # until it has been taken. Nothing on disk can put it behind
                # again afterwards, because there is nothing left to read.
                downstream = not (base / "sample_store.json").is_file()
            else:
                receipt_path = base / "deepseek_vision.json"
                receipt = read_json(receipt_path) if receipt_path.is_file() else None
                downstream = receipt is None or bool(unread_images(entry, receipt))
        except (ValueError, TypeError, OSError):
            downstream = True
        if downstream:
            behind.append("recognize")
        if downstream or not staged["clues"] or _older_than(base / "clues.json", base / "sample_store.json"):
            behind.append("clues")
            downstream = True
        for name, flag, source, produces in STAGE_CHAIN:
            stale = downstream or not staged[flag]
            if not stale:
                stale = _older_than(base / produces, base / source)
            if not stale:
                recorded = (ran.get(name) or {}).get("settings")
                if isinstance(recorded, dict):
                    stale = any(recorded.get(key) != params.get(key) for key in STAGE_SETTINGS[name])
            if stale:
                downstream = True
                behind.append(name)
        return behind

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
            # The store reading, which the scenes are written from. The assembled
            # document is not the marker: it also appears, labelled partial, for a
            # store that never got one.
            'synthesis': (base / 'deepseek_conclusion.json').is_file(),
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


def _older_than(artifact: Path, source: Path) -> bool:
    """Whether ``artifact`` was written before ``source`` last was.

    A missing artifact is not this function's business — the caller has already
    asked whether the step finished at all.
    """
    try:
        return artifact.stat().st_mtime < source.stat().st_mtime
    except OSError:
        return False


def stage_runs(base: Path) -> dict:
    """What each step was last run with, as the app wrote it down.

    Empty for a store the command line produced, which is read as "no opinion"
    rather than "nothing recorded here is current".
    """
    path = base / RUN_STATE_FILE
    if not path.is_file():
        return {}
    try:
        value = read_json(path)
    except (ValueError, TypeError, OSError):
        return {}
    runs = value.get("stages") if value.get("schema") == SCHEMA_RUN_STATE else None
    return runs if isinstance(runs, dict) else {}


def record_stage_run(base: Path, stage: str, settings: dict) -> None:
    """Note that ``stage`` just ran, and with which of its settings."""
    value = {"schema": SCHEMA_RUN_STATE, "stages": stage_runs(base)}
    value["stages"][stage] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "settings": settings}
    write_json(base / RUN_STATE_FILE, value)


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
