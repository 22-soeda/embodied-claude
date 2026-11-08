# Windows で体なしの v0 を飼う

このフォークの `develop_v0` ブランチは、本家の Core（記憶・欲求・人間関係・自己モデル）を Windows ネイティブで動かし、
「定期的に目を覚まして、話したくなったら向こうから声をかけてくる」ところまでを足したもの。
体（カメラ・マイク・スピーカー）はまだない。声は PC の通知だけ。

## 本家から変えたところ

| ファイル | 変えたこと |
|---|---|
| `.gitattributes` | `.sh` `.ts` `.conf` を LF に固定。Windows の `core.autocrlf=true` で CRLF になると Git Bash が `$'\r'` で落ちる |
| `autonomous-action.sample.sh` | Git Bash で動くように直した。`jq.exe` の CRLF を落とす（落とさないとセッション ID に `\r` が入り、毎回記憶のない新規セッションになる）、`sed -i ''` を BSD/GNU 両対応に、起床のモデルと effort を `HEARTBEAT_MODEL` `HEARTBEAT_EFFORT` で指定できるように |
| `.claude/settings.json` | `autoMemoryEnabled: false`。Claude Code 自体の自動メモリが有効だと、memory-mcp ではなくそちらに覚えてしまう。あわせてフックの時間切れを延ばした（SessionStart 10→60 秒、PreToolUse 5→15 秒、ほか 10→30 秒）。Windows では `uv run` の起動が遅く、MCP サーバーが一斉に立ち上がる起床直後は SessionStart が 15 秒かかって打ち切られた。すると「このセッションが本人だ」という登録が抜け、意図を登録しても PreToolUse がそれを別人の意図として扱うので、notify も記憶も書き込みも全部止まる |
| `.gitignore` | `SOUL.md` `TODO.md` `ROUTINES.md` `notes/` `diary/` を追加。公開フォークなので暮らしの中身は push しない |
| `desktop-notify-mcp/server.py` | 声の代わりの通知。MCP サーバー名は `desktop`。`notify` は外向きの行為として individual-kernel のゲートを通る。`list_recent_notifications` は読むだけ。サーバー名を `notify` にすると道具の正式名が全部 `mcp__notify__…` になり、ゲートが読むだけの道具まで外向きと判定する（2026-11-08 に一度そうなっていた） |
| `scripts/windows/configure_v0.py` | 機械ごとの設定（Git に入らないもの）をまとめて作る |
| `scripts/windows/register-heartbeat.ps1` | cron の代わりにタスクスケジューラへ登録する |

## まっさらな PC に入れる（SSD が壊れたときの戻し方）

Git・uv・Claude Code が入っている前提。

```powershell
git clone https://github.com/22-soeda/embodied-claude.git
cd embodied-claude
git checkout develop_v0
git remote add upstream https://github.com/lifemate-ai/embodied-claude.git
winget install --id jqlang.jq -e
winget install --id Oven-sh.Bun -e
scripts\setup.cmd --profile core --non-interactive
uv run --no-sync python scripts/windows/configure_v0.py --companion 添田
```

ここで `SOUL.md` `TODO.md` を書き直す（バックアップがあれば戻す）。そのあと:

```powershell
bash ./autonomous-action.sh --dry-run
powershell -NoProfile -File scripts\windows\register-heartbeat.ps1 -IntervalMinutes 60
```

間隔は 60 分にしている。本家の cron は 20 分おきだが、「話したい」欲求は 3 時間で溜まる設定なので、
20 分おきだと大半の起床が空振りで利用枠だけ減る。

`setup.cmd` をもう一度走らせると `.mcp.json` から `notify` が消える。そのときは `configure_v0.py` も走らせ直す。

### push しても残らないもの

記憶と関係は `~\.claude\memories\memory.db` と `~\.claude\sociality\social.db` にある。人格と日々のメモは
リポジトリ直下の `SOUL.md` `TODO.md` `ROUTINES.md` `notes/` `diary/`、欲求の状態は `desires.json`。
どれも Git に入らないので、SSD が壊れると消える。残したいなら非公開の場所へ別にバックアップする。

## 使い方

### 話しかけられたら

右下に「Windows PowerShell」から通知が来る。返事はこのリポジトリで Claude Code を開いて書く。

```powershell
cd C:\Users\soeda\embodied-claude
claude
```

記憶は memory-mcp に入っているので、新しいセッションでも前のことを思い出せる。

### 何をしていたか見る

| 見たいもの | 見方 |
|---|---|
| 通知で言ったこと | `.autonomous-logs\notify-history.jsonl` |
| 起床ごとのログ | `.autonomous-logs\YYYYMMDD_HHMMSS.log`（1 日で消える） |
| 欲求の溜まり具合 | `bun run scripts/desire-tick.ts status` |
| やりたいこと | `TODO.md` |
| 次の起床 | `Get-ScheduledTaskInfo -TaskPath \EmbodiedClaude\ -TaskName heartbeat` |

### 止める・動かす・頻度を変える

```powershell
Disable-ScheduledTask -TaskPath \EmbodiedClaude\ -TaskName heartbeat   # 一時停止
Enable-ScheduledTask  -TaskPath \EmbodiedClaude\ -TaskName heartbeat   # 再開
powershell -NoProfile -File scripts\windows\register-heartbeat.ps1 -IntervalMinutes 60   # 1 時間おきに
powershell -NoProfile -File scripts\windows\register-heartbeat.ps1 -Unregister          # 登録を消す
```

起きる時間帯の濃淡（平日の 7・12・18〜24 時は毎回、それ以外は毎時 00 分に確率で、深夜は 10%）は
`autonomous-action.sh` が決めている。確率や土日は `schedule.conf` で変える。

### モデルを変える

`schedule.conf` の `HEARTBEAT_MODEL` と `HEARTBEAT_EFFORT`。既定は `sonnet` と `medium`。
空にすると Claude Code の既定（この PC では Opus・xhigh）になり、利用枠の減りが速い。
