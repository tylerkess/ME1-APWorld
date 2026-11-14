"""Apply this repo's patches to the game mod in mod/DLC_MOD_ArchipelagoME1.

usage: python tools/le1mod/build_mod.py <LE1 game folder> [--base <released mod>]
  <LE1 game folder>: ...\\Mass Effect Legendary Edition\\Game\\ME1 (vanilla packages and the Oodle DLL come from it)
  --base: first replace mod/DLC_MOD_ArchipelagoME1 with the one in a release's MassEffectAPSetup-<version>-ME1.zip,
          ArchipelagoME1-mod-<version>.zip, or a folder holding DLC_MOD_ArchipelagoME1
Running it again gives the same files: vanilla packages are read from the game, and a patch finds nothing left to do
in a package it already patched. No game file is changed.

Patches (squadmates join only through Archipelago items):
- the vanilla transition map (PlotManagerAuto.StateTransitionMap, in BIOC_Materials.pcc, shipped in the mod as an
  override) and the mod's own map (PlotManagerAutoAPME1.pcc): every element that sets a squadmate's in-party bool
  (3939-3944) sets SQUAD_JOIN_SUPPRESSED instead. The in-squad bools stay, so scripted scenes still spawn their
  squadmate for that scene.
- shop stock (BIOG_2DA_APME1_Treasure_X.pcc, Treasure_Tables_TreasureTables_part_1): a merchant's stock draws `drops`
  entries from its treasure table, and the mod's Archipelago Item entries are extra entries in those tables, so each
  such table draws all its entries (vanilla and Archipelago); every shop slot is then stocked.
- landing gates (mod/BASEGAME/CookedPCConsole/Engine.pcc, the game's GalaxyMap_Planet; the mod's DLC table keeps no
  vanilla row numbers): planets with a section that needs two squadmates (Agebinium) are landable only while the
  client's two-squadmates bool is set; planets whose landing puts the Mako in reach are usable only while the client's
  Mako bool is set.
- story recruitment (PlotManager.pcc conditionals, and Citadel conversations in each language): checks that read
  Garrus's, Wrex's or Tali's in-party bool as "recruited in the story" read the story's own flag instead, since the
  client sets the in-party bool when the item arrives (CONDITIONAL_SWAPS, CONVERSATION_PATCHES). Eden Prime's
  trench (BIOA_PRO10_05_DSG.pcc) does the same for Ashley, so her join conversation plays once (KISMET_CHECKS).
- The Look (PlotManagerAutoAPME1.pcc, the Council's Spectre induction in each language): mod transition 20103 sets
  bool 20103, and the induction runs it right after the asari Councilor's look (BOOL_TRANSITIONS, TRANSITION_NODES).
- Virmire transmission (PlotManagerAutoAPME1.pcc): mod transition 20104 does what the Council's Virmire transmission
  does (opens Virmire, starts its quest and journal entry); the client runs it when the coordinates arrive.

Crash fix:
- Bring Down the Sky outcomes (BIOC_Materials.pcc, PlotManagerAuto.ConsequenceMap): the DLC's OutcomeMap names three
  outcomes (BDTS_OUTCOMES) that no ConsequenceMap defines. Looking one up walks every registered ConsequenceMap, and
  with mods installed the last entry of that list can be invalid, which crashed the game on examining a body on
  Asteroid X57. Each one is added to the game's map as an empty consequence, so the lookup stops at the first map;
  the game does nothing for them either way.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conversation as conversation_mod  # noqa: E402
import le1pkg  # noqa: E402
import transitions  # noqa: E402
import twoda  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MOD_FOLDER = "DLC_MOD_ArchipelagoME1"
MOD_DIR = os.path.join(REPO, "mod", MOD_FOLDER)
IN_PARTY_BOOLS = set(range(3939, 3945))  # Kaidan, Ashley, Garrus, Wrex, Liara, Tali
SQUAD_JOIN_SUPPRESSED = 20199  # set where vanilla would add a squadmate to the party; read by nothing
TRANSITION_MAPS = {  # package -> its StateTransitionMap export, and whether it comes from the game or the mod
    "BIOC_Materials.pcc": ("PlotManagerAuto.StateTransitionMap", "game"),
    "PlotManagerAutoAPME1.pcc": ("StateTransitionMap", "mod"),
}


def extract_mod(source: str, dest: str) -> str:
    """Copy the released mod into dest; returns the folder that holds DLC_MOD_ArchipelagoME1."""
    if os.path.isdir(source):
        root = source
    else:
        with zipfile.ZipFile(source) as z:
            z.extractall(dest)
        root = dest
    for folder, dirs, _ in os.walk(root):
        if MOD_FOLDER in dirs and os.path.isfile(os.path.join(folder, MOD_FOLDER, "AutoLoad.ini")):
            return folder
    raise SystemExit(f"no {MOD_FOLDER}/AutoLoad.ini in {source}")


def patch_transition_map(src: str, dest: str, export_name: str, dll: str) -> list[transitions.BoolWrite]:
    pkg = le1pkg.read(src, dll)
    export = pkg.find("BioStateEventMap", export_name)
    none = pkg.name_index("None")
    start = export.offset + 4  # net index
    if struct.unpack_from("<ii", pkg.image, start) != (none, 0):
        raise SystemExit(f"{export_name}: expected an empty property list")
    changed = transitions.retarget_bool_sets(pkg.image, start + 8, IN_PARTY_BOOLS, SQUAD_JOIN_SUPPRESSED)
    le1pkg.write(pkg, dest, dll)
    check = le1pkg.read(dest, dll)  # round trip: the written package decompresses to the patched image
    if check.image != pkg.image:
        raise SystemExit(f"{dest}: written package does not match the patched image")
    return changed


CONSEQUENCES = ("BIOC_Materials.pcc", "PlotManagerAuto.ConsequenceMap")  # in the mod (patch_transition_map writes it)
# Outcomes of Bring Down the Sky's OutcomeMap with no consequence in any map: 6015 Finished_Plot (mission end),
# 6086 Found_a_body (the bodies and the radio log), 6153 Found_All.
BDTS_OUTCOMES = (6015, 6086, 6153)


def with_empty_consequences(data: bytes, start: int) -> bytes:
    """The ConsequenceMap (`data` from `start` to its end) with every missing BDTS_OUTCOMES entry added, empty."""
    for outcome in BDTS_OUTCOMES:
        if outcome not in transitions.transition_ids(data, start):
            data = transitions.add_transition(data, start, outcome, [])
    return data


def add_empty_consequences(path: str, dll: str) -> list[int]:
    """Add BDTS_OUTCOMES to the ConsequenceMap of the mod's `path`; returns the outcomes added."""
    pkg = le1pkg.read(path, dll)
    export = pkg.find("BioStateEventMap", CONSEQUENCES[1])
    data = bytes(pkg.image[export.offset:export.offset + export.size])
    start = 4 + 8  # net index, empty property list
    before = transitions.transition_ids(data, start)
    patched = with_empty_consequences(data, start)
    if patched != data:
        # Appended, not relaid out: the package holds textures whose bulk data offsets must not move.
        le1pkg.write_image(pkg, le1pkg.append_exports(pkg, {export.index: patched}), path, dll)
        check = le1pkg.read(path, dll)
        written = check.find("BioStateEventMap", CONSEQUENCES[1])
        if bytes(check.image[written.offset:written.offset + written.size]) != patched:
            raise SystemExit(f"{path}: {CONSEQUENCES[1]} does not read back")
    return [o for o in BDTS_OUTCOMES if o not in before]


