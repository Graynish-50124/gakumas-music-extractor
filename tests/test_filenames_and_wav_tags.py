from __future__ import annotations

import base64
import io
import wave

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.wave import WAVE

from core.audio import (
    ConvertedAudio,
    convert_wav_to_flac,
    read_flac_embedded_artwork,
    read_flac_tags,
    read_mp3_embedded_artwork,
    read_wav_embedded_artwork,
    read_wav_info_tags,
    verify_audio_components,
    write_flac_tags,
    write_mp3_tags,
    write_wav_info_tags,
)
from core.extractor import ExtractionEngine
from core.models import (
    FILENAME_ORIGINAL,
    FILENAME_TITLE,
    FILENAME_TITLE_CHARACTER,
    KIND_GENERAL,
    KIND_LIVE,
    SINGING_INST,
    SINGING_VOCAL,
    AssetRef,
    ExtractionOptions,
    SongGroup,
)


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + len(payload).to_bytes(4, "little") + payload + (b"\x00" if len(payload) & 1 else b"")


def _wav_with_album() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8_000)
        wav.writeframes(b"\x00\x00" * 16)
    data = bytearray(output.getvalue())
    data.extend(_chunk(b"LIST", b"INFO" + _chunk(b"IPRD", "既存アルバム".encode("utf-8") + b"\x00")))
    data[4:8] = (len(data) - 8).to_bytes(4, "little")
    return bytes(data)


PNG_ARTWORK = b"\x89PNG\r\n\x1a\n" + b"test-cover-art"
MINIMAL_FLAC = base64.b64decode(
    "ZkxhQwAAACIAEAAQAAAMAAAMAfQA8AAAABBwvI9LcqhpIUaL+OhEHc5RhAAALAwAAABM"
    "YXZmNjMuMS4xMDEBAAAAFAAAAGVuY29kZXI9TGF2ZjYzLjEuMTAx//hkCAAPzgAAAA6F"
)
HAS_FFMPEG = verify_audio_components()["ffmpeg"] is not None


def _asset(name: str = "sud_music_general_amao-001-amao_game.awb") -> AssetRef:
    return AssetRef(name, 1, name, 0, "", "", "resource", object())


def _group(
    *,
    key: str = "normal",
    character_id: str = "amao",
    data_type: str = KIND_GENERAL,
    version: str = "game",
    title: str = "Fluorite",
    singing: str = SINGING_VOCAL,
) -> SongGroup:
    return SongGroup(
        key=key,
        internal_id="amao-001",
        character_id=character_id,
        data_type=data_type,
        singing=singing,
        version=version,
        base_name="sud_music_general_amao-001-amao_game",
        title=title,
    )


def test_wav_title_and_artist_tags_preserve_other_info() -> None:
    original = _wav_with_album()
    tagged = write_wav_info_tags(
        original,
        title="Fluorite",
        artist="有村麻央",
        artwork=PNG_ARTWORK,
    )
    assert read_wav_info_tags(tagged) == {
        "IPRD": "既存アルバム",
        "INAM": "Fluorite",
        "IART": "有村麻央",
    }
    assert "有村麻央".encode("cp932") in tagged
    assert "有村麻央".encode("utf-8") not in tagged
    assert read_wav_embedded_artwork(tagged) == PNG_ARTWORK
    assert not WAVE(io.BytesIO(tagged)).tags.getall("TALB")
    with wave.open(io.BytesIO(tagged), "rb") as wav:
        assert wav.getnframes() == 16
        assert wav.readframes(16) == b"\x00\x00" * 16

    updated = write_wav_info_tags(tagged, title="新タイトル", artist="花海咲季")
    assert read_wav_info_tags(updated)["INAM"] == "新タイトル"
    assert read_wav_info_tags(updated)["IART"] == "花海咲季"
    assert updated.count(b"INAM") == 1
    assert updated.count(b"IART") == 1
    assert read_wav_embedded_artwork(updated) == PNG_ARTWORK


def test_mp3_title_artist_and_cover_art_tags() -> None:
    original = b"\xff\xfb\x90\x64" + b"\x00" * 256
    tagged = write_mp3_tags(
        original,
        title="Fluorite",
        artist="有村麻央",
        artwork=PNG_ARTWORK,
    )
    tags = ID3(io.BytesIO(tagged))
    assert str(tags["TIT2"]) == "Fluorite"
    assert str(tags["TPE1"]) == "有村麻央"
    assert not tags.getall("TALB")
    assert read_mp3_embedded_artwork(tagged) == PNG_ARTWORK


def test_flac_metadata_writer_does_not_require_ffmpeg() -> None:
    flac = write_flac_tags(
        MINIMAL_FLAC,
        title="Fluorite",
        artist="有村麻央",
        artwork=PNG_ARTWORK,
    )
    assert read_flac_tags(flac)["title"] == "Fluorite"
    assert read_flac_tags(flac)["artist"] == "有村麻央"
    assert "album" not in read_flac_tags(flac)
    assert read_flac_embedded_artwork(flac) == PNG_ARTWORK


@pytest.mark.skipif(not HAS_FFMPEG, reason="同梱FFmpegなし")
def test_wav_to_flac_preserves_pcm_and_writes_metadata() -> None:
    source = _wav_with_album()
    flac = convert_wav_to_flac(
        source,
        title="Fluorite",
        artist="有村麻央",
        artwork=PNG_ARTWORK,
    )
    parsed = FLAC(io.BytesIO(flac))
    assert flac.startswith(b"fLaC")
    assert parsed.info.sample_rate == 8_000
    assert parsed.info.channels == 1
    assert parsed.info.total_samples == 16
    assert read_flac_tags(flac)["title"] == "Fluorite"
    assert read_flac_tags(flac)["artist"] == "有村麻央"
    assert "album" not in read_flac_tags(flac)
    assert read_flac_embedded_artwork(flac) == PNG_ARTWORK


@pytest.mark.skipif(not HAS_FFMPEG, reason="同梱FFmpegなし")
def test_flac_only_does_not_leave_wav_file(tmp_path) -> None:
    engine = ExtractionEngine(None, {"amao": "有村麻央"})
    group = _group()
    written = engine._save_converted(
        group,
        _asset(),
        _wav_with_album(),
        "audio/wav",
        ExtractionOptions(
            output_dir=tmp_path,
            save_wav=False,
            save_flac=True,
            save_awb=False,
        ),
        PNG_ARTWORK,
    )
    flac = tmp_path / "Fluorite＿有村麻央.flac"
    assert written == [flac]
    assert flac.read_bytes().startswith(b"fLaC")
    assert not (tmp_path / "Fluorite＿有村麻央.wav").exists()


def test_artwork_is_embedded_without_saving_a_separate_image(tmp_path, monkeypatch) -> None:
    import core.extractor as extractor_module

    artwork_asset = AssetRef(
        "img_general_music_jacket_amao-001.png",
        2,
        "artwork",
        len(PNG_ARTWORK),
        "",
        "",
        "resource",
        object(),
    )
    group = _group()
    group.artwork = artwork_asset
    group.assets["AWB"] = _asset()
    engine = ExtractionEngine(None, {"amao": "有村麻央"})
    monkeypatch.setattr(
        engine,
        "_obtain",
        lambda asset, _index, _total: PNG_ARTWORK if asset is artwork_asset else b"raw",
    )
    monkeypatch.setattr(
        extractor_module,
        "decode_game_audio_to_wav",
        lambda _asset_ref, _raw: ConvertedAudio(_wav_with_album(), "audio/wav"),
    )

    written = engine.extract(
        [group],
        [group],
        ExtractionOptions(output_dir=tmp_path, save_wav=True, save_awb=False),
    )
    wav = tmp_path / "Fluorite＿有村麻央.wav"
    image = tmp_path / "Fluorite＿有村麻央.png"
    assert written == [wav]
    assert not image.exists()
    assert read_wav_embedded_artwork(wav.read_bytes()) == PNG_ARTWORK


def test_three_filename_formats_and_live_marker() -> None:
    engine = ExtractionEngine(None, {"amao": "有村麻央"})
    group = _group()
    asset = _asset()
    assert engine._output_filename(group, asset, "wav", FILENAME_TITLE_CHARACTER) == "Fluorite＿有村麻央.wav"
    assert engine._output_filename(group, asset, "awb", FILENAME_ORIGINAL) == (
        "sud_music_general_amao-001-amao_game.awb"
    )
    assert engine._output_filename(group, asset, "mp3", FILENAME_TITLE) == "Fluorite.mp3"
    assert engine._output_filename(group, asset, "flac", FILENAME_TITLE) == "Fluorite.flac"

    live = _group(key="live", data_type=KIND_LIVE, version="true-001")
    live_asset = _asset("sud_music_live_amao-001-amao_true-001.unity3d")
    assert engine._output_filename(live, live_asset, "wav", FILENAME_TITLE_CHARACTER) == (
        "Fluorite＿有村麻央［ライブ］.wav"
    )

    short_live = _group(key="short", data_type=KIND_LIVE, version="normal-001")
    assert engine._output_filename(short_live, live_asset, "wav", FILENAME_TITLE_CHARACTER) == (
        "Fluorite＿有村麻央［ライブ・短縮版］.wav"
    )

    instrumental = _group(title="Fluorite [Instrumental]", singing=SINGING_INST)
    assert engine._output_filename(instrumental, asset, "wav", FILENAME_TITLE_CHARACTER) == (
        "Fluorite＿有村麻央［インスト］.wav"
    )


def test_windows_invalid_title_characters_become_readable_fullwidth_forms() -> None:
    engine = ExtractionEngine(None, {"amao": "有村麻央"})
    group = _group(title='A/B? C:D* "E"')
    assert engine._output_filename(group, _asset(), "wav", FILENAME_TITLE_CHARACTER) == (
        "A／B？ C：D＊ ”E”＿有村麻央.wav"
    )


def test_title_only_collision_gets_character_discriminator() -> None:
    engine = ExtractionEngine(None, {"amao": "有村麻央", "hrnm": "姫崎莉波"})
    first = _group(key="amao")
    second = _group(key="hrnm", character_id="hrnm")
    assert engine._output_filename(first, _asset(), "wav", FILENAME_TITLE) == "Fluorite.wav"
    assert engine._output_filename(second, _asset(), "wav", FILENAME_TITLE) == "Fluorite＿姫崎莉波.wav"


def test_unit_character_names_are_expanded() -> None:
    engine = ExtractionEngine(None, {"amao": "有村麻央", "hrnm": "姫崎莉波"})
    unit = _group(character_id="amaohrnm")
    assert engine._character_name(unit) == "有村麻央・姫崎莉波"
