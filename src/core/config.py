from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import DEFAULT_FILTER_TYPES, FILENAME_TITLE_CHARACTER, MUSIC_KINDS, SongMetadata
from .song_metadata import merge_song_metadata, metadata_key, parse_metadata_document


APP_NAME = "GakumasMusicExtractor"


def bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def app_data_dir() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return root / APP_NAME


def default_music_dir() -> Path:
    music = Path.home() / "Music"
    return music / "GakumasExtracted"


@dataclass
class AppSettings:
    game_data_dir: str = ""
    manifest_path: str = ""
    output_dir: str = ""
    default_wav: bool = True
    default_flac: bool = False
    default_normalize_loudness: bool = True
    default_awb: bool = True
    default_mp3: bool = False
    default_acb: bool = False
    default_artwork: bool = True
    default_artwork_file: bool = False
    default_official_metadata: bool = True
    filename_format: str = FILENAME_TITLE_CHARACTER
    filter_types: list[str] = field(default_factory=lambda: list(DEFAULT_FILTER_TYPES))
    auto_scan: bool = True
    online_fallback: bool = True
    manifest_mode: str = "local_preferred"
    theme: str = "system"

    def __post_init__(self) -> None:
        if not isinstance(self.filter_types, list):
            self.filter_types = list(DEFAULT_FILTER_TYPES)
        else:
            selected = set(self.filter_types)
            self.filter_types = [kind for kind in MUSIC_KINDS if kind in selected]

    @property
    def resolved_output_dir(self) -> Path:
        return Path(self.output_dir).expanduser() if self.output_dir else default_music_dir()


class ConfigStore:
    def __init__(self, root: Path | None = None):
        self.root = root or app_data_dir()
        self.settings_path = self.root / "settings.json"

    def load_settings(self) -> AppSettings:
        if not self.settings_path.exists():
            return AppSettings(output_dir=str(default_music_dir()))
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            allowed = AppSettings.__dataclass_fields__.keys()
            return AppSettings(**{key: raw[key] for key in allowed if key in raw})
        except (OSError, ValueError, TypeError):
            return AppSettings(output_dir=str(default_music_dir()))

    def save_settings(self, settings: AppSettings) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def ensure_mapping_files(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        defaults = bundled_root() / "config"
        for name in ("characters.json", "song_names.json"):
            target = self.root / name
            source = defaults / name
            if not target.exists() and source.exists():
                shutil.copy2(source, target)
        metadata_overrides = self.root / "song_metadata.json"
        if not metadata_overrides.exists():
            metadata_overrides.write_text("{}\n", encoding="utf-8")

    def load_mapping(self, name: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in (bundled_root() / "config" / name, self.root / name):
            if not path.exists():
                continue
            try:
                value: Any = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    result.update(
                        {str(key): str(item) for key, item in value.items() if item}
                    )
            except (OSError, ValueError, TypeError):
                continue
        return result

    def load_song_metadata(self) -> dict[str, SongMetadata]:
        result: dict[str, SongMetadata] = {}
        for path in (
            bundled_root() / "config" / "official_song_metadata.json",
            self.root / "song_metadata.json",
        ):
            if not path.exists():
                continue
            try:
                document: Any = json.loads(path.read_text(encoding="utf-8"))
                for key, metadata in parse_metadata_document(document).items():
                    result[key] = merge_song_metadata(
                        result.get(key, SongMetadata()),
                        metadata,
                    )
            except (OSError, ValueError, TypeError):
                continue
        # Also index the catalog by stable game IDs from the bundled master.
        # This keeps credits attached when an older user song-name override uses
        # slightly different punctuation or spelling for the same music asset.
        official_names_path = bundled_root() / "config" / "song_names.json"
        try:
            official_names: Any = json.loads(official_names_path.read_text(encoding="utf-8"))
            if isinstance(official_names, dict):
                for game_id, title in official_names.items():
                    metadata = result.get(metadata_key(str(title)))
                    if metadata is not None:
                        result.setdefault(metadata_key(str(game_id)), metadata)
        except (OSError, ValueError, TypeError):
            pass
        return result
