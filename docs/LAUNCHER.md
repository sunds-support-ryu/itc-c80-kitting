# EXE起動とGitHub更新

`release/1.0/ITC-C80-Launcher-1.0.exe`を起動します。最初にGitHubの最新Releaseを確認し、`update-manifest.yaml`のファイルバージョン・サイズ・SHA256に従って必要ファイルをダウンロードします。全部の検証後に更新して`bootstrap.py`を起動します。

GitHubリポジトリは `sunds-support-ryu/itc-c80-kitting` です。リポジトリはEXEに内蔵されており、新PCで設定ファイルがなくても入力不要です。別のリポジトリを使う場合だけ `launcher_settings.yaml` の `github_repository` を変更してください。更新失敗時は確認して現在版を使用できます。途中更新の復旧が未完了の場合は起動しません。

## GitHub Releaseの作成

1. GitHubでリポジトリを作成。
2. アプリを修正後、`python tools/build_release.py --version 2.1.1` を実行。
3. GitHubで公開Releaseを作成（例：タグ v2.1.1）。
4. `release/assets/` の `update-manifest.yaml` と各プログラムファイルを **Releaseの添付ファイル** としてアップロード。
5. 起動器は最新の公開Releaseを読み込みます。Draft / Pre-releaseは対象外です。

変更のないファイルは前のファイルバージョンを保ち、変更ファイルだけ指定版へ更新します。YAMLは`path / version / asset / size / sha256`を記録します。`web/index.html`はRelease上で`web__index.html`という添付名です。

`release/ITC-C80-Portable.zip`は初回配布用です。EXE、既定の起動器設定とアプリコードを含み、作業履歴・保存済みパスワード・configファイル・撮影画像は含みません。

## 実行環境

EXE自体はPythonなしで更新チェックできます。相機検査の実行にはPython 3.9以降と従来のNpcapが必要です。`python_executable`が空欄の場合、`runtime/python.exe`またはPCのpy/pythonを使用します。必要なら相対パスでPythonの場所を指定できます。Pythonライブラリの確認・インストールは既存step0を使います。configファイルは従来どおり別に配置してください。

私有GitHubリポジトリは環境変数 `ITC_GITHUB_TOKEN` を設定します。TokenをYAMLやReleaseに保存しません。

## 更新時の保護

`data/`, `records/`, `evidence/`, `config file/`, `launcher_settings.yaml`をリモート清単から上書きできません。旧プログラムは`data/update_backups/`に保存します。起動中のアプリや別の起動器があればロックで更新を止めます。新しいEXE自身の更新は自動対象外で、Release添付から手動で差し替えます。

## 検証

`ITC-C80-Launcher-1.0.exe --self-test` は更新モジュールの確認だけで相機を起動しません。
`--update-only` は更新チェックのみです。
まだReleaseを公開していないため、実際のGitHub Releaseダウンロードは検証していません。模擬Releaseでダウンロード検証・不正パス拒否・破損拒否・ロールバック・中断復旧を検証しています。
