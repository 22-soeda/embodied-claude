"""
Desire Updater - エージェントの自発的な欲求レベルを計算してJSONに保存する。

ChromaDB（memory-mcp）から各欲求に関連する最新記憶のタイムスタンプを取得し、
「最後に〇〇してから何時間か」を計算して欲求レベル(0.0〜1.0)を算出する。

v2: ホメオスタシス/アロスタシス拡張
- 各欲求にセットポイント（平衡値）を追加
- 不快度（discomfort）= セットポイントからの乖離度
- dominantは不快度が最も高いものに
- アロスタシス: 時間帯によるセットポイントの予測的調整
- 新欲求: identity_coherence, cognitive_load

cronで5分ごとに実行:
  */5 * * * * cd /path/to/desire-system && uv run python desire_updater.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from backend import (
    ChromaMemoryAdapter,
    DesireMemoryAdapter,
    NullMemoryAdapter,
    SQLiteMemoryAdapter,
    make_default_adapter,
)

load_dotenv()

# 欲求レベル出力先
# expanduser() is required. The default is built from Path.home() and is fine,
# but .env carries DESIRES_PATH=~/.claude/desires.json as a plain string, and an
# unexpanded tilde is a relative path: the updater created a directory literally
# named `~` under its working directory and wrote there. It had been doing so
# since at least 2026-04-08, which is why the file the kernel reads stopped
# changing on 2026-05-18 while the updater reported success every five minutes.
DESIRES_PATH = Path(
    os.getenv("DESIRES_PATH", str(Path.home() / ".claude" / "desires.json"))
).expanduser()

# 一緒にいる人の名前（miss_companion 欲求で使う）
COMPANION_NAME = os.getenv("COMPANION_NAME", "あなた")

# 自分（エージェント）の名前と一人称（identity_coherence 欲求で使う）。
# COMPANION_NAME と対になる設定。ここを固定名にすると、別の名前で運用している
# エージェントでは identity_coherence のキーワードが一致せず、自己同一性の欲求
# だけが満たされにくくなる。しかもキーワード不一致はエラーを出さないため、
# 「最近その話をしていないだけ」と区別がつかない。
SELF_NAME = os.getenv("SELF_NAME", "自分")
SELF_PRONOUN = os.getenv("SELF_PRONOUN", "自分")

# ──────────────────────────────────────────────
# 時間帯（アロスタシス用）。既定値はこれまでの JST / 0-5 時 / 5-7 時と同じ。
# ──────────────────────────────────────────────
#
# タイムゾーンも深夜帯も固定だと、JST 以外の地域では現地の昼間に「深夜モード」が
# 発火し、夜勤の生活では逆に効く。しかもエラーもログも出ないので、外からは
# 「今日はあまり寂しがらないな」としか見えない。env で上書きできるようにする。
#
# DESIRE_TIMEZONE : IANA 名（Asia/Tokyo）か固定オフセット（+09:00 / -05:00）。
#                   解決できなければ +09:00 に落ちる（クラッシュしない）。
# DESIRE_NIGHT_START / DESIRE_NIGHT_END : 深夜帯 [start, end)。end < start なら
#                   日付をまたぐ帯（22-5 時など）として扱う。
# DESIRE_DAWN_END : 早朝帯 [NIGHT_END, DAWN_END)。
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_NIGHT_START = 0
DEFAULT_NIGHT_END = 5
DEFAULT_DAWN_END = 7

_OFFSET_RE = re.compile(r"^([+-])(\d{1,2})(?::?(\d{2}))?$")


def _fixed_offset(spec: str) -> tzinfo | None:
    """Parse ``+09:00`` / ``-0500`` / ``+9`` into a fixed-offset timezone."""
    m = _OFFSET_RE.match(spec.strip())
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    if hours > 23 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes), spec.strip())


def resolve_timezone(spec: str | None = None) -> tzinfo:
    """Resolve ``DESIRE_TIMEZONE`` to a tzinfo, defaulting to Asia/Tokyo.

    Accepts an IANA name or a fixed offset. When zoneinfo cannot resolve the
    name (Windows without tzdata, or a typo) it falls back to the fixed +09:00
    that this module always used, rather than failing a body sense over a
    configuration string.
    """
    raw = (spec if spec is not None else os.getenv("DESIRE_TIMEZONE", "")).strip()
    if not raw:
        raw = DEFAULT_TIMEZONE
    fixed = _fixed_offset(raw)
    if fixed is not None:
        return fixed
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(timedelta(hours=9), "JST")


def _env_hour(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value % 24


def quiet_bands() -> tuple[int, int, int]:
    """Return ``(night_start, night_end, dawn_end)`` hours from env (or defaults)."""
    return (
        _env_hour("DESIRE_NIGHT_START", DEFAULT_NIGHT_START),
        _env_hour("DESIRE_NIGHT_END", DEFAULT_NIGHT_END),
        _env_hour("DESIRE_DAWN_END", DEFAULT_DAWN_END),
    )


def hour_in_band(hour: int, start: int, end: int) -> bool:
    """``start <= hour < end`` on a 24-hour clock, wrapping past midnight if end < start."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end



