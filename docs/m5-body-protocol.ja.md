# M5 の体と m5-link のあいだの決まりごと

M5Stack CoreS3 の体（`m5-firmware/`）が、デスクトップの `m5-link/` に WebSocket で**自分からつなぎに行く**。PetitOnes のくらうどぷちと同じ向きで、ろーかるぷちとは逆。だから体はどの Wi-Fi にいても、頭まで届けばいい。

- 家の LAN：`ws://<デスクトップの IP>:8790/body?token=<合言葉>`
- 外から：`wss://<Tailscale Funnel の名前>/body?token=<合言葉>`（未検証）

合言葉は `m5-link/m5-link.toml` の `[server] token`。初回起動で作られる。

## フレーム

- テキスト：JSON。種類は `"t"`
- バイナリ：先頭 1 バイトが種類、残りが中身
  - `0x01` 音声。PCM 16 bit リトルエンディアン、モノラル、16 kHz
  - `0x02` JPEG

体が受け取れる 1 フレームは 15 KB まで（arduinoWebSockets の上限）。m5-link は音声を 4 KB ずつ送る。

## 体 → m5-link

| `t` | いつ | 中身 |
|---|---|---|
| `hello` | つながった直後 | `name` `fw` `camera` |
| `state` | つながった直後と 30 秒ごと | `battery` `charging` `near` `proximity` `light` `rssi` `ssid` `uptime_s` |
| `listen_start` | 画面をタップ（1 回）か長押し（往復） | `loop` |
| （`0x01` 音声） | 聞いている間、64 ms ごと | 1024 サンプル |
| `listen_end` | 話し終わった・何も聞こえない・30 秒・取り消し | `reason`: `silence` `max_length` `no_speech` `cancelled` |
| `spoken` | 返事を言い終えた | |
| `speech_interrupted` | 話している途中でタップされた | |
| `snapshot` + （`0x02` JPEG） | 頼まれた写真、または「みてみて」（横にスワイプ） | `id` `reason`: `requested` `mitemite`、`bytes` |
| `snapshot_failed` | カメラが撮れなかった | `id` |
| `event` | 体に起きたこと | `kind`: `picked_up` `shaken` `near` `far` `battery_low` `poked`（寝ているときのタップ） `slept` `woke`、眠り・目覚めには `reason`: `head` `battery` `touch` |

話し終わりの判定は体がする。最初の約 0.2 秒で部屋の雑音を測り、その 2.5 倍（最低でも RMS 350）を超える音が 2 回続いたら「話している」、そのあと 0.9 秒静かなら「話し終わった」。6 秒なにも聞こえなければ `no_speech`。

## m5-link → 体

| `t` | 意味 | 中身 |
|---|---|---|
| `face` | 表情 | `mood`: `happy` `sad` `surprised` `thinking` `sleepy` `love` `neutral`、`ms`（0 = 次まで） |
| `look` | 視線 | `x` `y`（-100〜100）、`ms` |
| `thinking` | 考え中の表示 | `on`。往復中に `false` が来たら体はまた聞き始める |
| `speak_start` + （`0x01` 音声）+ `speak_end` | 返事を話す | `bytes` `rate` `text`（字幕） |
| `caption` | 字幕だけ出す | `text` `ms` |
| `listen` | 聞き始める | `loop` |
| `stop_listening` | 聞くのをやめる | |
| `snapshot` | 写真を撮る | `id` |
| `sleep` / `wake` | 眠る・起きる | |
| `power_save` | 省電力（明るさ 40 まで、顔は 10 回/秒） | `on` |
| `config` | 設定を変えて保存 | `color`（RRGGBB）`volume`（0〜100）`name` |

## 顔の右上の印

| 印 | 意味 |
|---|---|
| NO WIFI | 登録した Wi-Fi のどれにもつながっていない |
| NO BRAIN | Wi-Fi はあるが m5-link とつながらない |
| LOW BATTERY | 電池 15% 以下で充電していない |
| LISTEN / LISTEN LOOP | 聞いている（往復モード） |
| THINK | 頭が考えている |
| LOOK | 写真を撮っている |

眠り方は PetitOnes の体と同じ（ファーム 0.2.0 から）。頭に `sleep` を頼まれるか、電池が 10% 以下で充電していないと眠る。目がゆっくり閉じて小さな音が鳴り、明るさ 15 の寝顔になる。眠っているあいだは聞かず、話さない（頭から返事が来ても音を出さず、すぐ `spoken` を返す）。頭の `wake` か、1 秒以内に 3 回タップで起きる（起きる音が鳴る）。1〜2 回のタップは `poked` として伝えるだけ。

頭と 60 秒つながらないときも寝顔になるが、これは眠りではなく、頭が戻ればそのまま起きる。

`state` には `asleep`（眠っているか）、`sleep_reason`（`head` `battery`、頭がいないだけなら `no_head`）、`power_save` も載る。明るさの既定は 80（PetitOnes と同じ）。

## 体の設定

初回、または電源を入れるときに画面を押さえていると、体が自分の Wi-Fi を出して QR を表示する。スマホで読み取ると設定ページが開く。USB でつないでいるときは、シリアル（115200）からも設定できる。

```
wifi 1 <SSID> <パスワード>
url ws://192.168.3.17:8790/body
token <合言葉>
name <体の名前>
show
reboot
```

開発中は `http://<体の IP>/status` で体の状態を JSON で読める。
