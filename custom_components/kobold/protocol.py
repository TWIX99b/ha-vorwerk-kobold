"""Pure parsing helpers for the Vorwerk Kobold cloud (no Home Assistant imports).

Everything in here works on plain ``dict``/``list`` data so it can be unit
tested without Home Assistant. The parsers are deliberately tolerant: the
cloud adds and drops fields over time, and a single unknown or missing field
must never cause a whole state update to be thrown away.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import time
from typing import Any

# Home Assistant VacuumActivity values (kept as plain strings here)
ACTIVITY_CLEANING = "cleaning"
ACTIVITY_DOCKED = "docked"
ACTIVITY_IDLE = "idle"
ACTIVITY_PAUSED = "paused"
ACTIVITY_RETURNING = "returning"
ACTIVITY_ERROR = "error"

CLEANING_ACTIONS = frozenset(
    {"cleaning", "fast_mapping", "mapping", "map_creation", "spot_cleaning"}
)
RETURNING_ACTIONS = frozenset({"docking", "returning", "going_home"})

FAN_SPEEDS = ("auto", "eco", "turbo", "pet")

BAG_STATES = ("ok", "full", "missing", "other")

# Error codes with a translated name; anything else is reported as "other"
KNOWN_ERRORS = (
    "brush_stuck",
    "bin_full",
    "dustbin_missing",
    "cleaning_path_blocked",
    "navigation_path_problems_returning_home",
    "bumper_stuck",
    "wheel_stuck",
    "robot_stuck",
    "battery_low",
)
ERROR_OPTIONS = ("none", *KNOWN_ERRORS, "other")

MODEL_NAMES = {"VRXX": "Kobold VR7", "VR7": "Kobold VR7"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RobotError:
    """One error or warning reported by the robot."""

    code: str
    severity: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity in (None, "", "error")


@dataclass
class RobotState:
    """Normalised robot state (from ``last_state``/``state_changed``)."""

    state: str | None = None
    action: str | None = None
    charge: int | None = None
    is_charging: bool = False
    is_docked: bool = False
    base_type: str | None = None
    commands: frozenset[str] = frozenset()
    errors: tuple[RobotError, ...] = ()
    bag_status: str | None = None
    base_state: str | None = None
    base_error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def activity(self) -> str:
        return map_activity(self)

    @property
    def active_errors(self) -> tuple[RobotError, ...]:
        return tuple(err for err in self.errors if err.is_error)

    @property
    def bag(self) -> str | None:
        return normalise_bag_status(self.bag_status)

    @property
    def has_cleaning_center(self) -> bool:
        """True for bases that report a dust bag (e.g. the RB7 service station)."""
        if self.bag_status or self.base_state:
            return True
        return self.base_type not in (None, "", "standard")

    @property
    def error_option(self) -> str:
        if not self.errors:
            return "none"
        code = self.errors[0].code
        return code if code in KNOWN_ERRORS else "other"


def parse_robot_state(body: Any) -> RobotState:
    """Parse the robot state body, ignoring unknown and missing fields."""
    body = _as_dict(body)
    details = _as_dict(body.get("details"))
    center = _as_dict(body.get("cleaning_center"))
    commands_raw = _as_dict(body.get("available_commands"))

    errors: list[RobotError] = []
    for item in body.get("errors") or []:
        if isinstance(item, dict) and item.get("code"):
            errors.append(
                RobotError(code=str(item["code"]), severity=item.get("severity"))
            )
        elif isinstance(item, str) and item:
            errors.append(RobotError(code=item))

    return RobotState(
        state=body.get("state") or None,
        action=body.get("action") or None,
        charge=_as_int(details.get("charge")),
        is_charging=_as_bool(details.get("is_charging")),
        is_docked=_as_bool(details.get("is_docked")),
        base_type=details.get("base_type") or None,
        commands=frozenset(k for k, v in commands_raw.items() if _as_bool(v)),
        errors=tuple(errors),
        bag_status=center.get("bag_status") or None,
        base_state=center.get("state") or None,
        base_error=center.get("base_error") or None,
        raw=body,
    )


def map_activity(robot: RobotState) -> str:
    """Map a robot state to a Home Assistant vacuum activity."""
    if robot.state == "error" or robot.active_errors:
        return ACTIVITY_ERROR
    if robot.action in CLEANING_ACTIONS and robot.state != "paused":
        return ACTIVITY_CLEANING
    if robot.action in RETURNING_ACTIONS:
        return ACTIVITY_RETURNING
    if robot.state == "paused":
        return ACTIVITY_PAUSED
    if robot.is_docked or robot.is_charging:
        return ACTIVITY_DOCKED
    if robot.state == "busy":
        return ACTIVITY_CLEANING if robot.action else ACTIVITY_IDLE
    return ACTIVITY_IDLE


def normalise_bag_status(raw: str | None) -> str | None:
    """Map ``bag_ok``/``bag_full``/… onto a fixed set of options."""
    if not raw:
        return None
    value = raw.lower().removeprefix("bag_")
    return value if value in BAG_STATES else "other"


@dataclass
class CleaningRun:
    track_uuid: str | None
    track_name: str | None
    state: str | None
    area: float | None


@dataclass
class CleaningProgress:
    """Normalised ``cleaning_state`` message."""

    cleaning_type: str | None = None
    floorplan_uuid: str | None = None
    started_by: str | None = None
    start: str | None = None
    end: str | None = None
    runs: tuple[CleaningRun, ...] = ()

    @property
    def area(self) -> float | None:
        areas = [run.area for run in self.runs if run.area is not None]
        return round(sum(areas), 2) if areas else None


def parse_cleaning_state(payload: Any) -> CleaningProgress:
    """Parse a ``cleaning_state`` payload (Phoenix ``body`` or plain ``state``)."""
    payload = _as_dict(payload)
    body = _as_dict(payload.get("body") or payload.get("state") or payload)
    timing = _as_dict(body.get("timing"))
    runs = []
    for run in body.get("runs") or []:
        run = _as_dict(run)
        stats = _as_dict(run.get("stats"))
        runs.append(
            CleaningRun(
                track_uuid=run.get("track_uuid"),
                track_name=run.get("track_name"),
                state=run.get("state"),
                area=_as_float(stats.get("area")),
            )
        )
    return CleaningProgress(
        cleaning_type=body.get("cleaning_type") or None,
        floorplan_uuid=body.get("floorplan_uuid"),
        started_by=body.get("started_by") or None,
        start=timing.get("start") or None,
        end=timing.get("end") or None,
        runs=tuple(runs),
    )


# --- Phoenix channel protocol -------------------------------------------------


@dataclass(frozen=True)
class SocketEvent:
    """A decoded websocket message."""

    kind: str  # "state", "cleaning", "reply_ok", "reply_error", "other"
    robot_id: str | None
    payload: Any


def robot_topic(robot_id: str) -> str:
    return f"robots:{robot_id}"


def phx_message(
    join_ref: str | None, ref: str, topic: str, event: str, payload: dict
) -> str:
    return json.dumps([join_ref, ref, topic, event, payload])


def _robot_from_topic(topic: Any) -> str | None:
    if isinstance(topic, str) and topic.startswith("robots:"):
        return topic.split(":", 1)[1]
    return None


def decode_socket_message(raw: str | bytes) -> list[SocketEvent]:
    """Decode one websocket text frame into zero or more events."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []

    if isinstance(data, list) and len(data) >= 5:
        topic, event, payload = data[2], data[3], data[4]
        robot_id = _robot_from_topic(topic)
        payload = _as_dict(payload)
        if event == "phx_reply":
            status = payload.get("status")
            response = _as_dict(payload.get("response"))
            if status not in (None, "ok"):
                return [SocketEvent("reply_error", robot_id, payload)]
            if "body" in response and _looks_like_state(response["body"]):
                return [SocketEvent("state", robot_id, response["body"])]
            return [SocketEvent("reply_ok", robot_id, response)]
        if event in ("last_state", "state_changed"):
            body = payload.get("body", payload.get("state"))
            if body is not None:
                return [SocketEvent("state", robot_id, body)]
            return []
        if event == "cleaning_state":
            return [SocketEvent("cleaning", robot_id, payload)]
        if event == "phx_error":
            return [SocketEvent("reply_error", robot_id, payload)]
        return [SocketEvent("other", robot_id, {"event": event, "payload": payload})]

    if isinstance(data, dict):
        event_type = data.get("event_type")
        payload = data.get("payload")
        robot_id = _as_dict(payload).get("robot_id") or data.get("robot_id")
        if event_type == "state_changed":
            body = _as_dict(payload).get("state")
            if body is not None:
                return [SocketEvent("state", robot_id, body)]
            return []
        if event_type == "cleaning_state":
            return [SocketEvent("cleaning", robot_id, payload)]
        return [SocketEvent("other", robot_id, data)]

    return []


