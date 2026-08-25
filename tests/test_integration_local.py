from __future__ import annotations

import wave
from pathlib import Path

import pytest

from core.config import ConfigStore
from core.audio import (
    read_mp3_embedded_artwork,
    read_wav_embedded_artwork,
    read_wav_info_tags,
)
from core.extractor import ExtractionEngine, OctoCacheResolver
from core.manifest import load_local_manifest, select_latest_manifest
from core.models import ExtractionOptions
from core.scanner import scan_music_assets


OCTO_ROOT = Path.home() / "gakumas" / "octo"


@pytest.mark.skipif(not OCTO_ROOT.exists(), reason="学マスPC版ローカルデータなし")
def test_real_manifest_acceptance_and_cache() -> None:
    candidate = select_latest_manifest(OCTO_ROOT)
    manifest = load_local_manifest(candidate.path)
    groups = scan_music_assets(manifest, ConfigStore().load_mapping("song_names.json"))
    group = next(
        item
        for item in groups
        if item.internal_id == "all-018"
        and item.character_id == "hrnm"
        and item.singing == "歌入り"
    )
    assert set(group.assets) >= {"ACB", "AWB", "MP3"}
    assert group.title in {"「ねえ、言っちゃうよ。」", "ねぇ、言っちゃうよ。"}
    live = next(
        item
        for item in groups
        if item.internal_id == "all-018"
        and item.character_id == "hrnm"
        and item.data_type == "ライブ"
        and item.version == "true-001"
    )
    assert live.title in {"「ねえ、言っちゃうよ。」", "ねぇ、言っちゃうよ。"}
    assert not live.is_short_version
    short_live = next(item for item in groups if item.data_type == "ライブ" and item.version.startswith("normal-"))
    assert short_live.is_short_version
    named_music = [item for item in groups if item.data_type != "BGM"]
    assert sum(bool(item.title) for item in named_music) / len(named_music) > 0.98
    resolver = OctoCacheResolver(OCTO_ROOT)
    assert resolver.find(group.assets["AWB"]) is not None
    assert group.artwork is not None
    assert resolver.find(group.artwork) is not None


@pytest.mark.skipif(not OCTO_ROOT.exists(), reason="学マスPC版ローカルデータなし")
def test_real_awb_and_wav_extraction(tmp_path: Path) -> None:
    manifest = load_local_manifest(select_latest_manifest(OCTO_ROOT).path)
    groups = scan_music_assets(manifest, ConfigStore().load_mapping("song_names.json"))
    group = next(
        item
        for item in groups
        if item.internal_id == "all-018"
        and item.character_id == "hrnm"
        and item.singing == "歌入り"
    )
    engine = ExtractionEngine(OCTO_ROOT, {"hrnm": "姫崎莉波"})
    written = engine.extract(
        [group],
        groups,
        ExtractionOptions(
            output_dir=tmp_path,
            save_wav=True,
            save_awb=True,
            save_mp3=True,
        ),
    )
    awb = tmp_path / f"{group.title}＿姫崎莉波.awb"
    wav = tmp_path / f"{group.title}＿姫崎莉波.wav"
    mp3 = tmp_path / f"{group.title}＿姫崎莉波.mp3"
    artwork = tmp_path / f"{group.title}＿姫崎莉波.png"
    assert awb in written and awb.read_bytes()[:4] == b"AFS2"
    assert wav in written and wav.read_bytes()[:4] in {b"RIFF", b"RF64"}
    assert read_wav_info_tags(wav.read_bytes()) == {
        "INAM": group.title,
        "IART": "姫崎莉波",
    }
    assert artwork in written and artwork.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert read_wav_embedded_artwork(wav.read_bytes()) == artwork.read_bytes()
    assert mp3 in written and mp3.read_bytes().startswith(b"ID3")
    assert read_mp3_embedded_artwork(mp3.read_bytes()) == artwork.read_bytes()


@pytest.mark.skipif(not OCTO_ROOT.exists(), reason="学マスPC版ローカルデータなし")
def test_real_live_true_wav_extraction(tmp_path: Path) -> None:
    manifest = load_local_manifest(select_latest_manifest(OCTO_ROOT).path)
    groups = scan_music_assets(manifest, ConfigStore().load_mapping("song_names.json"))
    live = next(
        item
        for item in groups
        if item.internal_id == "all-018"
        and item.character_id == "hrnm"
        and item.data_type == "ライブ"
        and item.version == "true-001"
    )
    engine = ExtractionEngine(OCTO_ROOT, {"hrnm": "姫崎莉波"})
    written = engine.extract(
        [live],
        groups,
        ExtractionOptions(output_dir=tmp_path, save_wav=True, save_awb=False),
    )
    wav = tmp_path / f"{live.title}＿姫崎莉波［ライブ］.wav"
    artwork = tmp_path / f"{live.title}＿姫崎莉波［ライブ］.png"
    assert wav in written and wav.read_bytes()[:4] in {b"RIFF", b"RF64"}
    with wave.open(str(wav), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getframerate() == 48_000
        assert stream.getnframes() > 48_000 * 60
    assert read_wav_info_tags(wav.read_bytes()) == {
        "INAM": live.title,
        "IART": "姫崎莉波",
    }
    assert artwork in written and artwork.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert read_wav_embedded_artwork(wav.read_bytes()) == artwork.read_bytes()
    from UnityPy.export import AudioClipConverter

    assert AudioClipConverter.pyfmodex is None
