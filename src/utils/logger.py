from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_file_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gakumas_music_extractor")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        path = log_dir / f"app-{datetime.now():%Y%m%d}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger

