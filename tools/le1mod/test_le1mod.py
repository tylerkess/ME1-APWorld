"""Tests for the LE1 mod patcher. Pure Python; the package round trip needs the game and runs only when LE1_GAME is
set to the LE1 game folder (...\\Game\\ME1).

usage: python -m unittest tools/le1mod/test_le1mod.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_mod  # noqa: E402
import conversation  # noqa: E402
import le1pkg  # noqa: E402
import transitions  # noqa: E402
import twoda  # noqa: E402


def transition_map(*events: tuple[int, list[list[int]]]) -> bytearray:
    data = bytearray(struct.pack("<i", len(events)))
    for transition, elements in events:
        data += struct.pack("<3i", transition, 0, len(elements))
        for element in elements:
            data += struct.pack(f"<{len(element)}i", *element)
    return data


class TestTransitions(unittest.TestCase):
    def test_retargets_only_true_in_party_writes(self) -> None:
        data = transition_map(
            (1992, [[0, 0, 3817, 1, 0], [0, 0, 3939, 1, 0]]),  # in squad + in party
            (2223, [[0, 0, 3939, 0, 0]]),  # leaves the party: kept
            (4057, [[4, 0, 57, 1, 0, 1]]),  # int element
            (188, [[7, 0, 1, 4, 2, 0, 5659, 0, 0]]),  # local int element
            (1212, [[8, 0, 3944, 1, 0, 0, 3203, 2, 3205, 3207]]),  # substate with two siblings
        )
        before = bytes(data)
        changed = transitions.retarget_bool_sets(data, 0, build_mod.IN_PARTY_BOOLS, build_mod.SQUAD_JOIN_SUPPRESSED)
        self.assertEqual([(w.transition, w.bool_index) for w in changed], [(1992, 3939), (1212, 3944)])
        self.assertEqual(len(data), len(before))
        after = {(w.transition, w.bool_index, w.value) for w in transitions.bool_writes(data, 0)}
        self.assertEqual(after, {(1992, 3817, 1), (1992, 20199, 1), (2223, 3939, 0), (1212, 20199, 1)})

    def test_unknown_element_type_stops(self) -> None:
        with self.assertRaises(ValueError):
            transitions.bool_writes(transition_map((1, [[9, 0, 0, 0, 0]])), 0)


class TestEmptyConsequences(unittest.TestCase):
    def test_adds_the_missing_outcomes_once(self) -> None:
        start = 12
        data = b"\x00" * start + transition_map(
            (4925, [[3, 0, 5664, 0, 745, 0, 2976, 0, 5]]),  # function element
            (4392, [[1, 0, 77], [2, 0, 5, 0, 0, 0]]),  # consequence and float elements
        )
        out = build_mod.with_empty_consequences(data, start)
        self.assertEqual(transitions.transition_ids(out, start), [4925, 4392, *build_mod.BDTS_OUTCOMES])
        self.assertTrue(all(transitions.transitions_elements(out, start)[t] == [] for t in build_mod.BDTS_OUTCOMES))
        self.assertEqual(build_mod.with_empty_consequences(out, start), out)


class TestTwoDA(unittest.TestCase):
    def test_build_reads_back(self) -> None:
        names = ["None", "m_lstRowNumbers", "ArrayProperty", "tid", "drops", "Name", "csid", "QMPremium", "Other"]
        pkg = le1pkg.Package("fake", b"", None, bytearray(), names=names)
        rows = {307: {"tid": 307, "drops": 22, "Name": "QMPremium", "csid": 1},
                168: {"tid": 168, "drops": 14, "Name": "Other", "csid": 1}}
        data = twoda.build(pkg, 2, ["tid", "drops", "Name", "csid"], rows)
        pkg.image = bytearray(data)
        export = le1pkg.Export(1, 0, 0, "table", len(data), 0, 0)
        columns, read_rows = twoda.read(pkg, export)
        self.assertEqual(columns, ["tid", "drops", "Name", "csid"])
        self.assertEqual(read_rows, rows)

    def test_numbered_names_mixed_cells_and_empty_cells(self) -> None:
        names = ["None", "m_lstRowNumbers", "ArrayProperty", "Shader", "Bump", "Event", "GXM_Planet"]
        pkg = le1pkg.Package("fake", b"", None, bytearray(), names=names)
        rows = {89: {"Shader": "GXM_Planet_178", "Bump": 0.25, "Event": 7}, 90: {"Shader": "GXM_Planet", "Bump": 0}}
        data = twoda.build(pkg, 2, ["Shader", "Bump", "Event"], rows)
        self.assertIn(struct.pack("<ii", names.index("GXM_Planet"), 179), data)  # base name and number, as UE3 stores it
        pkg.image = bytearray(data)
        _, read_rows = twoda.read(pkg, le1pkg.Export(1, 0, 0, "table", len(data), 0, 0))
        self.assertEqual(read_rows, rows)
        self.assertIsInstance(read_rows[90]["Bump"], int)


def planet(map_number: int, usable_planet: int = 974) -> dict[str, object]:
    return {"Map": map_number, "UsableConditional": 1, "UsableFunction": 974, "UsableParameter": 1,
            "UsablePlanetConditional": 1, "UsablePlanetFunction": usable_planet, "UsablePlanetParameter": 1}


class TestPlanetGates(unittest.TestCase):
    MAPS = {1: {"Map": "BIOA_STA00"}, 3: {"Map": "BIOA_WAR00"}, 53: {"Map": "BIOA_UNC53"}, 80: {"Map": "BIOA_UNC80"},
            101: {"Map": "BIOA_FRE31"}, 400: {"Map": "BIOA_PRC2"}}

    def test_mako_levels_need_the_mako_and_keep_their_landing_gate(self) -> None:
        planets = {1: planet(1, 836), 77: planet(3, 981), 89: planet(53), 163: planet(80, 2507), 169: planet(101, 2507),
                   6207: planet(400), 7: planet(-1)}
        rows = build_mod.planet_gate_rows(planets, self.MAPS)
        self.assertEqual(sorted(rows), [77, 89, 163, 6207])  # not the Citadel, a freighter or a planet without a level
        for number in (77, 163, 6207):
            self.assertEqual((rows[number]["UsableConditional"], rows[number]["UsableFunction"], rows[number]["UsableParameter"]),
                             (0, build_mod.MAKO, 1))
            self.assertEqual(rows[number]["UsablePlanetFunction"], planets[number]["UsablePlanetFunction"])
        self.assertEqual((rows[89]["UsableFunction"], rows[89]["UsablePlanetConditional"], rows[89]["UsablePlanetFunction"],
                          rows[89]["UsablePlanetParameter"]), (build_mod.MAKO, 0, build_mod.TWO_SQUADMATES, 1))


class TestAddTransition(unittest.TestCase):
    def test_adds_a_bool_transition_once(self) -> None:
        start = 4
        data = b"\x00" * start + transition_map((20101, [[0, 0, 20199, 1, 0], [0, 0, 20101, 1, 0]]))
        out = transitions.add_bool_transition(data, start, 20103, 20103)
        self.assertEqual(transitions.transition_ids(out, start), [20101, 20103])
        self.assertEqual([(w.transition, w.bool_index, w.value) for w in transitions.bool_writes(bytearray(out), start)],
                         [(20101, 20199, 1), (20101, 20101, 1), (20103, 20103, 1)])
        with self.assertRaises(ValueError):
            transitions.add_bool_transition(out, start, 20103, 20103)


class TestStaleGates(unittest.TestCase):
    def test_gate_flags_outside_the_gated_rows_are_stale(self) -> None:
        planets = {1: planet(1), 2: {**planet(1), "UsableConditional": 0, "UsableFunction": build_mod.MAKO},
                   3: {**planet(1), "UsablePlanetConditional": 0, "UsablePlanetFunction": 4681}}
        self.assertEqual(build_mod.stale_gates(planets, {1}), [2])
        self.assertEqual(build_mod.stale_gates(planets, {1, 2}), [])


class TestStoryRecruitment(unittest.TestCase):
    def test_swaps_each_int_const_once_and_is_idempotent(self) -> None:
        code = b"\x1b\x00" + b"\x1d" + struct.pack("<i", 3941) + b"\x27" + b"\x1d" + struct.pack("<i", 3942) + b"\x16"
        swaps = {3941: 20101, 3942: 20102}
        out = build_mod.swap_int_consts(code, swaps)
        self.assertEqual(len(out), len(code))
        self.assertEqual(out, code.replace(struct.pack("<i", 3941), struct.pack("<i", 20101))
                                  .replace(struct.pack("<i", 3942), struct.pack("<i", 20102)))
        self.assertIsNone(build_mod.swap_int_consts(out, swaps))
        with self.assertRaises(ValueError):
            build_mod.swap_int_consts(code + b"\x1d" + struct.pack("<i", 3941), swaps)

    def test_node_condition_and_bool_rewrite(self) -> None:
        names = ["None", "m_EntryList", "ArrayProperty", "nConditionalFunc", "IntProperty", "bFireConditional",
                 "BoolProperty", "nConditionalParam", "nStateTransition"]

        def prop(name: str, kind: str, value: bytes) -> bytes:
            size = 0 if kind == "BoolProperty" else len(value)
            return struct.pack("<iiiiii", names.index(name), 0, names.index(kind), 0, size, 0) + value

        def node(func: int, fire: bool, param: int) -> bytes:
            return (prop("nStateTransition", "IntProperty", struct.pack("<i", -1))
                    + prop("nConditionalFunc", "IntProperty", struct.pack("<i", func))
                    + prop("bFireConditional", "BoolProperty", bytes([fire]))
                    + prop("nConditionalParam", "IntProperty", struct.pack("<i", param)) + struct.pack("<ii", 0, 0))
        body = struct.pack("<i", 2) + node(-1, False, -1) + node(418, True, 4)
        data = bytearray(struct.pack("<i", 0) + prop("m_EntryList", "ArrayProperty", body) + struct.pack("<ii", 0, 0))
        cond = conversation.node_condition(names, data, "m_EntryList", 1)
        self.assertEqual((cond.func, cond.fire, cond.param), (418, True, 4))
        conversation.set_bool_condition(data, cond, 20102, True)
        again = conversation.node_condition(names, data, "m_EntryList", 1)
        self.assertEqual((again.func, again.fire, again.param), (20102, False, 1))
        self.assertEqual(conversation.node_condition(names, data, "m_EntryList", 0).func, -1)
        self.assertEqual(conversation.property_offset(names, data, "m_EntryList"), 4 + 24)
        with self.assertRaises(KeyError):
            conversation.property_offset(names, data, "nStateTransition")  # inside the array, not top level

    def test_kismet_checks_name_their_bools(self) -> None:
        for checks in build_mod.KISMET_CHECKS.values():
            for full_name, old, new in checks:
                self.assertIn(".BioSeqAct_PMCheckState_", full_name)
                self.assertEqual((old, new), (build_mod.ASHLEY_IN_PARTY, build_mod.ASHLEY_RECRUITED))


@unittest.skipUnless(os.environ.get("LE1_GAME"), "set LE1_GAME to the LE1 game folder")
class TestGamePackage(unittest.TestCase):
    def test_round_trip(self) -> None:
        game = os.environ["LE1_GAME"]
        dll = os.path.join(game, "Binaries", "Win64", "oo2core_8_win64.dll")
        src = os.path.join(game, "BioGame", "CookedPCConsole", "PlotManager.pcc")
        pkg = le1pkg.read(src, dll)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "PlotManager.pcc")
            le1pkg.write(pkg, out, dll)
            self.assertEqual(le1pkg.read(out, dll).image, pkg.image)

    def test_append_exports_moves_nothing_else(self) -> None:
        dll = os.path.join(os.environ["LE1_GAME"], "Binaries", "Win64", "oo2core_8_win64.dll")
        src = os.path.join(build_mod.MOD_DIR, "CookedPCConsole", build_mod.SHOP_PACKAGE)
        pkg = le1pkg.read(src, dll)
        target = pkg.exports[0]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, build_mod.SHOP_PACKAGE)
            le1pkg.write_image(pkg, le1pkg.append_exports(pkg, {target.index: b"APTEST"}), out, dll)
            again = le1pkg.read(out, dll)
            kept = [c[:2] for c in pkg.header.chunks]
            self.assertEqual([c[:2] for c in again.header.chunks][:len(kept)], kept)  # the game's chunks stay
            for a, b in zip(pkg.exports, again.exports):
                if a.index == target.index:
                    self.assertEqual(bytes(again.image[b.offset:b.offset + b.size]), b"APTEST")
                else:
                    self.assertEqual((a.offset, a.size), (b.offset, b.size), a.name)

    def test_relayout(self) -> None:
        """Nothing added or replaced gives the same image; an added name reads back and the tables still parse."""
        dll = os.path.join(os.environ["LE1_GAME"], "Binaries", "Win64", "oo2core_8_win64.dll")
        src = os.path.join(build_mod.MOD_DIR, "CookedPCConsole", build_mod.SHOP_PACKAGE)
        pkg = le1pkg.read(src, dll)
        self.assertEqual(le1pkg.relayout(pkg, [], {}), bytes(pkg.image))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, build_mod.SHOP_PACKAGE)
            le1pkg.write_image(pkg, le1pkg.relayout(pkg, ["APTestName"], {}), out, dll)
            again = le1pkg.read(out, dll)
            self.assertEqual(again.names, pkg.names + ["APTestName"])
            self.assertEqual([e.name for e in again.exports], [e.name for e in pkg.exports])
            for a, b in zip(pkg.exports, again.exports):
                self.assertEqual(pkg.image[a.offset:a.offset + a.size], again.image[b.offset:b.offset + b.size], a.name)


if __name__ == "__main__":
    unittest.main()
