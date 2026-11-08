"""Per-machine setup for the body-less Windows heartbeat (v0).

Run after `scripts\\setup.cmd --profile core`, from the repository root:

    uv run --no-sync python scripts/windows/configure_v0.py --companion 名前

Everything it touches is ignored by Git, so a fresh clone needs it again.
It is safe to re-run: existing files are kept, only missing pieces are added.

1. `.mcp.json`: add the `desktop` server (desktop-notify-mcp). Setup does not
   keep unknown servers, so re-run this after every setup run. An entry from
   before the rename, under the name `notify`, is removed.
2. `.claude/settings.local.json`: approve `desktop` for headless runs, and set
   `permissions.allow`, which autonomous-action.sh turns into --allowedTools.
3. Copy the templates that are still missing: schedule.conf, desires.conf,
   SOUL.md, TODO.md, ROUTINES.md, autonomous-action.sh.
4. schedule.conf: describe the v0 body in the time rules, and pin the
   heartbeat model and effort.
5. desires.conf: add the desire to talk, which the upstream sample lacks.
   With --with-body the desire to talk comes from desire-system instead (it
   goes down when the companion actually talks to the body), so it is taken
   out of desires.conf, and desire-system/.env points desire-system at the
   body's desires (m5-link/desire-config.json) and body state.
6. Its own documents, as PetitOnes' characters keep them: SOUL_REFLECTIONS.md
   and REFLECTION_INDEX.md from examples/, notes/ and diary/ with a README,
   and the textbook's routines (diary, a summary of the day's talk, reading
   SOUL.md again) added to ROUTINES.md. The heartbeat may edit these.
7. `.mcp.json`: add the `reader` server (scripts/reader_mcp.py), and let the
   heartbeat search and fetch the web and read with it, as PetitOnes do.
"""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Not "notify": the gate would then read every tool of this server as outward,
# the read-only list_recent_notifications included (see desktop-notify-mcp).
NOTIFY_SERVER_NAME = "desktop"
OLD_NOTIFY_SERVER_NAME = "notify"
NOTIFY_SERVER = {
    "command": "uv",
    "args": ["run", "--no-sync", "python", "desktop-notify-mcp/server.py"],
}

# Reading from outside: a page or a book, one page at a time, as written (ported
# from PetitOnes' reader). A uv script with its own dependencies; its tool names
# contain "read_", so the individual-kernel gate counts them as read-only.
READER_SERVER_NAME = "reader"
READER_SERVER = {
    "command": "uv",
    "args": ["run", "--script", "scripts/reader_mcp.py"],
}

# The M5 body, through m5-link (added with --with-body). It runs in m5-link's
# own environment, so `uv sync --project m5-link` has to have happened.
BODY_SERVER_NAME = "body"
BODY_SERVER = {
    "command": "uv",
    "args": ["run", "--project", "m5-link", "--no-sync", "python", "-m", "m5_link.body_mcp"],
}

# What a heartbeat may do without asking. Writes are limited to the agent's own
# notes; Bash and PowerShell are left out on purpose.
HEARTBEAT_ALLOW = [
    "mcp__memory",
    "mcp__desire-system",
    "mcp__sociality",
    "mcp__individual-kernel",
    f"mcp__{NOTIFY_SERVER_NAME}",
    # From outside, as PetitOnes' heartbeat has them: search, fetch, and reading.
    "WebSearch",
    "WebFetch",
    f"mcp__{READER_SERVER_NAME}",
    "Read",
    "Glob",
    "Grep",
    "Edit(./TODO.md)",
    "Edit(./ROUTINES.md)",
    # Its own documents, as in PetitOnes' textbook ("2. 自分を知る"): the agent
    # keeps them itself. autonomous-action.sh gives the order of changes
    # (reason in SOUL_REFLECTIONS.md first) and what not to change alone.
    "Edit(./SOUL.md)",
    "Edit(./SOUL_REFLECTIONS.md)",
    "Edit(./REFLECTION_INDEX.md)",
    "Write(./notes/**)",
    "Edit(./notes/**)",
    "Write(./diary/**)",
    "Edit(./diary/**)",
]

