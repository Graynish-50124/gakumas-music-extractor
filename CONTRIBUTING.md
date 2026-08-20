# コントリビューション

不具合報告や改善提案を歓迎します。ゲームの音源、Manifest、キャッシュ、個人情報をIssueやPull Requestへ添付しないでください。

## 開発環境

Windows 11とPython 3.12を対象としています。プロジェクト直下のPowerShellで次を実行してください。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\tools\fetch_ffmpeg.ps1
.\.venv\Scripts\python.exe -m pytest -q
```

ローカルの学マスデータがない環境では、実データを使う結合テストは自動的にスキップされます。

## Pull Request

- 変更理由と確認方法を記載してください。
- 新しい挙動には可能な範囲でテストを追加してください。
- ゲーム由来のバイナリや抽出物をコミットしないでください。
- 提出したコードは本リポジトリと同じGPL-3.0で配布されることに同意してください。
