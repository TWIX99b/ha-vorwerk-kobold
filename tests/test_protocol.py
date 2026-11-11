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
