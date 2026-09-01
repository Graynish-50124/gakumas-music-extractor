from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


KIND_GENERAL = "通常楽曲"
KIND_CHARACTER = "キャラクター専用曲"
KIND_UNIT = "ユニット曲"
KIND_LIVE = "ライブ"
KIND_BGM = "BGM"
MUSIC_KINDS = (
    KIND_GENERAL,
    KIND_CHARACTER,
    KIND_UNIT,
    KIND_LIVE,
    KIND_BGM,
)
DEFAULT_FILTER_TYPES = (KIND_GENERAL, KIND_CHARACTER)

SINGING_VOCAL = "歌入り"
SINGING_INST = "インスト"
SINGING_UNKNOWN = "対象外"

FILENAME_TITLE_CHARACTER = "title_character"
FILENAME_ORIGINAL = "original"
FILENAME_TITLE = "title"
VALID_FILENAME_FORMATS = {
    FILENAME_TITLE_CHARACTER,
    FILENAME_ORIGINAL,
    FILENAME_TITLE,
}


@dataclass(frozen=True)
class ManifestCandidate:
    path: Path
    modified_at: datetime
    size: int


@dataclass(frozen=True)
class ManifestInfo:
    source: str
    revision: int
    updated_at: datetime | None
    manifest_path: Path | None
    octo_root: Path | None
    object_count: int


@dataclass(frozen=True)
class AssetRef:
    name: str
    object_id: int
    object_name: str
    size: int
    md5: str
    url: str
    object_type: str
    source_object: Any = field(compare=False, repr=False)

    @property
    def extension(self) -> str:
        suffix = Path(self.name).suffix.lower().lstrip(".")
        return suffix or ("unity3d" if self.object_type == "assetbundle" else "")


@dataclass
class SongGroup:
    key: str
    internal_id: str
    character_id: str
    data_type: str
    singing: str
    version: str
    base_name: str
    title: str = ""
    metadata: SongMetadata = field(default_factory=lambda: SongMetadata())
    assets: dict[str, AssetRef] = field(default_factory=dict)
    artwork: AssetRef | None = None
    related_live_keys: list[str] = field(default_factory=list)

    @property
    def has_live(self) -> bool:
        return self.data_type == KIND_LIVE or bool(self.related_live_keys)

    @property
    def is_short_version(self) -> bool:
        """Return whether this is a shortened live-stage audio asset."""
        return self.data_type == KIND_LIVE and self.version.casefold().startswith("normal-")

    @property
    def has_artwork(self) -> bool:
        return self.artwork is not None

    @property
    def search_text(self) -> str:
        names = " ".join(asset.name for asset in self.assets.values())
        artwork_name = self.artwork.name if self.artwork else ""
        return " ".join(
            (
                self.internal_id,
                self.character_id,
                self.data_type,
                self.singing,
                self.version,
                self.base_name,
                self.title,
                self.metadata.search_text,
                artwork_name,
                "短縮版" if self.is_short_version else "通常版",
                names,
            )
        ).casefold()


@dataclass(frozen=True)
class SongMetadata:
    """Officially sourced music credits and release information."""

    performer: str = ""
    lyricist: str = ""
    composer: str = ""
    arranger: str = ""
    album: str = ""
    release_date: str = ""
    track_number: str = ""
    disc_number: str = ""
    label: str = ""
    copyright: str = ""
    source_url: str = ""
    credit_source_url: str = ""
    verified_at: str = ""

    @property
    def has_official_info(self) -> bool:
        return any(
            (
                self.performer,
                self.lyricist,
                self.composer,
                self.arranger,
                self.album,
                self.release_date,
                self.track_number,
                self.disc_number,
                self.label,
                self.copyright,
            )
        )

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.performer,
                self.lyricist,
                self.composer,
                self.arranger,
                self.album,
                self.release_date,
                self.label,
            )
        )


@dataclass
class ScanResult:
    manifest: Any = field(repr=False)
    info: ManifestInfo
    groups: list[SongGroup]


@dataclass
class ExtractionOptions:
    output_dir: Path
    save_wav: bool = True
    save_flac: bool = False
    normalize_loudness: bool = True
    save_awb: bool = True
    save_mp3: bool = False
    save_acb: bool = False
    embed_artwork: bool = True
    embed_official_metadata: bool = True
    save_artwork: bool = False
    include_live: bool = False
    filename_format: str = FILENAME_TITLE_CHARACTER


ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]
