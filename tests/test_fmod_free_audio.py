from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

from GkmasObjectManager.media.audio import GkmasAWBAudio

from core.audio import _decode_unity_audio_blob, _pack_wav_files


def _minimal_wav(payload: bytes = b"\x00\x00") -> bytes:
    fmt = (
        b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
    )
    data = b"data" + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + (4 + len(fmt) + len(data)).to_bytes(4, "little") + b"WAVE" + fmt + data


def test_unity_wav_payload_passes_through_without_external_decoder() -> None:
    wav = _minimal_wav()
    assert _decode_unity_audio_blob(wav) == [wav]


def test_multiple_decoded_samples_are_packed_as_wav_zip() -> None:
    first = _minimal_wav(b"\x00\x00")
    second = _minimal_wav(b"\x01\x00")
    converted = _pack_wav_files([first, second])
    assert converted.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(converted.data)) as archive:
        assert archive.namelist() == ["sample_01.wav", "sample_02.wav"]
        assert archive.read("sample_01.wav") == first
        assert archive.read("sample_02.wav") == second


def test_pyinstaller_specs_exclude_fmod_runtime() -> None:
    project = Path(__file__).resolve().parents[1]
    for name in ("GakumasMusicExtractor-onedir.spec", "GakumasMusicExtractor-onefile.spec"):
        text = (project / name).read_text(encoding="utf-8")
        assert 'if "fmod" not in source.casefold()' in text
        assert '"pyfmodex", "fmod_toolkit"' in text


def test_gom_vgmstream_decoder_hides_windows_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        output = Path(str(command[4]).replace("?s", "1"))
        output.write_bytes(_minimal_wav())

    monkeypatch.setattr(subprocess, "run", fake_run)
    decoder = object.__new__(GkmasAWBAudio)
    decoder.ext = "awb"
    segments = decoder._read_segments(b"test")
    assert len(segments) == 1
    assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