# Routines from PetitOnes' textbook (petit/templates/ROUTINES.md, Apache-2.0),
# added to ROUTINES.md when missing. The diary is also written at night by
# m5-link from the day's memories when the agent has not written it itself.
REFLECTION_ROUTINES = [
    ("日記を書く（夜。今日あったこと・感じたこと・気づいたことを diary/YYYY-MM-DD.txt に短く）", "daily"),
    ("{companion}との会話まとめ（今日話したことで残したいことを記憶に。「忘れて」と言われたことは残さない）",
     "daily"),
    ("SOUL.md の読み返し（空欄を自分の言葉で埋める。変えたいところは先に SOUL_REFLECTIONS.md に理由を書く）",
     "weekly"),
]
# The rest of the textbook's ROUTINES.md template (petit/templates/ROUTINES.md),
# added when ROUTINES.md has no such section yet.
ROUTINES_SECTIONS = {
    "## active time": (
        "## active time\n\n"
        "（自分が動く時間帯を、{companion}の暮らしに合わせて決める。休日と平日で変えてよい。"
        "いまの仕組みでは、平日は 7 時台・12 時台・18〜24 時、休日は 7〜24 時に 30 分ごとに起きる。"
        "それ以外は毎時ちょうどに、昼は半分、深夜は 1 割の確率で起きる）\n"
    ),
    "## 心がけ": (
        "## 心がけ\n\n"
        "- 夜と早朝は音を出さない\n"
        "- 夜は日記を書き、記憶に「おやすみ、スリープした」と書いて寝る。朝は寝ていた記憶を読む\n"
    ),
}

FOLDER_READMES = {
    "notes": "# notes\n\n詩・発見・提案を置く。詩を書いたら必ずここに。\n",
    "diary": "# diary\n\n一日一枚 `YYYY-MM-DD.txt`。夜に短く。要約 `diary_summary.md` は仕組みが作る。\n",
}

V0_DAY_TIME_RULE = (
    "体はまだない。目も耳も口もなく、部屋に人がいるかは分からない。"
    "声は notify（この PC の通知）だけ。伝えたいことがあるときだけ使い、"
    "1 回の Heartbeat で notify は多くても 1 回。前に何を言ったかは "
    "list_recent_notifications で見られる。"
)
V0_NIGHT_TIME_RULE = "深夜帯。notify は使わないこと。静かに考えるか、記憶を整理するだけ。"

# The day's rhythm follows PetitOnes' textbook ("7. 暮らし", "3.5 寝る前にすること"):
# in the morning wake the body and read what was written before sleeping; at night
# write the diary, write "おやすみ、スリープした" to memory, and sleep; in the small
# hours be quiet (their heartbeat's night rule).
BODY_DAY_TIME_RULE = (
    "M5Stack の体がある（顔・カメラ・マイク・スピーカー・タッチ・傾き・近接センサー）。"
    "朝、体が寝ていたら（get_body_state の asleep）body の wake で起こし、寝ていた記憶と日記の要約を読む。"
    "人が近くにいそうなとき（get_body_state で near が true、list_body_events に最近の持ち上げや"
    "タッチがある）は body の say で声をかけてよい。いなさそうなときは notify（PC の通知）を使う。"
    "どちらも伝えたいことがあるときだけ、1 回の Heartbeat で多くても 1 回。体が寝ているあいだは say を使わない。"
    "周りを見たいときは body の see。"
    "夜、眠りたくなったら（欲求の「眠りたい」）、日記を書き、記憶に「おやすみ、スリープした」と書いてから body の sleep で眠る。"
)
BODY_NIGHT_TIME_RULE = (
    "現在は深夜帯。say も notify も絶対に使わない。静かに観察のみ。"
    "体がまだ起きていたら、記憶に「おやすみ、スリープした」と書いてから body の sleep で眠る。"
)

HEARTBEAT_SETTINGS = {
    "HEARTBEAT_MODEL": "sonnet",
    "HEARTBEAT_EFFORT": "medium",
}

TALK_DESIRE_NAME = "話したい"
# 0.6 / 0.000056 per second = about 3 hours to reach the threshold.
TALK_DESIRE_RATE = "0.000056"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_reader_server() -> None:
    mcp_path = REPO_ROOT / ".mcp.json"
    config = read_json(mcp_path)
    servers = config.setdefault("mcpServers", {})
    if servers.get(READER_SERVER_NAME) == READER_SERVER:
        print(f"kept      .mcp.json {READER_SERVER_NAME} server")
    else:
        servers[READER_SERVER_NAME] = READER_SERVER
        write_json(mcp_path, config)
        print(f"added     .mcp.json {READER_SERVER_NAME} server")
    # The first start installs its dependencies; do it now, not inside an MCP startup timeout.
    subprocess.run(["uv", "run", "--script", "scripts/reader_mcp.py", "--check"], cwd=REPO_ROOT, check=False)


