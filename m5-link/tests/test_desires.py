import asyncio
import json
import time

from m5_link import desires as desires_module
from m5_link.body import Body
from m5_link.body_diary import BodyDiary
from m5_link.desires import Desires


ALL_NAMES_READ_ELSEWHERE = {
    "miss_companion": {"label": "{companion}と話したい"},
    "observe_room": {"label": "部屋を見たい"},
    "browse_curiosity": {"label": "調べたい"},
}


def make_desires(tmp_path, monkeypatch, levels: dict[str, float], own: str | None = None) -> Desires:
    """own: the text of the agent's copy (default: every name others read); the template has them all too."""
    config = tmp_path / "desire-config.json"
    config.write_text(own if own is not None else json.dumps({"desires": ALL_NAMES_READ_ELSEWHERE}), encoding="utf-8")
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"desires": {**ALL_NAMES_READ_ELSEWHERE,
                                                "observe_room": {"label": "雛形の部屋"}}}), encoding="utf-8")
    snapshot = tmp_path / "desires.json"
    snapshot.write_text(json.dumps({"desires": levels, "dominant": "miss_companion"}), encoding="utf-8")
    monkeypatch.setattr(desires_module, "DESIRE_CONFIG_PATH", config)
    monkeypatch.setattr(desires_module, "TEMPLATE_DESIRE_CONFIG_PATH", template)
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


def test_a_desire_the_agent_added_shows_by_its_own_label(tmp_path, monkeypatch):
    own = json.dumps({"desires": {**ALL_NAMES_READ_ELSEWHERE, "read_the_brain": {"label": "ハルの仕事を読みたい"}}})
    desires = make_desires(tmp_path, monkeypatch, {"read_the_brain": 0.8}, own=own)
    assert desires.strong_lines() == ["ハルの仕事を読みたい（0.80）"]


def test_a_copy_that_cannot_be_read_falls_back_to_the_template_labels(tmp_path, monkeypatch):
    desires = make_desires(tmp_path, monkeypatch, {"observe_room": 0.9}, own='{"desires": {,}}')
    assert desires.strong_lines() == ["雛形の部屋（0.90）"]


def test_a_name_others_read_gone_from_the_copy_reaches_the_heartbeat(tmp_path, monkeypatch):
    own = json.dumps({"desires": {name: spec for name, spec in ALL_NAMES_READ_ELSEWHERE.items()
                                  if name != "browse_curiosity"}})
    desires = make_desires(tmp_path, monkeypatch, {}, own=own)
    digest = tmp_path / "desire-digest.txt"
    desires.write_digest(digest)
    assert digest.read_text(encoding="utf-8").splitlines() == [
        "（欲求の設定）desire-config.json に browse_curiosity が無い。ほかの仕組みが名前で読むので、この名前は残してほしい"
    ]


def test_falling_asleep_is_recorded_only_when_the_agent_wants_to_sleep(tmp_path, monkeypatch):
    recorded = []

    async def fake_update(satisfy=None, summary=""):
        recorded.append(satisfy)
        return True

    without_sleep = make_desires(tmp_path, monkeypatch, {})
    monkeypatch.setattr(without_sleep, "update", fake_update)
    asyncio.run(without_sleep.slept("battery"))

    with_sleep = make_desires(tmp_path, monkeypatch, {}, own=json.dumps(
        {"desires": {**ALL_NAMES_READ_ELSEWHERE, "want_to_sleep": {"label": "眠りたい"}}}))
    monkeypatch.setattr(with_sleep, "update", fake_update)
    asyncio.run(with_sleep.slept("battery"))

    assert recorded == ["want_to_sleep"]


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
