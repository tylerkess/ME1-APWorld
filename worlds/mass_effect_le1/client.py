"""Archipelago client for Mass Effect Legendary Edition 1.

The client talks to LE1APBridge.asi over its local named pipe.  Pipe I/O is
kept behind :class:`BridgeConnection` so the game-facing behavior can be
tested without either LE1 or an Archipelago server.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
from dataclasses import dataclass
import logging
import random
import re
import unicodedata
from typing import BinaryIO, Callable, Protocol, Sequence
import zlib

import colorama

import Utils
from CommonClient import ClientCommandProcessor, CommonContext, get_base_parser, gui_enabled, handle_url_arg, server_loop
from NetUtils import ClientStatus

from .data import (COORDINATES, EXPERIENCE, FRAGMENT, HUMAN_SQUAD, ID_BASE, ITEM_NAME_TO_ID, ITEMS, LOCATIONS,
                   LOCATION_NAME_TO_ID, MAKO, SHOP_SLOTS_MAX, SHOPS, UPGRADE, Region, shop_bool)
from .gear import GEAR_LINES, UPGRADE_ROWS
from .levelup import CREDIT_REWARDS, CREDIT_REWARDS_COLUMNS, XP_REWARDS, XP_REWARDS_COLUMNS
from .tracker import (MARKER_AREAS, MARKER_IN_LOGIC_BASE, MARKER_LOCKED_BASE, Tracker, area_line, area_region,
                      level_area, summary_lines)
from .world import MassEffectLE1World

logger = logging.getLogger("ME1Client")

PIPE_NAME = r"\\.\pipe\LE1APBridge"
# LE1APExtras (tools/le1apextras): squad level and Experience grants, next to the bridge.
EXTRAS_PIPE_NAME = r"\\.\pipe\LE1APExtras"
EXPERIENCE_INDEX_PLOT_INT = 2010  # Experience items granted, written only by the extras' XPTXN
READ_BYTES = 65536  # one reply (a SNAP reply is a few KB)
RECEIVED_INDEX_PLOT_INT = 2000  # written only by the bridge's TXN (v12)
SEED_PLOT_INT, SLOT_PLOT_INT = 2004, 2005  # the save's seed/slot identity, written only by IDSET (0, 0 = not bound)
NONCE_PLOT_INT = 2006  # bridge v12 NONCE: marks the save the client checks before binding it (IDSET compares it)
# Build identity (R13): the oldest bridge and mod this client works with. The bridge answers PING with its version; the
# release writes the mod version into DLC_MOD_ArchipelagoME1\ArchipelagoME1.version, which the bridge reads (MODVER).
BRIDGE_VERSION = 13  # v13: a GIVE into a full inventory answers ERR full and waits (v12 lost the item)
MOD_VERSION = "0.2.12"
# An unbound save is bound to the seed only if it has at most this many checks done (a fresh career): a save from
# another seed or a vanilla save loaded by mistake would otherwise send its progress as checks. The open start alone
# sets 6 (live 2026-10-26: the Started checks of Feros, Liara, Noveria, UNC Cerberus, Hostage and Rogue VI).
BIND_MAX_TRUE_LOCATIONS = 10
GOAL_PLOT_BOOL = 6530  # CH4 Final_Choice.Game_Complete: transition 5110 in the final speech (BIOA_CRD00, both Council outcomes); 2511 End_Game_Done is never set on LE1
VIRMIRE_DONE_PLOT_BOOL = 2509
VIRMIRE_SURVIVOR_PLOT_BOOLS = (3827, 3828)  # set by the Virmire choice, which removes one of Kaidan and Ashley
ILOS_OPEN_PLOT_BOOL = 4103  # D5: set by the client once the fragment gate (and D9) is met
VIRMIRE_OPEN_PLOT_BOOL = 4104  # set by the Council's Virmire transmission (vanilla T2302)
VIRMIRE_TRANSMISSION_TRANSITION = 20104  # the mod's copy of that transmission (tools/le1mod/build_mod.py)
UNLOCK_CONTAINERS_PLOT_BOOL = 20190  # the bridge opens Decryption containers like plain ones while this is set
INFINITE_SPRINT_PLOT_BOOL = 20191  # bridge v9+ (fixed in v11): sprinting never drains stamina while this is set
TWO_SQUADMATES_PLOT_BOOL = 20194  # set while two squadmates are in the party: the mod's landing gate (Agebinium)
# Journal "Archipelago" quest (le1mod journal): shown once JOURNAL_ACTIVE is set through its transition (bridge XST);
# its plot items read the values below, which the client keeps current.
JOURNAL_ACTIVE_PLOT_BOOL = 20192
MAKO_PLOT_BOOL = 20193
FRAGMENTS_HELD_PLOT_INT = 2002
FRAGMENTS_REQUIRED_PLOT_INT = 2003
IN_LOGIC_PLOT_BOOL_BASE = 20800  # + data.Region index: that region has checks in logic now
SHOP_SLOTS_PLOT_INT = 2001  # shop_slots + 1: the mod's shop-slot conditionals hide slots at or above it (0 = show all)
POLL_INTERVAL_SECONDS = 2.0
# HUD notices (bridge v9 NOTE -> BioEventNotifier.AddNotice): the game's "Level Up!" notice type and icon, context 0.
# Owner sweep (2026-10-11, 0.2.2): only type 1 and 3 show our text; context 3 (the Level Up call's) plays the Level Up
# sound, context 0 is silent; the box shows about 19 characters, so text is wrapped into lines of NOTICE_LINE_CHARS.
NOTICE_TYPE, NOTICE_CONTEXT, NOTICE_ICON, NOTICE_TTL_MS = 1, 0, 2, 6000
NOTICE_LINE_CHARS = 18
NOTICES_PER_POLL = 4  # lines per poll
NOTICE_LEVELS = ("all", "important", "off")  # /notices: "important" skips sends and area lines
# F7 summary (bridge v11 MSGBOX): one dismissable message box; style 0 = DisplayQuickMessageBoxEx, 1 = the Alternate one.
MSGBOX_STYLE = 0
MSGBOX_MAX_BYTES = 8000  # the bridge's limit; a longer summary falls back to notices (nothing is cut)
# The box shows about 7 lines of about 56 characters (live 2026-10-26: a 13-line summary ran past the panel), so the
# summary is wrapped and split into pages: F7 shows page 1, F7 on an open page shows the next (bridge v12 KEYS).
BOX_LINE_CHARS = 56
BOX_PAGE_LINES = 7
MSGBOX_BRIDGE_VERSION = 11  # bridges before v11 have no MSGBOX and drop requests over 511 bytes: ask VER first
# Shop slot location -> the transition its plot item runs when bought (= its bool, D19); bridge v11 SHOPITEM labels the
# slot with the scouted AP item.
SHOP_TRANSITIONS = {LOCATION_NAME_TO_ID[f"{shop.name} Slot {slot + 1}"]: shop_bool(m, slot)
                    for m, shop in enumerate(SHOPS) for slot in range(SHOP_SLOTS_MAX)}
SHOP_LABEL_BRIDGE_VERSION = 11


class BridgeError(Exception):
    """Base class for bridge transport and protocol failures."""


class GameNotReadyError(BridgeError):
    """The bridge is running, but LE1 has no world/save loaded yet."""


class BridgeProtocolError(BridgeError):
    """The bridge rejected a request or returned a malformed reply."""


class DeliveryGapError(BridgeProtocolError):
    """TXN found the save's received index short of the item (another save was loaded): resync from the save."""


class InventoryFullError(BridgeError):
    """TXN GIVE found the squad inventory full (the shop's 300-item rule): the item waits, its index unchanged."""


class IdentityMismatchError(BridgeError):
    """The loaded save belongs to another seed or slot, or cannot be bound to this one."""


