from __future__ import annotations

import json
from pathlib import Path

from core.config import ConfigStore
from core.song_metadata import metadata_key


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bundled_song_catalog_contains_current_official_titles() -> None:
    mapping = json.loads(
        (PROJECT_ROOT / "config" / "song_names.json").read_text(encoding="utf-8")
    )
    assert len(mapping) >= 600
    assert mapping["sud_music_general_all-018-hrnm_game"] == "「ねえ、言っちゃうよ。」"
    assert mapping["all-018/hrnm"] == "「ねえ、言っちゃうよ。」"
    assert mapping["sud_music_general_unit-004-amaohrnm_game"] == "SUGAR FLAVOR"


def test_bundled_official_metadata_catalog_contains_verified_credits() -> None:
    catalog = ConfigStore(PROJECT_ROOT / "work" / "test-metadata-config").load_song_metadata()
    assert len(catalog) >= 140
    campus = catalog[metadata_key("Campus mode!!")]
    assert campus.lyricist == "田淵智也"
    assert campus.composer == "田淵智也"
    assert campus.arranger == "滝澤俊輔（TRYTONELABO）"
    assert campus.album == "Campus mode!!"
    assert campus.release_date == "2024-06-10"
    assert campus.source_url.startswith(
        "https://gakuen-label.idolmaster-official.jp/discography/"
    )
    sakura = catalog[metadata_key("桜フォトグラフ")]
    assert sakura.lyricist == "Safari Natsukawa"
    assert sakura.composer == "春川仁志(sixth floor)"
