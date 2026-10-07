# 検査記録の保存規約

`start.bat` から起動する。Step1外観確認 → Step2 RTSP映像確認の順に検査する。
プログラム・記録・証拠はプロジェクトを基準とする相対パスを使用する。
フォルダ全体を移動しても同じ構成で起動でき、CSVの証拠パスはCSV自身の場所を基準とする。
外観確認と1台表示のRTSP検査は Enter = OK / Esc = NG。選択中の検査枠で使用できる。
入力欄・証拠確認中では判定しない。4台表示のRTSP検査は画面のOK/NGボタンで判定する。

```text
ITC-C80修復/
├─ start.bat
├─ step0.py / step1.py / step2.py / step3.py
├─ config file/                導入する設定ファイルを1個だけ置く
├─ evidence_store.py            共通の分類・命名・保存処理
├─ records/
│  ├─ camera_inspection.csv     検査結果・NG理由・証拠への相対パス
│  └─ step1_evidence.csv        外観証拠・SN・MAC・問題箇所の枠数
└─ evidence/
   └─ YYYY-MM-DD/
      ├─ B/                    外観
      ├─ E1/                   RTSP接続不可
      ├─ E2/                   IR-CUT不具合
      └─ Z/                    そのた
```

ファイル名は `{分類}-{SN}_{MAC}_{YYYYMMDD_HHMMSS_ffffff}.{拡張子}`。
MACのコロンはハイフンに置き換える。識別子のファイル名禁止文字は `_` に置き換える。
SNが不明の場合は `UNKNOWN`。日時は撮影・保存時のローカル時刻。既存画像を上書きしない。

| 分類 | 例 | 内容 |
| --- | --- | --- |
| B | `B-SN001_AA-BB-CC-DD-EE-FF_20261001_143000_123456.jpg` | USBカメラで撮影した外観と、元画像座標の問題箇所の枠 |
| E1 | `E1-SN001_AA-BB-CC-DD-EE-FF_20261001_143001_123456.png` | RTSP映像なし。SN・MAC・IP・時刻・接続状態を表示した記録図 |
| E2 | `E2-SN001_AA-BB-CC-DD-EE-FF_20261001_143002_123456.png` | IR-CUT不具合のRTSP画像 |
| Z | `Z-SN001_AA-BB-CC-DD-EE-FF_20261001_143003_123456.png` | そのたのRTSP画像。手入力した理由を検査CSVに保存 |

E1の記録図はカメラ画像ではないことを画像内にも明記する。NGは必ず人が確定し、最終初期化（option=0）は実行しない。

再起動時に、旧保存場所の `camera_inspection.csv` と `step1_evidence.csv` を `records/` へ移す。
同名CSVがすでにある場合、旧CSVは `_legacy_日時.csv` として保持し、上書きしない。
旧検査CSVの6列・7列は次回保存時に「NG理由」「証拠」を含む8列へ移行し、既存行を保持する。
過去の未分類画像は内容を推定せず、元の場所と名前を保持する。

外観OK/NGのどちらもStep2へ進む。外観NGはB画像と「外観NG」の段階記録を保存し、Step2の最終判定とは別に保持する。撮影のキャンセル・保存失敗時は外観確認を継続する。

RTSP検査でOKを押すとStep3へ進む。旧option=0は実行しない。
Step3はSNを核対し、`config file/` の設定をRSA/AES方式で導入する。導入パスワードは `example-import-password`（相機のログインパスワード変更ではない）。
導入成功応答を確認後、既存 `config_writer.py` と同じ確認用アカウント `itc_cam` でログインし、SNを再確認してから `{"options": 2}` でIP Resetを要求する。
設定ファイル選択・書込・SN・ログイン確認で失敗した場合はIP Resetを実行しない。応答不明時に書込・Resetを自動再送しない。
Step3成功時のみOKカウントを増やし、CSVの「初期化」列に `Step3 OK` / `Step3 NG` を記録する。Step3 OKはIP Reset要求への成功応答を示し、変更後IPへの再接続確認ではない。
必要パッケージ `cryptography` はstep0が他の依存と同様に確認する。

Step3処理中は対象枠に大きく RUNING と表示し、その枠を次の機器に使用しない。成功時は OK を0.5秒間隔で点滅、失敗時は NG を表示する。完了表示は次の機器が来ていても最低2秒保持する。