def override_rows(path: str, export_name: str, rows: dict[int, dict[str, object]], dll: str) -> None:
    """Write `rows` into the mod's Bio2DANumberedRows export `export_name` (rows already there with other numbers
    stay), adding the names they need, and check that the table reads back."""
    pkg = le1pkg.read(path, dll)
    export = pkg.find("Bio2DANumberedRows", export_name)
    columns, table = twoda.read(pkg, export)
    table.update(rows)
    names = ["m_lstRowNumbers", "ArrayProperty", "None"]
    names += [twoda.split_name(row[c])[0] for row in rows.values() for c in row if isinstance(row[c], str)]
    added = le1pkg.add_names(pkg, list(dict.fromkeys(names)))
    pkg.names.extend(added)
    net_index = struct.unpack_from("<i", pkg.image, export.offset)[0]
    data = twoda.build(pkg, net_index, columns, table)
    del pkg.names[len(pkg.names) - len(added):]
    le1pkg.write_image(pkg, le1pkg.relayout(pkg, added, {export.index: data}), path, dll)
    check = le1pkg.read(path, dll)
    if twoda.read(check, check.find("Bio2DANumberedRows", export_name))[1] != table:
        raise SystemExit(f"{path}: {export_name} does not read back")


SHOP_PACKAGE = "BIOG_2DA_APME1_Treasure_X.pcc"
VANILLA_TREASURE_TABLES = "BIOG_2DA_TreasureTables_X.Treasure_Tables_TreasureTables"  # in Engine.pcc
VANILLA_TREASURE_ENTRIES = "BIOG_2DA_TreasureTables_X.Treasure_Tables_TreasureEntries"


