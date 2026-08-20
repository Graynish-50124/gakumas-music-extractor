from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return project_root()


def configure_runtime() -> None:
    """Make bundled Python packages and media tools discoverable."""

    root = bundle_root()
    candidates = [root, root / "vendor", project_root() / "vendor"]
    for candidate in reversed(candidates):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    binary_dirs = [
        root / "ffmpeg" / "bin",
        root / "bin",
        project_root() / "vendor" / "ffmpeg" / "bin",
    ]
    existing = [str(path) for path in binary_dirs if path.is_dir()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
        ffmpeg = Path(existing[0]) / "ffmpeg.exe"
        ffprobe = Path(existing[0]) / "ffprobe.exe"
        if ffmpeg.exists():
            os.environ["FFMPEG_BINARY"] = str(ffmpeg)
        if ffprobe.exists():
            os.environ["FFPROBE_BINARY"] = str(ffprobe)


def component_paths() -> dict[str, Path | None]:
    root = bundle_root()
    gom_roots = [
        root / "GkmasObjectManager",
        root / "vendor" / "GkmasObjectManager",
        project_root() / "vendor" / "GkmasObjectManager",
    ]
    vgmstream = next(
        (
            path / "bin" / "vgmstream" / "vgmstream-win.exe"
            for path in gom_roots
            if (path / "bin" / "vgmstream" / "vgmstream-win.exe").exists()
        ),
        None,
    )
    ffmpeg_candidates = [
        root / "ffmpeg" / "bin" / "ffmpeg.exe",
        root / "bin" / "ffmpeg.exe",
        project_root() / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    ffmpeg = next((path for path in ffmpeg_candidates if path.exists()), None)
    return {"vgmstream": vgmstream, "ffmpeg": ffmpeg}

