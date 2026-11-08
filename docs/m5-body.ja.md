# M5Stack の体を付ける

頭（Claude と記憶）は家のデスクトップに置いたまま、M5Stack CoreS3 の体が自分から頭へつなぎに行く。体で話しかけると、体の声で返事が返ってくる。通信の中身は [m5-body-protocol.ja.md](m5-body-protocol.ja.md)。

```
[CoreS3]  タップで話す ─ 声 ─▶ [m5-link]  耳（faster-whisper）
   ▲                              │  頭（Claude Agent SDK、記憶は memory-mcp）
   └──── 返事の声と字幕 ◀──────────┘  口（AivisSpeech Engine）
```

## 置き場所

| もの | 場所 |
|---|---|
| 体のファーム | `m5-firmware/`（PlatformIO） |
| 頭と体をつなぐ常駐プログラム | `m5-link/`（Python、自前の uv 環境） |
| 頭が体を使う MCP | `m5-link/m5_link/body_mcp.py`（サーバー名 `body`） |
| m5-link の設定と合言葉 | `m5-link/m5-link.toml`（Git に入らない。初回起動で作られる） |
| m5-link のログ | `m5-link/state/m5-link.log` |
| 撮った写真 | `captures/`（Git に入らない） |

## デスクトップの準備（一度だけ）

前提は [windows-v0.ja.md](windows-v0.ja.md) の v0 が入っていること。

```powershell
cd $HOME\embodied-claude
uv sync --project m5-link --extra cuda    # NVIDIA の GPU がなければ --extra cuda を外す
uv run --no-sync python scripts/windows/configure_v0.py --companion 添田 --with-body
```

AivisSpeech Engine を `~\AivisSpeech-Engine\Windows-x64\run.exe` に置く。m5-link が動いていなければ起動する。場所が違うときは `m5-link.toml` の `[mouth] engine_path`。

## m5-link を動かす

```powershell
cd $HOME\embodied-claude
uv run --project m5-link python -m m5_link
```

最初の行に、体に入れる住所と合言葉が出る。

```
body url: ws://192.168.3.17:8790/body  token: xxxxxxxx
```

ブラウザで `http://127.0.0.1:8790/` を開くと、体と頭の状態、会話の記録、文字で話しかける欄がある。

m5-link は 1 時間おきの定期起床も受け持つ。タスクスケジューラの heartbeat は登録しない（二重に起きる）。

ふだんは、ログオン時に自動で起動するように登録しておく。落ちても 1 分で起き直す。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\register-m5-link.ps1
```

`git pull` でコードを更新したら、同じ 1 行をもう一度流すと再起動する。動いている m5-link を、頭や MCP サーバー、声のエンジンまで含めて止めてから起動し直す。タスクスケジューラの「終了」だけだと一番上の conhost しか止まらず、残りがポートを持ったまま残る。外すときは `-Unregister` を付ける。

頭の会話セッションは `m5-link/state/head-session-id` を引き継いで続く。頭に渡す道具や設定を変えたときは、このファイルを消してから再起動する。引き継いだセッションは、外した道具の定義も最初の形のまま送り続けるので。

## 体に書き込む

CoreS3 を USB-C でデスクトップかノートにつなぐ。

```powershell
cd $HOME\embodied-claude\m5-firmware
~\.platformio\penv\Scripts\pio.exe run -t upload
```

初めて起動すると、体が自分の Wi-Fi（`body-setup-xxxx`）を出して QR を表示する。スマホで読み取ると設定ページが開くので、家の Wi-Fi、頭の住所（上の `body url`）、合言葉を入れる。USB でつないだままなら、シリアルからも入れられる。

```powershell
~\.platformio\penv\Scripts\pio.exe device monitor
```
```
wifi 1 <SSID> <パスワード>
url ws://192.168.3.17:8790/body
token <合言葉>
reboot
```

あとで設定を変えるときは、電源を入れるときに画面を押さえておく。

## 体の使い方

| 操作 | 体がすること |
|---|---|
| タップ | 聞き始める。話し終えて少し黙ると、頭が考えて返事をする |
| 長押し | 往復モード。返事のあとまた聞く。黙っていると終わる |
| 聞いている途中・話している途中でタップ | やめる |
| 横にスワイプ | 「みてみて」。写真を撮って頭に見せる |
| 持ち上げる・揺らす | びっくりした顔。頭が一言いうこともある |

右上の印：NO WIFI（Wi-Fi がない）、NO BRAIN（頭とつながらない）、LISTEN、THINK、LOOK、LOW BATTERY。頭と 1 分つながらないと寝顔になる。
