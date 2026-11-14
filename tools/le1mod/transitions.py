"""BioStateEventMap (plot transition map) binary: read its elements, retarget bool writes in place, add transitions.

Layout after the export's net index and empty property list: int count, then per transition: int id, int version,
int element count, elements. An element starts with its type (LEX BioStateEventMap.cs): 0 bool (5 ints: type, version,
bool, value, useParam), 1 consequence (3), 2 float (6), 3 function call (9), 4 int (6), 5-7 local bool, float and int
(9), 8 substate bool (8 ints, the last a sibling count, then that many ints). A ConsequenceMap has the same layout.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

BOOL, SUBSTATE = 0, 8
FIXED_INTS = {BOOL: 5, 1: 3, 2: 6, 3: 9, 4: 6, 5: 9, 6: 9, 7: 9}  # element type -> ints


@dataclass(frozen=True)
class BoolWrite:
    transition: int
    offset: int  # of the bool index in the buffer
    bool_index: int
    value: int


def bool_writes(data: bytes | bytearray, start: int) -> list[BoolWrite]:
    """Every bool element of the map that begins at `start` (its transition count)."""
    count = struct.unpack_from("<i", data, start)[0]
    o, writes = start + 4, []
    for _ in range(count):
        transition, _version, elements = struct.unpack_from("<3i", data, o)
        o += 12
        for _ in range(elements):
            kind = struct.unpack_from("<i", data, o)[0]
            if kind == SUBSTATE:
                ints = 8 + struct.unpack_from("<i", data, o + 28)[0]
            elif kind in FIXED_INTS:
                ints = FIXED_INTS[kind]
            else:
                raise ValueError(f"transition {transition}: unknown element type {kind} at {o}")
            if kind in (BOOL, SUBSTATE):
                bool_index, value = struct.unpack_from("<2i", data, o + 8)
                writes.append(BoolWrite(transition, o + 8, bool_index, value))
            o += 4 * ints
    return writes


def retarget_bool_sets(data: bytearray, start: int, bools: set[int], target: int) -> list[BoolWrite]:
    """Point every element that sets one of `bools` to true at `target` instead; returns the elements changed."""
    changed = [w for w in bool_writes(data, start) if w.bool_index in bools and w.value == 1]
    for w in changed:
        struct.pack_into("<i", data, w.offset, target)
    return changed


def transitions_elements(data: bytes | bytearray, start: int) -> dict[int, list[bytes]]:
    """Each transition of the map that begins at `start` (and runs to the end of `data`): its elements, raw."""
    count = struct.unpack_from("<i", data, start)[0]
    o, found = start + 4, {}
    for _ in range(count):
        transition, _version, elements = struct.unpack_from("<3i", data, o)
        o += 12
        raw = []
        for _ in range(elements):
            kind = struct.unpack_from("<i", data, o)[0]
            if kind == SUBSTATE:
                size = 4 * (8 + struct.unpack_from("<i", data, o + 28)[0])
            elif kind in FIXED_INTS:
                size = 4 * FIXED_INTS[kind]
            else:
                raise ValueError(f"transition {transition}: unknown element type {kind} at {o}")
            raw.append(bytes(data[o:o + size]))
            o += size
        found[transition] = raw
    if o != len(data):
        raise ValueError(f"map ends at {o}, data at {len(data)}")
    return found


def transition_ids(data: bytes | bytearray, start: int) -> list[int]:
    """The transition ids of the map that begins at `start`, in order."""
    return list(transitions_elements(data, start))


def bool_element(bool_index: int) -> bytes:
    """An element that sets `bool_index` to true."""
    return struct.pack("<5i", BOOL, 0, bool_index, 1, 0)


def add_transition(data: bytes | bytearray, start: int, transition: int, elements: list[bytes]) -> bytes:
    """The map (`data` from `start` to its end) with transition `transition` added, running `elements`."""
    if transition in transition_ids(data, start):
        raise ValueError(f"transition {transition} already exists")
    count = struct.unpack_from("<i", data, start)[0]
    event = struct.pack("<3i", transition, 0, len(elements)) + b"".join(elements)
    return bytes(data[:start]) + struct.pack("<i", count + 1) + bytes(data[start + 4:]) + event


def add_bool_transition(data: bytes | bytearray, start: int, transition: int, bool_index: int) -> bytes:
    """The map with transition `transition` added: it sets `bool_index` to true."""
    return add_transition(data, start, transition, [bool_element(bool_index)])