# ──────────────────────────────────────────────
# DesireConfig: 欲求の定義（セットポイント付き）
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class DesireConfig:
    """欲求の設定。"""
    satisfaction_hours: float   # この時間で欲求レベルが1.0に達する
    set_point: float            # 平衡値（ここが「心地よい」レベル）
    keywords: list[str]         # ChromaDB検索キーワード
    label: str                  # 日本語ラベル
    description: str = ""       # 何の気持ちか（設定ファイルの欲求だけ。get_desires の説明に出す）


DESIRE_CONFIGS: dict[str, DesireConfig] = {
    "look_outside": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_LOOK_OUTSIDE_HOURS", "1.0")),
        set_point=0.3,
        keywords=["外を見た", "空を見た", "夜景", "朝の空", "ベランダから見た", "窓から外を"],
        label="外を見たい",
    ),
    "browse_curiosity": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_BROWSE_CURIOSITY_HOURS", "2.0")),
        set_point=0.3,
        keywords=["WebSearchで調べた", "WebSearch", "検索した", "調査した", "論文を読んだ"],
        label="何か調べたい",
    ),
    "miss_companion": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_MISS_COMPANION_HOURS", "3.0")),
        set_point=0.3,
        keywords=[
            f"{COMPANION_NAME}の顔を見た", f"{COMPANION_NAME}を見た",
            f"{COMPANION_NAME}がいた", f"{COMPANION_NAME}を確認した",
        ],
        label=f"{COMPANION_NAME}に会いたい",
    ),
    "observe_room": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_OBSERVE_ROOM_HOURS", "0.167")),
        set_point=0.2,
        keywords=["look_around", "部屋を観察した", "カメラで部屋を", "4方向"],
        label="部屋を観察したい",
    ),
    "identity_coherence": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_IDENTITY_COHERENCE_HOURS", "1.0")),
        set_point=0.9,
        keywords=[
            f"{SELF_NAME}として", f"{SELF_PRONOUN}は", f"自分が{SELF_NAME}",
            "記憶を引いた", "recallした", "思い出した",
        ],
        label=f"自分が{SELF_NAME}である確信",
    ),
    "cognitive_load": DesireConfig(
        satisfaction_hours=float(os.getenv("DESIRE_COGNITIVE_LOAD_HOURS", "1.5")),
        set_point=0.3,
        keywords=[
            "考えた", "分析した", "議論した", "設計した", "実装した",
            "コードを書いた", "問題を解いた",
        ],
        label="頭を使いたい",
    ),
}

