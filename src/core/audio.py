from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def write_wav_info_tags(data: bytes, *, title: str, artist: str) -> bytes:
    """Set only title/artist RIFF INFO fields and preserve every other WAV chunk/tag."""

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
    return bytes(result)


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


def decode_game_audio_to_wav(asset: AssetRef, raw: bytes) -> ConvertedAudio:
    """Decode an AWB/ACB or Unity live AudioClip through GOM's proven handlers."""

    if asset.extension in {"awb", "acb"} and not component_paths()["vgmstream"]:
        raise ComponentMissingError("WAV変換に必要なvgmstreamが見つかりません")

    obj = asset.source_object
    prepared = raw
    if asset.object_type == "assetbundle" and not prepared.startswith(UNITY_SIGNATURE):
        prepared = GkmasAssetBundleDeobfuscator(obj._deobf_key).process(prepared)
        if not prepared.startswith(UNITY_SIGNATURE):
            raise AudioConversionError("ライブ音源AssetBundleの復号に失敗しました")

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
