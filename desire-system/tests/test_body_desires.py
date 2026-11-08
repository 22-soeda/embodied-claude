"""Desires defined per body (DESIRE_CONFIG_FILE) and moved by the body's state."""

import json
from datetime import datetime, timezone

import pytest

import desire_updater
from desire_updater import (
    BODY_STATE_MAX_AGE_SECS,
    apply_body_effects,
    compute_desires,
    load_body_state,
    load_desire_file,
    record_satisfaction,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
NOW_TS = NOW.timestamp()
BATTERY_LOW = {"when": "battery_low", "below": 20, "effects": {"*": {"multiply": 0.5}}}
RECENT_CONTACT = {"when": "contact_within", "minutes": 20,
                  "effects": {"miss_companion": {"multiply": 0.5}}}


class NeverSatisfied:
    """A memory store with nothing in it: every desire is at 1.0."""

    _is_desire_adapter = True

    def __init__(self):
        self.recorded = []

    def latest_satisfaction_ts(self, keywords):
        return None

    def record_satisfaction(self, *, desire_name, summary, ts, metadata=None):
        self.recorded.append((desire_name, summary))
        return "id"


def test_a_desire_file_replaces_the_set_and_fills_in_names(tmp_path):
    path = tmp_path / "desires.json"
    path.write_text(json.dumps({
        "desires": {"miss_companion": {"label": "{companion}と話したい", "satisfaction_hours": 3,
                                       "keywords": ["{companion}と話した"]}},
        "body_effects": [BATTERY_LOW],
        "priority": ["want_to_sleep", "miss_companion"],
    }), encoding="utf-8")
    loaded = load_desire_file(path)
    configs = loaded.desires
    companion = desire_updater.COMPANION_NAME
    assert list(configs) == ["miss_companion"]
    assert configs["miss_companion"].label == f"{companion}と話したい"
    assert configs["miss_companion"].keywords == [f"{companion}と話した"]
    assert configs["miss_companion"].set_point == 0.3
    assert loaded.body_effects == [BATTERY_LOW]
    assert loaded.priority == ["miss_companion"]  # names not defined in the file are dropped


def test_a_cross_effect_moves_other_desires_past_a_threshold():
    sleepy = [{"when": {"desire": "want_to_sleep", "op": ">", "value": 0.8},
               "effects": {"browse_curiosity": {"multiply": 0.5}}}]
    assert desire_updater.apply_cross_effects(
        {"want_to_sleep": 0.9, "browse_curiosity": 0.8}, sleepy) == {
        "want_to_sleep": 0.9, "browse_curiosity": 0.4}
    assert desire_updater.apply_cross_effects(
        {"want_to_sleep": 0.5, "browse_curiosity": 0.8}, sleepy)["browse_curiosity"] == 0.8


def test_with_a_priority_the_strongest_desire_leads_and_ties_follow_the_order(monkeypatch):
    monkeypatch.setattr(desire_updater, "PRIORITY", ["want_to_sleep", "miss_companion"])
    levels = {"miss_companion": 0.9, "want_to_sleep": 0.9, "browse_curiosity": 0.5}
    discomforts = {"miss_companion": 0.1, "want_to_sleep": 0.1, "browse_curiosity": 0.9}
    assert desire_updater.choose_dominant(levels, discomforts) == "want_to_sleep"


def test_a_low_battery_halves_every_desire():
    levels = {"miss_companion": 1.0, "observe_room": 0.6}
    body = {"battery": 12, "charging": False}
    assert apply_body_effects(levels, body, [BATTERY_LOW], NOW_TS) == {
        "miss_companion": 0.5, "observe_room": 0.3}


def test_charging_is_not_a_low_battery():
    levels = {"miss_companion": 1.0}
    body = {"battery": 12, "charging": True}
    assert apply_body_effects(levels, body, [BATTERY_LOW], NOW_TS) == levels


def test_a_recent_touch_halves_only_wanting_to_talk():
    levels = {"miss_companion": 0.8, "observe_room": 0.8}
    body = {"last_contact_at": NOW_TS - 5 * 60}
    assert apply_body_effects(levels, body, [RECENT_CONTACT], NOW_TS) == {
        "miss_companion": 0.4, "observe_room": 0.8}


def test_an_old_touch_does_nothing():
    levels = {"miss_companion": 0.8}
    body = {"last_contact_at": NOW_TS - 60 * 60}
    assert apply_body_effects(levels, body, [RECENT_CONTACT], NOW_TS) == levels


def test_a_stale_body_state_is_ignored(tmp_path):
    path = tmp_path / "body.json"
    path.write_text(json.dumps({"updated_at": NOW_TS - BODY_STATE_MAX_AGE_SECS - 1, "battery": 5}))
    assert load_body_state(NOW_TS, str(path)) is None


def test_compute_desires_applies_the_body(monkeypatch):
    monkeypatch.setattr(desire_updater, "BODY_EFFECTS", [BATTERY_LOW])
    state = compute_desires(NeverSatisfied(), now=NOW, body={"battery": 10, "charging": False})
    assert set(state.desires.values()) == {0.5}


def test_a_satisfaction_record_starts_with_the_first_keyword():
    adapter = NeverSatisfied()
    record_satisfaction(adapter, "miss_companion", "体で話した")
    marker = desire_updater.DESIRE_CONFIGS["miss_companion"].keywords[0]
    assert adapter.recorded == [("miss_companion", f"{marker}。体で話した")]


def test_satisfying_an_unknown_desire_is_refused():
    with pytest.raises(SystemExit):
        desire_updater.main(["--satisfy", "fly_to_the_moon"])
