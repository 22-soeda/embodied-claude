"""A nightly backup of everything that makes the agent this agent.

The fork is public, so the agent's own life stays out of Git: its soul,
reflections, notes and diary, its memory and social databases, its desires,
the body's diary, the conversation transcripts and m5-link's settings. All of
it lives on this one PC. Every night (PetitOnes back up their house the same
way, with backup_petit.sh) m5-link zips it into [backup] dir - OneDrive on the
desktop, so the copy also leaves the machine - and keeps the newest ``keep``.

SQLite databases are copied with SQLite's own backup API, so a copy taken
while the MCP servers are writing is still a consistent database. Login
credentials (~/.claude/.credentials.json) are never included.

Inside the zip, repo/ mirrors the repository root and home/ mirrors the home
folder; manifest.json lists every file. To restore, stop m5-link, unzip, and
copy repo/ over the repository and home/ over the home folder.

    python -m m5_link.backup [--to DIR]     make one now (DIR defaults to the config)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT, BackupConfig, load_config

log = logging.getLogger(__name__)

HOME = Path.home()
REPO_FILES = [
    "SOUL.md", "SOUL_REFLECTIONS.md", "REFLECTION_INDEX.md", "TODO.md", "ROUTINES.md",
    "diary_summary.md", "desire-config.json", "desires.conf", "desires.json", "schedule.conf",
    "heartbeat-session-id", "last-session-date.txt", ".mcp.json",
    ".claude/settings.local.json", "desire-system/.env", "m5-link/m5-link.toml",
    ".autonomous-logs/notify-history.jsonl",
]
REPO_FOLDERS = ["notes", "diary", "captures", "m5-link/state"]
HOME_FILES = [".claude/desires.json", ".claude/morning_briefing.json"]
# Claude Code keeps a project's transcripts under its path with every
# non-alphanumeric character turned into "-" (C:\x\embodied-claude -> C--x-embodied-claude).
HOME_FOLDERS = [f".claude/projects/{re.sub(r'[^A-Za-z0-9]', '-', str(REPO_ROOT))}"]
DATABASES = [".claude/memories/memory.db", ".claude/sociality/social.db"]  # under home
SKIPPED_SUFFIXES = (".log", ".log.1", ".log.2", ".log.3", ".db-wal", ".db-shm")


def make_backup(destination: Path, keep: int) -> Path:
    """Write one zip into ``destination`` and drop the oldest beyond ``keep``."""
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"embodied-claude-{datetime.now():%Y%m%d-%H%M}.zip"
    manifest: list[dict] = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive, \
            tempfile.TemporaryDirectory() as scratch:
        for path, name in _plain_files():
            archive.write(path, name)
            manifest.append({"file": name, "bytes": path.stat().st_size})
        for relative in DATABASES:
            source = HOME / relative
            if not source.exists():
                continue
            copy = Path(scratch) / source.name
            _copy_database(source, copy)
            archive.write(copy, f"home/{relative}")
            manifest.append({"file": f"home/{relative}", "bytes": copy.stat().st_size, "sqlite": True})
        archive.writestr("manifest.json", json.dumps(
            {"made_at": datetime.now().isoformat(timespec="seconds"), "files": manifest},
            ensure_ascii=False, indent=1))
    _drop_old(destination, keep)
    log.info("backup written: %s (%d files, %.1f MB)", target, len(manifest), target.stat().st_size / 1e6)
    return target


def _plain_files():
    """(path, name in the zip) for every file that is copied as it is."""
    for root, files, folders, prefix in ((REPO_ROOT, REPO_FILES, REPO_FOLDERS, "repo"),
                                         (HOME, HOME_FILES, HOME_FOLDERS, "home")):
        for relative in files:
            path = root / relative
            if path.is_file():
                yield path, f"{prefix}/{relative}"
        for relative in folders:
            for path in sorted((root / relative).rglob("*")):
                if path.is_file() and not path.name.endswith(SKIPPED_SUFFIXES):
                    yield path, f"{prefix}/{path.relative_to(root).as_posix()}"


def _copy_database(source: Path, copy: Path) -> None:
    reader = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    writer = sqlite3.connect(copy)
    try:
        reader.backup(writer)
    finally:  # closed before the temporary folder is removed (Windows keeps open files)
        writer.close()
        reader.close()


def _drop_old(destination: Path, keep: int) -> None:
    backups = sorted(destination.glob("embodied-claude-*.zip"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
        log.info("old backup removed: %s", old.name)


def backup_folder(config: BackupConfig) -> Path | None:
    return Path(config.dir).expanduser() if config.dir else None


def main() -> None:
    parser = argparse.ArgumentParser(description="back up the agent's own files and databases")
    parser.add_argument("--to", type=Path, help="folder for the zip (default: [backup] dir)")
    args = parser.parse_args()
    config = load_config().backup
    destination = args.to or backup_folder(config)
    if destination is None:
        raise SystemExit("no folder: set [backup] dir in m5-link.toml, or pass --to")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(make_backup(destination, config.keep))


if __name__ == "__main__":
    main()