def patch_shop_draws(path: str, engine: str, dll: str) -> dict[int, dict[str, object]]:
    """Override rows for every treasure table the mod's shop entries are in: its vanilla row with drops set to all its
    entries (vanilla and the mod's). Returns the rows written."""
    pkg = le1pkg.read(path, dll)
    _, entries = twoda.read(pkg, pkg.find("Bio2DANumberedRows", "Treasure_Tables_TreasureEntries_part_1"))
    slots: dict[int, int] = {}
    for entry in entries.values():
        slots[entry["tid"]] = slots.get(entry["tid"], 0) + 1
    vanilla_pkg = le1pkg.read(engine, dll)
    _, vanilla = twoda.read(vanilla_pkg, vanilla_pkg.find("Bio2DANumberedRows", VANILLA_TREASURE_TABLES))
    _, vanilla_entries = twoda.read(vanilla_pkg, vanilla_pkg.find("Bio2DANumberedRows", VANILLA_TREASURE_ENTRIES))
    entry_counts: dict[int, int] = {}
    for entry in vanilla_entries.values():
        entry_counts[entry["tid"]] = entry_counts.get(entry["tid"], 0) + 1
    rows = {tid: {**vanilla[tid], "drops": entry_counts.get(tid, 0) + count} for tid, count in sorted(slots.items())}
    override_rows(path, "Treasure_Tables_TreasureTables_part_1", rows, dll)
    return rows


PLANET_PACKAGE = "BIOG_2DA_APME1_GalaxyMap_X.pcc"
ENGINE_PACKAGE = "Engine.pcc"  # holds the game's galaxy-map tables; the mod ships a patched copy (BASEGAME)
BASEGAME_COOKED = os.path.join(REPO, "mod", "BASEGAME", "CookedPCConsole")
VANILLA_PLANETS = "BIOG_2DA_GalaxyMap_X.GalaxyMap_Planet"  # in Engine.pcc
VANILLA_MAPS = "BIOG_2DA_GalaxyMap_X.GalaxyMap_Map"
TWO_SQUADMATES = 20194  # set by the client while two squadmates are in the party
MAKO = 20193  # set by the client while the M35 Mako is received (always, when the Mako is not shuffled)
# GalaxyMap_Planet row -> plot bool its landing needs (UsablePlanet); the world's logic has the same planets (uncharted.py)
PLANET_GATES = {89: TWO_SQUADMATES}  # Agebinium
# Levels whose landing puts the Mako in reach: Noveria, Feros, Virmire, Therum, Asteroid X57 and every uncharted planet.
# Their planets are usable only while MAKO is set (Usable; UsablePlanet keeps the vanilla or coordinate gate).
MAKO_LEVELS = frozenset({"BIOA_ICE00", "BIOA_WAR00", "BIOA_JUG00", "BIOA_LAV00", "BIOA_PRC2"})
MAKO_LEVEL_PREFIX = "BIOA_UNC"
GATE_BOOLS = {MAKO, TWO_SQUADMATES}


