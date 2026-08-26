from __future__ import annotations

import argparse
import io
import json
import os
import sys
import traceback
import wave
from collections import Counter
from pathlib import Path

from app_bootstrap import bundle_root, component_paths, configure_runtime


configure_runtime()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gakumas Music Extractor")
    parser.add_argument("--scan-only", action="store_true", help="GUIを開かずPhase 1スキャンを実行")
    parser.add_argument("--manifest", help="手動octocacheevaiパス")
    parser.add_argument("--game-data-dir", help="学マスのoctoフォルダまたは親フォルダ")
    parser.add_argument("--online", action="store_true", help="オンラインPC版Manifestを使用")
    parser.add_argument("--limit", type=int, default=30, help="コンソール表示件数")
    parser.add_argument("--self-test", action="store_true", help="同梱ランタイムとコンポーネントを診断")
    parser.add_argument(
        "--acceptance-test",
        action="store_true",
        help="実データで指定曲のAWB/WAV/FLAC/ライブ抽出を診断",
    )
    parser.add_argument("--output", help="受け入れテストの出力先")
    parser.add_argument("--report", help="診断/スキャン結果をJSONファイルにも保存")
    return parser


def _emit_report(payload: dict, path: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if sys.stdout:
        print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")


def run_self_test(report_path: str | None) -> int:
    result: dict[str, object] = {
        "python": sys.version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
    }
    errors: list[str] = []
    try:
        import PySide6
        import GkmasObjectManager
        import cryptography
        import google.protobuf
        import mutagen

        result["PySide6"] = PySide6.__version__
        result["GkmasObjectManager"] = str(Path(GkmasObjectManager.__file__).resolve())
        result["cryptography"] = cryptography.__version__
        result["protobuf"] = google.protobuf.__version__
        result["mutagen"] = mutagen.version_string
    except Exception as exc:
        errors.append(f"Pythonモジュール: {exc}")

    paths = component_paths()
    result["components"] = {name: str(path) if path else None for name, path in paths.items()}
    for name, path in paths.items():
        if not path or not path.is_file():
            errors.append(f"{name} が見つかりません")
    bundled_fmod: list[str] = []
    if getattr(sys, "frozen", False):
        runtime_root = bundle_root()
        for path in runtime_root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.casefold()
            is_fmod_runtime = name == "fmod.dll" or (
                name.startswith("libfmod")
                and path.suffix.casefold() in {".so", ".dylib"}
            )
            if is_fmod_runtime:
                bundled_fmod.append(str(path.relative_to(runtime_root)))
    result["bundled_fmod_runtime_files"] = bundled_fmod
    if bundled_fmod:
        errors.append("配布物にFMODランタイムが含まれています")
    result["errors"] = errors
    result["ok"] = not errors
    _emit_report(result, report_path)
    return 0 if not errors else 2


def run_scan(args: argparse.Namespace) -> int:
    from core.config import ConfigStore
    from core.manifest import load_preferred_manifest
    from core.scanner import scan_music_assets

    store = ConfigStore()
    store.ensure_mapping_files()
    manifest, info = load_preferred_manifest(
        octo_root=args.game_data_dir or None,
        manifest_path=args.manifest or None,
        mode="online" if args.online else "local_preferred",
        online_fallback=False if args.manifest else True,
    )
    groups = scan_music_assets(manifest, store.load_mapping("song_names.json"))
    kinds = Counter(group.data_type for group in groups)
    hrnm_018 = [
        group
        for group in groups
        if group.internal_id == "all-018" and group.character_id == "hrnm"
        and group.data_type != "ライブ"
    ]
    live_018 = [
        group
        for group in groups
        if group.internal_id == "all-018"
        and group.character_id == "hrnm"
        and group.data_type == "ライブ"
    ]
    payload = {
        "source": info.source,
        "revision": info.revision,
        "updated_at": info.updated_at.isoformat() if info.updated_at else None,
        "manifest_path": str(info.manifest_path) if info.manifest_path else None,
        "object_count": info.object_count,
        "music_group_count": len(groups),
        "kinds": dict(kinds),
        "acceptance": {
            "all_018_hrnm": [
                {
                    "key": group.key,
                    "singing": group.singing,
                    "formats": sorted(group.assets),
                    "artwork": group.artwork.name if group.artwork else None,
                }
                for group in hrnm_018
            ],
            "live_true_001": any(group.version == "true-001" for group in live_018),
        },
        "sample": [
            {
                "id": group.internal_id,
                "title": group.title or None,
                "character": group.character_id,
                "type": group.data_type,
                "singing": group.singing,
                "version": group.version,
                "short_version": group.is_short_version,
                "formats": sorted(group.assets),
                "artwork": group.artwork.name if group.artwork else None,
            }
            for group in groups[: max(args.limit, 0)]
        ],
    }
    _emit_report(payload, args.report)
    accepted = bool(hrnm_018) and payload["acceptance"]["live_true_001"]
    return 0 if accepted else 3


def run_acceptance_test(args: argparse.Namespace) -> int:
    if not args.output:
        _emit_report({"ok": False, "errors": ["--output が必要です"]}, args.report)
        return 4

    from mutagen.flac import FLAC
    from mutagen.id3 import ID3

    from core.audio import (
        read_flac_embedded_artwork,
        read_flac_tags,
        read_mp3_embedded_artwork,
        read_wav_embedded_artwork,
        read_wav_info_tags,
    )
    from core.config import ConfigStore
    from core.extractor import ExtractionEngine
    from core.manifest import load_preferred_manifest
    from core.models import ExtractionOptions
    from core.scanner import scan_music_assets

    manifest, info = load_preferred_manifest(
        octo_root=args.game_data_dir or None,
        manifest_path=args.manifest or None,
        mode="local_preferred",
        online_fallback=False,
    )
    groups = scan_music_assets(manifest, ConfigStore().load_mapping("song_names.json"))
    general = next(
        group
        for group in groups
        if group.internal_id == "all-018"
        and group.character_id == "hrnm"
        and group.data_type == "通常楽曲"
        and group.singing == "歌入り"
    )
    live = next(
        group
        for group in groups
        if group.internal_id == "all-018"
        and group.character_id == "hrnm"
        and group.data_type == "ライブ"
        and group.version == "true-001"
    )
    output = Path(args.output).expanduser().resolve()
    engine = ExtractionEngine(info.octo_root, {"hrnm": "姫崎莉波"})
    written = engine.extract(
        [general],
        groups,
        ExtractionOptions(
            output_dir=output,
            save_wav=True,
            save_flac=True,
            save_awb=True,
            save_mp3=True,
            save_acb=False,
            save_artwork=True,
            include_live=True,
        ),
    )
    general_awb = output / f"{general.title}＿姫崎莉波.awb"
    general_wav = output / f"{general.title}＿姫崎莉波.wav"
    general_flac = output / f"{general.title}＿姫崎莉波.flac"
    general_mp3 = output / f"{general.title}＿姫崎莉波.mp3"
    live_wav = output / f"{live.title}＿姫崎莉波［ライブ］.wav"
    live_flac = output / f"{live.title}＿姫崎莉波［ライブ］.flac"
    general_artwork = output / f"{general.title}＿姫崎莉波.png"
    live_artwork = output / f"{live.title}＿姫崎莉波［ライブ］.png"
    general_wav_data = general_wav.read_bytes() if general_wav.exists() else b""
    general_flac_data = general_flac.read_bytes() if general_flac.exists() else b""
    general_mp3_data = general_mp3.read_bytes() if general_mp3.exists() else b""
    live_wav_data = live_wav.read_bytes() if live_wav.exists() else b""
    live_flac_data = live_flac.read_bytes() if live_flac.exists() else b""
    general_tags = read_wav_info_tags(general_wav_data) if general_wav_data else {}
    live_tags = read_wav_info_tags(live_wav_data) if live_wav_data else {}
    general_flac_tags = read_flac_tags(general_flac_data) if general_flac_data else {}
    live_flac_tags = read_flac_tags(live_flac_data) if live_flac_data else {}
    general_embedded_artwork = (
        read_wav_embedded_artwork(general_wav_data) if general_wav_data else None
    )
    live_embedded_artwork = (
        read_wav_embedded_artwork(live_wav_data) if live_wav_data else None
    )
    general_artwork_data = general_artwork.read_bytes() if general_artwork.exists() else b""
    live_artwork_data = live_artwork.read_bytes() if live_artwork.exists() else b""
    general_mp3_tags = ID3(general_mp3) if general_mp3.exists() else None
    general_flac_file = FLAC(general_flac) if general_flac.exists() else None
    live_flac_file = FLAC(live_flac) if live_flac.exists() else None
    with wave.open(io.BytesIO(general_wav_data)) as stream:
        general_wav_pcm = (stream.getframerate(), stream.getnchannels(), stream.getnframes())
    with wave.open(io.BytesIO(live_wav_data)) as stream:
        live_wav_pcm = (stream.getframerate(), stream.getnchannels(), stream.getnframes())
    checks = {
        "general_awb": general_awb.exists() and general_awb.read_bytes()[:4] == b"AFS2",
        "general_wav": general_wav.exists() and general_wav.read_bytes()[:4] in {b"RIFF", b"RF64"},
        "general_flac": general_flac_data.startswith(b"fLaC"),
        "general_mp3": general_mp3_data.startswith(b"ID3"),
        "live_true_wav": live_wav.exists() and live_wav.read_bytes()[:4] in {b"RIFF", b"RF64"},
        "live_true_flac": live_flac_data.startswith(b"fLaC"),
        "general_wav_title": general_tags.get("INAM") == general.title,
        "general_wav_artist": general_tags.get("IART") == "姫崎莉波",
        "general_wav_artist_cp932": (
            "姫崎莉波".encode("cp932") in general_wav_data
            and "姫崎莉波".encode("utf-8") not in general_wav_data
        ),
        "live_wav_title": live_tags.get("INAM") == live.title,
        "live_wav_artist": live_tags.get("IART") == "姫崎莉波",
        "general_flac_title": general_flac_tags.get("title") == general.title,
        "general_flac_artist": general_flac_tags.get("artist") == "姫崎莉波",
        "live_flac_title": live_flac_tags.get("title") == live.title,
        "live_flac_artist": live_flac_tags.get("artist") == "姫崎莉波",
        "general_artwork_png": general_artwork_data.startswith(b"\x89PNG\r\n\x1a\n"),
        "live_artwork_png": live_artwork_data.startswith(b"\x89PNG\r\n\x1a\n"),
        "general_wav_embedded_artwork": general_embedded_artwork == general_artwork_data,
        "general_flac_embedded_artwork": (
            read_flac_embedded_artwork(general_flac_data) == general_artwork_data
        ),
        "general_mp3_embedded_artwork": (
            read_mp3_embedded_artwork(general_mp3_data) == general_artwork_data
        ),
        "live_wav_embedded_artwork": live_embedded_artwork == live_artwork_data,
        "live_flac_embedded_artwork": (
            read_flac_embedded_artwork(live_flac_data) == live_artwork_data
        ),
        "general_flac_pcm": (
            general_flac_file is not None
            and (
                general_flac_file.info.sample_rate,
                general_flac_file.info.channels,
                general_flac_file.info.total_samples,
            ) == general_wav_pcm
        ),
        "live_flac_pcm": (
            live_flac_file is not None
            and (
                live_flac_file.info.sample_rate,
                live_flac_file.info.channels,
                live_flac_file.info.total_samples,
            ) == live_wav_pcm
        ),
        "no_album_tag": "IPRD" not in general_tags and "IPRD" not in live_tags,
        "no_mp3_album_tag": general_mp3_tags is not None and not general_mp3_tags.getall("TALB"),
        "no_flac_album_tag": "album" not in general_flac_tags and "album" not in live_flac_tags,
    }
    payload = {
        "ok": all(checks.values()),
        "source": info.source,
        "revision": info.revision,
        "checks": checks,
        "written": [str(path) for path in written],
    }
    _emit_report(payload, args.report)
    return 0 if payload["ok"] else 5


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from core.config import ConfigStore
    from gui.main_window import MainWindow

    QApplication.setOrganizationName("Personal")
    QApplication.setApplicationName("GakumasMusicExtractor")
    app = QApplication(sys.argv)
    store = ConfigStore()
    settings = store.load_settings()
    window = MainWindow(settings, store)
    window.show()
    return app.exec()


def main() -> int:
    args = build_parser().parse_args()
    cli_mode = args.self_test or args.acceptance_test or args.scan_only
    if cli_mode:
        try:
            if args.self_test:
                return run_self_test(args.report)
            if args.acceptance_test:
                return run_acceptance_test(args)
            return run_scan(args)
        except Exception as exc:
            _emit_report(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
                args.report,
            )
            return 10
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
