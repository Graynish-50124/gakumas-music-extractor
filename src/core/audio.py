from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import UnityPy
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TIT2, TPE1
from mutagen.wave import WAVE

from GkmasObjectManager.const import UNITY_SIGNATURE
from GkmasObjectManager.object.deobfuscate import GkmasAssetBundleDeobfuscator

from app_bootstrap import component_paths

from .models import AssetRef


class AudioConversionError(RuntimeError):
    pass


class ComponentMissingError(AudioConversionError):
    pass


def _riff_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    if len(chunk_id) != 4:
        raise ValueError("RIFF chunk ID must be four bytes")
    padding = b"\x00" if len(payload) % 2 else b""
    return chunk_id + len(payload).to_bytes(4, "little") + payload + padding


def _iter_riff_chunks(data: bytes, start: int, end: int):
    cursor = start
    rf64_data_size: int | None = None
    while cursor + 8 <= end:
        chunk_id = data[cursor : cursor + 4]
        declared_size = int.from_bytes(data[cursor + 4 : cursor + 8], "little")
        size = declared_size
        if data[:4] == b"RF64" and chunk_id == b"data" and declared_size == 0xFFFFFFFF:
            if rf64_data_size is None:
                raise AudioConversionError("RF64のds64チャンクを確認できません")
            size = rf64_data_size
        chunk_end = cursor + 8 + size + (size & 1)
        if chunk_end > end:
            raise AudioConversionError("WAVのRIFFチャンクが破損しています")
        payload = data[cursor + 8 : cursor + 8 + size]
        if chunk_id == b"ds64" and len(payload) >= 16:
            rf64_data_size = int.from_bytes(payload[8:16], "little")
        yield chunk_id, payload, data[cursor:chunk_end]
        cursor = chunk_end
    if cursor != end:
        raise AudioConversionError("WAV末尾のRIFFチャンク境界を確認できません")


def _decode_info_text(value: bytes) -> str:
    value = value.rstrip(b"\x00")
    for encoding in ("utf-8", "cp932"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def read_wav_info_tags(data: bytes) -> dict[str, str]:
    """Read RIFF INFO text tags from a WAV payload."""

    if len(data) < 12 or data[:4] not in {b"RIFF", b"RF64"} or data[8:12] != b"WAVE":
        raise AudioConversionError("WAVのRIFFヘッダーを確認できません")
    result: dict[str, str] = {}
    for chunk_id, payload, _raw in _iter_riff_chunks(data, 12, len(data)):
        if chunk_id != b"LIST" or len(payload) < 4 or payload[:4] != b"INFO":
            continue
        for info_id, info_payload, _info_raw in _iter_riff_chunks(payload, 4, len(payload)):
            try:
                key = info_id.decode("ascii")
            except UnicodeDecodeError:
                continue
            result[key] = _decode_info_text(info_payload)
    return result


def detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise AudioConversionError("アルバムアートの画像形式を確認できません")


def _update_id3(tags: ID3, *, title: str, artist: str, artwork: bytes | None) -> None:
    tags.delall("TIT2")
    tags.delall("TPE1")
    if title:
        tags.add(TIT2(encoding=1, text=[title]))
    if artist:
        tags.add(TPE1(encoding=1, text=[artist]))
    if artwork is not None:
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=1,
                mime=detect_image_mime(artwork),
                type=3,
                desc="Cover",
                data=artwork,
            )
        )


def _write_wav_id3(data: bytes, *, title: str, artist: str, artwork: bytes) -> bytes:
    stream = io.BytesIO(data)
    try:
        wave = WAVE(stream)
        if wave.tags is None:
            wave.add_tags()
        _update_id3(wave.tags, title=title, artist=artist, artwork=artwork)
        wave.save(stream, v2_version=3)
        return stream.getvalue()
    except Exception as exc:
        raise AudioConversionError(f"WAVへのアルバムアート埋め込みに失敗しました: {exc}") from exc


def _id3_front_cover(tags: ID3 | None) -> bytes | None:
    if tags is None:
        return None
    pictures = tags.getall("APIC")
    if not pictures:
        return None
    preferred = next((picture for picture in pictures if int(picture.type) == 3), pictures[0])
    return bytes(preferred.data)


