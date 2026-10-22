"""Peer-to-peer Send & Receive: the signaling hub.

Files never touch this server. Two browsers find each other here (the sender
"hosts" an identifier, the receiver "joins" it, the sender approves) and then
swap WebRTC offer/answer/ICE messages through this socket until their own
encrypted data channel is up. The file bytes go over that channel, directly or
through the TURN relay, never through this process.

What the hub holds, in memory only: an HMAC of each live identifier (never the
identifier itself, which may be a phone number), the sockets, and two random
confirmation words per join. A room disappears when its sender disconnects, or
after ROOM_TTL_SECONDS without a pairing. Nothing logged here names an
identifier.

State is per-process, like the in-memory rate limiter: _assert_single_worker()
keeps the app on one worker. Running several workers would need this map in
Redis pub/sub.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ROOM_TTL_SECONDS = int(os.environ.get("TRANSFER_ROOM_TTL_SECONDS", "600"))
# A paired session stays open while the transfer runs; this caps how long.
PAIRED_TTL_SECONDS = int(os.environ.get("TRANSFER_PAIRED_TTL_SECONDS", "7200"))
MAX_MESSAGE_BYTES = 16 * 1024
MAX_ROOMS = int(os.environ.get("TRANSFER_MAX_ROOMS", "5000"))
MAX_ROOMS_PER_CLIENT = 3
MAX_PENDING_PER_ROOM = 5
HOSTS_PER_MINUTE = 10
# Joins are what someone guessing identifiers would spam.
JOINS_PER_MINUTE = 20
MIN_IDENTIFIER = 6
MAX_IDENTIFIER = 64
MAX_DEVICE_LABEL = 48
TURN_CREDENTIAL_TTL = 3600

# Short, unambiguous words; two of them (65k combinations) let the sender and
# receiver confirm out loud that they are looking at the same request.
WORDS = (
    "amber acorn alpine anchor apple arrow aspen atlas autumn badge bamboo banjo basil beacon berry "
    "birch bison blaze bloom bluff bolt bonsai breeze brick brook cabin cactus camel candle canyon "
    "cedar cherry cider citrus clover cobalt comet copper coral cosmo cotton crane crystal cypress "
    "dahlia daisy delta denim dingo dolphin dune eagle ember falcon fern fiesta finch fjord flint "
    "forest fossil fox frost galaxy garnet gecko ginger glacier gold granite grape gull harbor hazel "
    "heron honey husky iris island ivory jade jasmine jasper jungle kayak kelp kiwi koala lagoon "
    "lantern lark lava lemon lilac lime linen lotus lunar lynx magnet mango maple marble meadow "
    "mesa meteor mint mocha monsoon moss nectar nova oak oasis ocean olive onyx opal orbit orchid "
    "otter owl panda papaya pearl pebble pepper petal pine pixel plum polar poppy prism puffin "
    "quartz quill rain raven reef ripple river robin rocket ruby saffron sage salmon sapphire "
    "satin sequoia shell sierra silver sky slate snow solar spruce stone storm summit sunset swan "
    "tango teal thistle thunder tiger timber topaz tulip tundra twig umber valley velvet violet "
    "walnut wave willow wind wren yak yarrow zebra zen zephyr"
).split()

_PHONE_CHARS = re.compile(r"^[\d\s+().-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOCAL_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d{1,5})?$")
# Origins the Capacitor app loads from (see the CORS block in main.py).
_APP_ORIGINS = ("capacitor://localhost", "https://localhost", "http://localhost")


def normalize_identifier(raw: object) -> Optional[str]:
    """Canonical form of what a person typed, or None if it can't be used.

    Case and spacing don't matter ("Blue Tiger" == "blue  tiger"), and anything
    that looks like a phone number is reduced to its digits so "+91 98200 12345"
    and "919820012345" find each other.
    """
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.strip().lower().split())
    if _PHONE_CHARS.match(text):
        digits = re.sub(r"\D", "", text)
        if len(digits) >= MIN_IDENTIFIER:
            text = digits
    if not (MIN_IDENTIFIER <= len(text) <= MAX_IDENTIFIER):
        return None
    return text


def clean_device_label(raw: object) -> str:
    if not isinstance(raw, str):
        return "Unknown device"
    label = _CONTROL.sub("", raw).strip()[:MAX_DEVICE_LABEL]
    return label or "Unknown device"


def turn_credentials(secret: str, now: Optional[float] = None, ttl: int = TURN_CREDENTIAL_TTL) -> Dict[str, str]:
    """coturn `use-auth-secret` (TURN REST API) credentials, valid for `ttl` seconds."""
    expires = int(now if now is not None else time.time()) + ttl
    username = f"{expires}:{secrets.token_hex(4)}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return {"username": username, "credential": base64.b64encode(digest).decode()}


def _split_env(name: str, default: str = "") -> List[str]:
    return [u.strip() for u in os.environ.get(name, default).split(",") if u.strip()]


def ice_servers() -> List[dict]:
    """STUN, plus the TURN relay with fresh credentials when it is configured."""
    servers: List[dict] = []
    stun = _split_env("TRANSFER_STUN_URLS", "stun:stun.cloudflare.com:3478,stun:stun.l.google.com:19302")
    if stun:
        servers.append({"urls": stun})
    turn_urls = _split_env("TURN_URLS")
    turn_secret = os.environ.get("TURN_SECRET", "")
    if turn_urls and turn_secret:
        servers.append({"urls": turn_urls, **turn_credentials(turn_secret)})
    return servers


def origin_allowed(origin: Optional[str], site_origins: Iterable[str]) -> bool:
    """Browsers always send Origin on a WebSocket handshake; only our own pages
    (the site, the desktop app on 127.0.0.1, the mobile app) may use the hub."""
    if not origin:
        return False
    if origin in _APP_ORIGINS or origin in site_origins:
        return True
    return bool(_LOCAL_ORIGIN.match(origin))


class _Peer:
    __slots__ = ("ws", "rid", "words", "device")

    def __init__(self, ws: WebSocket, rid: str, words: List[str], device: str):
        self.ws = ws
        self.rid = rid
        self.words = words
        self.device = device


class _Room:
    __slots__ = ("key", "sender", "client", "created", "pending", "receiver")

    def __init__(self, key: str, sender: WebSocket, client: str):
        self.key = key
        self.sender = sender
        self.client = client
        self.created = time.monotonic()
        self.pending: Dict[str, _Peer] = {}
        self.receiver: Optional[_Peer] = None


async def _send(ws: Optional[WebSocket], message: dict) -> None:
    if ws is None:
        return
    try:
        await ws.send_text(json.dumps(message))
    except Exception:  # the other side already went away
        pass


async def _close(ws: Optional[WebSocket], code: int = 1000) -> None:
    if ws is None:
        return
    try:
        await ws.close(code)
    except Exception:
        pass


class TransferHub:
    def __init__(self, client_identity: Callable, site_origins: Iterable[str]):
        self._client_identity = client_identity
        self._site_origins = frozenset(site_origins)
        # A process-random key: the map never holds an identifier, and a
        # memory dump can't be brute-forced back to a phone number offline.
        self._key = secrets.token_bytes(32)
        self.rooms: Dict[str, _Room] = {}

    def room_key(self, identifier: str) -> str:
        return hmac.new(self._key, identifier.encode(), hashlib.sha256).hexdigest()

    def _rate_ok(self, ws: WebSocket, client: str, bucket: str, limit: int) -> bool:
        state = ws.app.state
        if not getattr(state, "rate_limit_enabled", False):
            return True
        allowed, _ = state.rate_limiter.check(f"{client}:transfer-{bucket}", limit)
        return allowed

    async def handle(self, ws: WebSocket) -> None:
        if not origin_allowed(ws.headers.get("origin"), self._site_origins):
            await ws.close(code=1008)
            return
        await ws.accept()
        client = self._client_identity(ws)
        state = {"room": None, "role": None, "peer": None, "connected": time.monotonic()}
        try:
            while True:
                # An absolute deadline, checked before every read: a client that
                # keeps pinging must not be able to hold a room past its TTL.
                timeout = self._timeout(state)
                if timeout <= 0:
                    await _send(ws, {"t": "error", "code": "expired"})
                    break
                try:
                    text = await asyncio.wait_for(ws.receive_text(), timeout=timeout)
                except asyncio.TimeoutError:
                    await _send(ws, {"t": "error", "code": "expired"})
                    break
                if len(text) > MAX_MESSAGE_BYTES:
                    await _send(ws, {"t": "error", "code": "too_large"})
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if not await self._dispatch(ws, client, state, msg):
                    break
        except (WebSocketDisconnect, RuntimeError):
            # RuntimeError: the other side's handler already closed this socket
            # (a denied receiver, or a sender that left).
            pass
        finally:
            await self._leave(ws, state)
            await _close(ws)

    def _timeout(self, state: dict) -> float:
        """Seconds left before this socket's deadline (may be zero or negative)."""
        room: Optional[_Room] = state["room"]
        if room is None:
            # A socket that never hosts or joins gets a minute, pings or not.
            return state["connected"] + 60.0 - time.monotonic()
        limit = PAIRED_TTL_SECONDS if room.receiver is not None else ROOM_TTL_SECONDS
        return room.created + limit - time.monotonic()

    async def _dispatch(self, ws: WebSocket, client: str, state: dict, msg: dict) -> bool:
        kind = msg.get("t")
        if kind == "ping":
            await _send(ws, {"t": "pong"})
            return True
        if state["role"] is None:
            if kind == "host":
                return await self._host(ws, client, state, msg)
            if kind == "join":
                return await self._join(ws, client, state, msg)
            return True
        room: _Room = state["room"]
        if state["role"] == "sender":
            if kind == "approve":
                await self._approve(room, msg.get("rid"))
            elif kind == "deny":
                peer = room.pending.pop(str(msg.get("rid")), None)
                if peer:
                    await _send(peer.ws, {"t": "denied"})
                    await _close(peer.ws)
            elif kind == "signal" and room.receiver is not None:
                await _send(room.receiver.ws, {"t": "signal", "data": msg.get("data")})
            return True
        # Receiver: may only signal once approved, and only to its own sender.
        peer: _Peer = state["peer"]
        if kind == "signal" and room.receiver is peer:
            await _send(room.sender, {"t": "signal", "data": msg.get("data")})
        return True

    async def _host(self, ws: WebSocket, client: str, state: dict, msg: dict) -> bool:
        if not self._rate_ok(ws, client, "host", HOSTS_PER_MINUTE):
            await _send(ws, {"t": "error", "code": "rate_limited"})
            return False
        identifier = normalize_identifier(msg.get("id"))
        if identifier is None:
            await _send(ws, {"t": "error", "code": "invalid"})
            return True
        key = self.room_key(identifier)
        if key in self.rooms:
            await _send(ws, {"t": "error", "code": "taken"})
            return True
        if len(self.rooms) >= MAX_ROOMS or sum(r.client == client for r in self.rooms.values()) >= MAX_ROOMS_PER_CLIENT:
            await _send(ws, {"t": "error", "code": "busy"})
            return False
        room = _Room(key, ws, client)
        self.rooms[key] = room
        state.update(room=room, role="sender")
        await _send(ws, {"t": "hosted", "ttl": ROOM_TTL_SECONDS})
        return True

    async def _join(self, ws: WebSocket, client: str, state: dict, msg: dict) -> bool:
        if not self._rate_ok(ws, client, "join", JOINS_PER_MINUTE):
            await _send(ws, {"t": "error", "code": "rate_limited"})
            return False
        identifier = normalize_identifier(msg.get("id"))
        room = self.rooms.get(self.room_key(identifier)) if identifier else None
        if room is None:
            await _send(ws, {"t": "error", "code": "not_found"})
            return True
        if room.receiver is not None or len(room.pending) >= MAX_PENDING_PER_ROOM:
            await _send(ws, {"t": "error", "code": "busy"})
            return True
        peer = _Peer(ws, secrets.token_urlsafe(8), [secrets.choice(WORDS) for _ in range(2)],
                     clean_device_label(msg.get("device")))
        room.pending[peer.rid] = peer
        state.update(room=room, role="receiver", peer=peer)
        await _send(ws, {"t": "waiting", "words": peer.words})
        await _send(room.sender, {"t": "request", "rid": peer.rid, "words": peer.words, "device": peer.device})
        return True

    async def _approve(self, room: _Room, rid: object) -> None:
        if room.receiver is not None:
            return
        peer = room.pending.pop(str(rid), None)
        if peer is None:
            return
        room.receiver = peer
        # One receiver per room: everyone else still waiting is turned away.
        for other in list(room.pending.values()):
            await _send(other.ws, {"t": "denied"})
            await _close(other.ws)
        room.pending.clear()
        # TURN credentials go only to an approved pair, never to a guesser.
        await _send(peer.ws, {"t": "approved", "iceServers": ice_servers()})
        await _send(room.sender, {"t": "paired", "rid": peer.rid, "iceServers": ice_servers()})

    async def _leave(self, ws: WebSocket, state: dict) -> None:
        room: Optional[_Room] = state["room"]
        if room is None:
            return
        if state["role"] == "sender":
            if self.rooms.get(room.key) is room:
                del self.rooms[room.key]
            for peer in [*room.pending.values(), *([room.receiver] if room.receiver else [])]:
                await _send(peer.ws, {"t": "peer_left"})
                await _close(peer.ws)
            room.pending.clear()
            return
        peer: _Peer = state["peer"]
        room.pending.pop(peer.rid, None)
        if room.receiver is peer:
            # The sender's room is used up; a fresh transfer hosts a new one.
            await _send(room.sender, {"t": "peer_left"})
        else:
            await _send(room.sender, {"t": "request_cancelled", "rid": peer.rid})


def site_origins_from(base_url: str, extra: Iterable[str] = ()) -> List[str]:
    parts = urlsplit(base_url)
    origins = [f"{parts.scheme}://{parts.netloc}"] if parts.scheme and parts.netloc else []
    return origins + [o for o in extra if o]


def create_transfer_router(client_identity: Callable, site_origins: Iterable[str]) -> APIRouter:
    hub = TransferHub(client_identity, site_origins)
    router = APIRouter()

    @router.websocket("/api/transfer/ws")
    async def transfer_ws(websocket: WebSocket):
        await hub.handle(websocket)

    router.hub = hub  # type: ignore[attr-defined]  # tests reach in to inspect rooms
    return router