class BuildMismatchError(BridgeError):
    """The installed bridge or mod is older than this client needs."""


# Requests that must not be sent twice when a reply is lost: a resend would repeat a notice or a box, or lose presses.
# Everything else is a read, an absolute write, or a TXN (idempotent per received index).
NOT_REPEATABLE = frozenset({"NOTE", "MSGBOX", "KEYS"})


def seed_identity(seed_name: str, team: int, slot: int) -> tuple[int, int]:
    """Two positive 31-bit ints naming this seed and slot, stored in the save (never 0, which means unbound)."""
    return (zlib.crc32(seed_name.encode("utf-8")) & 0x7FFF_FFFF) or 1, ((team << 16) | slot) or 1


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


PipeOpener = Callable[[str, str, int], BinaryIO]


class BridgeConnection:
    """Small reconnecting request/reply client for LE1APBridge's named pipe."""

    def __init__(self, pipe_name: str = PIPE_NAME, opener: PipeOpener = open) -> None:
        self.pipe_name = pipe_name
        self._opener = opener
        self._pipe: BinaryIO | None = None
        # bridge v12: the save identity this client works on. The bridge runs SETB / SETI / XST only on the save bound
        # to it; it is announced again on every new pipe connection (the bridge forgets it when a client disconnects).
        self.session: tuple[int, int] | None = None

    def close(self) -> None:
        if self._pipe is not None:
            with contextlib.suppress(OSError):
                self._pipe.close()
            self._pipe = None

    def _connect(self) -> BinaryIO:
        if self._pipe is None:
            pipe = self._opener(self.pipe_name, "r+b", 0)
            if self.session is not None:
                pipe.write(f"SESSION {self.session[0]} {self.session[1]}".encode("ascii"))
                if pipe.read(READ_BYTES) != b"OK":
                    with contextlib.suppress(OSError):
                        pipe.close()
                    raise OSError("the bridge refused SESSION")
            self._pipe = pipe
        return self._pipe

    def set_session(self, identity: tuple[int, int]) -> None:
        if identity != self.session:
            self.session = identity
            self.close()  # the next request reconnects and announces it

    def request(self, request: str) -> str:
        """Send one ASCII request, reconnecting once after a transport failure (not for NOT_REPEATABLE requests)."""
        if not request.isascii():
            raise ValueError("bridge requests must be ASCII")

        last_error: OSError | None = None
        for _attempt in range(1 if request.split(" ", 1)[0] in NOT_REPEATABLE else 2):
            try:
                pipe = self._connect()
                pipe.write(request.encode("ascii"))
                reply = pipe.read(READ_BYTES).decode("ascii")
                if not reply:
                    raise OSError("bridge closed the pipe without replying")
                return self._parse_reply(request, reply)
            except (OSError, UnicodeDecodeError) as error:
                last_error = error if isinstance(error, OSError) else OSError(str(error))
                self.close()

        raise BridgeError(f"bridge request failed after reconnect: {last_error}") from last_error

    @staticmethod
    def _parse_reply(request: str, reply: str) -> str:
        if reply == "ERR noworld":
            raise GameNotReadyError("LE1 has no world loaded")
        if reply == "ERR nosquad":
            raise GameNotReadyError("LE1 has no player squad yet")
        if reply == "ERR timeout":  # the game thread is busy (load screen); the request may still run, so the next
            raise GameNotReadyError(f"{request.split()[0]}: timeout")  # poll rereads the save and TXN skips what landed
        if reply == "ERR identity":
            raise IdentityMismatchError("another save was loaded")
        if reply == "ERR full":
            raise InventoryFullError(f"{request.split()[0]}: inventory full")
        if reply.startswith("ERR gap "):
            raise DeliveryGapError(f"{request!r}: {reply}")
        if reply.startswith("ERR "):
            raise BridgeProtocolError(f"{request!r}: {reply}")
        if reply == "OK":
            return ""
        if reply.startswith("OK "):
            return reply[3:]
        raise BridgeProtocolError(f"{request!r}: malformed reply {reply!r}")

    def ping(self) -> int:
        """The bridge version: v12+ answers PING with it, an older bridge with a bare pong (counted as 11)."""
        value = self.request("PING")
        if value == "pong":
            return 11
        if not value.startswith("pong ") or not value[5:].isdigit():
            raise BridgeProtocolError(f"PING returned an unexpected value {value!r}")
        return int(value[5:])

    def mod_version(self) -> str | None:
        """The installed mod's version (bridge v12 MODVER), or None if the mod has no version file."""
        try:
            return self.request("MODVER")
        except BridgeProtocolError:
            return None

    def identity(self) -> tuple[int, int]:
        value = self.request("ID").split()
        if len(value) != 2 or not all(part.lstrip("-").isdigit() for part in value):
            raise BridgeProtocolError(f"ID: invalid identity {value!r}")
        return int(value[0]), int(value[1])

    def nonce(self, value: int) -> None:
        """Mark the loaded save (int 2006) before checking it for binding."""
        self.request(f"NONCE {value}")

    def bind_identity(self, identity: tuple[int, int], nonce: int) -> None:
        """Bind the save the client checked: the bridge refuses if the loaded save no longer holds `nonce` (another
        save was loaded), or is bound to another identity."""
        self.request(f"IDSET {identity[0]} {identity[1]} {nonce}")

    def snapshot(self) -> Snapshot:
        """Every plot value the client reads each poll, with the save identity, from one game-thread step (SNAP)."""
        values = self.request(SNAP_REQUEST).split()
        try:
            return Snapshot.parse(values)
        except (ValueError, IndexError) as error:
            raise BridgeProtocolError(f"SNAP: malformed reply ({error})") from error

    def transaction(self, identity: tuple[int, int], index: int, delivery: str) -> bool:
        """Deliver received item `index` (TXN); True if applied now, False if the save already had it."""
        value = self.request(f"TXN {identity[0]} {identity[1]} {index} {delivery}")
        if value not in {"applied", "done"}:
            raise BridgeProtocolError(f"TXN {index}: unexpected reply {value!r}")
        return value == "applied"

    def get_bool(self, index: int) -> bool:
        value = self.request(f"GETB {index}")
        if value not in {"0", "1"}:
            raise BridgeProtocolError(f"GETB {index}: invalid bool {value!r}")
        return value == "1"

    def get_int(self, index: int) -> int:
        value = self.request(f"GETI {index}")
        try:
            return int(value)
        except ValueError as error:
            raise BridgeProtocolError(f"GETI {index}: invalid int {value!r}") from error

    def set_bool(self, index: int, value: bool) -> None:
        self.request(f"SETB {index} {int(value)}")

    def set_int(self, index: int, value: int) -> None:
        self.request(f"SETI {index} {value}")

    def note(self, text: str) -> None:
        """One HUD notice line; callers wrap longer text with notice_lines."""
        self.request(f"NOTE {NOTICE_TYPE} {NOTICE_CONTEXT} {NOTICE_ICON} {NOTICE_TTL_MS} {notice_text(text)}")

    def version(self) -> int:
        """The bridge version: VER (v11+); an older bridge refuses it and counts as 10."""
        try:
            value = self.request("VER")
        except BridgeProtocolError:
            return 10
        try:
            return int(value)
        except ValueError as error:
            raise BridgeProtocolError(f"VER: invalid version {value!r}") from error

    def message_box(self, lines: Sequence[str]) -> None:
        """Show the lines in one dismissable message box (bridge v11)."""
        self.request(f"MSGBOX {MSGBOX_STYLE} " + message_box_text(lines))

    def shop_item(self, transition: int, name: str, description: str) -> None:
        """Label the shop slot whose plot item runs `transition` (bridge v11)."""
        def clean(text: str) -> str:
            return notice_text(text).replace("|", "/")
        self.request(f"SHOPITEM {transition} {clean(name)}|{clean(description)}")

    def shop_clear(self) -> None:
        """Forget every shop label (bridge v11)."""
        self.request("SHOPCLR")

    def run_transition(self, transition: int) -> None:
        """Run an AP state transition through the game's plot manager (bridge v10)."""
        self.request(f"XST {transition}")

    def map_name(self) -> str:
        """The current world's map package (bridge v10)."""
        return self.request("MAP")

    def key_presses(self) -> tuple[int, int]:
        """Tracker hotkey presses since the last call (bridge v10): (presses, presses that closed our box (v12))."""
        value = self.request("KEYS").split()
        try:
            presses, closes = (int(value[0]), int(value[1]) if len(value) > 1 else 0)
        except (ValueError, IndexError) as error:
            raise BridgeProtocolError(f"KEYS: invalid counts {value!r}") from error
        return presses, closes


