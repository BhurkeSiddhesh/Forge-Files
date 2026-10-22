"""Send & Receive signaling hub: pairing, approval, relay, limits, privacy."""
import base64
import hashlib
import hmac
import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
import transfer_signaling as ts

ORIGIN = {"origin": "https://www.forgefiles.org"}
WS = "/api/transfer/ws"


@pytest.fixture
def client(monkeypatch):
    main.transfer_router.hub.rooms.clear()
    monkeypatch.setattr(main.app.state, "rate_limit_enabled", False)
    yield TestClient(main.app)
    main.transfer_router.hub.rooms.clear()


def _host(ws, identifier="blue tiger 42"):
    ws.send_json({"t": "host", "id": identifier})
    return ws.receive_json()


def _pair(client, sender, identifier="blue tiger 42"):
    assert _host(sender, identifier)["t"] == "hosted"
    receiver = client.websocket_connect(WS, headers=ORIGIN).__enter__()
    receiver.send_json({"t": "join", "id": identifier.upper(), "device": "Chrome on Android"})
    waiting = receiver.receive_json()
    request = sender.receive_json()
    assert waiting["t"] == "waiting" and request["t"] == "request"
    assert waiting["words"] == request["words"]
    assert request["device"] == "Chrome on Android"
    return receiver, request["rid"]


def test_host_join_approve_and_relay(client):
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        receiver, rid = _pair(client, sender)
        sender.send_json({"t": "approve", "rid": rid})
        approved = receiver.receive_json()
        paired = sender.receive_json()
        assert approved["t"] == "approved" and paired["t"] == "paired"
        assert approved["iceServers"] and paired["iceServers"]

        sender.send_json({"t": "signal", "data": {"sdp": "offer"}})
        assert receiver.receive_json() == {"t": "signal", "data": {"sdp": "offer"}}
        receiver.send_json({"t": "signal", "data": {"sdp": "answer"}})
        assert sender.receive_json() == {"t": "signal", "data": {"sdp": "answer"}}
        receiver.close()
        assert sender.receive_json()["t"] == "peer_left"


def test_receiver_cannot_signal_before_approval(client):
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        receiver, _ = _pair(client, sender)
        receiver.send_json({"t": "signal", "data": {"sdp": "sneaky"}})
        receiver.send_json({"t": "ping"})
        assert receiver.receive_json() == {"t": "pong"}
        sender.send_json({"t": "ping"})
        # The sender sees its own pong next, not the receiver's signal.
        assert sender.receive_json() == {"t": "pong"}
        receiver.close()


def test_deny_closes_the_receiver(client):
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        receiver, rid = _pair(client, sender)
        sender.send_json({"t": "deny", "rid": rid})
        assert receiver.receive_json() == {"t": "denied"}


def test_identifier_already_in_use(client):
    with client.websocket_connect(WS, headers=ORIGIN) as first, \
            client.websocket_connect(WS, headers=ORIGIN) as second:
        assert _host(first)["t"] == "hosted"
        assert _host(second, "  BLUE   tiger 42 ") == {"t": "error", "code": "taken"}


def test_unknown_identifier_is_not_found(client):
    with client.websocket_connect(WS, headers=ORIGIN) as receiver:
        receiver.send_json({"t": "join", "id": "nobody-here"})
        assert receiver.receive_json() == {"t": "error", "code": "not_found"}


def test_room_is_removed_when_sender_leaves(client):
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        _host(sender)
        assert len(main.transfer_router.hub.rooms) == 1
    with client.websocket_connect(WS, headers=ORIGIN) as receiver:
        receiver.send_json({"t": "join", "id": "blue tiger 42"})
        assert receiver.receive_json()["code"] == "not_found"
    assert main.transfer_router.hub.rooms == {}


def test_unpaired_room_expires(client, monkeypatch):
    monkeypatch.setattr(ts, "ROOM_TTL_SECONDS", 0)
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        assert _host(sender)["t"] == "hosted"
        assert sender.receive_json() == {"t": "error", "code": "expired"}


def test_pinging_does_not_keep_a_room_past_its_ttl(client, monkeypatch):
    monkeypatch.setattr(ts, "ROOM_TTL_SECONDS", 0.5)
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        assert _host(sender)["t"] == "hosted"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.1)
            sender.send_json({"t": "ping"})
            reply = sender.receive_json()
            if reply != {"t": "pong"}:
                break
        assert reply == {"t": "error", "code": "expired"}
    assert main.transfer_router.hub.rooms == {}


