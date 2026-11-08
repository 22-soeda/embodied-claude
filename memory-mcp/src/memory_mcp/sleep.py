"""Sleep: the nightly consolidation, decay and forgetting of memories.

Adapted from PetitOnes/m5-petit-memory (src/memory_mcp/sleep.py, Apache License
2.0), where it runs every night at 04:05 for each character. Three phases over
memories at least min_age_days old:

    1. merge   - memories of one category that are nearly the same (cosine
                 similarity >= 0.85) become one "🌙 記憶整理で統合" memory
    2. decay   - a memory whose retention score is low loses one importance
    3. forget  - importance 1, neutral, outside any episode, rarely recalled,
                 two weeks old: deleted

Never touched: importance 4 or more, a strong emotion (happy, moved,
excited, surprised), and anything that says it was a first time (初めて).

Changes from the original: ages are computed from both naive timestamps and
timezone-aware ones (desire-system records its satisfactions with +00:00, and
subtracting those from a naive "now" raised TypeError); deletion uses this
store's delete(); and there is a command, `memory-sleep [--dry-run]`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from dataclasses import asdict
from datetime import datetime

import numpy as np

from .config import MemoryConfig, SleepConfig
from .store import EMOTION_BOOST_MAP, MemoryStore
from .types import Memory, SleepStats
from .vector import cosine_similarity, decode_vector

logger = logging.getLogger(__name__)

# Words that mark a first experience; such memories are kept.
_FIRST_TIME_KEYWORDS = ("初めて", "はじめて", "初の", "first time", "first")


def _is_first_experience(content: str) -> bool:
    lower = content.lower()
    return any(keyword in lower for keyword in _FIRST_TIME_KEYWORDS)


def _truncate_at_boundary(text: str, limit: int) -> str:
    """Cut at a 。 or 、 rather than in the middle of a phrase, and add …"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for punctuation in ("。", "、"):
        index = window.rfind(punctuation)
        if index > 0:
            return window[: index + 1] + "…"
    return window + "…"


def _age_days(timestamp: str, now: datetime) -> float | None:
    """Days since ``timestamp`` (naive local time or timezone-aware), or None if unreadable."""
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)  # to naive local, like ``now``
    return max(0.0, (now - moment).total_seconds() / 86400)


def calculate_retention_score(memory: Memory, now: datetime | None = None) -> float:
    """How much a memory deserves to stay, 0.0-1.0.

    retention = (importance / 5) * 0.3 + emotion_strength * 0.2
              + recency * 0.3 (half-life about 30 days) + access_frequency * 0.2
    """
    now = now or datetime.now()
    importance_part = (memory.importance / 5.0) * 0.3
    emotion_part = EMOTION_BOOST_MAP.get(memory.emotion, 0.0) * 0.2
    age = _age_days(memory.timestamp, now)
    recency_part = math.exp(-(age or 0.0) / 30.0) * 0.3
    access_part = min(1.0, memory.access_count / 10.0) * 0.2
    return importance_part + emotion_part + recency_part + access_part


def _is_protected(memory: Memory, config: SleepConfig) -> bool:
    return (
        memory.importance >= config.protected_importance
        or memory.emotion in config.protected_emotions
        or _is_first_experience(memory.content)
    )


