"""Static tables for the Mass Effect (LE1) world.

IDs are an append-only ledger (DECISIONS.md P5): never renumber or reuse an ID, only add new ones.

`ot_plot` fields are hints taken from the 2007 original-trilogy plot database
(Trilogy Save Editor `me1_raw_plot_db.ron` / `me1_plot_db.ron`). Every index used here also resolves, with a
matching name, in Legendary Explorer's LE1 plot DB (games/masseffect1/CHECKS-CATALOG.md). They are not used by
generation; the client evaluates them, and in-game tests confirm each check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from BaseClasses import ItemClassification

from .codex import CODEX_ENTRIES
from .containers import CONTAINERS
from .gear import GEAR_LINES, GearLine
from .journal import JOURNAL_ENTRIES

ID_BASE = 0x4D45_0000  # "ME"
# ID layout inside ID_BASE (offsets): items 0x0000-0x0FFF (0x100-0x3FF gear lines, gear.py order), locations from
# 0x1000: 0x1000-0x10FF story, 0x1100-0x13FF codex, 0x1400-0x17FF containers and Aid Stations, 0x1A00-0x1FFF shop
# slots (8 per merchant), 0x2000-0x2FFF container drop slots (4 per container), 0x3000-0x33FF journal (journal.py order).
GAME_NAME = "Mass Effect Legendary Edition 1"  # placeholder, DECISIONS.md P4


class Region(str, Enum):
    NORMANDY = "Normandy"
    CITADEL = "Citadel"
    EDEN_PRIME = "Eden Prime"
    THERUM = "Therum"
    FEROS = "Feros"
    NOVERIA = "Noveria"
    VIRMIRE = "Virmire"
    ASTEROID_X57 = "Asteroid X57"
    ILOS = "Ilos"
    FINAL_CITADEL = "Citadel Finale"
    UNCHARTED = "Uncharted Worlds"  # uncharted planets and derelict freighters: after Act 1, with the Mako


# Landing on Ilos starts the finale with no way back: these regions never hold this world's progression items
# (regions.no_return_item_allowed). Other games' items are fine there.
NO_RETURN_REGIONS = (Region.ILOS, Region.FINAL_CITADEL)


@dataclass(frozen=True)
class ItemData:
    offset: int
    classification: ItemClassification
    count: int = 1  # copies in the pool; fragments are counted by option instead
    groups: frozenset[str] = field(default_factory=frozenset)


SQUAD = ("Kaidan Alenko", "Ashley Williams", "Garrus Vakarian", "Urdnot Wrex", "Tali'Zorah nar Rayya", "Liara T'Soni")
COORDINATES = {
    Region.EDEN_PRIME: "Eden Prime Coordinates",
    Region.THERUM: "Therum Coordinates",
    Region.FEROS: "Feros Coordinates",
    Region.NOVERIA: "Noveria Coordinates",
    Region.VIRMIRE: "Virmire Coordinates",
    Region.ASTEROID_X57: "Asteroid X57 Coordinates",
}
FRAGMENT = "Prothean Beacon Fragment"
MAKO = "M35 Mako"
FILLER = ("Credits", "Omni-Gel", "Medi-Gel", "Grenades")
FILLER_WEIGHTS = (70, 10, 10, 10)  # Credits scale with the squad level; the consumables cap out quickly
UPGRADE = "Random Upgrade"
EXPERIENCE = "Experience"  # squad XP, granted through LE1APExtras (tools/le1apextras)
GEAR_OFFSET = 0x100

_P = ItemClassification.progression
ITEMS: dict[str, ItemData] = {
    # 0x00-0x0F squad
    **{name: ItemData(i, _P, groups=frozenset({"Squad"})) for i, name in enumerate(SQUAD)},
    # 0x10-0x1F galaxy-map coordinates
    "Eden Prime Coordinates": ItemData(0x10, _P, groups=frozenset({"Coordinates"})),
    "Therum Coordinates": ItemData(0x11, _P, groups=frozenset({"Coordinates"})),
    "Feros Coordinates": ItemData(0x12, _P, groups=frozenset({"Coordinates"})),
    "Noveria Coordinates": ItemData(0x13, _P, groups=frozenset({"Coordinates"})),
    "Virmire Coordinates": ItemData(0x14, _P, groups=frozenset({"Coordinates"})),
    "Asteroid X57 Coordinates": ItemData(0x15, _P, groups=frozenset({"Coordinates"})),
    # 0x20-0x2F goal and gear
    FRAGMENT: ItemData(0x20, ItemClassification.progression_skip_balancing, count=0),
    MAKO: ItemData(0x21, _P, count=0),
    # 0x40-0x4F filler and upgrades (granted through the bridge's RES / GIVE, verified in game 2026-10-10)
    "Credits": ItemData(0x40, ItemClassification.filler, count=0),
    "Omni-Gel": ItemData(0x41, ItemClassification.filler, count=0),
    "Medi-Gel": ItemData(0x42, ItemClassification.filler, count=0),
    "Grenades": ItemData(0x43, ItemClassification.filler, count=0),
    UPGRADE: ItemData(0x44, ItemClassification.useful, count=0, groups=frozenset({"Gear"})),
    EXPERIENCE: ItemData(0x45, ItemClassification.useful, count=0),
    # 0x100-0x3FF gear lines: how many is set by useful_share and gear_weight
    **{line.name: ItemData(GEAR_OFFSET + i, ItemClassification.useful, count=0, groups=frozenset({"Gear", line.category}))
       for i, line in enumerate(GEAR_LINES)},
}
GEAR_BY_NAME: dict[str, GearLine] = {line.name: line for line in GEAR_LINES}

ITEM_NAME_TO_ID = {name: ID_BASE + data.offset for name, data in ITEMS.items()}
ITEM_NAME_GROUPS = {
    group: {name for name, data in ITEMS.items() if group in data.groups}
    for group in sorted({g for data in ITEMS.values() for g in data.groups})
}


@dataclass(frozen=True)
class LocationData:
    offset: int
    region: Region
    ot_plot: str | None = None  # hint only, see module docstring
    needs_act1: bool = False  # Citadel side content: logic waits for Citadel Act 1 (conservative, R15)
    squad: str | None = None  # a squadmate's conversation gives it (logic needs that squadmate)
    codex: str | None = None  # codexsanity tier ("story" or "full") for codex pages; None for story checks
    container: str | None = None  # "container", "drop" (extra treasure roll) or "station" (Aid Station)
    locked: bool = False  # Decryption container or station: a check only with unlock_containers
    journal: str | None = None  # journalsanity tier ("story" or "full") for journal checks
    excluded: bool = False  # a story choice can skip it: filler only (LocationProgressType.EXCLUDED)
    shop_slot: int | None = None  # shop slot index at its merchant (0-based): a check only when below shop_slots
    needs_event: str | None = None  # an event the logic requires (beyond needs_act1)


class ShopEntry(NamedTuple):
    name: str
    region: str  # Region member name
    needs_act1: bool = False
    needs_event: str | None = None
    missable: bool = False  # only open for a short while: filler only


# Shop slots (D19): merchant order, slot count and bools are shared with tools/le1mod/Shops.cs (append only). Slot s of
# merchant m: bool 20700 + 5m + s, set by the slot's plot-item transition when bought; location 0x1A00 + 8m + s.
SHOP_SLOTS_MAX = 5
SHOP_BOOL_BASE = 20700
SHOPS: tuple[ShopEntry, ...] = (
    ShopEntry("Normandy: Requisitions Officer", "NORMANDY"),
    # On the Normandy from Virmire's end (F1411: 2509) until the next docking anywhere (transition 5025 -> 6383).
    ShopEntry("Normandy: Commander Rentola", "NORMANDY", needs_event="Virmire Complete", missable=True),
    ShopEntry("Citadel: C-Sec Requisitions", "CITADEL", needs_act1=True),
    ShopEntry("Citadel: Presidium Store", "CITADEL", needs_act1=True),
    ShopEntry("Citadel: Morlan", "CITADEL", needs_act1=True),
    ShopEntry("Citadel: Expat", "CITADEL", needs_act1=True),
    ShopEntry("Citadel: Dr. Michel", "CITADEL", needs_act1=True),
    ShopEntry("Noveria: Opold and Petozi", "NOVERIA"),  # two shops, one stock table
    ShopEntry("Feros: Zhu's Hope Merchant", "FEROS"),
    ShopEntry("Virmire: Salarian Camp", "VIRMIRE"),
)


def shop_bool(merchant: int, slot: int) -> int:
    return SHOP_BOOL_BASE + SHOP_SLOTS_MAX * merchant + slot


_L = 0x1000  # location IDs live above item IDs inside the same base
LOCATIONS: dict[str, LocationData] = {
    # Citadel Act 1 (D3): recruitments are locations, squadmates are items. Recruitment checks must NOT use the
    # InParty bools (3939-3944): item delivery sets those, which would fake the check (DECISIONS P6/R10). Target: the
    # mod sets a dedicated AP check flag (P8) at each recruitment moment. Interim (test run v0, DECISIONS P6a): the
    # vanilla recruitment-moment bools below, named in the LE1 plot DB.
    # Garrus/Wrex: P8 flags set by the mod's recruit transitions 20101/20102 (le1mod recruit), not InParty.
    "Citadel: Recruit Garrus": LocationData(_L + 0x00, Region.CITADEL, "bool 20101 AP Garrus recruited (mod transition 20101 in place of 2122)"),
    "Citadel: Recruit Wrex": LocationData(_L + 0x01, Region.CITADEL, "bool 20102 AP Wrex recruited (mod transition 20102 in place of 2123)"),
    "Citadel: Recruit Tali": LocationData(_L + 0x02, Region.CITADEL, "bool 3721 Quarian_saved"),
    "Citadel: Expose Saren": LocationData(_L + 0x03, Region.CITADEL, "bool 5212 Citadel_Critical_Path.Plot_Complete"),
    "Citadel: The Look": LocationData(_L + 0x04, Region.CITADEL, "bool 20103 AP The Look (mod transition 20103: the Spectre induction, after the asari Councilor's look)"),
    # Eden Prime (D2)
    "Eden Prime: Ashley Joins": LocationData(_L + 0x20, Region.EDEN_PRIME, "bool 5508 First_Time_Talked_to_Flags.Ashley"),
    "Eden Prime: Beacon Vision": LocationData(_L + 0x21, Region.EDEN_PRIME, "bool 3569 BeaconNoticed"),
    # Therum
    "Therum: Rescue Liara": LocationData(_L + 0x30, Region.THERUM, "bool 2506 Caleston_Done"),
    # Feros
    "Feros: Confront Shiala": LocationData(_L + 0x41, Region.FEROS, "bool 3798 or 4133"),
    "Feros: Defeat the Thorian": LocationData(_L + 0x42, Region.FEROS, "bool 2507 Feros_Done"),
    # Noveria
    "Noveria: Hand Over the Evidence": LocationData(_L + 0x50, Region.NOVERIA, "bool 2687 or 2690"),
    "Noveria: Rachni Queen's Fate": LocationData(_L + 0x51, Region.NOVERIA, "bool 2587 or 2588"),
    "Noveria: Confront Benezia": LocationData(_L + 0x52, Region.NOVERIA, "bool 2508 Noveria_Done"),
    # Virmire
    "Virmire: The Survivor": LocationData(_L + 0x61, Region.VIRMIRE, "bool 3827 or 3828"),
    "Virmire: Destroy the Facility": LocationData(_L + 0x62, Region.VIRMIRE, "bool 2509 Virmire_Done"),
    # Bring Down the Sky (D6)
    "Asteroid X57: Bring Down the Sky": LocationData(_L + 0x70, Region.ASTEROID_X57, "bool 7291"),
    # Ilos
    "Ilos: Reach the Conduit": LocationData(_L + 0x80, Region.ILOS, "bool 2510 Ilos_Done"),
    # Codex pages (codexsanity), 0x1100+ in codex.py order. The page ID is its plot bool. Citadel pages wait for Act 1.
    **{entry.name: LocationData(_L + 0x100 + i, Region[entry.region], f"bool {entry.plot_bool}",
                                needs_act1=entry.region == "CITADEL", squad=entry.squad, codex=entry.tier)
       for i, entry in enumerate(CODEX_ENTRIES)},
    # Containers and Aid Stations, 0x1400+ in containers.py order; the bridge sets each one's mod bool when used.
    **{entry.name: LocationData(_L + 0x400 + i, Region[entry.region], f"bool {entry.plot_bool}",
                                container=entry.kind, locked=entry.locked)
       for i, entry in enumerate(CONTAINERS)},
    # Shop slots (shop_slots), 0x1A00 + 8 * merchant + slot.
    **{f"{shop.name} Slot {slot + 1}": LocationData(_L + 0xA00 + 8 * m + slot, Region[shop.region],
                                                    f"bool {shop_bool(m, slot)}", needs_act1=shop.needs_act1,
                                                    excluded=shop.missable, shop_slot=slot, needs_event=shop.needs_event)
       for m, shop in enumerate(SHOPS) for slot in range(SHOP_SLOTS_MAX)},
    # Drop slots (containersanity with_drops), 0x2000 + 4 * container index + roll: same bool as their container.
    **{f"{entry.name} (Item {roll})": LocationData(_L + 0x1000 + 4 * i + roll, Region[entry.region],
                                                   f"bool {entry.plot_bool}", container="drop", locked=entry.locked)
       for i, entry in enumerate(CONTAINERS) for roll in range(2, entry.drops + 1)},
    # Journal (journalsanity), 0x3000+: a quest's first entry ("Started") and its completion ("Completed", excluded).
    **{entry.name: LocationData(_L + 0x2000 + i, Region[entry.region], "bool " + " or ".join(map(str, entry.plot_bools)),
                                needs_act1=entry.region == "CITADEL", squad=entry.squad, journal=entry.tier,
                                excluded=entry.excluded)
       for i, entry in enumerate(JOURNAL_ENTRIES)},
}

LOCATION_NAME_TO_ID = {name: ID_BASE + data.offset for name, data in LOCATIONS.items()}
LOCATION_NAME_GROUPS = {
    region.value: {name for name, data in LOCATIONS.items() if data.region is region} for region in Region
}
LOCATION_NAME_GROUPS = {group: names for group, names in LOCATION_NAME_GROUPS.items() if names}
LOCATION_NAME_GROUPS["Codex"] = {name for name, data in LOCATIONS.items() if data.codex}
LOCATION_NAME_GROUPS["Journal"] = {name for name, data in LOCATIONS.items() if data.journal}
LOCATION_NAME_GROUPS["Containers"] = {name for name, data in LOCATIONS.items() if data.container in ("container", "drop")}
LOCATION_NAME_GROUPS["Aid Stations"] = {name for name, data in LOCATIONS.items() if data.container == "station"}
LOCATION_NAME_GROUPS["Shops"] = {name for name, data in LOCATIONS.items() if data.shop_slot is not None}

# Event locations: not in the ID tables, they hold locked event items.
EVENT_ACT1 = "Citadel Act 1 Complete"  # Spectre status itself is set at game start (D8)
EVENT_THERUM = "Therum Complete"  # bool 2506 Caleston_Done: squadmate conversations count it (squad_talk.py)
EVENT_FEROS = "Feros Complete"  # bool 2507 Feros_Done: opens Nodacrux, Chasca, MSV Cornucopia (uncharted.py)
EVENT_NOVERIA = "Noveria Complete"  # bool 2508 Noveria_Done: opens Altahe, Nepmos (uncharted.py)
EVENT_VIRMIRE = "Virmire Complete"  # Ilos requires it (D9); bool 2509 Virmire_Done (sent in game, SCOPE P2-5)
HUMAN_SQUAD = ("Kaidan Alenko", "Ashley Williams")  # events, not items, with human_squad_at_citadel
EVENT_VICTORY = "Victory"  # defeat Saren and Sovereign: bool 6530 Game_Complete, set in the final speech before the credits
