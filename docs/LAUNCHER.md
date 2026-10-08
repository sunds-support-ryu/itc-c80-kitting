# 起動とGitHub更新

## EXEを使用しない起動

標準入口は`start.pyw`です。PythonのGUI起動器 → GitHub更新 → bootstrap → 本地検査ウィンドウの順に実行し、独自EXEやCUIウィンドウは使用しません。`.pyw`はインストール済みPythonの`pythonw.exe`に関連付けてください。関連付けがないPCでは、下記の`start.vbs`を補助入口として使用できます。

Python本体とNpcapは各PCにインストールしてください。検査設定・config・履歴・画像は更新対象外です。ログはUTF-8で保存します。`pythonw.exe start.pyw --self-test`は更新・カメラ操作なしで起動器を確認します。

`start.vbs`をダブルクリックしてください。インストール済みのPythonで更新ウィンドウを開き、GitHub更新後に検査ウィンドウを起動します。独自のEXEは使用せず、コンソールも表示しません。Windowsのセキュリティ設定は変更しません。スクリプトやPython自体が管理ポリシーで禁止されているPCでは、管理者の許可が必要です。

Python 3.9以降、Tk、Npcapが必要です。起動器はPyYAMLとrequestsを確認し、不足時にpipでインストールします。検査用ライブラリは従来のbootstrapで確認します。

自動検出できない場合は、`start.vbs`と同じ場所に`python_path.txt`を作成し、Pythonの実行ファイルの絶対パス、または配布フォルダからの相対パスを1行で記入してください。例：`runtime\pythonw.exe`。`launcher_settings.yaml`の`python_executable`は検査側Pythonの指定として引き続き利用できます。

`cscript //nologo start.vbs --self-test`で更新・相機操作なしの起動確認ができます。ログは`data/logs/script_launcher.log`と`data/logs/launcher.log`です。

`python_path.txt`はUTF-8で保存してください（BOMあり／なしの両方に対応）。韓国語などを含むパスでも読み込めます。子プロセスにはUTF-8を指定し、起動ログにはPythonの版・実行ファイル・配布フォルダを記録します。起動失敗時は終了コードとログ場所を表示します。

EXEなしのRelease作成：`python tools/build_release.py --version 1.1.1 --script-only`。`release/assets/1.1.1`のファイルと`release/ITC-C80-Script-Launcher.zip`を公開します。設定・検査履歴・画像・configは含みません。

## 従来のEXE起動

`release/native/ITC-C80-Launcher-1.0.exe`を起動します。最初にGitHubの最新Releaseを確認し、`update-manifest.yaml`のファイルバージョン・サイズ・SHA256に従って必要ファイルをダウンロードします。全部の検証後に更新して`bootstrap.py`を起動します。

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
模擬Releaseでダウンロード検証・不正パス拒否・破損拒否・ロールバック・中断復旧を検証しています。

本地ウィンドウ版はwindowed EXEです。エラーはダイアログ、進捗は更新ウィンドウ、ログはdata/logsに表示・保存します。console版EXEを新しいEXEへ差し替えてください。