class SleepEngine:
    """Merge, then decay, then forget."""

    def __init__(self, store: MemoryStore, config: SleepConfig | None = None):
        self._store = store
        self._config = config or SleepConfig()

    async def run(self, dry_run: bool = True) -> SleepStats:
        """Run one sleep. With dry_run, report what would change and change nothing."""
        now = datetime.now()
        candidates = [
            memory for memory in await self._store.get_all()
            if (age := _age_days(memory.timestamp, now)) is not None and age >= self._config.min_age_days
        ]

        merged = await self._merge(candidates, dry_run)
        merged_ids = {memory_id for entry in merged for memory_id in entry["group"]}
        remaining = [memory for memory in candidates if memory.id not in merged_ids]

        protected = 0
        decayed: list[dict] = []
        for memory in remaining:
            if _is_protected(memory, self._config):
                protected += 1
                continue
            retention = calculate_retention_score(memory, now)
            if retention < self._config.decay_retention_threshold and memory.importance > 1:
                new_importance = max(1, memory.importance - 1)
                decayed.append({"id": memory.id, "importance": f"{memory.importance}→{new_importance}",
                                "retention_score": round(retention, 3)})
                if not dry_run:
                    await self._store.update_memory_fields(memory.id, importance=new_importance)

        decayed_ids = {entry["id"] for entry in decayed}
        forgotten: list[dict] = []
        for memory in remaining:
            if _is_protected(memory, self._config) or memory.id in decayed_ids:
                continue  # a memory that has just lost importance is not also forgotten tonight
            if self._should_forget(memory, now):
                forgotten.append({"id": memory.id, "content": memory.content[:80]})
                if not dry_run:
                    await self._store.delete(memory.id)

        return SleepStats(merged=merged, decayed=decayed, forgotten=forgotten,
                          protected=protected, dry_run=dry_run)

    def _should_forget(self, memory: Memory, now: datetime) -> bool:
        age = _age_days(memory.timestamp, now)
        return (
            memory.importance == 1
            and memory.emotion == "neutral"
            and not memory.episode_id
            and age is not None
            and age >= self._config.forget_min_age_days
            and memory.access_count < self._config.forget_max_access
        )

    async def _merge(self, candidates: list[Memory], dry_run: bool) -> list[dict]:
        """Group nearly identical memories of one category and fold each group into one."""
        by_category: dict[str, list[Memory]] = {}
        for memory in candidates:
            if not _is_protected(memory, self._config):
                by_category.setdefault(memory.category, []).append(memory)

        merged: list[dict] = []
        db = self._store._ensure_connected()
        for category, memories in by_category.items():
            if len(memories) < 2:
                continue
            by_id = {memory.id: memory for memory in memories}
            placeholders = ",".join("?" * len(by_id))
            rows = db.execute(
                f"SELECT memory_id, vector FROM embeddings WHERE memory_id IN ({placeholders})",
                list(by_id),
            ).fetchall()
            vectors: dict[str, np.ndarray] = {
                row["memory_id"]: decode_vector(bytes(row["vector"])) for row in rows
            }
            for group in self._similar_groups(vectors):
                group_memories = sorted((by_id[memory_id] for memory_id in group),
                                        key=lambda memory: memory.timestamp)
                content = (
                    f"🌙 記憶整理で統合: {category}の記録 {len(group_memories)}件"
                    f"（{group_memories[0].timestamp[:10]}〜{group_memories[-1].timestamp[:10]}）。"
                    f"主な内容: {_truncate_at_boundary(group_memories[0].content, 150)}"
                )
                merged.append({"group": [memory.id for memory in group_memories], "into": content})
                if not dry_run:
                    await self._store.merge_memories(
                        source_ids=[memory.id for memory in group_memories],
                        merged_content=content,
                        importance=max(memory.importance for memory in group_memories),
                        emotion=max((memory.emotion for memory in group_memories),
                                    key=lambda emotion: EMOTION_BOOST_MAP.get(emotion, 0.0)),
                        category=category,
                    )
        return merged

    def _similar_groups(self, vectors: dict[str, np.ndarray]) -> list[list[str]]:
        """Greedy grouping: each memory takes every not-yet-grouped one similar enough to it."""
        available = list(vectors)
        groups: list[list[str]] = []
        while available:
            seed = available.pop(0)
            group = [seed]
            for other in list(available):
                similarity = float(cosine_similarity(vectors[seed], vectors[other].reshape(1, -1))[0])
                if similarity >= self._config.similarity_threshold:
                    group.append(other)
                    available.remove(other)
            if len(group) >= 2:
                groups.append(group)
        return groups


async def _run(dry_run: bool) -> SleepStats:
    store = MemoryStore(MemoryConfig.from_env())
    await store.connect()
    try:
        return await SleepEngine(store).run(dry_run=dry_run)
    finally:
        await store.disconnect()


def main(argv: list[str] | None = None) -> None:
    """`memory-sleep [--dry-run]`: one night's consolidation; prints what it did as JSON."""
    parser = argparse.ArgumentParser(description="nightly memory consolidation, decay and forgetting")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = parser.parse_args(argv)
    stats = asyncio.run(_run(args.dry_run))
    print(json.dumps(asdict(stats) | {"counts": {"merged": len(stats.merged), "decayed": len(stats.decayed),
                                                 "forgotten": len(stats.forgotten)}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