# ──────────────────────────────────────────────
# 設定ファイルによる差し替え（体ごとの欲求）
# ──────────────────────────────────────────────
#
# DESIRE_CONFIG_FILE に JSON を置くと、上の既定の欲求を丸ごと差し替える。体に
# よってできることが違う（首振りカメラなら外を見られるが、M5Stack の体は見られ
# ない）ので、こなせない欲求を外し、満たす言葉をその体の言い方に合わせるため。
# PetitOnes の desire_config.json と同じ考え方。
#
#   {"desires": {"miss_companion": {"label": "{companion}と話したい",
#                                   "satisfaction_hours": 3, "set_point": 0.3,
#                                   "keywords": ["{companion}と話した", ...]}},
#    "body_effects": [ ... 下の「体の状態の効き」 ... ],
#    "cross_effects": [{"when": {"desire": "want_to_sleep", "op": ">", "value": 0.8},
#                       "effects": {"browse_curiosity": {"multiply": 0.5}}}],
#    "priority": ["want_to_sleep", "miss_companion", ...]}
#
# cross_effects と priority も PetitOnes のとおり。ある欲求がしきい値を越えたら
# ほかの欲求を動かし、主な欲求（dominant）は「いちばん強いもの、同じなら
# priority の順」で決める（ファイルに priority が無ければ、これまでどおり不快度で）。
#
# 欲求の名前は、既定にあるものは同じ名前を使う。individual-kernel の allostasis と
# interaction-orchestrator が名前で読むので。{companion} {self} {pronoun} は
# COMPANION_NAME / SELF_NAME / SELF_PRONOUN に置き換わる。読めないファイルは
# 標準エラーに一行出して既定の欲求に戻る（体の感覚ひとつで止まらないように）。


def _fill_names(text: str) -> str:
    return (
        text.replace("{companion}", COMPANION_NAME)
        .replace("{self}", SELF_NAME)
        .replace("{pronoun}", SELF_PRONOUN)
    )


@dataclass(frozen=True)
class DesireFile:
    """What a DESIRE_CONFIG_FILE holds."""

    desires: dict[str, DesireConfig]
    body_effects: list[dict[str, Any]]
    cross_effects: list[dict[str, Any]]
    priority: list[str]


def load_desire_file(path: Path) -> DesireFile:
    """Read a desire config file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    configs = {
        name: DesireConfig(
            satisfaction_hours=float(spec["satisfaction_hours"]),
            set_point=float(spec.get("set_point", 0.3)),
            keywords=[_fill_names(keyword) for keyword in spec.get("keywords", [])],
            label=_fill_names(spec.get("label", name)),
            description=_fill_names(spec.get("description", "")),
        )
        for name, spec in raw.get("desires", {}).items()
    }
    if not configs:
        raise ValueError("no desires in the file")
    return DesireFile(
        desires=configs,
        body_effects=list(raw.get("body_effects", [])),
        cross_effects=list(raw.get("cross_effects", [])),
        priority=[name for name in raw.get("priority", []) if name in configs],
    )


BODY_EFFECTS: list[dict[str, Any]] = []
CROSS_EFFECTS: list[dict[str, Any]] = []
PRIORITY: list[str] = []  # empty: the dominant desire is the most uncomfortable one
DESIRE_CONFIG_FILE = os.getenv("DESIRE_CONFIG_FILE", "").strip()
if DESIRE_CONFIG_FILE:
    try:
        _desire_file = load_desire_file(Path(DESIRE_CONFIG_FILE).expanduser())
        DESIRE_CONFIGS = _desire_file.desires
        BODY_EFFECTS = _desire_file.body_effects
        CROSS_EFFECTS = _desire_file.cross_effects
        PRIORITY = _desire_file.priority
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            f"[desire-updater] DESIRE_CONFIG_FILE={DESIRE_CONFIG_FILE} を読めなかったので"
            f"既定の欲求を使う: {error}",
            file=sys.stderr,
        )


# ──────────────────────────────────────────────
# 体の状態の効き（PetitOnes の sensor_effects と同じ考え方）
# ──────────────────────────────────────────────
#
# 体を持つ側（m5-link など）が DESIRE_BODY_STATE_FILE に今の体の状態を書く:
#   {"updated_at": <unix 秒>, "battery": 76, "charging": true,
#    "last_contact_at": <最後に触れられた unix 秒 | null>}
# 設定ファイルの body_effects がそれを見て、記憶から計算した欲求レベルを増減する:
#   {"when": "battery_low", "below": 20, "effects": {"*": {"multiply": 0.5}}}
#   {"when": "contact_within", "minutes": 20, "effects": {"miss_companion": {"multiply": 0.5}}}
# 古い状態は使わない。書く側が止まっていれば、体はもう感じていないので。
BODY_STATE_MAX_AGE_SECS = 15 * 60


def load_body_state(now_ts: float, path: str | None = None) -> dict[str, Any] | None:
    """The body's state, or None when there is none or it is stale."""
    path = path if path is not None else os.getenv("DESIRE_BODY_STATE_FILE", "").strip()
    if not path:
        return None
    try:
        body = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    age_secs = now_ts - float(body.get("updated_at", 0))
    return body if age_secs <= BODY_STATE_MAX_AGE_SECS else None


