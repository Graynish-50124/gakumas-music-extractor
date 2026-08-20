from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import zipfile
from pathlib import Path

import requests

from .audio import decode_game_audio_to_wav, write_wav_info_tags
from .models import (
    FILENAME_ORIGINAL,
    FILENAME_TITLE,
    FILENAME_TITLE_CHARACTER,
    VALID_FILENAME_FORMATS,
    KIND_LIVE,
    SINGING_INST,
    AssetRef,
    ExtractionOptions,
    LogCallback,
    ProgressCallback,
    SongGroup,
)


class ExtractionError(RuntimeError):
    pass


class ExtractionCancelled(ExtractionError):
    pass


class OctoCacheResolver:
    def __init__(self, octo_root: Path | None):
        self.octo_root = octo_root

    def find(self, asset: AssetRef) -> Path | None:
        if not self.octo_root or not self.octo_root.is_dir():
            return None
        prefix = "A" if asset.object_type == "assetbundle" else "R"
        encoded_id = f"{prefix}{asset.object_id}".encode("ascii").hex()
        shard = str(asset.object_id % 10)
        for version_root in sorted(self.octo_root.glob("v*"), reverse=True):
            folder = version_root / "400" / shard / encoded_id
            if not folder.is_dir():
                continue
            for candidate in folder.glob(f"{asset.md5}*"):
                if candidate.is_file() and candidate.name != ".meta" and candidate.stat().st_size == asset.size:
                    return candidate
        return None