def add_notify_server() -> None:
    mcp_path = REPO_ROOT / ".mcp.json"
    if not mcp_path.exists():
        raise SystemExit(".mcp.json is missing: run scripts\\setup.cmd --profile core first")
    config = read_json(mcp_path)
    servers = config.setdefault("mcpServers", {})
    removed_old = servers.pop(OLD_NOTIFY_SERVER_NAME, None) is not None
    if servers.get(NOTIFY_SERVER_NAME) == NOTIFY_SERVER and not removed_old:
        print(f"kept      .mcp.json {NOTIFY_SERVER_NAME} server")
        return
    servers[NOTIFY_SERVER_NAME] = NOTIFY_SERVER
    write_json(mcp_path, config)
    if removed_old:
        print(f"renamed   .mcp.json {OLD_NOTIFY_SERVER_NAME} server -> {NOTIFY_SERVER_NAME}")
    else:
        print(f"added     .mcp.json {NOTIFY_SERVER_NAME} server")


def add_body_server() -> bool:
    if not (REPO_ROOT / "m5-link" / ".venv").exists():
        print("skipped   body server: run `uv sync --project m5-link` first")
        return False
    mcp_path = REPO_ROOT / ".mcp.json"
    config = read_json(mcp_path)
    servers = config.setdefault("mcpServers", {})
    if servers.get(BODY_SERVER_NAME) == BODY_SERVER:
        print(f"kept      .mcp.json {BODY_SERVER_NAME} server")
        return True
    servers[BODY_SERVER_NAME] = BODY_SERVER
    write_json(mcp_path, config)
    print(f"added     .mcp.json {BODY_SERVER_NAME} server")
    return True


def update_local_settings(with_body: bool) -> None:
    settings_path = REPO_ROOT / ".claude" / "settings.local.json"
    settings = read_json(settings_path)
    server_names = [NOTIFY_SERVER_NAME] + ([BODY_SERVER_NAME] if with_body else [])

    enabled = settings.setdefault("enabledMcpjsonServers", [])
    if OLD_NOTIFY_SERVER_NAME in enabled:
        enabled.remove(OLD_NOTIFY_SERVER_NAME)
    for name in server_names:
        if name not in enabled:
            enabled.append(name)

    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    old_rule = f"mcp__{OLD_NOTIFY_SERVER_NAME}"
    if old_rule in allow:
        allow.remove(old_rule)
    for rule in HEARTBEAT_ALLOW + [f"mcp__{name}" for name in server_names]:
        if rule not in allow:
            allow.append(rule)

    write_json(settings_path, settings)
    print(f"updated   .claude/settings.local.json ({', '.join(server_names)} approval, permissions.allow)")


def copy_if_missing(template: str, target: str) -> bool:
    target_path = REPO_ROOT / target
    if target_path.exists():
        print(f"kept      {target}")
        return False
    shutil.copyfile(REPO_ROOT / template, target_path)
    print(f"created   {target} from {template}")
    return True


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def set_shell_variable(lines: list[str], key: str, value: str) -> None:
    """Replace `KEY=...` in a sourced shell file, or append it."""
    assignment = f'{key}="{value}"'
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = assignment
            return
    lines.append(assignment)


