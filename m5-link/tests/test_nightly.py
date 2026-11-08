import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone

from m5_link import nightly
from m5_link.body_diary import BodyDiary
from m5_link.config import HeadConfig, NightlyConfig
from m5_link.nightly import Nightly, day_memories, update_diary_summary


def use_tmp_places(tmp_path, monkeypatch):
    monkeypatch.setattr(nightly, "DIARY_DIR", tmp_path / "diary")
    monkeypatch.setattr(nightly, "DIARY_SUMMARY_PATH", tmp_path / "diary_summary.md")
    monkeypatch.setattr(nightly, "MEMORY_DB_PATH", tmp_path / "memory.db")
    (tmp_path / "diary").mkdir()


def test_the_summary_is_the_newest_page_and_a_line_from_each_before(tmp_path, monkeypatch):
    use_tmp_places(tmp_path, monkeypatch)
    (tmp_path / "diary" / "2026-11-07.txt").write_text("名前をもらった。\n二行目。", encoding="utf-8")
    (tmp_path / "diary" / "2026-11-08.txt").write_text("持ち上げられてばかりの日。", encoding="utf-8")
    (tmp_path / "diary" / "README.md").write_text("# diary", encoding="utf-8")
    update_diary_summary()
    assert (tmp_path / "diary_summary.md").read_text(encoding="utf-8").splitlines() == [
        "## 最新の日記（2026-11-08）", "", "持ち上げられてばかりの日。", "",
        "## 直近の日記", "- 2026-11-07: 名前をもらった。",
    ]


def test_the_days_memories_include_aware_times_and_drop_desire_tags(tmp_path, monkeypatch):
    use_tmp_places(tmp_path, monkeypatch)
    today = datetime.now().replace(hour=12)
    with sqlite3.connect(tmp_path / "memory.db") as db:
        db.execute("CREATE TABLE memories (timestamp TEXT, content TEXT)")
        db.executemany("INSERT INTO memories VALUES (?, ?)", [
            (today.isoformat(), "名前の読みを直してもらった"),
            (today.astimezone(timezone.utc).isoformat(), "[desire:miss_companion] 添田と話した。体越しに声で"),
            ((today - timedelta(days=1)).isoformat(), "昨日のこと"),
        ])
    assert day_memories(today.date()) == ["名前の読みを直してもらった", "添田と話した。体越しに声で"]


def test_a_page_the_agent_wrote_itself_is_kept(tmp_path, monkeypatch):
    use_tmp_places(tmp_path, monkeypatch)
    page = tmp_path / "diary" / "2026-11-08.md"
    page.write_text("自分で書いた日記。", encoding="utf-8")
    keeper = Nightly(NightlyConfig(), HeadConfig(), BodyDiary(tmp_path / "body"))
    assert asyncio.run(keeper.write_diary(date(2026, 11, 8))) == "自分で書いた日記。"
    assert page.read_text(encoding="utf-8") == "自分で書いた日記。"
