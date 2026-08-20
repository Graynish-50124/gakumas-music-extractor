from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path

from app_bootstrap import component_paths, configure_runtime


configure_runtime()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gakumas Music Extractor")
    parser.add_argument("--scan-only", action="store_true", help="GUIを開かずPhase 1スキャンを実行")
    parser.add_argument("--manifest", help="手動octocacheevaiパス")
    parser.add_argument("--game-data-dir", help="学マスのoctoフォルダまたは親フォルダ")
    parser.add_argument("--online", action="store_true", help="オンラインPC版Manifestを使用")
    parser.add_argument("--limit", type=int, default=30, help="コンソール表示件数")
    parser.add_argument("--self-test", action="store_true", help="同梱ランタイムとコンポーネントを診断")
    parser.add_argument("--acceptance-test", action="store_true", help="実データで指定曲のAWB/WAV/ライブ抽出を診断")
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

        result["PySide6"] = PySide6.__version__
        result["GkmasObjectManager"] = str(Path(GkmasObjectManager.__file__).resolve())
        result["cryptography"] = cryptography.__version__
        result["protobuf"] = google.protobuf.__version__
    except Exception as exc:
        errors.append(f"Pythonモジュール: {exc}")

    paths = component_paths()
    result["components"] = {name: str(path) if path else None for name, path in paths.items()}
    for name, path in paths.items():
        if not path or not path.is_file():
            errors.append(f"{name} が見つかりません")
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

    from core.audio import read_wav_info_tags
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
            save_awb=True,
            save_mp3=False,
            save_acb=False,
            include_live=True,
        ),
    )
    general_awb = output / f"{general.title}＿姫崎莉波.awb"
    general_wav = output / f"{general.title}＿姫崎莉波.wav"
    live_wav = output / f"{live.title}＿姫崎莉波［ライブ］.wav"
    general_wav_data = general_wav.read_bytes() if general_wav.exists() else b""
    live_wav_data = live_wav.read_bytes() if live_wav.exists() else b""
    general_tags = read_wav_info_tags(general_wav_data) if general_wav_data else {}
    live_tags = read_wav_info_tags(live_wav_data) if live_wav_data else {}
    checks = {
        "general_awb": general_awb.exists() and general_awb.read_bytes()[:4] == b"AFS2",
        "general_wav": general_wav.exists() and general_wav.read_bytes()[:4] in {b"RIFF", b"RF64"},
        "live_true_wav": live_wav.exists() and live_wav.read_bytes()[:4] in {b"RIFF", b"RF64"},
        "general_wav_title": general_tags.get("INAM") == general.title,
        "general_wav_artist": general_tags.get("IART") == "姫崎莉波",
        "general_wav_artist_cp932": (
            "姫崎莉波".encode("cp932") in general_wav_data
            and "姫崎莉波".encode("utf-8") not in general_wav_data
        ),
        "live_wav_title": live_tags.get("INAM") == live.title,
        "live_wav_artist": live_tags.get("IART") == "姫崎莉波",
        "no_album_tag": "IPRD" not in general_tags and "IPRD" not in live_tags,
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
