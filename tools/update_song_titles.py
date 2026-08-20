from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/vertesan/gakumasu-diff/main/Music.yaml"
)
GENERAL_ASSET_RE = re.compile(
    r"^sud_music_general_(?P<scope>[^-]+)-(?P<number>\d+)-"
    r"(?P<performer>[^_]+)_(?P<version>.+)$",
    re.IGNORECASE,
)


def _load_source(source: str) -> list[dict]:
    path = Path(source)
    if path.is_file():
        raw = path.read_bytes()
    else:
        request = Request(source, headers={"User-Agent": "GakumasMusicExtractor/1.0"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, list):
        raise ValueError("Music.yamlのルートが配列ではありません")
    return [item for item in parsed if isinstance(item, dict)]


def build_mapping(records: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in records:
        asset_id = str(record.get("gameVersionAssetId") or "").strip()
        title = str(record.get("displayTitle") or record.get("title") or "").strip()
        if not asset_id or not title:
            continue

        # Exact asset-base lookup is authoritative and distinguishes instrumental data.
        mapping[asset_id] = title

        # Live assets use the same scope/number/performer but a different base name.
        # Add a generic vocal key so their title is visible before extraction as well.
        match = GENERAL_ASSET_RE.match(asset_id)
        if match and "inst" not in match.group("version").casefold().split("-"):
            data = match.groupdict()
            mapping[f"{data['scope'].casefold()}-{data['number']}/{data['performer'].casefold()}"] = title

    return dict(sorted(mapping.items(), key=lambda item: item[0].casefold()))


def main() -> int:
    parser = argparse.ArgumentParser(description="学マスMusicマスターから曲名マッピングを生成")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Music.yamlのパスまたはURL")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "config" / "song_names.json"),
        help="生成先song_names.json",
    )
    args = parser.parse_args()

    records = _load_source(args.source)
    mapping = build_mapping(records)
    if not mapping:
        raise RuntimeError("曲名マッピングを1件も生成できませんでした")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"records={len(records)} mappings={len(mapping)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