class ExtrasConnection(BridgeConnection):
    """LE1APExtras' named pipe (tools/le1apextras/PROTOCOL.md): same request/reply transport, no SESSION."""

    def __init__(self, pipe_name: str = EXTRAS_PIPE_NAME, opener: PipeOpener = open) -> None:
        super().__init__(pipe_name, opener)

    def squad_level(self) -> int:
        value = self.request("LEVEL").split()
        if len(value) != 2 or not all(part.lstrip("-").isdigit() for part in value):
            raise BridgeProtocolError(f"LEVEL: invalid reply {value!r}")
        return int(value[0])

    def experience_index(self) -> int:
        value = self.request("XPIDX")
        if not value.lstrip("-").isdigit():
            raise BridgeProtocolError(f"XPIDX: invalid index {value!r}")
        return int(value)

    def experience_transaction(self, identity: tuple[int, int], index: int, amount: int) -> bool:
        """Grant Experience item `index` (XPTXN); True if applied now, False if the save already had it."""
        value = self.request(f"XPTXN {identity[0]} {identity[1]} {index} {amount}")
        if value not in {"applied", "done"}:
            raise BridgeProtocolError(f"XPTXN {index}: unexpected reply {value!r}")
        return value == "applied"


def notice_text(text: str) -> str:
    """ASCII and single-line, so any slot or item name fits the bridge's NOTE request."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(char if char.isprintable() else " " for char in folded).split()) or "?"


def summary_pages(lines: Sequence[str], width: int = BOX_LINE_CHARS, height: int = BOX_PAGE_LINES) -> list[list[str]]:
    """The F7 summary as message box pages: lines wrapped at spaces to `width` (a longer word is split), each page a
    title plus at most `height` - 1 lines, and "F7: next page" on every page but the last. Nothing is dropped."""
    wrapped = [chunk for line in lines for chunk in notice_lines(line, width)]
    per_page = height - 1
    if len(wrapped) > per_page:
        per_page -= 1  # room for the footer
    chunks = [wrapped[start:start + per_page] for start in range(0, len(wrapped), per_page)] or [[]]
    if len(chunks) == 1:
        return [["Archipelago"] + chunks[0]]
    return [[f"Archipelago ({number}/{len(chunks)})"] + chunk + (["F7: next page"] if number < len(chunks) else [])
            for number, chunk in enumerate(chunks, 1)]


def message_box_text(lines: Sequence[str]) -> str:
    """The MSGBOX body: each line cleaned like a notice, joined with newlines."""
    return "\n".join(notice_text(line) for line in lines)


def notice_lines(text: str, width: int = NOTICE_LINE_CHARS) -> list[str]:
    """The notice as HUD lines of at most `width` characters, split at spaces (a longer word is split); nothing dropped."""
    lines: list[str] = []
    for word in notice_text(text).split(" "):
        for start in range(0, len(word), width):
            chunk = word[start:start + width]
            if lines and len(lines[-1]) + 1 + len(chunk) <= width:
                lines[-1] += " " + chunk
            else:
                lines.append(chunk)
    return lines


class PlotReader(Protocol):
    def get_bool(self, index: int) -> bool: ...

    def get_int(self, index: int) -> int: ...


@dataclass(frozen=True)
class PlotPredicate:
    kind: str
    indices: tuple[int, ...]
    threshold: int | None = None

    def evaluate(self, bridge: PlotReader) -> bool:
        if self.kind == "bool":
            return any(bridge.get_bool(index) for index in self.indices)
        assert self.kind == "int" and self.threshold is not None
        return bridge.get_int(self.indices[0]) >= self.threshold


_BOOL_PREDICATE = re.compile(r"^bool\s+(\d+(?:\s+or\s+\d+)*)(?:\s+.*)?$", re.IGNORECASE)
_INT_PREDICATE = re.compile(r"^int\s+(\d+)\s*>=\s*(-?\d+)(?:\s+.*)?$", re.IGNORECASE)


def parse_plot_predicate(description: str | None) -> PlotPredicate | None:
    """Parse the supported leading forms used by ``LocationData.ot_plot``."""
    if not description:
        return None
    if match := _BOOL_PREDICATE.fullmatch(description.strip()):
        indices = tuple(int(value) for value in re.split(r"\s+or\s+", match.group(1), flags=re.IGNORECASE))
        return PlotPredicate("bool", indices)
    if match := _INT_PREDICATE.fullmatch(description.strip()):
        return PlotPredicate("int", (int(match.group(1)),), int(match.group(2)))
    return None


COORDINATE_ITEM_TO_PLOT_BOOL = {
    ID_BASE + item.offset: 20000 + item.offset - 0x10
    for item in ITEMS.values()
    if "Coordinates" in item.groups
}
VIRMIRE_COORDINATES_PLOT_BOOL = COORDINATE_ITEM_TO_PLOT_BOOL[ITEM_NAME_TO_ID[COORDINATES[Region.VIRMIRE]]]


# R10: a squadmate is recruited (selectable) when its InParty bool is set.
SQUAD_INPARTY_PLOT_BOOL = {
    "Kaidan Alenko": 3939,
    "Ashley Williams": 3940,
    "Garrus Vakarian": 3941,
    "Urdnot Wrex": 3942,
    "Liara T'Soni": 3943,
    "Tali'Zorah nar Rayya": 3944,
}
ITEM_TO_PLOT_BOOL = {
    **COORDINATE_ITEM_TO_PLOT_BOOL,
    **{ITEM_NAME_TO_ID[name]: plot_bool for name, plot_bool in SQUAD_INPARTY_PLOT_BOOL.items()},
}
FRAGMENT_ITEM_ID = ITEM_NAME_TO_ID[FRAGMENT]


def coordinate_bool_for_item(item_id: int) -> int | None:
    return COORDINATE_ITEM_TO_PLOT_BOOL.get(item_id)


def plot_bool_for_item(item_id: int) -> int | None:
    return ITEM_TO_PLOT_BOOL.get(item_id)


class ReceivedItem(Protocol):
    item: int


class ItemBridge(Protocol):
    def get_bool(self, index: int) -> bool: ...

    def get_int(self, index: int) -> int: ...

    def set_bool(self, index: int, value: bool) -> None: ...

    def set_int(self, index: int, value: int) -> None: ...

    def transaction(self, identity: tuple[int, int], index: int, delivery: str) -> bool: ...


GEAR_BY_ITEM_ID = {ITEM_NAME_TO_ID[line.name]: line for line in GEAR_LINES}
UPGRADE_ITEM_ID = ITEM_NAME_TO_ID[UPGRADE]
EXPERIENCE_ITEM_ID = ITEM_NAME_TO_ID[EXPERIENCE]
# Bridge RES types: 0 credits, 1 medigel, 2 salvage (omni-gel), 3 grenades. Credits are the game's LargeReward at the
# squad level (LE1APExtras LEVEL), or CREDITS_PER_LEVEL times the gear level when the squad level is unknown.
CREDITS_PER_LEVEL = 500
CREDITS_REWARD_COLUMN = CREDIT_REWARDS_COLUMNS.index("LargeReward")
# experience_size option value -> its LevelUp_Experience column
EXPERIENCE_COLUMNS = tuple(XP_REWARDS_COLUMNS.index(name) for name in ("MinorQuest", "MediumQuest", "MajorQuest", "MainPlot"))
RESOURCE_ITEMS = {
    ITEM_NAME_TO_ID["Credits"]: (0, None),
    ITEM_NAME_TO_ID["Omni-Gel"]: (2, 10),
    ITEM_NAME_TO_ID["Medi-Gel"]: (1, 3),
    ITEM_NAME_TO_ID["Grenades"]: (3, 2),
}
NO_MANUFACTURER = -1


def gear_level(gear_seen: int, gear_total: int) -> int:
    """Sophistication for the next gear delivery: I for the first gear line received, X for the last."""
    if gear_total <= 1:
        return 1
    return 1 + min(9, 9 * gear_seen // (gear_total - 1))


def upgrade_row(seed_name: str, index: int) -> int:
    """The Random Upgrade at received index `index`; the same on every replay of this seed."""
    return UPGRADE_ROWS[random.Random(f"{seed_name}:{index}").randrange(len(UPGRADE_ROWS))]


def level_row(table: Sequence[Sequence[int]], squad_level: int) -> Sequence[int]:
    """A level-up table's row for `squad_level`, clamped to the table."""
    return table[min(max(squad_level, 1), len(table)) - 1]