def configure_schedule(created: bool, with_body: bool) -> None:
    path = REPO_ROOT / "schedule.conf"
    lines = path.read_text(encoding="utf-8").splitlines()
    if with_body:
        set_shell_variable(lines, "DAY_TIME_RULE", BODY_DAY_TIME_RULE)
        set_shell_variable(lines, "NIGHT_TIME_RULE", BODY_NIGHT_TIME_RULE)
    elif created:
        set_shell_variable(lines, "DAY_TIME_RULE", V0_DAY_TIME_RULE)
        set_shell_variable(lines, "NIGHT_TIME_RULE", V0_NIGHT_TIME_RULE)
    for key, value in HEARTBEAT_SETTINGS.items():
        if not any(line.startswith(f"{key}=") for line in lines):
            if not any(line.startswith("# --- Heartbeat model") for line in lines):
                lines += ["", "# --- Heartbeat model (claude -p --model / --effort) ---"]
            set_shell_variable(lines, key, value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("updated   schedule.conf (v0 time rules, heartbeat model)")


def add_talk_desire(companion: str) -> None:
    path = REPO_ROOT / "desires.conf"
    lines = path.read_text(encoding="utf-8").splitlines()
    if any(line.startswith(f"{TALK_DESIRE_NAME} |") for line in lines):
        print(f"kept      desires.conf {TALK_DESIRE_NAME}")
        return
    prompt = (
        f"（内部衝動）{companion}のことが気になる。list_recent_notifications で前に"
        f"何を言ったかを見てから、伝えたいことがあれば notify で声をかけてみるか。"
    )
    lines.append(f"{TALK_DESIRE_NAME} | {TALK_DESIRE_RATE} | {prompt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"added     desires.conf {TALK_DESIRE_NAME}")


def set_up_reflection(companion: str) -> None:
    """The documents PetitOnes' characters deepen their thinking in (textbook, "2. 自分を知る")."""
    copy_if_missing("examples/SOUL_REFLECTIONS.sample.md", "SOUL_REFLECTIONS.md")
    copy_if_missing("examples/REFLECTION_INDEX.sample.md", "REFLECTION_INDEX.md")
    for folder, readme in FOLDER_READMES.items():
        path = REPO_ROOT / folder / "README.md"
        if not path.exists():
            path.parent.mkdir(exist_ok=True)
            path.write_text(readme, encoding="utf-8", newline="\n")
            print(f"created   {folder}/README.md")

    routines = REPO_ROOT / "ROUTINES.md"
    text = routines.read_text(encoding="utf-8")
    missing = [(name.format(companion=companion), interval) for name, interval in REFLECTION_ROUTINES
               if name.format(companion=companion).split("（")[0] not in text]
    if missing:
        rows = "".join(f"| {name} | {interval} | — |\n" for name, interval in missing)
        text = _insert_table_rows(text, rows)
        print(f"added     ROUTINES.md {len(missing)} routines from the PetitOnes textbook")
    for heading, section in ROUTINES_SECTIONS.items():
        if heading not in text:
            text = text.rstrip("\n") + "\n\n" + section.format(companion=companion)
            print(f"added     ROUTINES.md {heading[3:]}")
    routines.write_text(text, encoding="utf-8", newline="\n")


def _insert_table_rows(text: str, rows: str) -> str:
    """Append rows right after the last line of the first Markdown table (or at the end)."""
    lines = text.splitlines(keepends=True)
    table_lines = [index for index, line in enumerate(lines) if line.lstrip().startswith("|")]
    if not table_lines:
        return text.rstrip("\n") + "\n\n" + rows
    end = table_lines[0]
    while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
        end += 1
    return "".join(lines[: end + 1]) + rows + "".join(lines[end + 1 :])


def remove_talk_desire() -> None:
    path = REPO_ROOT / "desires.conf"
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith(f"{TALK_DESIRE_NAME} |")]
    if len(kept) == len(lines):
        return
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    print(f"removed   desires.conf {TALK_DESIRE_NAME} (desire-system's miss_companion takes over)")


def configure_body_desires(companion: str) -> None:
    """Point desire-system (its MCP server and its updater) at the body's desires.

    desire-system reads desire-system/.env (python-dotenv) whichever process
    starts it, so the head's satisfy_desire recomputes with the same desires
    and body state as m5-link's five-minute run.
    """
    path = REPO_ROOT / "desire-system" / ".env"
    wanted = {
        "DESIRE_CONFIG_FILE": (REPO_ROOT / "m5-link" / "desire-config.json").as_posix(),
        "DESIRE_BODY_STATE_FILE": (REPO_ROOT / "m5-link" / "state" / "body-now.json").as_posix(),
        "COMPANION_NAME": companion,
    }
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in wanted]
    lines += [f"{key}={value}" for key, value in wanted.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("updated   desire-system/.env (the body's desires)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--companion",
        default="あなた",
        help="what the agent calls the person it talks to (used in the talk desire)",
    )
    parser.add_argument(
        "--refresh-script",
        action="store_true",
        help="overwrite autonomous-action.sh with autonomous-action.sample.sh",
    )
    parser.add_argument(
        "--with-body",
        action="store_true",
        help="add the M5 body (m5-link's body MCP) and switch the time rules to the body-aware ones",
    )
    args = parser.parse_args()

    add_notify_server()
    add_reader_server()
    with_body = args.with_body and add_body_server()
    update_local_settings(with_body)

    schedule_created = copy_if_missing("schedule.sample.conf", "schedule.conf")
    configure_schedule(schedule_created, with_body)

    copy_if_missing("desires.sample.conf", "desires.conf")
    if with_body:
        remove_talk_desire()
        configure_body_desires(args.companion)
    else:
        add_talk_desire(args.companion)

    copy_if_missing("examples/SOUL.sample.md", "SOUL.md")
    copy_if_missing("examples/TODO.sample.md", "TODO.md")
    copy_if_missing("examples/ROUTINES.sample.md", "ROUTINES.md")
    set_up_reflection(args.companion)

    script = REPO_ROOT / "autonomous-action.sh"
    if args.refresh_script and script.exists():
        script.unlink()
    copy_if_missing("autonomous-action.sample.sh", "autonomous-action.sh")
    make_executable(script)

    print("\nnext: edit SOUL.md, then")
    print("  bash ./autonomous-action.sh --dry-run")
    print("  powershell -NoProfile -File scripts\\windows\\register-heartbeat.ps1")


if __name__ == "__main__":
    main()
