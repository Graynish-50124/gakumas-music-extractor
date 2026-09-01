from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.song_metadata import base_title_key, metadata_key  # noqa: E402


DISCOGRAPHY_URL = "https://gakuen-label.idolmaster-official.jp/discography"
DETAIL_URL = DISCOGRAPHY_URL + "/{}"
SOUNDTRACK_URL = "https://gamemusic.bn-ent.net/release/680/"
ROLE_LABELS = (
    "作詞・作曲・編曲|作詞／作曲／編曲|作詞/作曲/編曲|作詞作曲編曲|"
    "作詞・作曲|作曲・編曲|作詞・編曲|作曲／編曲|作詞作曲|歌唱|歌|作詞|作曲|編曲"
)
ROLE_RE = re.compile(
    rf"({ROLE_LABELS})\s*[:：]\s*(.*?)(?=(?:\s|\u3000)*(?:{ROLE_LABELS})\s*[:：]|$)"
)
SOLO_SUFFIX_RE = re.compile(
    r"\s*[\[［][^\]］]*?solo\s*ver\.?[\]］]\s*$",
    re.IGNORECASE,
)


def _fetch(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "GakumasMusicExtractor metadata catalog updater"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _discography_records(document: str) -> list[dict[str, Any]]:
    chunks = [
        json.loads(f'"{match.group(1)}"')
        for match in re.finditer(
            r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)</script>',
            document,
        )
    ]
    payload = "".join(chunks)
    marker = '"contents":'
    position = payload.find(marker)
    if position < 0:
        raise RuntimeError("公式ディスコグラフィの楽曲一覧を確認できません")
    value, _end = json.JSONDecoder().raw_decode(payload[position + len(marker) :])
    if not isinstance(value, list):
        raise RuntimeError("公式ディスコグラフィのデータ形式が変わりました")
    return [record for record in value if isinstance(record, dict)]


def _source_title_key(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value)))
    value = unicodedata.normalize("NFKC", value).strip()
    value = SOLO_SUFFIX_RE.sub("", value)
    return metadata_key(value)


def _parse_credits(value: Any) -> dict[str, str]:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text).replace("\n", "\u3000")
    result: dict[str, str] = {}
    for match in ROLE_RE.finditer(text):
        label, credit = match.group(1), match.group(2).strip()
        roles: list[str] = []
        if label in {"歌", "歌唱"}:
            roles.append("performer")
        if "作詞" in label:
            roles.append("lyricist")
        if "作曲" in label or "作詞作曲" in label:
            roles.append("composer")
        if "編曲" in label:
            roles.append("arranger")
        for role in roles:
            if credit:
                result[role] = credit
    return result


def _release_date(value: Any) -> str:
    if not value:
        return ""
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (instant + timedelta(hours=9)).date().isoformat()


def _add_soundtrack_release(
    songs: dict[str, dict[str, str]],
    known_titles: dict[str, str],
    document: str,
) -> None:
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", document, flags=re.IGNORECASE | re.DOTALL)
    release_title = (
        html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        if title_match
        else "学園アイドルマスター オリジナルサウンドトラック"
    )
    date_match = re.search(
        r"発売日:\s*</dt>\s*<dd[^>]*>(\d{4})年(\d{1,2})月(\d{1,2})日</dd>",
        document,
        flags=re.IGNORECASE,
    )
    release_date = (
        f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        if date_match
        else ""
    )
    tracks = re.findall(
        r'class="release_track_list_item_name"[^>]*>(.*?)</span>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for index, raw_title in enumerate(tracks, start=1):
        source_title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        title = known_titles.get(metadata_key(source_title))
        if not title:
            continue
        metadata = songs.setdefault(title, {})
        metadata.setdefault("album", release_title)
        if release_date:
            metadata.setdefault("release_date", release_date)
        metadata.setdefault("track_number", str(index))
        metadata.setdefault("source_url", SOUNDTRACK_URL)


def build_catalog(
    records: list[dict[str, Any]],
    song_names: dict[str, str],
    soundtrack_document: str = "",
) -> dict[str, Any]:
    known_titles: dict[str, str] = {}
    for title in sorted(set(song_names.values()), key=lambda item: (len(item), item)):
        known_titles.setdefault(metadata_key(title), title)

    songs: dict[str, dict[str, str]] = {}
    credit_scores: dict[str, int] = {}
    releases: dict[str, tuple[str, str, str, str]] = {}

    for record in records:
        categories = record.get("category") or []
        category = str(categories[0]) if categories else ""
        credits = _parse_credits(record.get("artist"))
        date = _release_date(record.get("release_date"))
        record_id = str(record.get("id") or "")
        detail_url = DETAIL_URL.format(record_id)
        if category == "Streaming&DL":
            tracks = [(str(record.get("title") or ""), "")]
        else:
            tracks = [
                (str(track.get("name") or ""), str(track.get("index") or ""))
                for track in record.get("tracks") or []
                if isinstance(track, dict)
            ]

        for track_title, track_number in tracks:
            title = known_titles.get(_source_title_key(track_title))
            if not title:
                continue
            metadata = songs.setdefault(title, {})
            credit_score = 3 if category == "Streaming&DL" else (2 if credits else 0)
            if credit_score >= credit_scores.get(title, -1):
                metadata.update(credits)
                if credits:
                    metadata["credit_source_url"] = detail_url
                credit_scores[title] = credit_score

            candidate = (date, str(record.get("title") or ""), track_number, record_id)
            if date and (title not in releases or date < releases[title][0]):
                releases[title] = candidate

    for title, (date, album, track_number, record_id) in releases.items():
        metadata = songs[title]
        metadata["album"] = album
        metadata["release_date"] = date
        if track_number:
            metadata["track_number"] = track_number
        metadata["source_url"] = DETAIL_URL.format(record_id)

    if soundtrack_document:
        _add_soundtrack_release(songs, known_titles, soundtrack_document)

    base_credits = {
        base_title_key(title): metadata
        for title, metadata in songs.items()
        if metadata_key(title) == base_title_key(title)
    }
    for title, metadata in songs.items():
        base = base_credits.get(base_title_key(title), {})
        for field in ("lyricist", "composer", "arranger", "credit_source_url"):
            if not metadata.get(field) and base.get(field):
                metadata[field] = base[field]

    verified_at = datetime.now().date().isoformat()
    for metadata in songs.values():
        metadata["verified_at"] = verified_at

    return {
        "_meta": {
            "schema": 1,
            "generated_at": verified_at,
            "sources": [DISCOGRAPHY_URL, SOUNDTRACK_URL],
            "policy": "公式レーベル掲載値のみ。未確認項目は空欄。",
        },
        "songs": dict(sorted(songs.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="公式ディスコグラフィから楽曲情報を更新")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "config" / "official_song_metadata.json",
    )
    args = parser.parse_args()
    song_names = json.loads(
        (PROJECT_ROOT / "config" / "song_names.json").read_text(encoding="utf-8")
    )
    catalog = build_catalog(
        _discography_records(_fetch(DISCOGRAPHY_URL)),
        song_names,
        _fetch(SOUNDTRACK_URL),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{len(catalog['songs'])}曲の正式情報を {args.output} へ保存しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
