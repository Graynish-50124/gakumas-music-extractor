# Third-Party Notices

Gakumas Music Extractorのスタンドアロン配布物には、次の第三者コンポーネントが含まれます。各コンポーネントの著作権はそれぞれの権利者に帰属します。

| コンポーネント | 主なライセンス | 用途 / 参照先 |
|---|---|---|
| [GkmasObjectManager](https://github.com/AllenHeartcore/GkmasObjectManager) | GPL-3.0 | Manifest/アセット処理。GPL本文は`LICENSE`を参照 |
| FFmpeg | GPL-3.0（同梱ビルド） | メディア変換。同梱の`ffmpeg/LICENSE`を参照 |
| vgmstream | ISC系ライセンスほか | ゲーム音源のデコード。GkmasObjectManager同梱バイナリ |
| PySide6 / shiboken6 / Qt 6 | LGPL-3.0-only またはGPL | GUIランタイム |
| UnityPy | MIT | Unity AssetBundle解析。UnityPyのFMOD AudioClip変換機能は使用せず、FMODランタイムも配布物へ含めません |
| cryptography | Apache-2.0 またはBSD-3-Clause | Manifest復号 |
| protobuf | BSD-3-Clause | Manifest解析 |
| requests | Apache-2.0 | オンライン取得 |
| Pillow | MIT-CMU | UnityPy依存 |
| pydub | MIT | 音声処理 |
| PyYAML | MIT | GkmasObjectManager依存 |
| rich | MIT | GkmasObjectManager依存 |
| Brotli、lz4、etcpak、texture2ddecoder | MIT系ライセンス | UnityPy依存 |
| fsspec | BSD-3-Clause | UnityPy依存 |
| Python 3.12 | PSF License | 組み込みPythonランタイム |

本一覧は主要な同梱物の概要です。ソース配布物の各パッケージメタデータ、およびバイナリ配布物内のライセンスファイルが正式な条件です。

Gakumas Music Extractorの独自コードは、GkmasObjectManagerおよびFFmpegを同梱する配布形態との整合のためGPL-3.0として提供します。GPL-3.0本文は`LICENSE`に収録されています。

学園アイドルマスター、ゲーム内データ、楽曲、名称等の権利は各権利者に帰属します。本配布物にゲームデータや音源は含まれません。
