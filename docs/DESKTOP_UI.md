# 本地ウィンドウ

起動器1.0はWindows GUI版です。更新確認の進捗ウィンドウからPython検査アプリを起動し、CUIコンソールを表示しません。

検査画面はTkの本地ウィンドウです。ブラウザ、HTMLレンダラー、localhost HTTPサーバーは起動しません。既存のMAC/IP管理・外観検査・RTSP/IR-CUT・IR ON/OFF・config・貼紙確認・GAS送信を同じRuntimeで実行します。

設定は「接続 / IP分配 / 許可モデル / 認証 / 表示 / GAS送信」の6頁。許可モデル頁で選択MACのキャッシュを解除できます。パスワードの表示切替、GAS応答、受信コード例を利用できます。

外観NGは画像上をドラッグして問題箇所を囲み、「外観不具合 / 破損」を選択して保存します。IR-CUTとIR ON/OFFはOK/NGを操作員が確認します。

「ログ」ボタンから実行ログを見られます。ファイルは data/logs/tool.log、起動・更新・ライブラリ準備は data/logs/launcher.log に保存します。

新しい windowed EXE または start.vbs を使ってください。旧console EXEは自分自身を更新しないため、新版EXEをダウンロードして差し替えます。旧HTMLアプリは終了してから新しいウィンドウ版を起動してください。設定、CSV、作業状態、証拠画像は以前と同じフォルダを利用します。

旧HTMLは samples/legacy_web に参考用として保存しており、実行には使いません。