def test_approval_near_the_deadline_extends_both_sockets(client, monkeypatch):
    """The receiver's read was armed with the short unpaired deadline; approval
    must extend it rather than expire a transfer that has just been accepted."""
    monkeypatch.setattr(ts, "ROOM_TTL_SECONDS", 0.6)
    monkeypatch.setattr(ts, "PAIRED_TTL_SECONDS", 30)
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        receiver, rid = _pair(client, sender)
        time.sleep(0.3)
        sender.send_json({"t": "approve", "rid": rid})
        assert receiver.receive_json()["t"] == "approved"
        assert sender.receive_json()["t"] == "paired"
        time.sleep(1.0)  # well past the unpaired deadline
        sender.send_json({"t": "signal", "data": {"sdp": "offer"}})
        assert receiver.receive_json() == {"t": "signal", "data": {"sdp": "offer"}}
        receiver.send_json({"t": "signal", "data": {"sdp": "answer"}})
        assert sender.receive_json() == {"t": "signal", "data": {"sdp": "answer"}}
        receiver.close()


def test_oversized_message_is_refused(client):
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.send_text("x" * (ts.MAX_MESSAGE_BYTES + 1))
        assert ws.receive_json() == {"t": "error", "code": "too_large"}


def test_join_attempts_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(main.app.state, "rate_limit_enabled", True)
    monkeypatch.setattr(main.app.state, "rate_limiter", main.SlidingWindowRateLimiter())
    monkeypatch.setattr(ts, "JOINS_PER_MINUTE", 2)
    codes = []
    for _ in range(3):
        with client.websocket_connect(WS, headers=ORIGIN) as ws:
            ws.send_json({"t": "join", "id": "guessing-123"})
            codes.append(ws.receive_json()["code"])
    assert codes == ["not_found", "not_found", "rate_limited"]


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", "http://127.0.0.1.evil.example"])
def test_foreign_origins_are_rejected(client, origin):
    headers = {"origin": origin} if origin else {}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS, headers=headers) as ws:
            ws.receive_json()


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8765", "https://localhost", "capacitor://localhost"])
def test_desktop_and_mobile_origins_are_allowed(client, origin):
    with client.websocket_connect(WS, headers={"origin": origin}) as ws:
        ws.send_json({"t": "ping"})
        assert ws.receive_json() == {"t": "pong"}


def test_identifier_never_reaches_the_logs_or_the_room_map(client, caplog):
    phone = "+91 98200 12345"
    caplog.set_level(logging.DEBUG)
    with client.websocket_connect(WS, headers=ORIGIN) as sender:
        _host(sender, phone)
        receiver, _ = _pair_existing(client, sender, "919820012345")
        receiver.close()
        keys = list(main.transfer_router.hub.rooms)
    assert "9820012345" not in caplog.text
    assert all("9820012345" not in k for k in keys)


def _pair_existing(client, sender, identifier):
    receiver = client.websocket_connect(WS, headers=ORIGIN).__enter__()
    receiver.send_json({"t": "join", "id": identifier})
    assert receiver.receive_json()["t"] == "waiting"
    return receiver, sender.receive_json()["rid"]


@pytest.mark.parametrize("raw,expected", [
    ("+91 98200-12345", "919820012345"),
    ("(022) 555 0199", "0225550199"),
    ("  Blue   TIGER ", "blue tiger"),
    ("short", None),
    ("x" * 65, None),
    (123456, None),
])
def test_normalize_identifier(raw, expected):
    assert ts.normalize_identifier(raw) == expected


def test_turn_credentials_match_coturn_rest_format():
    creds = ts.turn_credentials("s3cret", now=1000, ttl=3600)
    expiry, _ = creds["username"].split(":")
    assert expiry == "4600"
    expected = base64.b64encode(hmac.new(b"s3cret", creds["username"].encode(), hashlib.sha1).digest()).decode()
    assert creds["credential"] == expected


def test_ice_servers_without_turn_is_stun_only(monkeypatch):
    monkeypatch.delenv("TURN_URLS", raising=False)
    monkeypatch.delenv("TURN_SECRET", raising=False)
    servers = ts.ice_servers()
    assert len(servers) == 1 and all(u.startswith("stun:") for u in servers[0]["urls"])


def test_ice_servers_with_turn(monkeypatch):
    monkeypatch.setenv("TURN_URLS", "turn:turn.example:3478,turns:turn.example:5349")
    monkeypatch.setenv("TURN_SECRET", "abc")
    turn = ts.ice_servers()[-1]
    assert turn["urls"] == ["turn:turn.example:3478", "turns:turn.example:5349"]
    assert turn["username"] and turn["credential"]