def credits_amount(squad_level: int | None, gear: int) -> int:
    if squad_level is None:
        return CREDITS_PER_LEVEL * gear
    return level_row(CREDIT_REWARDS, squad_level)[CREDITS_REWARD_COLUMN]


def experience_amount(squad_level: int, size: int) -> int:
    """XP for one Experience item: the game's quest reward of `size` (experience_size) at the squad level."""
    return level_row(XP_REWARDS, squad_level)[EXPERIENCE_COLUMNS[size]]


def delivery_op(item_id: int, index: int, gear_seen: int, gear_total: int, seed_name: str,
                log: logging.Logger = logger, squad_level: int | None = None) -> str:
    """The TXN delivery for one received item (plot flag, gear, resource, or NONE for what only the client counts)."""
    level = gear_level(gear_seen, gear_total)
    if (plot_bool := plot_bool_for_item(item_id)) is not None:
        return f"FLAG {plot_bool}"
    if (line := GEAR_BY_ITEM_ID.get(item_id)) is not None:
        return f"GIVE {line.item_row} {10 if line.spectre else level} {line.manufacturer_row}"
    if item_id == UPGRADE_ITEM_ID:
        return f"GIVE {upgrade_row(seed_name, index)} {level} {NO_MANUFACTURER}"
    if (resource := RESOURCE_ITEMS.get(item_id)) is not None:
        kind, amount = resource
        return f"RES {kind} {amount if amount is not None else credits_amount(squad_level, level)}"
    if item_id == FRAGMENT_ITEM_ID:
        log.info("Received a Prothean Beacon Fragment (counted by the client for the Ilos gate)")
    elif item_id == EXPERIENCE_ITEM_ID:
        pass  # granted by sync_experience through LE1APExtras
    else:
        log.info("Received unsupported item %s; skipping game write", item_id)
    return "NONE"


def sync_received_items(
    bridge: ItemBridge,
    received_items: Sequence[ReceivedItem],
    log: logging.Logger = logger,
    gear_total: int = 0,
    seed_name: str = "",
    *,
    identity: tuple[int, int],
    on_full: Callable[[int], None] | None = None,
    squad_level: int | None = None,
) -> int:
    """Deliver items after the save's durable received index, one TXN each, and return the new index.

    The bridge compares the save's identity and index, grants and advances the index in one game-thread step, so a
    failure mid-way (a load screen, a timeout, a lost reply) resumes at the first item not yet delivered and never
    grants twice. Gear levels depend only on the server's item order, so a replay (new game, older save) delivers the
    same items at the same levels. A gear item that finds the inventory full stops delivery there (strict order: the
    save holds one index) and `on_full` gets the number of items waiting; the next poll retries the same index."""
    saved_index = bridge.get_int(RECEIVED_INDEX_PLOT_INT)
    start_index = max(saved_index, 0)
    if saved_index < 0:
        log.warning("Save has negative AP received index %s; replaying from zero", saved_index)
    if start_index > len(received_items):
        log.warning(
            "Save AP received index %s is ahead of the server's %s items; leaving it unchanged",
            start_index,
            len(received_items),
        )
        return start_index

    gear_seen = sum(1 for item in received_items[:start_index] if item.item in GEAR_BY_ITEM_ID)
    for index in range(start_index, len(received_items)):
        item_id = received_items[index].item
        delivery = delivery_op(item_id, index, gear_seen, gear_total, seed_name, log, squad_level)
        try:
            if not bridge.transaction(identity, index, delivery):
                log.info("Received item %s was already in the save (retry after a lost reply)", index)
        except DeliveryGapError as error:  # the save changed under us: the next poll starts from its index
            log.warning("Item delivery paused, the save's received index changed: %s", error)
            return index
        except InventoryFullError:
            if on_full is not None:
                on_full(len(received_items) - index)
            return index
        if item_id in GEAR_BY_ITEM_ID:
            gear_seen += 1
    return len(received_items)


class ExperienceBridge(Protocol):
    def experience_index(self) -> int: ...

    def experience_transaction(self, identity: tuple[int, int], index: int, amount: int) -> bool: ...


def sync_experience(extras: ExperienceBridge, received_items: Sequence[ReceivedItem], delivered: int, squad_level: int,
                    size: int, *, identity: tuple[int, int], log: logging.Logger = logger) -> int:
    """Grant the Experience items among the first `delivered` received items that the save has not had yet, one XPTXN
    each, and return how many are still waiting. The extras keep their own index (EXPERIENCE_INDEX_PLOT_INT) and grant
    and advance it in one game-thread step, so a grant is never repeated; one that waits (no extras, a save made between
    the bridge's TXN and the grant) is granted on a later poll, at the squad level of that moment."""
    owed = sum(1 for item in received_items[:delivered] if item.item == EXPERIENCE_ITEM_ID)
    granted = extras.experience_index()
    if granted > owed:
        log.warning("Save has %s Experience items granted, more than the %s received; leaving it unchanged", granted, owed)
        return 0
    for index in range(granted, owed):
        amount = experience_amount(squad_level, size)
        try:
            if extras.experience_transaction(identity, index, amount):
                log.info("Experience: +%s XP (squad level %s)", amount, squad_level)
        except DeliveryGapError as error:  # the save changed under us: the next poll starts from its index
            log.warning("Experience paused, the save's Experience index changed: %s", error)
            return owed - index
    return 0


def sync_human_squad(bridge: ItemBridge) -> bool:
    """Put Kaidan and Ashley in the party (human_squad_at_citadel: they are not items), until the Virmire choice."""
    if any(bridge.get_bool(index) for index in VIRMIRE_SURVIVOR_PLOT_BOOLS):
        return False
    added = False
    for name in HUMAN_SQUAD:
        index = SQUAD_INPARTY_PLOT_BOOL[name]
        if not bridge.get_bool(index):
            bridge.set_bool(index, True)
            added = True
    return added


def sync_two_squadmates(bridge: ItemBridge, in_party: PlotReader) -> bool:
    """Keep TWO_SQUADMATES_PLOT_BOOL equal to "two or more InParty bools are set" (read from `in_party`); True if
    written."""
    value = sum(in_party.get_bool(index) for index in SQUAD_INPARTY_PLOT_BOOL.values()) >= 2
    if bridge.get_bool(TWO_SQUADMATES_PLOT_BOOL) == value:
        return False
    bridge.set_bool(TWO_SQUADMATES_PLOT_BOOL, value)
    return True