def planet_gate_rows(planets: dict[int, dict[str, object]], maps: dict[int, dict[str, object]]
                     ) -> dict[int, dict[str, object]]:
    """The gated GalaxyMap_Planet rows: each planet in PLANET_GATES or on a Mako level, from its vanilla row, with its
    gates. A Conditional of 0 makes its Function a plot bool and its Parameter the value it must have (the mod's Eden
    Prime row: bool 20000, 1)."""
    mako_maps = {number for number, row in maps.items()
                 if row.get("Map") in MAKO_LEVELS or str(row.get("Map", "")).startswith(MAKO_LEVEL_PREFIX)}
    rows: dict[int, dict[str, object]] = {}
    for number, planet in planets.items():
        row = dict(planet)
        if number in PLANET_GATES:
            row.update(UsablePlanetConditional=0, UsablePlanetFunction=PLANET_GATES[number], UsablePlanetParameter=1)
        if planet.get("Map") in mako_maps:
            row.update(UsableConditional=0, UsableFunction=MAKO, UsableParameter=1)
        if number in PLANET_GATES or planet.get("Map") in mako_maps:
            rows[number] = row
    return rows


def stale_gates(planets: dict[int, dict[str, object]], gated: set[int]) -> list[int]:
    """Rows outside `gated` whose Usable or UsablePlanet is one of GATE_BOOLS: a copy of Engine.pcc patched by an
    older gate set, not the game's own file."""
    return sorted(n for n, row in planets.items() if n not in gated and any(
        row.get(f"{column}Conditional") == 0 and row.get(f"{column}Function") in GATE_BOOLS
        for column in ("Usable", "UsablePlanet")))


def patch_engine(cooked: str, dll: str) -> dict[int, dict[str, object]]:
    """Write BASEGAME_COOKED/Engine.pcc: the game's, with planet_gate_rows in its GalaxyMap_Planet (same-size cell
    changes). Galaxy-map rows can't be overridden from the mod's DLC table: a row with a vanilla row's number makes
    landing on that planet load the wrong level. Returns the gated rows."""
    pkg = le1pkg.read(os.path.join(cooked, ENGINE_PACKAGE), dll)
    export = pkg.find("Bio2DANumberedRows", VANILLA_PLANETS)
    columns, planets = twoda.read(pkg, export)
    _, maps = twoda.read(pkg, pkg.find("Bio2DANumberedRows", VANILLA_MAPS))
    rows = planet_gate_rows(planets, maps)
    stale = stale_gates(planets, set(rows))
    if stale:
        raise SystemExit(f"{cooked}: {ENGINE_PACKAGE} has gates on rows {stale}: restore the game's own file")
    net_index = struct.unpack_from("<i", pkg.image, export.offset)[0]
    data = twoda.build(pkg, net_index, columns, {**planets, **rows})
    if len(data) != export.size:
        raise SystemExit(f"{ENGINE_PACKAGE}: GalaxyMap_Planet changes size ({export.size} -> {len(data)})")
    pkg.image[export.offset:export.offset + export.size] = data
    os.makedirs(BASEGAME_COOKED, exist_ok=True)
    path = os.path.join(BASEGAME_COOKED, ENGINE_PACKAGE)
    le1pkg.write(pkg, path, dll)
    if le1pkg.read(path, dll).image != pkg.image:
        raise SystemExit(f"{path}: written package does not match the patched image")
    return rows


def drop_vanilla_planet_rows(path: str, engine: str, dll: str) -> list[int]:
    """Remove rows from the mod's GalaxyMap_Planet_part_1 that reuse a vanilla row number; returns their numbers."""
    vanilla_pkg = le1pkg.read(engine, dll)
    _, vanilla = twoda.read(vanilla_pkg, vanilla_pkg.find("Bio2DANumberedRows", VANILLA_PLANETS))
    pkg = le1pkg.read(path, dll)
    export = pkg.find("Bio2DANumberedRows", "GalaxyMap_Planet_part_1")
    columns, table = twoda.read(pkg, export)
    dropped = sorted(n for n in table if n in vanilla)
    if dropped:
        for n in dropped:
            del table[n]
        net_index = struct.unpack_from("<i", pkg.image, export.offset)[0]
        le1pkg.write_image(pkg, le1pkg.relayout(pkg, [], {export.index: twoda.build(pkg, net_index, columns, table)}),
                           path, dll)
        check = le1pkg.read(path, dll)
        if twoda.read(check, check.find("Bio2DANumberedRows", "GalaxyMap_Planet_part_1"))[1] != table:
            raise SystemExit(f"{path}: GalaxyMap_Planet_part_1 does not read back")
    return dropped


