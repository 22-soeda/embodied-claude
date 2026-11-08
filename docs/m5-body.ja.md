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

声が読み違える言葉（とくに名前）は、`m5-link.toml` の `[mouth.readings]` に `"東海林" = "ショウジ"` の形で書く。m5-link が起動のたびにエンジンのユーザー辞書へ入れる。アクセントは平板になる。下がる位置を指定するときは `"橋" = ["ハシ", 1]`。

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

### 頭の会話セッション

会話の頭は、m5-link の中で Claude Code のセッションを 1 本、起こしたままにしている。MCP サーバーが立ち上がったままなので、返事が 1〜2 秒で返る。

- **毎朝区切る**：`m5-link.toml` の `[head] day_starts_at`（既定 4 時）に、その日のセッションを閉じて新しく始める。閉じる前に頭へ「持ち越したいことを memory に残して」と頼む。次の日の頭が覚えているのは、memory と SOUL.md に書いたことだけ。m5-link が止まっていて区切りをまたいだときは、次に起動したときに同じことをする
- **道具の顔ぶれが変わったら新しく始める**：`.mcp.json` のサーバーを足したり外したりすると、日の途中でも引き継がずに新しいセッションで起きる。引き継いだセッションは、外した道具の定義も最初の形のまま送り続けるため（2026-11-08、外したはずの claude.ai のコネクタが 17 万トークン分残った）
- **1 回の返事は 6 手まで**：`[head] max_turns`。memory を引く・残すなどの道具の呼び出しも 1 手に数える
- **起動のたびに確かめる**：頭がつながった MCP サーバーごとの状態と道具の数、文脈の大きさをログに出す。落ちたサーバーは ERROR で、文脈が 6 万トークンを超えたら WARNING で出る。ウェブの画面にも「頭」の欄と、落ちたサーバーの赤い印が出る
- セッションの状態は `m5-link/state/head-session.json`。消すと次の起動で新しく始まる
- 定期起床（`autonomous-action.sh`）のセッションも、その日最初の起床（朝の再構成）で前日のものを捨てて新しく始める。本家の作りは捨てずに引き継ぎ続けるので、文脈が一日中積み上がっていた

### 体の出来事

持ち上げられた・揺さぶられた・近くに誰か来た・電池が少ない、は頭を起こさない。体がその場で自分の顔を変え、m5-link は `m5-link/state/body-diary/` に日ごとに 1 行ずつ書くだけにする。頭は次に話すとき（声をかけられたとき・定期起床のとき）に、まだ読んでいない分を「体の記録」としてまとめて受け取る。

```
[体の記録]
20:11〜20:16 持ち上げられた（3 回）
20:12 近くに誰か来た
```

定期起床の頭が `body` MCP の `say` で体からしゃべった言葉も、「自分から体の声で話しかけた「…」」として体の記録に入る。会話の頭は別のセッションなので、これが無いと、体から話しかけられて答えても、何の返事か分からなかった。既読の印は会話と定期起床で別々に持つので、定期起床が先に読んでも、会話の頭には届く。

定期起床には、m5-link が `BODY_DIGEST_FILE` でファイルを渡し、`autonomous-action.sh` がプロンプトに入れてから消す。消えていれば既読になる。スケジュールで見送った回は消さないので、次の回に回る。M5 の体が無いときはこの節は出ない。頭は `body` MCP の `list_body_events` でも、さかのぼって読める。

### 欲求

体があるときの欲求は、PetitOnes と同じ考え方で、実際にやったかどうかで動く。

- **強さの出し方**：desire-system が、記憶の中から欲求ごとの言葉（「添田と話した」「部屋を見た」など）を探し、最後にそれをしてから何時間たったかで 0〜1 を出す。m5-link が 5 分ごとに計算させる
- **体の効き**：電池が 20% を切って充電していないと、どの欲求も半分になる。20 分以内に持ち上げられたり触れられたりしていると、「話したい」が半分になる
- **会話で満たす**：体で声・文字・「みてみて」のやりとりがあると、m5-link が「話したい」を満たしたと記憶に書く（30 分に 1 回まで）
- **定期起床へ**：起床の直前に計算し直し、0.6 を超えたものを `DESIRE_DIGEST_FILE` でプロンプトに渡す。プロンプトに出しただけでは下がらない。頭が実際にやるか、`satisfy_desire` で記録したときに下がる
- **欲求の顔ぶれ**：`m5-link/desire-config.json`。体でこなせる 3 つ（話したい・部屋を見たい・考えごとをしたい）に絞ってある。名前は desire-system のものを使う（個体カーネルと社会性の層が名前で読む）
- `scripts/windows/configure_v0.py --with-body` が `desire-system/.env` にこの設定を書き、タイマーの欲求（`desires.conf`）から「話したい」を抜く。タイマーの欲求には、記憶を刻む・振り返りなどの身の回りの促しだけが残る
- ウェブの画面の「欲求」に、今の強さがバーで出る

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
| 持ち上げる・揺らす | びっくりした顔。頭には次に話すときに伝わる |

右上の印：NO WIFI（Wi-Fi がない）、NO BRAIN（頭とつながらない）、LISTEN、THINK、LOOK、LOW BATTERY。頭と 1 分つながらないと寝顔になる。