def _safe_filename(value: str) -> str:
    value = value.translate(
        str.maketrans(
            {
                "<": "＜",
                ">": "＞",
                ':': "：",
                '"': "”",
                "/": "／",
                "\\": "＼",
                "|": "｜",
                "?": "？",
                "*": "＊",
            }
        )
    )
    value = re.sub(r'[\x00-\x1f]', "_", value).strip().rstrip(". ")
    if not value:
        value = "unnamed"
    if value.casefold() in {
        "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5",
        "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4",
        "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }:
        value = f"_{value}"
    return value[:180]


class ExtractionEngine:
    def __init__(
        self,
        octo_root: Path | None,
        characters: dict[str, str] | None = None,
        progress: ProgressCallback | None = None,
        log: LogCallback | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.octo_root = octo_root
        self.cache = OctoCacheResolver(octo_root)
        self.characters = characters or {}
        self.progress = progress or (lambda percent, message: None)
        self.log = log or (lambda message: None)
        self.cancel_event = cancel_event or threading.Event()
        self.session = requests.Session()
        self._claimed_filenames: dict[str, str] = {}

    def extract(
        self,
        selected: list[SongGroup],
        catalog: list[SongGroup],
        options: ExtractionOptions,
    ) -> list[Path]:
        groups = self._expand_live(selected, catalog) if options.include_live else list(selected)
        groups = list({group.key: group for group in groups}.values())
        self._claimed_filenames.clear()
        self._validate_output(options.output_dir)
        options.output_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        total = max(len(groups), 1)
        for index, group in enumerate(groups):
            self._check_cancel()
            display = self._display_name(group)
            self.progress(int(index / total * 100), f"{display} を処理中...")
            self.log(f"{display} の抽出を開始")
            raw_cache: dict[str, bytes] = {}

            if options.save_awb and "AWB" in group.assets:
                written.extend(self._save_raw(group, "AWB", options, raw_cache, index, total))
            if options.save_mp3 and "MP3" in group.assets:
                written.extend(self._save_raw(group, "MP3", options, raw_cache, index, total))
            if options.save_acb and "ACB" in group.assets:
                written.extend(self._save_raw(group, "ACB", options, raw_cache, index, total))
            if options.save_wav:
                source_key = next(
                    (key for key in ("AWB", "LIVE", "ACB") if key in group.assets),
                    None,
                )
                if source_key:
                    asset = group.assets[source_key]
                    raw = raw_cache.get(source_key)
                    if raw is None:
                        raw = self._obtain(asset, index, total)
                        raw_cache[source_key] = raw
                    self.progress(
                        int((index + 0.75) / total * 100),
                        f"{display}: WAVへデコード中...",
                    )
                    converted = decode_game_audio_to_wav(asset, raw)
                    written.extend(self._save_converted(group, asset, converted.data, converted.mimetype, options))
                    self.log(f"{display} WAV変換成功")
                elif group.data_type == KIND_LIVE:
                    self.log(f"{display}: WAV変換可能なライブ音源がありません")

            self.progress(int((index + 1) / total * 100), f"{display} 完了")

        self.progress(100, f"抽出完了: {len(written)}ファイル")
        return written

    def _expand_live(self, selected: list[SongGroup], catalog: list[SongGroup]) -> list[SongGroup]:
        by_key = {group.key: group for group in catalog}
        result = list(selected)
        for group in selected:
            result.extend(by_key[key] for key in group.related_live_keys if key in by_key)
        return result

    def _validate_output(self, output_dir: Path) -> None:
        output = output_dir.expanduser().resolve()
        if self.octo_root:
            game_root = self.octo_root.expanduser().resolve()
            if output == game_root or output.is_relative_to(game_root):
                raise ExtractionError("ゲームデータフォルダ内は出力先に指定できません")

    def _save_raw(
        self,
        group: SongGroup,
        format_key: str,
        options: ExtractionOptions,
        raw_cache: dict[str, bytes],
        index: int,
        total: int,
    ) -> list[Path]:
        asset = group.assets[format_key]
        raw = raw_cache.get(format_key)
        if raw is None:
            raw = self._obtain(asset, index, total)
            raw_cache[format_key] = raw
        filename = self._output_filename(group, asset, asset.extension, options.filename_format)
        target = options.output_dir / filename
        if self._write_new(target, raw):
            self.log(f"{asset.name} を保存")
            return [target]
        self.log(f"既存ファイルをスキップ: {target.name}")
        return []

    def _save_converted(
        self,
        group: SongGroup,
        source: AssetRef,
        data: bytes,
        mimetype: str,
        options: ExtractionOptions,
    ) -> list[Path]:
        title = group.title or group.internal_id
        artist = self._character_name(group)
        if mimetype != "application/zip":
            filename = self._output_filename(group, source, "wav", options.filename_format)
            target = options.output_dir / filename
            tagged = write_wav_info_tags(data, title=title, artist=artist)
            return [target] if self._write_new(target, tagged) else []

        written: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for counter, member in enumerate(archive.infolist(), start=1):
                if member.is_dir():
                    continue
                suffix = Path(member.filename).suffix or ".wav"
                stem = Path(self._output_filename(group, source, "wav", options.filename_format)).stem
                target = options.output_dir / f"{stem}_{counter:02d}{suffix}"
                tagged = write_wav_info_tags(archive.read(member), title=title, artist=artist)
                if self._write_new(target, tagged):
                    written.append(target)
        return written

    def _obtain(self, asset: AssetRef, index: int, total: int) -> bytes:
        self._check_cancel()
        cached = self.cache.find(asset)
        if cached:
            self.progress(int((index + 0.25) / total * 100), f"{asset.name}: ローカルキャッシュを確認中...")
            data = cached.read_bytes()
            self._verify(asset, data)
            self.log(f"ローカルOctoキャッシュを使用: {asset.name}")
            return data

        self.log(f"キャッシュ未検出、ゲームサーバーから取得: {asset.name}")
        try:
            with self.session.get(asset.url, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in response.iter_content(256 * 1024):
                    self._check_cancel()
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    fraction = min(downloaded / max(asset.size, 1), 1.0)
                    percent = int((index + 0.1 + fraction * 0.45) / total * 100)
                    self.progress(percent, f"{asset.name}: 取得中 {fraction:.0%}")
                data = b"".join(chunks)
        except requests.RequestException as exc:
            raise ExtractionError(f"{asset.name} の取得に失敗しました: {exc}") from exc
        self._verify(asset, data)
        return data

    @staticmethod
    def _verify(asset: AssetRef, data: bytes) -> None:
        if len(data) != asset.size:
            raise ExtractionError(
                f"{asset.name} のサイズが一致しません（期待 {asset.size:,} / 実際 {len(data):,}）"
            )
        if asset.md5 and hashlib.md5(data).hexdigest().casefold() != asset.md5:
            raise ExtractionError(f"{asset.name} のMD5確認に失敗しました")

    @staticmethod
    def _write_new(path: Path, data: bytes) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        path.write_bytes(data)
        return True

    def _display_name(self, group: SongGroup) -> str:
        return f"{self._character_name(group)} / {group.title or group.internal_id}"

    def _character_name(self, group: SongGroup) -> str:
        character_id = group.character_id
        if not character_id:
            return "共通"
        direct = self.characters.get(character_id)
        if direct:
            return direct
        if len(character_id) % 4 == 0:
            members = [character_id[index : index + 4] for index in range(0, len(character_id), 4)]
            names = [self.characters.get(member) for member in members]
            if all(names):
                return "・".join(str(name) for name in names)
        return character_id

    def _output_filename(
        self,
        group: SongGroup,
        asset: AssetRef,
        extension: str,
        filename_format: str,
    ) -> str:
        if filename_format not in VALID_FILENAME_FORMATS:
            filename_format = FILENAME_TITLE_CHARACTER
        if filename_format == FILENAME_ORIGINAL:
            stem = Path(asset.name).stem
        else:
            title, variant = self._filename_title_and_variant(group)
            if filename_format == FILENAME_TITLE_CHARACTER:
                stem = f"{title}＿{self._character_name(group)}{variant}"
            else:
                stem = f"{title}{variant}"
        return self._claim_filename(stem, extension, group, filename_format)

    @staticmethod
    def _filename_title_and_variant(group: SongGroup) -> tuple[str, str]:
        title = group.title or group.internal_id
        labels: list[str] = []
        if group.singing == SINGING_INST:
            title = re.sub(
                r"\s*[\[［](?:instrumental|インスト)[\]］]\s*$",
                "",
                title,
                flags=re.IGNORECASE,
            )
        if group.data_type == KIND_LIVE:
            labels.append("ライブ")
            if group.is_short_version:
                labels.append("短縮版")
        if group.singing == SINGING_INST:
            labels.append("インスト")
        variant = f"［{'・'.join(labels)}］" if labels else ""
        return title or group.internal_id, variant

    def _claim_filename(
        self,
        stem: str,
        extension: str,
        group: SongGroup,
        filename_format: str,
    ) -> str:
        extension = extension.casefold()
        candidate = f"{_safe_filename(stem)}.{extension}"
        key = candidate.casefold()
        owner = self._claimed_filenames.get(key)
        if owner in {None, group.key}:
            self._claimed_filenames[key] = group.key
            return candidate

        if group.data_type == KIND_LIVE and group.version:
            discriminator = group.version
        elif filename_format == FILENAME_TITLE:
            discriminator = self._character_name(group)
        else:
            discriminator = group.internal_id
        collision_stem = f"{stem}＿{discriminator}"
        counter = 1
        while True:
            suffix = "" if counter == 1 else f"＿{counter}"
            candidate = f"{_safe_filename(collision_stem + suffix)}.{extension}"
            key = candidate.casefold()
            owner = self._claimed_filenames.get(key)
            if owner in {None, group.key}:
                self._claimed_filenames[key] = group.key
                return candidate
            counter += 1

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise ExtractionCancelled("ユーザー操作によりキャンセルしました")