GARRUS_RECRUITED = 20101  # the mod's recruit transitions set these where vanilla set the in-party bool
WREX_RECRUITED = 20102
QUARIAN_SAVED = 3721  # Tali's recruitment (the Recruit Tali check)
GARRUS_IN_PARTY, WREX_IN_PARTY, TALI_IN_PARTY = 3941, 3942, 3944
INT_CONST = 0x1D  # UnrealScript IntConst token, followed by an int32
CONDITIONALS_PACKAGE = "PlotManager.pcc"
# Conditionals that read an in-party bool as "recruited in the story": they read the story flag instead, since the
# client sets the in-party bool when the squadmate's item arrives, which can be before the story recruits them.
CONDITIONAL_SWAPS = {
    1632: {GARRUS_IN_PARTY: GARRUS_RECRUITED, WREX_IN_PARTY: WREX_RECRUITED},  # Harkin_HasNothingToSay
    1368: {GARRUS_IN_PARTY: GARRUS_RECRUITED},  # Garrus_waiting_at_CSec
    790: {GARRUS_IN_PARTY: GARRUS_RECRUITED},  # Garrus_at_hangar_elevator
    1229: {GARRUS_IN_PARTY: GARRUS_RECRUITED},  # Can_ask_Executor_about_Garrus
    1742: {GARRUS_IN_PARTY: GARRUS_RECRUITED, TALI_IN_PARTY: QUARIAN_SAVED},  # Garrus_no_longer_needed
    1367: {WREX_IN_PARTY: WREX_RECRUITED},  # Wrex_waiting_in_Choras
    792: {WREX_IN_PARTY: WREX_RECRUITED},  # Wrex_at_hangar_elevator
}
NPC_IN_PARTY = 418  # NPCInParty(argument): 3 Garrus, 4 Wrex
# Citadel conversation nodes that check NPCInParty for the story's recruitment: package (each language) -> the
# conversation and its (node list, index, argument, story flag); the node checks the story flag instead.
CONVERSATION_PATCHES = {
    "BIOA_STA20_04A_DSG": ("sta20_banker_D.sta20_banker_dlg", [  # Barla Von: the Saren lead, unless Wrex told you
        ("m_EntryList", 18, 4, WREX_RECRUITED)]),
    "BIOA_STA30_01_DSG": ("sta30_krogan_cutscene_D.sta30_krogan_cutscene_dlg", [  # Wrex at C-Sec, Garrus along
        ("m_EntryList", 0, 3, GARRUS_RECRUITED)]),
    "BIOA_STA60_08A_DSG": ("sta60_garrus_doctor_D.sta60_garrus_doctor_dlg", [  # Dr. Michel, Wrex along
        ("m_EntryList", 12, 4, WREX_RECRUITED), ("m_EntryList", 30, 4, WREX_RECRUITED)]),
}
LANGUAGES = ("INT", "DE", "FR", "IT", "PLPC", "RA")


def swap_int_consts(code: bytes | bytearray, swaps: dict[int, int]) -> bytearray | None:
    """`code` with each IntConst old -> new of `swaps` (each old exactly once), None when already swapped."""
    out = bytearray(code)
    if all(out.count(bytes([INT_CONST]) + struct.pack("<i", new)) == 1 and
           out.count(bytes([INT_CONST]) + struct.pack("<i", old)) == 0 for old, new in swaps.items()):
        return None
    for old, new in swaps.items():
        token = bytes([INT_CONST]) + struct.pack("<i", old)
        if out.count(token) != 1:
            raise ValueError(f"IntConst {old} appears {out.count(token)} times")
        at = out.index(token)
        out[at + 1:at + 5] = struct.pack("<i", new)
    return out


def patch_conditionals(path: str, dll: str) -> list[int]:
    """Apply CONDITIONAL_SWAPS to the mod's PlotManager.pcc; returns the conditionals changed."""
    pkg = le1pkg.read(path, dll)
    changed = []
    for number, swaps in CONDITIONAL_SWAPS.items():
        export = pkg.find("Function", f"BioAutoConditionals.F{number}")
        try:
            code = swap_int_consts(pkg.image[export.offset:export.offset + export.size], swaps)
        except ValueError as error:
            raise SystemExit(f"{path}: F{number}: {error}")
        if code is not None:
            pkg.image[export.offset:export.offset + export.size] = code
            changed.append(number)
    if changed:
        le1pkg.write(pkg, path, dll)
        if le1pkg.read(path, dll).image != pkg.image:
            raise SystemExit(f"{path}: written package does not match the patched image")
    return changed


