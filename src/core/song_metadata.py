from __future__ import annotations

import re
import unicodedata
from dataclasses import fields, replace
from typing import Any, Mapping

from .models import SongGroup, SongMetadata


_TRAILING_AUDIO_VARIANT_RE = re.compile(
    r"\s*[\[［](?:instrumental|インスト(?:ゥルメンタル)?|off\s*vocal)[\]］]\s*$",
    re.IGNORECASE,
)


def metadata_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).strip()
    value = value.replace("〜", "~").replace("～", "~")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*([\[\]()])\s*", r"\1", value)
    if value.startswith("「") and value.endswith("」"):
        value = value[1:-1].strip()
    # The game master uses a reading aid while the official label title is 「初」.
    if value == "初(はじめ)":
        value = "初"
    return value.casefold()


def base_title_key(value: str) -> str:
    return _TRAILING_AUDIO_VARIANT_RE.sub("", metadata_key(value)).strip()


def song_metadata_from_mapping(value: Mapping[str, Any]) -> SongMetadata:
    allowed = {item.name for item in fields(SongMetadata)}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if key not in allowed or item is None:
            continue
        if isinstance(item, list):
            text = " / ".join(str(part).strip() for part in item if str(part).strip())
        else:
            text = str(item).strip()
        if text:
            normalized[key] = text
    return SongMetadata(**normalized)


def merge_song_metadata(base: SongMetadata, override: SongMetadata) -> SongMetadata:
    updates = {
        item.name: value
        for item in fields(SongMetadata)
        if (value := getattr(override, item.name))
    }
    return replace(base, **updates) if updates else base


def parse_metadata_document(value: Any) -> dict[str, SongMetadata]:
    if not isinstance(value, dict):
        return {}
    songs = value.get("songs", value)
    if not isinstance(songs, dict):
        return {}
    result: dict[str, SongMetadata] = {}
    for key, item in songs.items():
        if str(key).startswith("_") or not isinstance(item, dict):
            continue
        parsed = song_metadata_from_mapping(item)
        if parsed.has_official_info:
            result[metadata_key(str(key))] = parsed
    return result


def resolve_song_metadata(
    catalog: Mapping[str, SongMetadata],
    group: SongGroup,
) -> SongMetadata:
    exact_candidates = (
        group.key,
        group.base_name,
        f"{group.internal_id}/{group.character_id}",
        group.internal_id,
        group.title,
    )
    for candidate in exact_candidates:
        if candidate and (metadata := catalog.get(metadata_key(candidate))):
            return metadata
    if group.title and (metadata := catalog.get(base_title_key(group.title))):
        return metadata
    return SongMetadata()
