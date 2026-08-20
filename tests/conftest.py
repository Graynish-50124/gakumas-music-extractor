from __future__ import annotations

import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
VENDOR = PROJECT / "vendor"
for path in (SRC, VENDOR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app_bootstrap import configure_runtime


configure_runtime()