def patch_conversation(path: str, conversation: str, nodes: list[tuple[str, int, int, int]], dll: str) -> int:
    """Point each node's NPCInParty(argument) check at its story flag; returns how many nodes changed."""
    pkg = le1pkg.read(path, dll)
    export = pkg.find("BioConversation", conversation)
    data = pkg.image[export.offset:export.offset + export.size]
    changed = 0
    for list_name, index, argument, flag in nodes:
        cond = conversation_mod.node_condition(pkg.names, data, list_name, index)
        if (cond.func, cond.fire, cond.param) == (flag, False, 1):
            continue
        if (cond.func, cond.fire, cond.param) != (NPC_IN_PARTY, True, argument):
            raise SystemExit(f"{path}: {conversation} {list_name}[{index}] is not NPCInParty({argument})")
        conversation_mod.set_bool_condition(data, cond, flag, True)
        changed += 1
    if changed:
        pkg.image[export.offset:export.offset + export.size] = data
        le1pkg.write(pkg, path, dll)
        if le1pkg.read(path, dll).image != pkg.image:
            raise SystemExit(f"{path}: written package does not match the patched image")
    return changed


def patch_conversations(cooked: str, mod_cooked: str, dll: str) -> dict[str, int]:
    """CONVERSATION_PATCHES in every language; a package the mod does not ship yet is copied from the game."""
    changed = {}
    for base, (conversation, nodes) in CONVERSATION_PATCHES.items():
        for language in LANGUAGES:
            name = f"{base}_LOC_{language}.pcc"
            path = os.path.join(mod_cooked, name)
            if not os.path.isfile(path):
                shutil.copyfile(os.path.join(cooked, name), path)
            changed[name] = patch_conversation(path, conversation, nodes, dll)
    return changed


ASHLEY_IN_PARTY = 3940
ASHLEY_RECRUITED = 3818  # set with the in-party bool by Ashley's join conversation (transition 1994)
# Kismet plot checks that read an in-party bool as "recruited in the story": package -> (sequence object, old bool,
# new bool). The object checks the story flag instead.
KISMET_CHECKS = {
    "BIOA_PRO10_05_DSG.pcc": [
        # Eden Prime trench: two trigger volumes start Ashley's join conversation while she is not in the party
        ("TheWorld.PersistentLevel.Main_Sequence.Plots.TrenchEntrance.BioSeqAct_PMCheckState_2",
         ASHLEY_IN_PARTY, ASHLEY_RECRUITED),
        ("TheWorld.PersistentLevel.Main_Sequence.Plots.TrenchEntrance.BioSeqAct_PMCheckState_6",
         ASHLEY_IN_PARTY, ASHLEY_RECRUITED),
        # level load: the scene after Ashley's fight
        ("TheWorld.PersistentLevel.Main_Sequence.LevelStartup.AshleyGeth.BioSeqAct_PMCheckState_0",
         ASHLEY_IN_PARTY, ASHLEY_RECRUITED),
    ],
}


def patch_kismet_checks(cooked: str, mod_cooked: str, dll: str) -> dict[str, int]:
    """KISMET_CHECKS; a package the mod does not ship yet is copied from the game."""
    changed = {}
    for name, checks in KISMET_CHECKS.items():
        path = os.path.join(mod_cooked, name)
        if not os.path.isfile(path):
            shutil.copyfile(os.path.join(cooked, name), path)
        pkg = le1pkg.read(path, dll)
        count = 0
        for full_name, old, new in checks:
            export = pkg.find("BioSeqAct_PMCheckState", full_name)
            data = pkg.image[export.offset:export.offset + export.size]
            offset = conversation_mod.property_offset(pkg.names, data, "m_nIndex")
            current = struct.unpack_from("<i", data, offset)[0]
            if current == new:
                continue
            if current != old:
                raise SystemExit(f"{path}: {full_name} checks {current}, not {old}")
            struct.pack_into("<i", pkg.image, export.offset + offset, new)
            count += 1
        if count:
            le1pkg.write(pkg, path, dll)
            if le1pkg.read(path, dll).image != pkg.image:
                raise SystemExit(f"{path}: written package does not match the patched image")
        changed[name] = count
    return changed