def body_effect_applies(effect: dict[str, Any], body: dict[str, Any], now_ts: float) -> bool:
    when = effect.get("when")
    if when == "battery_low":
        battery = body.get("battery")
        below = float(effect.get("below", 20))
        return battery is not None and 0 <= float(battery) < below and not body.get("charging")
    if when == "contact_within":
        last_contact = body.get("last_contact_at")
        window_secs = float(effect.get("minutes", 20)) * 60
        return last_contact is not None and now_ts - float(last_contact) <= window_secs
    return False


def apply_body_effects(
    levels: dict[str, float],
    body: dict[str, Any] | None,
    effects: list[dict[str, Any]],
    now_ts: float,
) -> dict[str, float]:
    """Raise or lower desire levels by what the body is going through. "*" means every desire."""
    adjusted = dict(levels)
    if not body:
        return adjusted
    for effect in effects:
        if body_effect_applies(effect, body, now_ts):
            _apply_changes(adjusted, effect.get("effects", {}))
    return adjusted


_COMPARISONS = {
    ">": lambda level, value: level > value,
    ">=": lambda level, value: level >= value,
    "<": lambda level, value: level < value,
    "<=": lambda level, value: level <= value,
}


def apply_cross_effects(
    levels: dict[str, float], effects: list[dict[str, Any]]
) -> dict[str, float]:
    """When one desire passes a threshold, move others (PetitOnes' cross_effects).

    {"when": {"desire": "want_to_sleep", "op": ">", "value": 0.8},
     "effects": {"browse_curiosity": {"multiply": 0.5}}}
    """
    adjusted = dict(levels)
    for effect in effects:
        when = effect.get("when", {})
        name = when.get("desire", "")
        compare = _COMPARISONS.get(when.get("op", ">"))
        if name in adjusted and compare and compare(adjusted[name], float(when.get("value", 0))):
            _apply_changes(adjusted, effect.get("effects", {}))
    return adjusted


def _apply_changes(levels: dict[str, float], changes: dict[str, dict[str, float]]) -> None:
    """{"*": {"multiply": 0.5}, "want_to_sleep": {"add": 0.5}}, in order, kept within 0..1."""
    for target, change in changes.items():
        names = list(levels) if target == "*" else [target] if target in levels else []
        for name in names:
            value = levels[name]
            if "multiply" in change:
                value *= float(change["multiply"])
            if "add" in change:
                value += float(change["add"])
            levels[name] = round(max(0.0, min(1.0, value)), 3)


def choose_dominant(desires: dict[str, float], discomforts: dict[str, float]) -> str:
    """With a priority (PetitOnes): the strongest desire, ties broken by priority.
    Without: the most uncomfortable one, as desire-system always did."""
    if not PRIORITY:
        return max(discomforts, key=lambda name: discomforts[name])

    def rank(name: str) -> tuple[float, int]:
        place = PRIORITY.index(name) if name in PRIORITY else len(PRIORITY)
        return (-desires[name], place)

    return min(desires, key=rank)


# 後方互換: 旧モジュールが参照していた変数
DESIRE_KEYWORDS: dict[str, list[str]] = {
    name: cfg.keywords for name, cfg in DESIRE_CONFIGS.items()
}
SATISFACTION_HOURS: dict[str, float] = {
    name: cfg.satisfaction_hours for name, cfg in DESIRE_CONFIGS.items()
}


# ──────────────────────────────────────────────
# DesireState
# ──────────────────────────────────────────────

@dataclass
class DesireState:
    """現在の欲求状態。"""

    updated_at: str
    desires: dict[str, float] = field(default_factory=dict)
    discomforts: dict[str, float] = field(default_factory=dict)
    dominant: str = "observe_room"

    def to_dict(self) -> dict:
        return {
            "updated_at": self.updated_at,
            "desires": self.desires,
            "discomforts": self.discomforts,
            "dominant": self.dominant,
        }


# ──────────────────────────────────────────────
# 計算関数
# ──────────────────────────────────────────────