def read_wav_embedded_artwork(data: bytes) -> bytes | None:
    try:
        return _id3_front_cover(WAVE(io.BytesIO(data)).tags)
    except Exception as exc:
        raise AudioConversionError(f"WAVのアルバムアートを読み込めません: {exc}") from exc


def write_mp3_tags(data: bytes, *, title: str, artist: str, artwork: bytes) -> bytes:
    stream = io.BytesIO(data)
    try:
        try:
            tags = ID3(stream)
        except ID3NoHeaderError:
            tags = ID3()
        _update_id3(tags, title=title, artist=artist, artwork=artwork)
        tags.save(stream, v2_version=3)
        return stream.getvalue()
    except Exception as exc:
        raise AudioConversionError(f"MP3へのアルバムアート埋め込みに失敗しました: {exc}") from exc


def read_mp3_embedded_artwork(data: bytes) -> bytes | None:
    try:
        return _id3_front_cover(ID3(io.BytesIO(data)))
    except ID3NoHeaderError:
        return None
    except Exception as exc:
        raise AudioConversionError(f"MP3のアルバムアートを読み込めません: {exc}") from exc


def write_flac_tags(
    data: bytes,
    *,
    title: str,
    artist: str,
    artwork: bytes | None,
) -> bytes:
    if not data.startswith(b"fLaC"):
        raise AudioConversionError("FLACヘッダーを確認できません")
    stream = io.BytesIO(data)
    try:
        audio = FLAC(stream)
        if audio.tags is None:
            audio.add_tags()
        audio["title"] = [title] if title else []
        audio["artist"] = [artist] if artist else []
        if artwork is not None:
            audio.clear_pictures()
            picture = Picture()
            picture.type = 3
            picture.mime = detect_image_mime(artwork)
            picture.desc = "Cover"
            picture.data = artwork
            audio.add_picture(picture)
        stream.seek(0)
        audio.save(stream)
        return stream.getvalue()
    except Exception as exc:
        raise AudioConversionError(f"FLACへのタグ・アルバムアート埋め込みに失敗しました: {exc}") from exc


def read_flac_tags(data: bytes) -> dict[str, str]:
    try:
        audio = FLAC(io.BytesIO(data))
        return {
            str(key).casefold(): str(values[0])
            for key, values in (audio.tags or {}).items()
            if values
        }
    except Exception as exc:
        raise AudioConversionError(f"FLACのタグを読み込めません: {exc}") from exc


def read_flac_embedded_artwork(data: bytes) -> bytes | None:
    try:
        pictures = FLAC(io.BytesIO(data)).pictures
        if not pictures:
            return None
        preferred = next((picture for picture in pictures if int(picture.type) == 3), pictures[0])
        return bytes(preferred.data)
    except Exception as exc:
        raise AudioConversionError(f"FLACのアルバムアートを読み込めません: {exc}") from exc


def write_wav_info_tags(
    data: bytes,
    *,
    title: str,
    artist: str,
    artwork: bytes | None = None,
) -> bytes:
    """Set title/artist and optional cover art while preserving unrelated tags."""

    if len(data) < 12 or data[:4] not in {b"RIFF", b"RF64"} or data[8:12] != b"WAVE":
        raise AudioConversionError("WAVのRIFFヘッダーを確認できません")

    top_level: list[bytes] = []
    preserved_info: list[bytes] = []
    for chunk_id, payload, raw in _iter_riff_chunks(data, 12, len(data)):
        if chunk_id == b"LIST" and len(payload) >= 4 and payload[:4] == b"INFO":
            for info_id, _info_payload, info_raw in _iter_riff_chunks(payload, 4, len(payload)):
                if info_id not in {b"INAM", b"IART"}:
                    preserved_info.append(info_raw)
            continue
        top_level.append(raw)

    def text_chunk(chunk_id: bytes, value: str) -> bytes:
        # Windows' WAV property handlers commonly interpret RIFF INFO through
        # the active Japanese ANSI code page rather than UTF-8.  The bundled
        # title/character catalog is CP932-safe, so write it in that form to
        # prevent Japanese artist names from becoming mojibake.
        return _riff_chunk(chunk_id, value.encode("cp932", errors="replace") + b"\x00")

    if title:
        preserved_info.append(text_chunk(b"INAM", title))
    if artist:
        preserved_info.append(text_chunk(b"IART", artist))
    if preserved_info:
        top_level.append(_riff_chunk(b"LIST", b"INFO" + b"".join(preserved_info)))

    result = bytearray(data[:12] + b"".join(top_level))
    if result[:4] == b"RIFF":
        riff_size = len(result) - 8
        if riff_size > 0xFFFFFFFF:
            raise AudioConversionError("WAVがRIFFの最大サイズを超えました")
        result[4:8] = riff_size.to_bytes(4, "little")
    else:
        # RF64 keeps 0xFFFFFFFF in the RIFF header and stores the real size in ds64.
        cursor = 12
        while cursor + 16 <= len(result):
            chunk_size = int.from_bytes(result[cursor + 4 : cursor + 8], "little")
            if result[cursor : cursor + 4] == b"ds64" and chunk_size >= 8:
                result[cursor + 8 : cursor + 16] = (len(result) - 8).to_bytes(8, "little")
                break
            cursor += 8 + chunk_size + (chunk_size & 1)
    output = bytes(result)
    if artwork is not None:
        output = _write_wav_id3(output, title=title, artist=artist, artwork=artwork)
    return output


