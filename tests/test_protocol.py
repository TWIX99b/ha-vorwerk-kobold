"""Tests for the pure protocol module (no Home Assistant needed)."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "kobold_protocol", _ROOT / "custom_components/kobold/protocol.py"
)
protocol = importlib.util.module_from_spec(_SPEC)
sys.modules["kobold_protocol"] = protocol
_SPEC.loader.exec_module(protocol)

STATE_DOCKED = {
    "action": None,
    "state": "idle",
    "available_commands": {
        "cancel": False,
        "extract": True,
        "pause": False,
        "resume": False,
        "return_to_base": False,
        "start": True,
    },
    "cleaning_center": {"bag_status": "bag_ok", "base_error": None, "state": "idle"},
    "details": {
        "base_type": "rb7",
        "charge": 100,
        "is_charging": False,
        "is_docked": True,
        "is_quickboost": False,
        "quickboost_estimate": 0,
    },
    "errors": [],
}


def test_docked_state():
    state = protocol.parse_robot_state(STATE_DOCKED)
    assert state.activity == "docked"
    assert state.charge == 100
    assert state.commands == frozenset({"extract", "start"})
    assert state.bag == "ok"


def test_partial_details_do_not_break_parsing():
    # The old integration threw away the whole update when details were partial.
    state = protocol.parse_robot_state(
        {"state": "busy", "action": "cleaning", "details": {"charge": 64}}
    )
    assert state.activity == "cleaning"
    assert state.charge == 64
    assert state.is_docked is False


def test_unknown_fields_and_garbage():
    state = protocol.parse_robot_state(
        {"state": "idle", "details": "nope", "errors": ["brush_stuck", 5], "new": 1}
    )
    assert state.errors == (protocol.RobotError("brush_stuck"),)
    assert state.activity == "error"
    assert protocol.parse_robot_state(None).activity == "idle"


def test_warning_is_not_an_error():
    state = protocol.parse_robot_state(
        {**STATE_DOCKED, "errors": [{"code": "bin_full", "severity": "warning"}]}
    )
    assert state.activity == "docked"
    assert state.active_errors == ()


def test_activity_mapping():
    def act(**kw):
        return protocol.parse_robot_state(kw).activity

    assert act(state="paused", action="cleaning") == "paused"
    assert act(state="busy", action="docking") == "returning"
    assert act(state="busy", action="mapping") == "cleaning"
    assert act(state="busy") == "idle"
    assert act(state="idle", details={"is_charging": True}) == "docked"


def test_bag_status():
    assert protocol.normalise_bag_status("bag_full") == "full"
    assert protocol.normalise_bag_status("something_new") == "other"
    assert protocol.normalise_bag_status(None) is None


def test_phoenix_messages():
    reply = json.dumps(
        ["1", "2", "robots:abc", "phx_reply", {"status": "ok", "response": {"body": STATE_DOCKED}}]
    )
    events = protocol.decode_socket_message(reply)
    assert [(e.kind, e.robot_id) for e in events] == [("state", "abc")]

    join = json.dumps(["1", "1", "robots:abc", "phx_reply", {"status": "ok", "response": {}}])
    assert protocol.decode_socket_message(join)[0].kind == "reply_ok"

    err = json.dumps(["1", "1", "robots:abc", "phx_reply", {"status": "error", "response": {}}])
    assert protocol.decode_socket_message(err)[0].kind == "reply_error"

    last = json.dumps(["1", "3", "robots:abc", "last_state", {"body": STATE_DOCKED}])
    assert protocol.decode_socket_message(last)[0].kind == "state"

    assert protocol.decode_socket_message("not json") == []
    assert protocol.decode_socket_message("[1,2]") == []


def test_plain_json_events():
    msg = json.dumps(
        {"event_type": "state_changed", "payload": {"robot_id": "abc", "state": STATE_DOCKED}}
    )
    [event] = protocol.decode_socket_message(msg)
    assert (event.kind, event.robot_id) == ("state", "abc")

    msg = json.dumps({"event_type": "cleaning_state", "payload": {"state": {"runs": []}}})
    assert protocol.decode_socket_message(msg)[0].kind == "cleaning"


def test_cleaning_state():
    payload = {
        "body": {
            "cleaning_type": "persistent_map",
            "floorplan_uuid": "fp",
            "started_by": "app",
            "timing": {"start": "2026-11-11T09:00:00Z"},
            "runs": [
                {"track_name": "Küche", "track_uuid": "t1", "state": "done", "stats": {"area": 12.34}},
                {"track_name": "Stube", "track_uuid": "t2", "state": "running", "stats": {"area": "8"}},
            ],
        }
    }
    progress = protocol.parse_cleaning_state(payload)
    assert progress.area == 20.34
    assert [r.track_name for r in progress.runs] == ["Küche", "Stube"]
    assert protocol.parse_cleaning_state({"state": {}}).area is None


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"x.{body}.y"


def test_tokens():
    assert protocol.jwt_expiry(_jwt({"exp": 1000})) == 1000
    assert protocol.jwt_expiry("garbage") is None
    assert protocol.token_valid(_jwt({"exp": 2000}), margin=300, now=1000)
    assert not protocol.token_valid(_jwt({"exp": 1200}), margin=300, now=1000)
    assert protocol.token_valid("opaque-token")
    assert not protocol.token_valid(None)


def test_maps_and_segments():
    plan = protocol.parse_floorplan({"floorplan_uuid": "fp", "name": " Obergeschoss "})
    assert plan.name == "Obergeschoss"
    assert protocol.parse_floorplan({"name": "x"}) is None
    zones = protocol.parse_zones(
        [
            {"track_uuid": "t1", "name": "Küche", "type": "cleaning"},
            {"track_uuid": "t2", "name": "Nogo", "type": "nogo"},
            {"name": "broken"},
        ]
    )
    assert zones == (protocol.Zone("t1", "Küche"),)
    sid = protocol.segment_id("fp", "t1")
    assert protocol.split_segment_id(sid) == ("fp", "t1")
    assert protocol.split_segment_id("nope") is None


def test_cleaning_request():
    body = protocol.build_cleaning_request("turbo", "fp", ["t1", "t2"])
    assert body["ability"] == "cleaning.start"
    assert [r["map"]["zone_uuid"] for r in body["runs"]] == ["t1", "t2"]
    assert body["runs"][0]["settings"]["mode"] == "turbo"
    whole = protocol.build_cleaning_request("bogus")
    assert whole["runs"] == [{"settings": {"mode": "auto", "navigation_mode": "normal"}, "map": None}]


def test_cleaning_center_and_errors():
    standard = protocol.parse_robot_state(
        {"details": {"base_type": "standard"}, "cleaning_center": {"bag_status": None}}
    )
    assert not standard.has_cleaning_center
    assert standard.error_option == "none"
    rb7 = protocol.parse_robot_state(STATE_DOCKED)
    assert rb7.has_cleaning_center
    odd = protocol.parse_robot_state({"errors": [{"code": "laser_on_fire"}]})
    assert odd.error_option == "other"
    stuck = protocol.parse_robot_state({"errors": [{"code": "brush_stuck"}]})
    assert stuck.error_option == "brush_stuck"
    assert set(protocol.KNOWN_ERRORS) < set(protocol.ERROR_OPTIONS)
    assert protocol.MODEL_NAMES["VRXX"] == "Kobold VR7"


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def test_map_images():
    png64 = base64.b64encode(PNG).decode()
    plan = protocol.parse_floorplan(
        {"floorplan_uuid": "fp", "name": "OG", "processed_rank_binary": png64, "updated_at": "2026-11-11T09:00:00Z"}
    )
    assert plan.image == PNG and plan.updated_at == "2026-11-11T09:00:00Z"
    fallback = protocol.parse_floorplan(
        {"floorplan_uuid": "fp", "processed_rank_binary": "notpng", "processed_real_binary": png64}
    )
    assert fallback.image == PNG
    assert protocol.decode_png("eJzt") is None  # zlib, not png

    last = protocol.parse_cleaning_maps(
        [
            {"start_time": "2026-11-10T09:00:00Z", "processed_thumb_rank_binary": png64},
            {"start_time": "2026-11-11T09:00:00Z", "processed_thumb_rank_binary": png64, "started_by": "schedule"},
            {"start_time": "2026-11-12T09:00:00Z"},  # no image
        ]
    )
    assert last.start == "2026-11-11T09:00:00Z" and last.started_by == "schedule"
    assert protocol.parse_cleaning_maps([]) is None


SCHEDULING = {
    "enabled": True,
    "events": [
        {
            "command": {"ability": "cleaning.start", "runs": [{"map": {"floorplan_uuid": "fp", "zone_uuid": None}, "settings": {"mode": "eco"}}]},
            "days": ["sat", "mon", "thu", "wed", "tue", "sun", "fri"],
            "enabled": True,
            "schedule_uuid": "a",
            "time": "17:00",
        },
        {
            "command": {"ability": "cleaning.start", "runs": []},
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "enabled": False,
            "schedule_uuid": "b",
            "time": "10:00",
        },
    ],
}


def test_scheduling():
    enabled, events = protocol.parse_scheduling(SCHEDULING)
    assert enabled
    assert [e.uuid for e in events] == ["b", "a"]  # sorted by time
    daily = events[1]
    assert daily.days == protocol.DAY_ORDER and daily.mode == "eco" and daily.floorplan_uuid == "fp"
    assert protocol.days_label(daily.days) == "täglich"
    assert protocol.days_label(events[0].days, "en") == "Mon–Fri"
    assert protocol.days_label(("mon", "wed")) == "Mo, Mi"
    body = protocol.schedule_update_body(events[0], True)
    assert body["enabled"] is True and body["days"] == list(protocol.DAY_ORDER[:5])
    assert body["command"]["ability"] == "cleaning.start"


def test_next_schedule_run():
    from datetime import datetime, timezone

    _, events = protocol.parse_scheduling(SCHEDULING)
    # Wednesday 2026-11-11 18:00 -> daily 17:00 already passed -> Thursday 17:00
    now = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
    assert protocol.next_schedule_run(events, now) == datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc)
    early = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    assert protocol.next_schedule_run(events, early).hour == 17  # 10:00 one is disabled
    assert protocol.next_schedule_run((), now) is None


def test_language_codes():
    assert protocol.language_option("EN-GB") == "en_gb"
    assert protocol.language_code("zh_hans", ["DE", "ZH-HANS"]) == "ZH-HANS"


def test_setting_payload():
    current = {"available_languages": ["DE", "EN-GB"], "gender": "M", "language": "EN-GB"}
    assert protocol.setting_payload("robot_language", current, {"language": "DE"}) == {
        "language": "DE",
        "available_languages": ["DE", "EN-GB"],
    }
    assert protocol.setting_payload(
        "sounds", {"enabled": True, "voice": "on", "volume": 27}, {"volume": 40}
    ) == {"enabled": True, "volume": 40, "voice": "on"}


PRODUCTS = [
    {"id": 896293, "productId": "VK7", "name": "Kobold VK7 Akku-Staubsauger", "serialNo": "224"},
    {"id": 1038334, "robotId": "7f23", "productId": "VR7", "name": "Robot"},
    {"id": 897815, "productId": "SP7", "name": "Kobold SP7 Saugwischer\n"},
]


def test_vk7():
    (vk7,) = protocol.find_vk7_products(PRODUCTS)
    assert vk7.product_id == "896293" and vk7.serial == "224"
    protocol.apply_vk7_health(
        vk7,
        {
            "batteryStatus": "GOOD",
            "filterBagStatus": "HALF_FULL",
            "productTrackers": [
                {"id": 1, "name": "Bürste reinigen", "type": "CLEANING", "usageProgress": 30,
                 "countedHours": 6, "productLifeHours": 20, "maintenanceWindowHours": [15, 20, 30],
                 "activated": True},
                {"id": 2, "name": "off", "activated": False},
            ],
            "lastSynchronizationDate": "2026-04-09T08:14:03Z",
        },
    )
    assert vk7.battery == "good" and vk7.bag == "half_full"
    assert [t.name for t in vk7.trackers] == ["Bürste reinigen"]
    assert vk7.trackers[0].progress == 30 and vk7.trackers[0].kind == "cleaning"
    protocol.apply_vk7_log(
        vk7, {"totalVacuumedMinutes": 1768, "totalVacuumedEbMinutes": 567, "totalAreaVacuumed": 3536}
    )
    assert vk7.total_minutes == 1768 and vk7.minutes_by_mode == {"eb7": 567}
    assert vk7.total_area == 3536
    protocol.apply_vk7_health(vk7, {"batteryStatus": "SOMETHING_NEW"})
    assert vk7.battery is None


def test_mac_and_bytes():
    assert protocol.normalise_mac("00:13:43:f4:bc:ea") == "00:13:43:F4:BC:EA"
    assert protocol.normalise_mac("001343F4BCEA") == "00:13:43:F4:BC:EA"
    assert protocol.normalise_mac("nope") is None
    (vk7,) = protocol.find_vk7_products(
        [{"id": 1, "productId": "VK7", "macAddress": "00:13:43:F4:BC:EA"}]
    )
    assert vk7.mac == "00:13:43:F4:BC:EA"
    assert protocol.describe_bytes(b"VK7\x00") == {"hex": "564b3700", "length": 4, "text": "VK7", "uint_le": 0x00374B56}
    assert protocol.describe_bytes(b"\x10\x27") == {"hex": "1027", "length": 2, "uint_le": 10000}
    assert "text" not in protocol.describe_bytes(b"\xff\xfe\x00\x01")