MOD_TRANSITIONS_PACKAGE = "PlotManagerAutoAPME1.pcc"
VANILLA_TRANSITIONS = ("BIOC_Materials.pcc", "PlotManagerAuto.StateTransitionMap")  # the game's map
THE_LOOK = 20103  # transition and plot bool: the Citadel: The Look check
VIRMIRE_TRANSMISSION = 20104  # the client runs it once the Virmire coordinates arrive
# Mod transitions that set a plot bool of the same number: transition -> bool.
BOOL_TRANSITIONS = {THE_LOOK: THE_LOOK}
# Mod transitions that run the elements of game transitions, in order: transition -> game transitions.
COPIED_TRANSITIONS = {
    # The Council's Virmire transmission (nor20_virmire_open_dlg): Virmire_Open, the Virmire quest and its journal
    # entry (Begin_Plot), Salarians_STG, Joker_gave_Virmire_message. The game plays it after enough missions are
    # done, which an Archipelago seed doesn't line up with the Virmire coordinates.
    VIRMIRE_TRANSMISSION: (2302, 3552, 4885, 3667),
}


def mod_transition_elements(cooked: str, dll: str) -> dict[int, list[bytes]]:
    """Each mod transition's elements: BOOL_TRANSITIONS, and COPIED_TRANSITIONS from the game's transition map."""
    package, export_name = VANILLA_TRANSITIONS
    pkg = le1pkg.read(os.path.join(cooked, package), dll)
    export = pkg.find("BioStateEventMap", export_name)
    game = transitions.transitions_elements(pkg.image[export.offset:export.offset + export.size], 4 + 8)
    wanted = {t: [transitions.bool_element(b)] for t, b in BOOL_TRANSITIONS.items()}
    for transition, sources in COPIED_TRANSITIONS.items():
        wanted[transition] = [element for source in sources for element in game[source]]
    return wanted


def add_mod_transitions(path: str, wanted: dict[int, list[bytes]], dll: str) -> list[int]:
    """Add the `wanted` transitions missing from the mod's StateTransitionMap; returns the transitions added."""
    pkg = le1pkg.read(path, dll)
    export = pkg.find("BioStateEventMap", "StateTransitionMap")
    data = bytes(pkg.image[export.offset:export.offset + export.size])
    start = 4 + 8  # net index, empty property list
    present = transitions.transitions_elements(data, start)
    for transition, elements in wanted.items():
        if transition in present and present[transition] != elements:
            raise SystemExit(f"{path}: transition {transition} runs other elements than this build adds")
    added = [t for t in wanted if t not in present]
    for transition in added:
        data = transitions.add_transition(data, start, transition, wanted[transition])
    if added:
        le1pkg.write_image(pkg, le1pkg.relayout(pkg, [], {export.index: data}), path, dll)
        check = le1pkg.read(path, dll)
        written = check.find("BioStateEventMap", "StateTransitionMap")
        if bytes(check.image[written.offset:written.offset + written.size]) != data:
            raise SystemExit(f"{path}: StateTransitionMap does not read back")
    return added


# Conversation nodes that run a mod transition: package (each language) -> the conversation and its (node list,
# index, transition). Each node runs no transition in the game.
TRANSITION_NODES = {
    # The Council makes Shepard a Spectre: the silent beat after "Commander Shepard - step forward", which every path
    # reaches right after the asari Councilor's look.
    "BIOA_STA70_02council_DSG": ("sta90_council_spectre_induction_D.sta90_council_spectre_induction_dlg", [
        ("m_EntryList", 23, THE_LOOK)]),
}


