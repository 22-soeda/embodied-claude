import asyncio
import json
import time

from m5_link import desires as desires_module
from m5_link.body import Body
from m5_link.body_diary import BodyDiary
from m5_link.desires import Desires


def make_desires(tmp_path, monkeypatch, levels: dict[str, float]) -> Desires:
    config = tmp_path / "desire-config.json"
    config.write_text(json.dumps({"desires": {
        "miss_companion": {"label": "{companion}と話したい"},
        "observe_room": {"label": "部屋を見たい"},
    }}), encoding="utf-8")
    snapshot = tmp_path / "desires.json"
    snapshot.write_text(json.dumps({"desires": levels, "dominant": "miss_companion"}), encoding="utf-8")
    monkeypatch.setattr(desires_module, "DESIRE_CONFIG_PATH", config)
    monkeypatch.setattr(desires_module, "DESIRES_PATH", snapshot)
    return Desires(Body(), BodyDiary(tmp_path / "diary"), companion_name="ハル")


def test_only_strong_desires_reach_the_heartbeat(tmp_path, monkeypatch):
    desires = make_desires(tmp_path, monkeypatch, {"miss_companion": 0.72, "observe_room": 0.3})
    assert desires.strong_lines() == ["ハルと話したい（0.72）"]


def test_no_strong_desire_means_no_digest_file(tmp_path, monkeypatch):
    desires = make_desires(tmp_path, monkeypatch, {"miss_companion": 0.2})
    digest = tmp_path / "desire-digest.txt"
    digest.write_text("old", encoding="utf-8")
    desires.write_digest(digest)
    assert not digest.exists()


def test_talking_is_recorded_at_most_every_half_hour(tmp_path, monkeypatch):
    desires = make_desires(tmp_path, monkeypatch, {})
    recorded = []

    async def fake_update(satisfy=None, summary=""):
        recorded.append((satisfy, summary))
        return True

    monkeypatch.setattr(desires, "update", fake_update)
    asyncio.run(desires.talked("声"))
    asyncio.run(desires.talked("声"))
    assert recorded == [("miss_companion", "体越しに声で話しかけてくれた")]


def test_the_last_touch_is_found_in_the_diary(tmp_path):
    diary = BodyDiary(tmp_path)
    an_hour_ago = round(time.time() - 3600, 3)
    diary.record("picked_up", at=an_hour_ago)
    diary.record("near")  # later, but not a touch
    assert diary.last_event_at({"picked_up", "shaken"}, within_hours=2) == an_hour_ago
