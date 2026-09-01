from __future__ import annotations

import json

from core.config import ConfigStore
from core.song_metadata import metadata_key


def test_user_metadata_overrides_only_supplied_official_fields(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.ensure_mapping_files()
    (tmp_path / "song_metadata.json").write_text(
        json.dumps(
            {
                "Campus mode!!": {
                    "album": "ユーザー指定の収録作品",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metadata = store.load_song_metadata()[metadata_key("Campus mode!!")]
    assert metadata.album == "ユーザー指定の収録作品"
    assert metadata.composer == "田淵智也"
    assert metadata.lyricist == "田淵智也"