def patch_transition_nodes(cooked: str, mod_cooked: str, dll: str) -> dict[str, int]:
    """TRANSITION_NODES in every language; a package the mod does not ship yet is copied from the game."""
    changed = {}
    for base, (conversation, nodes) in TRANSITION_NODES.items():
        for language in LANGUAGES:
            name = f"{base}_LOC_{language}.pcc"
            path = os.path.join(mod_cooked, name)
            if not os.path.isfile(path):
                shutil.copyfile(os.path.join(cooked, name), path)
            pkg = le1pkg.read(path, dll)
            export = pkg.find("BioConversation", conversation)
            data = pkg.image[export.offset:export.offset + export.size]
            count = 0
            for list_name, index, transition in nodes:
                current, offset = conversation_mod.node_transition(pkg.names, data, list_name, index)
                if current == transition:
                    continue
                if current != -1:
                    raise SystemExit(f"{path}: {conversation} {list_name}[{index}] runs transition {current}")
                struct.pack_into("<i", data, offset, transition)
                count += 1
            if count:
                pkg.image[export.offset:export.offset + export.size] = data
                le1pkg.write(pkg, path, dll)
                if le1pkg.read(path, dll).image != pkg.image:
                    raise SystemExit(f"{path}: written package does not match the patched image")
            changed[name] = count
    return changed


def main(game: str, base: str | None = None) -> None:
    cooked = os.path.join(game, "BioGame", "CookedPCConsole")
    dll = os.path.join(game, "Binaries", "Win64", "oo2core_8_win64.dll")
    for path in (cooked, dll):
        if not os.path.exists(path):
            raise SystemExit(f"not found: {path} (pass the LE1 game folder, ...\\Game\\ME1)")
    if base is not None:
        tmp = tempfile.mkdtemp(prefix="le1mod")
        try:
            root = extract_mod(base, tmp)
            shutil.rmtree(MOD_DIR, ignore_errors=True)
            shutil.copytree(os.path.join(root, MOD_FOLDER), MOD_DIR)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.isfile(os.path.join(MOD_DIR, "AutoLoad.ini")):
        raise SystemExit(f"no {MOD_FOLDER} in {os.path.dirname(MOD_DIR)}; pass --base <released mod>")
    mod_cooked = os.path.join(MOD_DIR, "CookedPCConsole")
    for package, (export_name, origin) in TRANSITION_MAPS.items():
        src = os.path.join(cooked if origin == "game" else mod_cooked, package)
        changed = patch_transition_map(src, os.path.join(mod_cooked, package), export_name, dll)
        ids = sorted({w.transition for w in changed})
        print(f"{package}: {len(changed)} in-party writes retargeted (transitions {ids})")
    added = add_empty_consequences(os.path.join(mod_cooked, CONSEQUENCES[0]), dll)
    print(f"{CONSEQUENCES[0]}: empty consequences added: {added or 'none (already there)'}")
    rows = patch_shop_draws(os.path.join(mod_cooked, SHOP_PACKAGE), os.path.join(cooked, "Engine.pcc"), dll)
    print(f"{SHOP_PACKAGE}: shop draws " + ", ".join(f"{r['Name']} {r['drops']}" for r in rows.values()))
    dropped = drop_vanilla_planet_rows(os.path.join(mod_cooked, PLANET_PACKAGE), os.path.join(cooked, ENGINE_PACKAGE), dll)
    print(f"{PLANET_PACKAGE}: vanilla planet rows removed: {dropped or 'none'}")
    rows = patch_engine(cooked, dll)
    print(f"BASEGAME {ENGINE_PACKAGE}: landing gates on rows " + ", ".join(
        f"{number} (map {r['Map']}: usable {r['UsableFunction']}, land {r['UsablePlanetFunction']})"
        for number, r in rows.items()))
    changed = patch_conditionals(os.path.join(mod_cooked, CONDITIONALS_PACKAGE), dll)
    print(f"{CONDITIONALS_PACKAGE}: story recruitment conditionals changed: {changed or 'none (already patched)'}")
    for name, count in patch_conversations(cooked, mod_cooked, dll).items():
        print(f"{name}: {count} conversation nodes changed")
    for name, count in patch_kismet_checks(cooked, mod_cooked, dll).items():
        print(f"{name}: {count} plot checks changed")
    added = add_mod_transitions(os.path.join(mod_cooked, MOD_TRANSITIONS_PACKAGE), mod_transition_elements(cooked, dll), dll)
    print(f"{MOD_TRANSITIONS_PACKAGE}: transitions added: {added or 'none (already there)'}")
    for name, count in patch_transition_nodes(cooked, mod_cooked, dll).items():
        print(f"{name}: {count} conversation nodes now run a mod transition")
    print(f"mod: {MOD_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game")
    parser.add_argument("--base")
    args = parser.parse_args()
    main(args.game, args.base)