def sync_ilos_gate(
    bridge: ItemBridge,
    received_items: Sequence[ReceivedItem],
    fragments_required: int | None,
) -> bool:
    """Open Ilos (D5, D9) once enough fragments arrived and Virmire is done; the server's list is authoritative."""
    if fragments_required is None:
        return False
    fragments = sum(1 for item in received_items if item.item == FRAGMENT_ITEM_ID)
    if fragments < fragments_required or not bridge.get_bool(VIRMIRE_DONE_PLOT_BOOL):
        return False
    if not bridge.get_bool(ILOS_OPEN_PLOT_BOOL):
        bridge.set_bool(ILOS_OPEN_PLOT_BOOL, True)
        return True
    return False


def sync_virmire_open(bridge: ItemBridge) -> bool:
    """Run the Council's Virmire transmission (open Virmire, start its quest and journal entry) once the Virmire
    coordinates are in the save and Virmire is neither open nor done; True if it ran."""
    if not bridge.get_bool(VIRMIRE_COORDINATES_PLOT_BOOL) or bridge.get_bool(VIRMIRE_OPEN_PLOT_BOOL):
        return False
    if bridge.get_bool(VIRMIRE_DONE_PLOT_BOOL):
        return False
    bridge.run_transition(VIRMIRE_TRANSMISSION_TRANSITION)
    return True


LOCATION_PREDICATES = {
    LOCATION_NAME_TO_ID[name]: predicate
    for name, location in LOCATIONS.items()
    if (predicate := parse_plot_predicate(location.ot_plot)) is not None
}
UNSUPPORTED_LOCATIONS = tuple(
    name for name, location in LOCATIONS.items() if parse_plot_predicate(location.ot_plot) is None
)


