"""Tests for the nightly sleep (consolidation, decay, forgetting).

Adapted from PetitOnes/m5-petit-memory tests/test_sleep.py (Apache License 2.0).
Added: timezone-aware timestamps, and merging nearly identical memories.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from memory_mcp.config import SleepConfig
from memory_mcp.sleep import (
    SleepEngine,
    _is_first_experience,
    _is_protected,
    calculate_retention_score,
)
from memory_mcp.store import MemoryStore
from memory_mcp.types import Memory

A_MONTH_AGO = datetime.now() - timedelta(days=30)


def memory(content="x", emotion="neutral", importance=1, days_old=0, access_count=0) -> Memory:
    stamp = (datetime.now() - timedelta(days=days_old)).isoformat()
    return Memory(id="test", content=content, timestamp=stamp, emotion=emotion,
                  importance=importance, category="daily", access_count=access_count)


async def save_old(store: MemoryStore, content: str, *, emotion="neutral", importance=1,
                   category="daily", stamp: str | None = None, **extra) -> str:
    """Save a memory and move its timestamp a month back (or to ``stamp``)."""
    saved = await store.save(content=content, emotion=emotion, importance=importance,
                             category=category, **extra)
    db = store._ensure_connected()
    db.execute("UPDATE memories SET timestamp = ? WHERE id = ?",
               (stamp or A_MONTH_AGO.isoformat(), saved.id))
    db.commit()
    return saved.id


async def contents(store: MemoryStore) -> list[str]:
    return [m.content for m in await store.get_all()]


# ── retention and protection ───────────────────


def test_a_recent_important_memory_scores_high():
    assert calculate_retention_score(memory(emotion="excited", importance=5, access_count=10)) > 0.7


def test_an_old_trivial_memory_scores_low():
    assert calculate_retention_score(memory(importance=1, days_old=90)) < 0.2


def test_retention_stays_between_zero_and_one():
    for importance in range(1, 6):
        for emotion in ("neutral", "happy", "excited"):
            for days_old in (0, 30, 90):
                score = calculate_retention_score(memory(emotion=emotion, importance=importance,
                                                         days_old=days_old))
                assert 0.0 <= score <= 1.0


@pytest.mark.parametrize("protected", [
    memory(importance=4),
    memory(emotion="excited"),
    memory(content="初めて外を見た"),
    memory(content="This was my first time seeing snow"),
])
def test_important_emotional_and_first_memories_are_protected(protected):
    assert _is_protected(protected, SleepConfig())


def test_an_ordinary_memory_is_not_protected():
    assert not _is_protected(memory(content="普通の記録", importance=2), SleepConfig())


@pytest.mark.parametrize("text, first", [
    ("初めてカメラで撮影した", True),
    ("はじめて空を見た", True),
    ("First time seeing the stars", True),
    ("今日も空を見た", False),
])
def test_first_experiences_are_recognised(text, first):
    assert _is_first_experience(text) is first


# ── a night's sleep ────────────────────────────


@pytest_asyncio.fixture
async def store_with_old_memories(memory_store: MemoryStore) -> MemoryStore:
    await save_old(memory_store, "何もない日だった")                                   # forgotten
    await save_old(memory_store, "嬉しい出来事", emotion="excited")                     # kept: emotion
    await save_old(memory_store, "大切な思い出", importance=5, category="memory")       # kept: important
    await save_old(memory_store, "普通の会話をした", importance=2, category="conversation")  # decays
    await save_old(memory_store, "初めて星を見た", category="observation")             # kept: first
    return memory_store


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing(store_with_old_memories):
    before = await contents(store_with_old_memories)
    stats = await SleepEngine(store_with_old_memories).run(dry_run=True)
    assert stats.dry_run is True
    assert await contents(store_with_old_memories) == before


@pytest.mark.asyncio
async def test_a_trivial_old_memory_is_forgotten(store_with_old_memories):
    stats = await SleepEngine(store_with_old_memories).run(dry_run=False)
    assert any("何もない日" in entry["content"] for entry in stats.forgotten)
    assert "何もない日だった" not in await contents(store_with_old_memories)


@pytest.mark.asyncio
async def test_protected_memories_survive(store_with_old_memories):
    await SleepEngine(store_with_old_memories).run(dry_run=False)
    remaining = await contents(store_with_old_memories)
    assert {"大切な思い出", "嬉しい出来事", "初めて星を見た"} <= set(remaining)


@pytest.mark.asyncio
async def test_a_low_retention_memory_loses_importance(store_with_old_memories):
    stats = await SleepEngine(store_with_old_memories).run(dry_run=False)
    assert all("→" in entry["importance"] for entry in stats.decayed)


@pytest.mark.asyncio
async def test_protected_memories_are_counted(store_with_old_memories):
    stats = await SleepEngine(store_with_old_memories).run(dry_run=True)
    assert stats.protected >= 1


@pytest.mark.asyncio
async def test_a_memory_in_an_episode_is_not_forgotten(memory_store):
    await save_old(memory_store, "エピソードに属する記録", episode_id="ep-test")
    await SleepEngine(memory_store).run(dry_run=False)
    assert "エピソードに属する記録" in await contents(memory_store)


@pytest.mark.asyncio
async def test_timezone_aware_timestamps_are_aged_too(memory_store):
    # desire-system writes its records with +00:00; these must not stop the sleep
    aware = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    await save_old(memory_store, "[desire:miss_companion] 話した", stamp=aware)
    stats = await SleepEngine(memory_store).run(dry_run=True)
    assert any("desire" in entry["content"] for entry in stats.forgotten)


@pytest.mark.asyncio
async def test_nearly_identical_memories_are_merged_into_one(memory_store):
    await save_old(memory_store, "部屋の明かりがついていた", importance=2, category="observation")
    await save_old(memory_store, "部屋の明かりがついていた。", importance=2, category="observation")
    stats = await SleepEngine(memory_store).run(dry_run=False)
    assert len(stats.merged) == 1
    remaining = await contents(memory_store)
    assert len(remaining) == 1 and remaining[0].startswith("🌙 記憶整理で統合: observationの記録 2件")
