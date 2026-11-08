import json
import sqlite3
import zipfile

from m5_link import backup
from m5_link.backup import make_backup


def build_home_and_repo(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    (repo / "diary").mkdir(parents=True)
    (repo / "SOUL.md").write_text("# イエベ", encoding="utf-8")
    (repo / "diary" / "2026-11-08.txt").write_text("名前をもらった日。", encoding="utf-8")
    (repo / "m5-link" / "state").mkdir(parents=True)
    (repo / "m5-link" / "state" / "m5-link.log").write_text("noise", encoding="utf-8")
    (home / ".claude" / "memories").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{\"secret\": 1}", encoding="utf-8")
    with sqlite3.connect(home / ".claude" / "memories" / "memory.db") as db:
        db.execute("CREATE TABLE memories (content TEXT)")
        db.execute("INSERT INTO memories VALUES ('添田の読みは「そえだ」')")
    db.close()
    monkeypatch.setattr(backup, "REPO_ROOT", repo)
    monkeypatch.setattr(backup, "HOME", home)
    monkeypatch.setattr(backup, "HOME_FOLDERS", [])


def test_a_backup_holds_the_agents_files_and_a_working_memory_copy(tmp_path, monkeypatch):
    build_home_and_repo(tmp_path, monkeypatch)
    zip_path = make_backup(tmp_path / "backups", keep=14)
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        archive.extract("home/.claude/memories/memory.db", tmp_path / "out")
        manifest = json.loads(archive.read("manifest.json"))
    assert {"repo/SOUL.md", "repo/diary/2026-11-08.txt", "home/.claude/memories/memory.db"} <= names
    assert "home/.claude/.credentials.json" not in names  # never the login
    assert "repo/m5-link/state/m5-link.log" not in names
    assert len(manifest["files"]) == 3
    copy = sqlite3.connect(tmp_path / "out" / "home" / ".claude" / "memories" / "memory.db")
    assert copy.execute("SELECT content FROM memories").fetchone() == ("添田の読みは「そえだ」",)
    copy.close()


def test_only_the_newest_backups_are_kept(tmp_path, monkeypatch):
    build_home_and_repo(tmp_path, monkeypatch)
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in ("20261101", "20261102", "20261103"):
        (folder / f"embodied-claude-{day}-0430.zip").write_bytes(b"old")
    newest = make_backup(folder, keep=2)
    assert sorted(path.name for path in folder.glob("*.zip")) == [
        "embodied-claude-20261103-0430.zip", newest.name]
