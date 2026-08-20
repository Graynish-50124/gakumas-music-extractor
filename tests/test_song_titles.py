from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bundled_song_catalog_contains_current_official_titles() -> None:
    mapping = json.loads(
        (PROJECT_ROOT / "config" / "song_names.json").read_text(encoding="utf-8")
    )
    assert len(mapping) >= 600
    assert mapping["sud_music_general_all-018-hrnm_game"] == "「ねえ、言っちゃうよ。」"
    assert mapping["all-018/hrnm"] == "「ねえ、言っちゃうよ。」"
    assert mapping["sud_music_general_unit-004-amaohrnm_game"] == "SUGAR FLAVOR"