class _ConversionReporter:
    def update(self, stage: str, advance: int | None = None) -> None:
        del stage, advance

    def warning(self, message: str) -> None:
        del message

    def error(self, message: str) -> None:
        raise AudioConversionError(message)


@dataclass(frozen=True)
class ConvertedAudio:
    data: bytes
    mimetype: str


def verify_audio_components() -> dict[str, Path | None]:
    return component_paths()


def _run_audio_tool(command: list[str], tool_name: str, action: str = "音声デコード") -> None:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
    if len(detail) > 800:
        detail = detail[-800:]
    suffix = f": {detail}" if detail else ""
    raise AudioConversionError(f"{tool_name}による{action}に失敗しました{suffix}")


def convert_wav_to_flac(
    data: bytes,
    *,
    title: str,
    artist: str,
    artwork: bytes | None,
) -> bytes:
    """Losslessly encode decoded PCM WAV data as tagged FLAC."""

    if len(data) < 12 or data[:4] not in {b"RIFF", b"RF64"} or data[8:12] != b"WAVE":
        raise AudioConversionError("FLAC変換元のWAVヘッダーを確認できません")
    ffmpeg = component_paths()["ffmpeg"]
    if not ffmpeg:
        raise ComponentMissingError("FLAC変換に必要なFFmpegが見つかりません")

    with tempfile.TemporaryDirectory(prefix="gakumas-flac-") as temp_dir:
        work = Path(temp_dir)
        source = work / "source.wav"
        output = work / "output.flac"
        source.write_bytes(data)
        _run_audio_tool(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-c:a",
                "flac",
                "-compression_level",
                "5",
                str(output),
            ],
            "FFmpeg",
            "FLAC変換",
        )
        flac = output.read_bytes() if output.exists() else b""

    if not flac.startswith(b"fLaC"):
        raise AudioConversionError("FFmpegのFLAC出力形式を確認できません")
    return write_flac_tags(flac, title=title, artist=artist, artwork=artwork)


def _decode_with_vgmstream(raw: bytes) -> list[bytes]:
    vgmstream = component_paths()["vgmstream"]
    if not vgmstream:
        raise ComponentMissingError("ライブWAV変換に必要なvgmstreamが見つかりません")

    with tempfile.TemporaryDirectory(prefix="gakumas-vgmstream-") as temp_dir:
        work = Path(temp_dir)
        source = work / "clip.fsb"
        source.write_bytes(raw)
        _run_audio_tool(
            [
                str(vgmstream),
                "-S",
                "0",
                "-i",
                "-o",
                str(work / "?s.wav"),
                str(source),
            ],
            "vgmstream",
        )
        outputs = [path.read_bytes() for path in sorted(work.glob("*.wav"))]

    if not outputs:
        raise AudioConversionError("vgmstreamのWAVデコード結果が空です")
    if any(data[:4] not in {b"RIFF", b"RF64"} for data in outputs):
        raise AudioConversionError("vgmstreamのWAV出力形式を確認できません")
    return outputs


