from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .models import (
    KIND_BGM,
    KIND_CHARACTER,
    KIND_GENERAL,
    KIND_LIVE,
    KIND_UNIT,
    SINGING_INST,
    SINGING_UNKNOWN,
    SINGING_VOCAL,
    AssetRef,
    SongGroup,
)


GENERAL_RE = re.compile(
    r"^sud_music_general_(?P<scope>[^-]+)-(?P<number>\d+)-"
    r"(?P<performer>[^_]+)_(?P<version>[^.]+)\.(?P<ext>acb|awb|mp3)$",
    re.IGNORECASE,
)
LIVE_RE = re.compile(
    r"^sud_music_live_(?P<scope>[^-]+)-(?P<number>\d+)-"
    r"(?P<performer>[^_]+)_(?P<mode>normal|true)-(?P<take>\d+)"
    r"(?:\.unity3d)?$",
    re.IGNORECASE,
)
BGM_RE = re.compile(r"^(?P<base>sud_bgm_.+)\.(?P<ext>acb|awb|mp3)$", re.IGNORECASE)


def _asset_ref(obj: Any) -> AssetRef:
    name = str(obj.name)
    is_bundle = name.casefold().endswith(".unity3d") or hasattr(obj, "crc")
    return AssetRef(
        name=name,
        object_id=int(obj.id),
        object_name=str(obj.objectName),
        size=int(obj.size),
        md5=str(getattr(obj, "md5", "")).casefold(),
        url=str(obj._url),
        object_type="assetbundle" if is_bundle else "resource",
        source_object=obj,
    )


def _song_title(mapping: dict[str, str], group: SongGroup) -> str:
    for key in (
        group.key,
        group.base_name,
        f"{group.internal_id}/{group.character_id}",
        group.internal_id,
    ):
        if mapping.get(key):
            return mapping[key]
    return ""


def _artwork_names(group: SongGroup) -> tuple[str, ...]:
    """Return jacket resource names in game-preference order."""

    if group.data_type == KIND_BGM:
        return ("img_general_music_jacket_bgm-01.png",)
    if "-" not in group.internal_id:
        return ()
    _scope, number = group.internal_id.split("-", 1)
    if group.data_type == KIND_CHARACTER:
        return (f"img_general_music_jacket_char-{group.character_id}-{number}.png",)
    if group.data_type == KIND_UNIT:
        return (f"img_general_music_jacket_unit-{group.character_id}-{number}.png",)
    if group.singing == SINGING_INST:
        return (f"img_general_music_jacket_all-{number}-inst.png",)
    return (
        f"img_general_music_jacket_all-{group.character_id}-{number}.png",
        f"img_general_music_jacket_all-cmmn-{number}.png",
        f"img_general_music_jacket_all-cmmn-{number}-before.png",
        f"img_general_music_jacket_all-{number}-inst.png",
    )


def scan_music_assets(manifest: Any, song_names: dict[str, str] | None = None) -> list[SongGroup]:
    mapping = song_names or {}
    grouped: dict[str, SongGroup] = {}
    artworks = {
        str(obj.name).casefold(): _asset_ref(obj)
        for obj in manifest.search(r"^img_general_music_jacket_.*\.png$")
    }

    for obj in manifest.search(r"^sud_(?:music|bgm)_"):
        name = str(obj.name)
        general = GENERAL_RE.match(name)
        if general:
            data = general.groupdict()
            base = str(Path(name).with_suffix(""))
            scope = data["scope"].casefold()
            number = data["number"]
            performer = data["performer"].casefold()
            version = data["version"].casefold()
            key = f"general:{base}"
            if scope == "all":
                kind = KIND_GENERAL
            elif scope == "unit":
                kind = KIND_UNIT
            else:
                kind = KIND_CHARACTER
            group = grouped.setdefault(
                key,
                SongGroup(
                    key=key,
                    internal_id=f"{scope}-{number}",
                    character_id=performer,
                    data_type=kind,
                    singing=SINGING_INST if "inst" in version.split("-") else SINGING_VOCAL,
                    version=version,
                    base_name=base,
                ),
            )
            group.assets[data["ext"].upper()] = _asset_ref(obj)
            continue

        live = LIVE_RE.match(name)
        if live:
            data = live.groupdict()
            base = name.removesuffix(".unity3d")
            scope = data["scope"].casefold()
            number = data["number"]
            performer = data["performer"].casefold()
            version = f"{data['mode'].casefold()}-{data['take']}"
            key = f"live:{base}"
            grouped[key] = SongGroup(
                key=key,
                internal_id=f"{scope}-{number}",
                character_id=performer,
                data_type=KIND_LIVE,
                singing=SINGING_VOCAL,
                version=version,
                base_name=base,
                assets={"LIVE": _asset_ref(obj)},
            )
            continue

        bgm = BGM_RE.match(name)
        if bgm:
            data = bgm.groupdict()
            base = data["base"]
            key = f"bgm:{base}"
            character_id = ""
            group = grouped.setdefault(
                key,
                SongGroup(
                    key=key,
                    internal_id=base.removeprefix("sud_bgm_"),
                    character_id=character_id,
                    data_type=KIND_BGM,
                    singing=(
                        SINGING_INST
                        if re.search(r"(?:^|[-_])inst(?:[-_]|$)", base, re.IGNORECASE)
                        else SINGING_UNKNOWN
                    ),
                    version="",
                    base_name=base,
                ),
            )
            group.assets[data["ext"].upper()] = _asset_ref(obj)

    live_lookup: dict[tuple[str, str], list[str]] = {}
    for group in grouped.values():
        if group.data_type == KIND_LIVE:
            live_lookup.setdefault((group.internal_id, group.character_id), []).append(group.key)

    for group in grouped.values():
        group.title = _song_title(mapping, group)
        group.artwork = next(
            (artworks[name.casefold()] for name in _artwork_names(group) if name.casefold() in artworks),
            None,
        )
        if group.data_type in (KIND_GENERAL, KIND_CHARACTER, KIND_UNIT) and group.singing != SINGING_INST:
            group.related_live_keys = sorted(
                live_lookup.get((group.internal_id, group.character_id), [])
            )

    order = {
        KIND_GENERAL: 0,
        KIND_CHARACTER: 1,
        KIND_UNIT: 2,
        KIND_LIVE: 3,
        KIND_BGM: 4,
    }
    return sorted(
        grouped.values(),
        key=lambda item: (
            order.get(item.data_type, 99),
            item.internal_id,
            item.character_id,
            item.singing,
            item.version,
        ),
    )


def filter_groups(
    groups: Iterable[SongGroup],
    character_id: str = "",
    data_type: str = "",
    data_types: Iterable[str] | None = None,
    singing: str = "",
    short_version: bool | None = None,
    search: str = "",
) -> list[SongGroup]:
    needle = search.strip().casefold()
    selected_types = None if data_types is None else set(data_types)
    return [
        group
        for group in groups
        if (not character_id or group.character_id == character_id)
        and (not data_type or group.data_type == data_type)
        and (selected_types is None or group.data_type in selected_types)
        and (not singing or group.singing == singing)
        and (short_version is None or group.is_short_version is short_version)
        and (not needle or needle in group.search_text)
    ]