class Snapshot:
    """One SNAP reply: the save identity and the plot values the client reads, all from the same game-thread step."""

    def __init__(self, identity: tuple[int, int], bools: dict[int, bool], ints: dict[int, int]) -> None:
        self.identity, self._bools, self._ints = identity, bools, ints

    @classmethod
    def parse(cls, values: Sequence[str]) -> Snapshot:
        if len(values) != 2 + len(SNAP_BOOL_RANGES) + len(SNAP_INTS):
            raise ValueError(f"{len(values)} fields")
        bools: dict[int, bool] = {}
        for (first, count), text in zip(SNAP_BOOL_RANGES, values[2:2 + len(SNAP_BOOL_RANGES)]):
            if len(text) != (count + 3) // 4:
                raise ValueError(f"range {first}: {len(text)} digits")
            for offset in range(count):
                bools[first + offset] = bool(int(text[offset // 4], 16) >> (offset % 4) & 1)
        ints = {index: int(value) for index, value in zip(SNAP_INTS, values[2 + len(SNAP_BOOL_RANGES):])}
        return cls((int(values[0]), int(values[1])), bools, ints)

    def get_bool(self, index: int) -> bool:
        return self._bools[index]

    def get_int(self, index: int) -> int:
        return self._ints[index]


def evaluate_location_predicates(bridge: PlotReader) -> set[int]:
    return {location_id for location_id, predicate in LOCATION_PREDICATES.items() if predicate.evaluate(bridge)}


def _snap_spec() -> tuple[tuple[tuple[int, int], ...], tuple[int, ...]]:
    """What SNAP reads: every location predicate's bools (as one vanilla and one AP range), the goal, the InParty
    bools, and the ints."""
    bools = {index for predicate in LOCATION_PREDICATES.values() if predicate.kind == "bool" for index in predicate.indices}
    bools.add(GOAL_PLOT_BOOL)
    bools.update(SQUAD_INPARTY_PLOT_BOOL.values())  # sync_two_squadmates
    vanilla, ours = [index for index in bools if index < 20000], [index for index in bools if index >= 20000]
    ranges = [(0, max(vanilla) + 1)] + ([(min(ours), max(ours) - min(ours) + 1)] if ours else [])
    ints = {predicate.indices[0] for predicate in LOCATION_PREDICATES.values() if predicate.kind == "int"}
    return tuple(ranges), tuple(sorted(ints | {RECEIVED_INDEX_PLOT_INT, NONCE_PLOT_INT}))


SNAP_BOOL_RANGES, SNAP_INTS = _snap_spec()
SNAP_REQUEST = "SNAP " + " ".join([f"b{first}+{count}" for first, count in SNAP_BOOL_RANGES] + [f"i{index}" for index in SNAP_INTS])


class NoticeBridge(Protocol):
    def note(self, text: str) -> None: ...


def journal_plot_state(items: Sequence, fragments_required: int | None, mako_shuffled: bool,
                       status=None) -> list[tuple[str, int, int | bool]]:
    """The journal quest's plot values (fragments held/needed, Mako) and, with a tracker status, checks in logic per
    region (journal) and the galaxy-map callouts per area (in logic / locked)."""
    fragments = sum(1 for item in items if item.item == FRAGMENT_ITEM_ID)
    has_mako = not mako_shuffled or any(item.item == ITEM_NAME_TO_ID[MAKO] for item in items)
    values: list[tuple[str, int, int | bool]] = [("int", FRAGMENTS_HELD_PLOT_INT, fragments), ("bool", MAKO_PLOT_BOOL, has_mako)]
    if fragments_required is not None:
        values.append(("int", FRAGMENTS_REQUIRED_PLOT_INT, int(fragments_required)))
    if status is not None:
        in_logic = {area_region(area) for area, count in status.areas.items() if count.in_logic}
        values += [("bool", IN_LOGIC_PLOT_BOOL_BASE + index, region.value in in_logic) for index, region in enumerate(Region)]
        for index, area in enumerate(MARKER_AREAS):  # galaxy-map callouts
            count = status.area(area)
            values += [("bool", MARKER_IN_LOGIC_BASE + index, count.in_logic > 0), ("bool", MARKER_LOCKED_BASE + index, count.locked > 0)]
    return values


def deliver_notices(bridge: NoticeBridge, notices: collections.deque[str], limit: int = NOTICES_PER_POLL) -> None:
    """Show up to `limit` queued notices in game; one stays queued if the game cannot show it yet (load screen)."""
    for _ in range(min(limit, len(notices))):
        bridge.note(notices[0])
        notices.popleft()


class ME1CommandProcessor(ClientCommandProcessor):
    ctx: ME1Context

    def _cmd_notices(self, level: str = "") -> bool:
        """In-game notices: all, important (items for you, hints, fragments, Ilos) or off. No argument: show.
        The F7 summary box shows at every level."""
        if level and level.lower() not in NOTICE_LEVELS:
            self.output(f"Usage: /notices [{' | '.join(NOTICE_LEVELS)}]")
            return False
        if level:
            self.ctx.notice_level = level.lower()
            self.ctx.notices_reset = True  # the poll thread clears the queues (it is the only one taking from them)
        self.output(f"In-game notices: {self.ctx.notice_level}")
        return True

    def _cmd_bind(self) -> bool:
        """Bind the loaded save to this slot: only for a career started with a client before 0.2.4 (it has items but
        no seed identity). Load that career's save first."""
        self.ctx.bind_requested = True
        self.output("The loaded save will be bound to this slot at the next poll, if it is an older client's save.")
        return True


class ME1Context(CommonContext):
    game = MassEffectLE1World.game
    command_processor = ME1CommandProcessor
    items_handling = 0b111
    gear_total = 0  # from slot data on Connected
    unlock_containers = False
    shop_slots: int | None = None  # from slot data on Connected
    mako_shuffled = False  # from slot data on Connected
    human_squad_at_citadel = False  # from slot data on Connected
    tracker: Tracker | None = None  # the seed's logic, rebuilt on Connected (F7 summary, landing notices)
    tracker_enabled = True  # off if the bridge refuses MAP/KEYS (older than v10)
    # F7 summary waiting for the game: (message box lines, notice lines for a bridge older than v11)
    summary_pending: tuple[list[str], list[str]] | None = None
    summary_next: list[list[str]] = []  # the F7 summary's pages after the one shown (F7 on an open page shows the next)
    notices_reset = False  # set by /notices: drop queued sends (and notices, when off) on the next poll
    inventory_full_pending = 0  # received items waiting behind a gear item that found the inventory full (v13)
    shop_scouted = False  # LocationScouts sent for this connection's shop slots
    shop_labels_sent = False  # the bridge holds the current shop labels (reset on reconnects and new scout results)
    notice_level = "all"  # /notices: all, important or off
    _last_area: str | None = None
    build_checked = False  # bridge and mod versions checked for this bridge connection
    bind_requested = False  # /bind: adopt the loaded save of an older client (it has items but no identity)
    _last_bridge_error = ""  # a new bridge error is logged even while the bridge is already marked unavailable
    _refusal_reported: str | None = None  # the identity or build refusal last logged (each is logged once)
    extras: ExtrasConnection | None = None  # LE1APExtras
    experience_size = 2  # experience_size from slot data (major_quest)
    _extras_problem: str | None = None  # the LE1APExtras problem last logged (each is logged once)

    def __init__(
        self,
        server_address: str | None,
        password: str | None,
        bridge: BridgeConnection | None = None,
        extras: ExtrasConnection | None = None,
    ) -> None:
        self.bridge = bridge or BridgeConnection()
        self.extras = extras or ExtrasConnection()
        self._bridge_ready: bool | None = None
        self.fragments_required: int | None = None
        self.infinite_sprint = False  # from slot data on Connected
        self.notices: collections.deque[str] = collections.deque()  # HUD notice lines waiting for the game (never dropped)
        self.notices_enabled = True  # off if the bridge refuses NOTE (older than v9)
        # this poll's sends to other slots, shown as one notice (appended on the event loop, taken in the poll thread)
        self.sends_pending: collections.deque[str] = collections.deque()
        self._fragments_noticed: int | None = None
        super().__init__(server_address, password)
        for name in UNSUPPORTED_LOCATIONS:
            logger.info("Skipping location with unsupported plot predicate: %s", name)

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            slot_data = args.get("slot_data", {})
            self.fragments_required = slot_data.get("fragments_required")
            self.gear_total = slot_data.get("gear_total", 0)
            self.unlock_containers = bool(slot_data.get("unlock_containers", False))
            self.infinite_sprint = bool(slot_data.get("infinite_sprint", False))
            self.shop_slots = slot_data.get("shop_slots")
            self.mako_shuffled = bool(slot_data.get("mako_shuffled", False))
            self.human_squad_at_citadel = bool(slot_data.get("human_squad_at_citadel", False))
            self.experience_size = int(slot_data.get("experience_size", 2))
            self._fragments_noticed = None
            self._last_area = None
            self.shop_scouted = self.shop_labels_sent = False
            try:
                self.tracker = Tracker(slot_data)
            except Exception:  # the tracker is an aid: never let it stop the client
                logger.exception("In-game tracker unavailable for this seed")
                self.tracker = None

        elif cmd == "LocationInfo":  # CommonContext stores the items in locations_info
            self.shop_labels_sent = False

    def on_print_json(self, args: dict) -> None:
        super().on_print_json(args)
        self.notice_for_print(args)

    def notice_for_print(self, args: dict) -> None:
        """Items for this slot and hints show right away; this slot's sends wait for the poll's one batched notice."""
        if args.get("type") == "ItemSend" and not self.is_uninteresting_item_send(args):
            text = self.item_send_notice(args["item"], args["receiving"])
            if self.slot_concerns_self(args["receiving"]):
                self.notify(text)
            elif self.notices_enabled and self.notice_level == "all":
                self.sends_pending.append(text)
        elif args.get("type") == "Hint" and not args.get("found"):
            self.notify(self.hint_notice(args["item"], args["receiving"]))

    def notify(self, text: str, important: bool = True) -> None:
        """Queue a HUD notice (as wrapped lines) if the /notices level shows it."""
        if self.notices_enabled and (self.notice_level == "all" or (important and self.notice_level == "important")):
            self.notices.extend(notice_lines(text))

    def _flush_sends(self) -> None:
        """This poll's sends as one notice: the send itself if there was one, else a count (the client lists them)."""
        sends = [self.sends_pending.popleft() for _ in range(len(self.sends_pending))]
        if sends:
            self.notify(sends[0] if len(sends) == 1 else f"Sent {len(sends)} items", important=False)

    def hint_notice(self, item, receiving: int) -> str:
        """HUD text for a hint about this slot's item or one of this slot's locations."""
        name = self.item_names.lookup_in_slot(item.item, receiving)
        location = self.location_names.lookup_in_slot(item.location, item.player)
        if self.slot_concerns_self(receiving):
            where = location if self.slot_concerns_self(item.player) else f"{self.player_names[item.player]}'s {location}"
            return f"Hint: your {name} is at {where}"
        return f"Hint: {self.player_names[receiving]}'s {name} is at your {location}"

    def shop_label(self, item) -> tuple[str, str]:
        """Shop row name and description for a scouted item (NetworkItem: item, location, player = receiver, flags)."""
        name = self.item_names.lookup_in_slot(item.item, item.player)
        kind = "Progression" if item.flags & 1 else "Useful" if item.flags & 2 else "Trap" if item.flags & 4 else "Filler"
        if self.slot_concerns_self(item.player):
            return name, f"Archipelago item for you: {name}. {kind}."
        owner = self.player_names[item.player]
        return f"{owner}'s {name}", f"Archipelago item for {owner}: {name}. {kind}."

    def _send_shop_labels(self) -> None:
        """Give the bridge every scouted shop slot's label (bridge v11+; an older bridge keeps the generic name)."""
        self.shop_labels_sent = True
        if self.bridge.version() < SHOP_LABEL_BRIDGE_VERSION:
            return
        self.bridge.shop_clear()
        for location, transition in SHOP_TRANSITIONS.items():
            item = self.locations_info.get(location)
            if item is None:
                continue
            try:
                self.bridge.shop_item(transition, *self.shop_label(item))
            except BridgeProtocolError as error:  # e.g. a name too long for the request: that slot keeps its generic name
                logger.warning("Shop slot %s keeps its generic name: %s", transition, error)

    def item_send_notice(self, item, receiving: int) -> str:
        """HUD text for an item send that concerns this slot."""
        name = self.item_names.lookup_in_slot(item.item, receiving)
        if self.slot_concerns_self(receiving):
            if self.slot_concerns_self(item.player):
                return f"Found {name}"
            return f"Received {name} from {self.player_names[item.player]}"
        return f"Sent {name} to {self.player_names[receiving]}"

    def _server_ready(self) -> bool:
        return self.slot is not None and self.server is not None and not self.server.socket.closed

    def _refuse(self, error: type[BridgeError], message: str, notice: str | None = None) -> None:
        """Stop this poll with `message`, logged (and shown in game) only when it is new."""
        if self._refusal_reported != message:
            self._refusal_reported = message
            logger.error(message)
            if notice is not None and self.notices_enabled:
                with contextlib.suppress(BridgeError):
                    self.bridge.note(notice)
        raise error(message)

    def _check_build(self) -> None:
        """R13, fail closed: the bridge answers PING with its version and reads the mod's version file."""
        version = self.bridge.ping()
        if self.build_checked:
            return
        if version < BRIDGE_VERSION:
            self._refuse(BuildMismatchError, f"The game bridge is v{version}, this client needs v{BRIDGE_VERSION} or newer. "
                         f"Close the game and run install-bridge.ps1 from the bridge zip of this release.")
        mod = self.bridge.mod_version()
        if mod is None or version_key(mod) < version_key(MOD_VERSION):
            found = "is missing or has no version" if mod is None else f"is {mod}"
            self._refuse(BuildMismatchError, f"The Archipelago mod (DLC_MOD_ArchipelagoME1) {found}; this client needs "
                         f"{MOD_VERSION} or newer. Install the mod zip of this release.")
        self.build_checked = True
        logger.info("Game bridge v%s, mod %s", version, mod)

    def save_identity(self) -> tuple[int, int] | None:
        """This slot's identity in the save, from the seed the server reported (None until RoomInfo has arrived)."""
        seed = getattr(self, "server_seed_name", None)
        if not seed:
            return None
        return seed_identity(seed, getattr(self, "team", None) or 0, self.slot or 0)

    def _check_identity(self, identity: tuple[int, int], snapshot: Snapshot) -> bool:
        """True if the snapshot's save is bound to this slot. An unbound fresh career is bound (False this poll); a save
        bound to another seed or slot is refused. Binding marks the save with a nonce first and IDSET binds only the
        save that still holds it, so a save loaded in between (or a request that ran late) is never bound."""
        if snapshot.identity == identity:
            self._refusal_reported = None
            return True
        if snapshot.identity != (0, 0):
            self._refuse(IdentityMismatchError, "This save belongs to another Archipelago seed or slot. Load the save "
                         "for this slot, or start a new career.", "Archipelago: this save is from another seed")
        nonce = random.randint(1, 0x7FFF_FFFF)
        self.bridge.nonce(nonce)
        marked = self.bridge.snapshot()
        if marked.get_int(NONCE_PLOT_INT) != nonce or marked.identity != (0, 0):
            return False  # another save was loaded meanwhile: check again next poll
        index = marked.get_int(RECEIVED_INDEX_PLOT_INT)
        if index == 0:
            if marked.get_bool(GOAL_PLOT_BOOL):
                self._refuse(IdentityMismatchError, "This save is past the ending, so it cannot start an Archipelago "
                             "seed. Load an earlier save, or start a new career.")
            done = len(evaluate_location_predicates(marked))
            if done > BIND_MAX_TRUE_LOCATIONS:
                self._refuse(IdentityMismatchError, f"This save already has {done} Archipelago checks done, so it is not "
                             "a fresh career. Start a new career, or load the save for this slot.")
            message = f"Save bound to this seed and slot ({done} checks done)"
        elif self.bind_requested:
            message = f"Save from an older client bound to this seed and slot by /bind ({index} items received)"
        else:
            self._refuse(IdentityMismatchError, f"This save has {index} Archipelago items but no seed identity: it was "
                         "started with a client older than 0.2.4. If it is this slot's save, type /bind; otherwise load "
                         "this slot's save or start a new career.")
        try:
            self.bridge.bind_identity(identity, nonce)
        except BridgeProtocolError as error:
            if "ERR bound" in str(error):
                self._refuse(IdentityMismatchError, "This save belongs to another Archipelago seed or slot. Load the "
                             "save for this slot, or start a new career.")
            return False  # ERR changed: another save was loaded before IDSET ran
        self.bind_requested = False
        self._refusal_reported = None
        logger.info(message)
        return False  # deliveries start next poll, from a snapshot of the bound save

    def _read_game_state(self, server_ready: bool) -> tuple[set[int], bool]:
        self._check_build()
        items: tuple = ()
        snapshot: Snapshot | None = None
        if server_ready:
            identity = self.save_identity()
            if identity is None:
                return set(), False  # RoomInfo (the seed) has not arrived yet
            self.bridge.set_session(identity)  # the bridge writes plot values only to the save bound to this identity
            snapshot = self.bridge.snapshot()
            if not self._check_identity(identity, snapshot):
                return set(), False
            items = tuple(self.items_received)
            squad_level = self._squad_level()
            waiting: list[int] = []
            delivered = sync_received_items(self.bridge, items, gear_total=self.gear_total,
                                            seed_name=getattr(self, "seed_name", None) or "", identity=identity,
                                            on_full=waiting.append, squad_level=squad_level)
            self._inventory_full_notice(waiting[0] if waiting else 0, delivered == len(items))
            self._sync_experience(items, delivered, identity, squad_level)
            if self.human_squad_at_citadel and sync_human_squad(self.bridge):
                logger.info("Kaidan and Ashley joined the party (Kaidan and Ashley Join at the Citadel)")
            sync_two_squadmates(self.bridge, snapshot)
            if self.unlock_containers and not self.bridge.get_bool(UNLOCK_CONTAINERS_PLOT_BOOL):
                self.bridge.set_bool(UNLOCK_CONTAINERS_PLOT_BOOL, True)
                logger.info("Unlock Containers: Decryption containers now open like plain ones")
            if self.bridge.get_bool(INFINITE_SPRINT_PLOT_BOOL) != self.infinite_sprint:
                self.bridge.set_bool(INFINITE_SPRINT_PLOT_BOOL, self.infinite_sprint)
            if self.shop_slots is not None and self.bridge.get_int(SHOP_SLOTS_PLOT_INT) != self.shop_slots + 1:
                self.bridge.set_int(SHOP_SLOTS_PLOT_INT, self.shop_slots + 1)
            fragments = sum(1 for item in items if item.item == FRAGMENT_ITEM_ID)
            if self._fragments_noticed is not None and fragments > self._fragments_noticed:
                self.notify(f"Fragments: {fragments}/{self.fragments_required}")
            self._fragments_noticed = fragments
            if sync_ilos_gate(self.bridge, items, self.fragments_required):
                logger.info("Fragment gate met; Ilos opened")
                self.notify("Ilos is open")
            if sync_virmire_open(self.bridge):
                logger.info("Virmire coordinates: the Council's Virmire transmission ran (quest and journal entry)")
            self._sync_journal(items)
            if not self.shop_labels_sent and self.locations_info:
                self._send_shop_labels()
            if self.notices_enabled and self.tracker is not None and self.tracker_enabled:
                self._tracker_notices(items)
        # Checks and the goal come from the snapshot, whose identity was checked in the same game-thread step.
        true_locations = evaluate_location_predicates(snapshot) if snapshot is not None else set()
        goal_complete = snapshot is not None and snapshot.get_bool(GOAL_PLOT_BOOL)
        if self.notices_enabled:
            if self.notices_reset:
                self.notices_reset = False
                self.sends_pending.clear()
                if self.notice_level == "off":
                    self.notices.clear()
            self._flush_sends()
            self._deliver_summary()
            try:
                deliver_notices(self.bridge, self.notices)
            except GameNotReadyError:
                pass  # load screen: the notice stays queued for the next poll
            except BridgeProtocolError as error:
                logger.warning("In-game notices are off (bridge older than v9?): %s", error)
                self.notices_enabled = False
                self.notices.clear()
                self.sends_pending.clear()
        return true_locations, goal_complete

    def _extras_unavailable(self, problem: str) -> None:
        if self._extras_problem != problem:
            self._extras_problem = problem
            logger.warning("LE1APExtras: %s", problem)
        if self.extras is not None:
            self.extras.close()

    def _squad_level(self) -> int | None:
        """The squad level from LE1APExtras, or None without it (credits then scale with the gear level)."""
        if self.extras is None:
            return None
        try:
            level = self.extras.squad_level()
        except (BridgeError, OSError) as error:
            self._extras_unavailable(f"unavailable ({error}); Experience items wait, credits scale with gear")
            return None
        if self._extras_problem is not None:
            logger.info("LE1APExtras connected (squad level %s)", level)
            self._extras_problem = None
        return level

    def _sync_experience(self, items: Sequence, delivered: int, identity: tuple[int, int], squad_level: int | None) -> None:
        """Grant delivered Experience items through LE1APExtras; without it they wait (told once per count)."""
        if not any(item.item == EXPERIENCE_ITEM_ID for item in items[:delivered]):
            return
        if squad_level is None:
            self._extras_unavailable("not installed or not running: Experience items wait for it (install the bridge "
                                     "zip of this release)")
            return
        try:
            sync_experience(self.extras, items, delivered, squad_level, self.experience_size, identity=identity)
        except (BridgeError, OSError) as error:
            self._extras_unavailable(f"Experience delivery paused ({error})")

    def _inventory_full_notice(self, waiting: int, all_delivered: bool) -> None:
        """Tell the player once per count while gear waits for room, and once when the waiting items arrived. Each HUD
        notice is one line of NOTICE_LINE_CHARS (a longer text shows as several notices that replace each other)."""
        if waiting:
            if waiting != self.inventory_full_pending:
                logger.warning("Inventory full (300 items): %s received item(s) wait. Sell gear or convert it to "
                               "omni-gel; they arrive in order a few seconds later.", waiting)
                self.notify("Inventory full")
                self.notify(f"{waiting} items waiting")
            self.inventory_full_pending = waiting
        elif all_delivered and self.inventory_full_pending:
            logger.info("Inventory has room: %s waiting item(s) delivered", self.inventory_full_pending)
            self.notify(f"Got {self.inventory_full_pending} items")
            self.inventory_full_pending = 0

    def _tracker_notices(self, items: Sequence) -> None:
        """Queue the F7 summary and a notice for each newly entered area (both computed from the rebuilt logic)."""
        try:
            presses, closes = self.bridge.key_presses()
            area = level_area(self.bridge.map_name())
        except BridgeProtocolError as error:
            logger.warning("In-game tracker is off (bridge older than v10?): %s", error)
            self.tracker_enabled = False
            return
        entered = area is not None and area != self._last_area
        if closes and not presses and self.summary_next:  # F7 closed a page: show the next one
            self.summary_pending = (self.summary_next.pop(0), [])
        if not presses and not entered:
            return
        missing = set(self.missing_locations) - set(self.locations_checked)
        status = self.tracker.status([item.item for item in items], missing)
        if entered:
            self._last_area = area
            self.notify(area_line(status, area), important=False)
        if presses:
            pages = summary_pages(summary_lines(status, planets_shown=len(status.areas)))
            self.summary_pending, self.summary_next = (pages[0], summary_lines(status)), pages[1:]

    def _deliver_summary(self) -> None:
        """Show the pending F7 summary in the message box (bridge v11+); kept on a load screen, else sent as notices."""
        pending, self.summary_pending = self.summary_pending, None
        if pending is None:
            return
        box, notices = pending
        try:
            if (self.bridge.version() >= MSGBOX_BRIDGE_VERSION
                    and len(message_box_text(box).encode("utf-8")) <= MSGBOX_MAX_BYTES):
                self.bridge.message_box(box)
                return
        except GameNotReadyError:
            self.summary_pending = pending
            return
        except BridgeProtocolError as error:
            logger.warning("F7 summary sent as notices: %s", error)
        for line in notices:
            self.notices.extend(notice_lines(line))

    def _sync_journal(self, items: Sequence) -> None:
        """Keep the journal quest's plot state current; values are read back each poll, so a reloaded save is fixed."""
        status = None
        if self.tracker is not None:
            missing = set(self.missing_locations) - set(self.locations_checked)
            status = self.tracker.status([item.item for item in items], missing)
        for kind, index, value in journal_plot_state(items, self.fragments_required, self.mako_shuffled, status):
            if kind == "int" and self.bridge.get_int(index) != value:
                self.bridge.set_int(index, value)
            elif kind == "bool" and self.bridge.get_bool(index) != value:
                self.bridge.set_bool(index, value)
        if not self.bridge.get_bool(JOURNAL_ACTIVE_PLOT_BOOL):
            try:
                self.bridge.run_transition(JOURNAL_ACTIVE_PLOT_BOOL)  # through the plot manager, like vanilla quest updates
            except BridgeProtocolError:
                self.bridge.set_bool(JOURNAL_ACTIVE_PLOT_BOOL, True)  # bridge older than v10
            logger.info("Journal: Archipelago quest activated")

    async def poll_game(self) -> None:
        # Without a connected slot, items_received is not authoritative and sends would be dropped.
        server_ready = self._server_ready()
        true_locations, goal_complete = await asyncio.to_thread(self._read_game_state, server_ready)
        if not server_ready:
            return
        if not self.shop_scouted:
            self.shop_scouted = True
            shop_locations = sorted(set(SHOP_TRANSITIONS) & self.server_locations)
            if shop_locations:
                await self.send_msgs([{"cmd": "LocationScouts", "locations": shop_locations, "create_as_hint": 0}])

        new_locations = true_locations - self.checked_locations - self.locations_checked
        if self.server_locations:
            new_locations &= self.server_locations
        if new_locations:
            self.locations_checked.update(new_locations)
            await self.send_msgs([{"cmd": "LocationChecks", "locations": sorted(new_locations)}])
            logger.info("Sent %s new location check(s)", len(new_locations))

        if goal_complete and not self.finished_game:
            self.finished_game = True
            await self.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
            logger.info("Goal complete")

    async def bridge_loop(self) -> None:
        try:
            while not self.exit_event.is_set():
                try:
                    await self.poll_game()
                    if self._bridge_ready is not True:
                        logger.info("Connected to LE1APBridge with a game world loaded")
                    self._bridge_ready = True
                except GameNotReadyError:
                    if self._bridge_ready is not False:
                        logger.info("LE1APBridge is connected; waiting for a game world")
                    self._bridge_ready = False
                    self.shop_labels_sent = False
                except IdentityMismatchError:  # logged once by _refuse; nothing is touched
                    self._bridge_ready = False
                    self.shop_labels_sent = False
                except BuildMismatchError:  # logged once by _refuse
                    self._bridge_ready = False
                    self.shop_labels_sent = False
                    self.build_checked = False  # the player may update the bridge or mod and restart the game
                except BridgeError as error:
                    if self._bridge_ready is not False or str(error) != self._last_bridge_error:
                        logger.warning("LE1APBridge unavailable: %s", error)
                    self._last_bridge_error = str(error)
                    self._bridge_ready = False
                    self.shop_labels_sent = False
                    self.build_checked = False
                except OSError as error:
                    if self._bridge_ready is not False:
                        logger.warning("LE1APBridge pipe unavailable: %s", error)
                    self.bridge.close()
                    self._bridge_ready = False
                    self.shop_labels_sent = False
                    self.build_checked = False
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            self.bridge.close()
            if self.extras is not None:
                self.extras.close()


async def _main(args: argparse.Namespace) -> None:
    ctx = ME1Context(args.connect, args.password)
    ctx.auth = args.name
    ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
    bridge_task = asyncio.create_task(ctx.bridge_loop(), name="ME1 bridge loop")

    # get_base_parser only defines --nogui when stdout exists; the Launcher spawns clients without a console.
    if gui_enabled and not getattr(args, "nogui", False):
        ctx.run_gui()
    ctx.run_cli()

    try:
        await ctx.exit_event.wait()
    finally:
        bridge_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bridge_task
        await ctx.shutdown()


def launch(*launch_args: str) -> None:
    parser = get_base_parser(description="Mass Effect Legendary Edition 1 Archipelago Client")
    parser.add_argument("--name", default=None, help="Slot name to connect as.")
    parser.add_argument("url", nargs="?", help="archipelago:// connection URL")
    args = handle_url_arg(parser.parse_args(launch_args or None), parser)

    Utils.init_logging("ME1Client", exception_logger="Client")
    colorama.just_fix_windows_console()
    try:
        asyncio.run(_main(args))
    finally:
        colorama.deinit()


if __name__ == "__main__":
    launch()