def _decode_with_ffmpeg(raw: bytes, suffix: str) -> list[bytes]:
    ffmpeg = component_paths()["ffmpeg"]
    if not ffmpeg:
        raise ComponentMissingError("ライブWAV変換に必要なFFmpegが見つかりません")

    with tempfile.TemporaryDirectory(prefix="gakumas-ffmpeg-") as temp_dir:
        work = Path(temp_dir)
        source = work / f"clip{suffix}"
        output = work / "clip.wav"
        source.write_bytes(raw)
        _run_audio_tool(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            "FFmpeg",
        )
        data = output.read_bytes() if output.exists() else b""

    if data[:4] not in {b"RIFF", b"RF64"}:
        raise AudioConversionError("FFmpegのWAV出力形式を確認できません")
    return [data]


def _decode_unity_audio_blob(raw: bytes) -> list[bytes]:
    if len(raw) >= 12 and raw[:4] in {b"RIFF", b"RF64"} and raw[8:12] == b"WAVE":
        return [raw]
    if raw.startswith(b"OggS"):
        return _decode_with_ffmpeg(raw, ".ogg")
    if len(raw) >= 8 and raw[4:8] == b"ftyp":
        return _decode_with_ffmpeg(raw, ".m4a")
    return _decode_with_vgmstream(raw)


def _pack_wav_files(samples: list[bytes]) -> ConvertedAudio:
    if len(samples) == 1:
        return ConvertedAudio(samples[0], "audio/wav")
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, sample in enumerate(samples, start=1):
                info = zipfile.ZipInfo(f"sample_{index:02d}.wav", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, sample)
        return ConvertedAudio(buffer.getvalue(), "application/zip")


def _decode_unity_bundle_to_wav(prepared: bytes) -> ConvertedAudio:
    try:
        environment = UnityPy.load(prepared)
        readers = [
            reader
            for reader in environment.container.values()
            if getattr(getattr(reader, "type", None), "name", "") == "AudioClip"
        ]
        if not readers:
            readers = [
                reader
                for reader in environment.objects
                if getattr(getattr(reader, "type", None), "name", "") == "AudioClip"
            ]
        if not readers:
            raise AudioConversionError("ライブ音源AssetBundleにAudioClipがありません")

        samples: list[bytes] = []
        for reader in readers:
            clip = reader.read()
            raw_audio = bytes(getattr(clip, "m_AudioData", b""))
            if not raw_audio:
                raise AudioConversionError("ライブ音源AudioClipのデータが空です")
            samples.extend(_decode_unity_audio_blob(raw_audio))
        return _pack_wav_files(samples)
    except AudioConversionError:
        raise
    except Exception as exc:
        raise AudioConversionError(f"ライブ音源AudioClipの解析に失敗しました: {exc}") from exc


def decode_game_audio_to_wav(asset: AssetRef, raw: bytes) -> ConvertedAudio:
    """Decode AWB/ACB through GOM and Unity AudioClip data through vgmstream."""

    if asset.extension in {"awb", "acb"} and not component_paths()["vgmstream"]:
        raise ComponentMissingError("WAV変換に必要なvgmstreamが見つかりません")

    obj = asset.source_object
    prepared = raw
    if asset.object_type == "assetbundle" and not prepared.startswith(UNITY_SIGNATURE):
        prepared = GkmasAssetBundleDeobfuscator(obj._deobf_key).process(prepared)
        if not prepared.startswith(UNITY_SIGNATURE):
            raise AudioConversionError("ライブ音源AssetBundleの復号に失敗しました")

    if asset.object_type == "assetbundle":
        return _decode_unity_bundle_to_wav(prepared)

    obj._reporter = _ConversionReporter()
    obj._media = None
    media = obj.media
    media.downloader = lambda: {"bytes": prepared, "mtime": 0.0}
    try:
        data = media.get_data(audio_format="wav")
    except Exception as exc:
        raise AudioConversionError(f"ゲーム音源のWAVデコードに失敗しました: {exc}") from exc

    payload = data.get("bytes", b"")
    mimetype = str(data.get("mimetype", ""))
    if not payload:
        raise AudioConversionError("WAVデコード結果が空です")
    if mimetype != "application/zip" and not (
        payload.startswith(b"RIFF") or payload.startswith(b"RF64")
    ):
        raise AudioConversionError("WAVデコード結果の形式を確認できません")
    return ConvertedAudio(payload, mimetype)