def _looks_like_state(body: Any) -> bool:
    body = _as_dict(body)
    return any(key in body for key in ("state", "details", "available_commands"))


# --- Tokens -------------------------------------------------------------------


def jwt_expiry(token: str | None) -> float | None:
    """Return the ``exp`` claim (unix time) of a JWT without verifying it."""
    if not token or token.count(".") < 2:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return float(exp) if isinstance(exp, (int, float)) else None


def token_valid(token: str | None, margin: float = 300, now: float | None = None) -> bool:
    """True if the token has no known expiry or expires later than ``margin``."""
    exp = jwt_expiry(token)
    if exp is None:
        return bool(token)
    return exp - margin > (now if now is not None else time.time())


# --- Maps / segments ----------------------------------------------------------


@dataclass(frozen=True)
class Zone:
    track_uuid: str
    name: str


@dataclass(frozen=True)
class FloorPlan:
    floorplan_uuid: str
    name: str
    zones: tuple[Zone, ...] = ()
    image: bytes | None = field(default=None, repr=False, compare=False)
    updated_at: str | None = None


def decode_png(value: Any) -> bytes | None:
    """Decode a base64 PNG from the cloud; None if it is not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        return None
    return raw if raw.startswith(b"\x89PNG") else None


def parse_floorplan(data: Any) -> FloorPlan | None:
    data = _as_dict(data)
    uuid = data.get("floorplan_uuid")
    if not uuid:
        return None
    image = decode_png(data.get("processed_rank_binary")) or decode_png(
        data.get("processed_real_binary")
    )
    return FloorPlan(
        floorplan_uuid=str(uuid),
        name=str(data.get("name") or "").strip(),
        image=image,
        updated_at=data.get("updated_at") or data.get("promoted_at"),
    )


@dataclass(frozen=True)
class CleaningMap:
    """Thumbnail of a finished cleaning run."""

    start: str | None
    floorplan_uuid: str | None
    started_by: str | None
    image: bytes | None = field(default=None, repr=False, compare=False)


def parse_cleaning_maps(data: Any) -> CleaningMap | None:
    """Return the most recent cleaning map that has an image."""
    best: CleaningMap | None = None
    for item in data or []:
        item = _as_dict(item)
        image = decode_png(item.get("processed_thumb_rank_binary")) or decode_png(
            item.get("processed_thumb_real_binary")
        )
        if image is None:
            continue
        start = item.get("start_time") or _as_dict(item.get("timing")).get("start")
        candidate = CleaningMap(
            start=start,
            floorplan_uuid=item.get("floorplan_uuid"),
            started_by=item.get("started_by"),
            image=image,
        )
        if best is None or (start or "") > (best.start or ""):
            best = candidate
    return best


def parse_zones(data: Any) -> tuple[Zone, ...]:
    zones = []
    for item in data or []:
        item = _as_dict(item)
        uuid = item.get("track_uuid")
        if not uuid:
            continue
        if item.get("type") not in (None, "cleaning"):
            continue
        zones.append(Zone(track_uuid=str(uuid), name=str(item.get("name") or "").strip()))
    return tuple(zones)


SEGMENT_SEPARATOR = "/"


def segment_id(floorplan_uuid: str, track_uuid: str) -> str:
    return f"{floorplan_uuid}{SEGMENT_SEPARATOR}{track_uuid}"


def split_segment_id(value: str) -> tuple[str, str] | None:
    if SEGMENT_SEPARATOR not in value:
        return None
    floorplan, track = value.split(SEGMENT_SEPARATOR, 1)
    if not floorplan or not track:
        return None
    return floorplan, track


def build_cleaning_request(
    fan_speed: str,
    floorplan_uuid: str | None = None,
    track_uuids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the body for ``POST /robots/{id}/cleaning/v2``."""
    mode = fan_speed if fan_speed in FAN_SPEEDS else "auto"
    settings = {"mode": mode, "navigation_mode": "normal"}
    if floorplan_uuid is None:
        runs = [{"settings": settings, "map": None}]
    elif track_uuids:
        runs = [
            {
                "settings": dict(settings),
                "map": {
                    "floorplan_uuid": floorplan_uuid,
                    "zone_uuid": track,
                    "nogo_enabled": True,
                },
            }
            for track in track_uuids
        ]
    else:
        runs = [
            {
                "settings": settings,
                "map": {
                    "floorplan_uuid": floorplan_uuid,
                    "zone_uuid": None,
                    "nogo_enabled": True,
                },
            }
        ]
    return {"runs": runs, "ability": "cleaning.start"}