def calculate_discomfort(level: float, set_point: float) -> float:
    """セットポイントからの乖離度（不快度）を計算する。"""
    return abs(level - set_point)


def get_allostatic_set_point(desire_name: str, now: datetime) -> float:
    """
    アロスタシス: 時間帯によるセットポイントの予測的調整。

    深夜帯（既定 0-5 時）は social 系欲求のセットポイントを下げる（一人でも平気）。
    早朝帯（既定 5-7 時）は徐々に元に戻る。identity_coherence は常に高いまま。
    タイムゾーンと時間帯は DESIRE_TIMEZONE / DESIRE_NIGHT_START / DESIRE_NIGHT_END /
    DESIRE_DAWN_END で上書きできる（既定は従来どおり JST / 0-5 / 5-7）。
    """
    cfg = DESIRE_CONFIGS[desire_name]
    base_sp = cfg.set_point

    # ローカル時刻に変換（naive datetime の場合は UTC とみなす）
    local_tz = resolve_timezone()
    if now.tzinfo is None:
        now_local = now.replace(tzinfo=timezone.utc).astimezone(local_tz)
    else:
        now_local = now.astimezone(local_tz)

    hour = now_local.hour
    night_start, night_end, dawn_end = quiet_bands()

    # identity_coherenceは時間帯に関係なく不変
    if desire_name == "identity_coherence":
        return base_sp

    # 深夜帯の調整
    if hour_in_band(hour, night_start, night_end):
        if desire_name == "miss_companion":
            # 深夜は一人でも平気: セットポイントを下げる
            return max(0.0, base_sp - 0.15)
        if desire_name in ("look_outside", "observe_room"):
            # 深夜は外や部屋を見る欲求も落ち着く
            return max(0.0, base_sp - 0.1)

    # 早朝帯: 徐々に元に戻る
    if hour_in_band(hour, night_end, dawn_end):
        if desire_name == "miss_companion":
            return max(0.0, base_sp - 0.05)

    return base_sp


def get_latest_memory_timestamp(
    adapter_or_collection: Any,
    keywords: list[str],
) -> datetime | None:
    """Return the latest timestamp in memory matching any of ``keywords``.

    Accepts either a :class:`DesireMemoryAdapter` or a legacy Chroma collection.
    """

    return _coerce_adapter(adapter_or_collection).latest_satisfaction_ts(keywords)


