"""Web application layer: upload screenshots, run the pipeline, show scenes.

The pipeline stages still live in ``scripts/`` as importable modules that the
command line drives, so this package puts that directory on the import path and
calls the same functions the CLI does. Extracting them into ``src/`` is worth
doing, but only once the two callers have stopped changing.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