# --- Schedules ------------------------------------------------------------------

DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_LABELS = {
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
_DAY_GROUPS = {
    "de": {"all": "täglich", "workdays": "Mo–Fr", "weekend": "Wochenende"},
    "en": {"all": "daily", "workdays": "Mon–Fri", "weekend": "weekend"},
}


@dataclass(frozen=True)
class ScheduleEvent:
    uuid: str
    enabled: bool
    days: tuple[str, ...]
    time: str
    mode: str | None = None
    floorplan_uuid: str | None = None
    zone_uuids: tuple[str, ...] = ()
    command: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _norm_day(value: Any) -> str | None:
    text = str(value or "").strip().lower()[:3]
    return text if text in DAY_ORDER else None


def parse_scheduling(data: Any) -> tuple[bool, tuple[ScheduleEvent, ...]]:
    data = _as_dict(data)
    events = []
    for item in data.get("events") or []:
        item = _as_dict(item)
        uuid = item.get("schedule_uuid") or item.get("scheduleUuid")
        time_ = str(item.get("time") or "")
        if not uuid or len(time_) < 4:
            continue
        days = tuple(
            day for day in DAY_ORDER if day in {_norm_day(d) for d in item.get("days") or []}
        )
        command = _as_dict(item.get("command"))
        runs = [_as_dict(r) for r in command.get("runs") or []]
        first = runs[0] if runs else {}
        events.append(
            ScheduleEvent(
                uuid=str(uuid),
                enabled=_as_bool(item.get("enabled")),
                days=days,
                time=time_[:5],
                mode=_as_dict(first.get("settings")).get("mode"),
                floorplan_uuid=_as_dict(first.get("map")).get("floorplan_uuid"),
                zone_uuids=tuple(
                    z for z in (_as_dict(r.get("map")).get("zone_uuid") for r in runs) if z
                ),
                command=command,
            )
        )
    events.sort(key=lambda e: (e.time, e.uuid))
    return _as_bool(data.get("enabled", True)), tuple(events)


def days_label(days: tuple[str, ...], language: str = "de") -> str:
    lang = "de" if language.startswith("de") else "en"
    group = _DAY_GROUPS[lang]
    if set(days) == set(DAY_ORDER):
        return group["all"]
    if set(days) == set(DAY_ORDER[:5]):
        return group["workdays"]
    if set(days) == set(DAY_ORDER[5:]):
        return group["weekend"]
    labels = _DAY_LABELS[lang]
    return ", ".join(labels[DAY_ORDER.index(d)] for d in days)


def schedule_update_body(event: ScheduleEvent, enabled: bool) -> dict[str, Any]:
    """Body for ``PUT /robots/{id}/scheduling/{uuid}`` that only flips ``enabled``."""
    return {
        "enabled": enabled,
        "days": list(event.days),
        "time": event.time,
        "command": event.command,
    }


def next_schedule_run(events: tuple[ScheduleEvent, ...], now: Any) -> Any:
    """Next start (aware datetime in ``now``'s timezone) of any enabled event."""
    from datetime import timedelta  # local import keeps module import cheap

    best = None
    for event in events:
        if not event.enabled or not event.days:
            continue
        try:
            hour, minute = (int(part) for part in event.time.split(":")[:2])
        except ValueError:
            continue
        for offset in range(8):
            day = now + timedelta(days=offset)
            if DAY_ORDER[day.weekday()] not in event.days:
                continue
            start = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start <= now:
                continue
            if best is None or start < best:
                best = start
            break
    return best


# --- Robot language -------------------------------------------------------------


def language_option(code: str) -> str:
    """Cloud language code ("EN-GB") to a Home Assistant option key ("en_gb")."""
    return code.strip().lower().replace("-", "_")


def language_code(option: str, available: list[str]) -> str:
    for code in available:
        if language_option(code) == option:
            return code
    return option.upper().replace("_", "-")


# --- Robot settings -------------------------------------------------------------

# Fields the app sends back in ``PUT /robots/{id}/settings/{name}`` ({"payload": ...}).
SETTING_FIELDS: dict[str, tuple[str, ...]] = {
    "sounds": ("enabled", "volume", "voice"),
    "robot_language": ("language", "available_languages"),
    "object_avoidance": ("enabled", "streaming_level"),
    "overhang_detection": ("enabled",),
}


def setting_payload(name: str, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    merged = {**(current or {}), **changes}
    fields = SETTING_FIELDS.get(name)
    if fields is None:
        return merged
    return {key: merged[key] for key in fields if key in merged}


# --- VK7 stick vacuum (data synced by the MyKobold app) ---------------------------

VK7_BATTERY_STATES = ("good", "medium", "reduced")
VK7_BAG_STATES = ("empty", "half_full", "full")


@dataclass(frozen=True)
class Vk7Tracker:
    tracker_id: str
    name: str
    kind: str | None
    progress: int | None
    counted_hours: float | None
    life_hours: float | None
    maintenance_hours: tuple[float, ...] = ()


@dataclass
class Vk7Data:
    product_id: str
    serial: str
    name: str
    model: str = "VK7"
    mac: str | None = None
    battery: str | None = None
    bag: str | None = None
    trackers: tuple[Vk7Tracker, ...] = ()
    total_minutes: int | None = None
    minutes_by_mode: dict[str, int] = field(default_factory=dict)
    total_area: float | None = None
    last_sync: str | None = None


def find_vk7_products(products: Any) -> list[Vk7Data]:
    found = []
    for item in products or []:
        item = _as_dict(item)
        if str(item.get("productId") or "").upper() != "VK7" or item.get("id") is None:
            continue
        name = " ".join(str(item.get("name") or "").split()) or "Kobold VK7"
        found.append(
            Vk7Data(
                product_id=str(item["id"]),
                serial=str(item.get("serialNo") or item["id"]),
                name=name,
                mac=normalise_mac(item.get("macAddress")),
            )
        )
    return found


def normalise_mac(value: Any) -> str | None:
    text = "".join(c for c in str(value or "") if c in "0123456789abcdefABCDEF").upper()
    if len(text) != 12:
        return None
    return ":".join(text[i : i + 2] for i in range(0, 12, 2))


def describe_bytes(data: bytes | bytearray | None) -> dict[str, Any]:
    """Hex, text (if printable) and little-endian integers of a GATT value."""
    if data is None:
        return {}
    raw = bytes(data)
    result: dict[str, Any] = {"hex": raw.hex(), "length": len(raw)}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text and all(c.isprintable() or c in "\r\n\t" for c in text.rstrip("\x00")):
        result["text"] = text.rstrip("\x00")
    if 0 < len(raw) <= 8:
        result["uint_le"] = int.from_bytes(raw, "little")
    return result


def _lower_option(value: Any, options: tuple[str, ...]) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in options else None


def apply_vk7_health(device: Vk7Data, data: Any) -> None:
    data = _as_dict(data)
    device.battery = _lower_option(data.get("batteryStatus"), VK7_BATTERY_STATES)
    device.bag = _lower_option(data.get("filterBagStatus"), VK7_BAG_STATES)
    trackers = []
    for item in data.get("productTrackers") or []:
        item = _as_dict(item)
        if item.get("id") is None or item.get("activated") is False:
            continue
        trackers.append(
            Vk7Tracker(
                tracker_id=str(item["id"]),
                name=" ".join(str(item.get("name") or item["id"]).split()),
                kind=(str(item["type"]).lower() if item.get("type") else None),
                progress=_as_int(item.get("usageProgress")),
                counted_hours=_as_float(item.get("countedHours")),
                life_hours=_as_float(item.get("productLifeHours")),
                maintenance_hours=tuple(
                    v for v in (_as_float(h) for h in item.get("maintenanceWindowHours") or [])
                    if v is not None
                ),
            )
        )
    device.trackers = tuple(trackers)
    device.last_sync = data.get("lastSynchronizationDate") or device.last_sync


_VK7_MODE_KEYS = {
    "totalVacuumedPassiveMinutes": "passive",
    "totalVacuumedEbMinutes": "eb7",
    "totalVacuumedSpMinutes": "sp7",
    "totalVacuumedPbMinutes": "pb7",
}


def apply_vk7_log(device: Vk7Data, data: Any) -> None:
    data = _as_dict(data)
    device.total_minutes = _as_int(data.get("totalVacuumedMinutes"))
    device.minutes_by_mode = {
        mode: value
        for key, mode in _VK7_MODE_KEYS.items()
        if (value := _as_int(data.get(key))) is not None
    }
    device.total_area = _as_float(data.get("totalAreaVacuumed"))
    device.last_sync = data.get("lastSynchronizationDate") or device.last_sync