def calculate_desire_level(
    last_satisfied: datetime | None,
    satisfaction_hours: float,
    now: datetime | None = None,
) -> float:
    """
    欲求レベルを 0.0〜1.0 で計算する。
    last_satisfied が None（一度も満たされてない）なら 1.0。
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if last_satisfied is None:
        return 1.0

    if last_satisfied.tzinfo is None:
        last_satisfied = last_satisfied.replace(tzinfo=timezone.utc)

    elapsed_hours = (now - last_satisfied).total_seconds() / 3600
    return max(0.0, min(1.0, elapsed_hours / satisfaction_hours))


def compute_desires(
    adapter_or_collection: Any,
    now: datetime | None = None,
    body: dict[str, Any] | None = None,
) -> DesireState:
    """全欲求レベルを計算してDesireStateを返す。

    ``adapter_or_collection`` accepts either a :class:`DesireMemoryAdapter`
    (preferred) or a legacy ChromaDB collection for backwards compatibility
    with pre-v0.3 callers. A Chroma collection is wrapped transparently.

    ``body`` is the body's state for BODY_EFFECTS; when omitted it is read from
    DESIRE_BODY_STATE_FILE (if any), so every caller - the updater, and the MCP
    server recomputing after satisfy_desire - sees the same body.
    """

    if now is None:
        now = datetime.now(timezone.utc)
    now_ts = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()

    adapter = _coerce_adapter(adapter_or_collection)

    desires: dict[str, float] = {}
    discomforts: dict[str, float] = {}

    for desire_name, cfg in DESIRE_CONFIGS.items():
        last_ts = get_latest_memory_timestamp(adapter, cfg.keywords)
        level = calculate_desire_level(last_ts, cfg.satisfaction_hours, now)
        desires[desire_name] = round(level, 3)

    if BODY_EFFECTS:
        body = body if body is not None else load_body_state(now_ts)
        desires = apply_body_effects(desires, body, BODY_EFFECTS, now_ts)
    if CROSS_EFFECTS:
        desires = apply_cross_effects(desires, CROSS_EFFECTS)

    for desire_name, level in desires.items():
        # アロスタシス: 時間帯でセットポイントを調整
        adjusted_sp = get_allostatic_set_point(desire_name, now)
        discomforts[desire_name] = round(calculate_discomfort(level, adjusted_sp), 3)

    dominant = choose_dominant(desires, discomforts)

    return DesireState(
        updated_at=now.isoformat(),
        desires=desires,
        discomforts=discomforts,
        dominant=dominant,
    )


def _coerce_adapter(value: Any) -> DesireMemoryAdapter:
    """Accept either a DesireMemoryAdapter, an adapter-shaped duck, or a chromadb Collection.

    Detection is strict: only objects that are either (a) a concrete built-in
    adapter class or (b) explicitly marked with ``_is_desire_adapter is True``
    are treated as adapters. Anything else is wrapped as a legacy Chroma
    collection. This avoids MagicMock false-positives in tests, because
    ``getattr(mock, "_is_desire_adapter", False)`` returns a child Mock object
    that is truthy but is not identical to ``True``.
    """

    if isinstance(value, (SQLiteMemoryAdapter, ChromaMemoryAdapter, NullMemoryAdapter)):
        return value
    if getattr(value, "_is_desire_adapter", False) is True:
        return value
    # Legacy Chroma collection — wrap it inline.
    collection = value

    class _LegacyAdapter:
        _is_desire_adapter = True

        def latest_satisfaction_ts(self, keywords):
            try:
                results = collection.get(limit=500, include=["documents", "metadatas"])
            except Exception:
                return None
            latest: datetime | None = None
            for doc, meta in zip(
                results.get("documents", []), results.get("metadatas", [])
            ):
                if not any(kw in doc for kw in keywords):
                    continue
                ts_str = (meta or {}).get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if latest is None or ts > latest:
                    latest = ts
            return latest

        def record_satisfaction(self, *, desire_name, summary, ts, metadata=None):
            return ""

    return _LegacyAdapter()


def save_desires(state: DesireState, path: Path = DESIRES_PATH) -> None:
    """desires.json に保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)


def load_desires(path: Path = DESIRES_PATH) -> DesireState | None:
    """desires.json を読み込む。存在しなければ None。"""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return DesireState(
            updated_at=data["updated_at"],
            desires=data["desires"],
            discomforts=data.get("discomforts", {}),
            dominant=data["dominant"],
        )
    except Exception:
        return None


def record_satisfaction(adapter: Any, desire_name: str, summary: str = "") -> str:
    """Write that a desire was met into memory, in words the next computation finds.

    The record starts with the desire's first keyword ("添田と話した。…"),
    which is what makes compute_desires see it.
    """
    marker = DESIRE_CONFIGS[desire_name].keywords[0]
    return _coerce_adapter(adapter).record_satisfaction(
        desire_name=desire_name,
        summary=f"{marker}。{summary or marker}",
        ts=datetime.now(timezone.utc),
        metadata={"outcome": "satisfied"},
    )


def main(argv: list[str] | None = None) -> None:
    """メインエントリポイント（cronから呼ばれる）。

    --satisfy NAME [--summary TEXT] records the desire as met first, for
    callers outside the MCP server (m5-link counts a conversation through the
    body as talking with the companion).
    """
    parser = argparse.ArgumentParser(description="recompute desires.json from memory")
    parser.add_argument("--satisfy", metavar="DESIRE", help="record this desire as met first")
    parser.add_argument("--summary", default="", help="what was done, for the memory record")
    args = parser.parse_args(argv)

    adapter = make_default_adapter()
    if args.satisfy:
        if args.satisfy not in DESIRE_CONFIGS:
            parser.error(f"unknown desire {args.satisfy!r}; known: {', '.join(DESIRE_CONFIGS)}")
        record_satisfaction(adapter, args.satisfy, args.summary)
    state = compute_desires(adapter)
    save_desires(state)
    print(
        f"[desire-updater] 更新完了: dominant={state.dominant} "
        f"desires={state.desires} "
        f"discomforts={state.discomforts} "
        f"backend={type(adapter).__name__}"
    )


if __name__ == "__main__":
    main()
