from __future__ import annotations

from typing import TYPE_CHECKING

from rule_builder.rules import Has, HasFromList

from . import data, squad_talk, uncharted
from .regions import GROUNDED_QUESTS, HAS_MAKO, location_included

if TYPE_CHECKING:
    from .world import MassEffectLE1World

HAS_ACT1 = Has(data.EVENT_ACT1)
# Scenes that only happen with that squadmate along (generated tables do not carry it).
NEEDS_SQUADMATE = {
    "Virmire: Wrex and the Genophage: Started": "Urdnot Wrex",  # bools 3015/3021/5472: Kirrahe's camp, Wrex's standoff
}
# Quests that only work with two squadmates (playtest 2026-11-03); planets: uncharted.TWO_SQUADMATE_MAPS.
TWO_SQUADMATES = HasFromList(*data.SQUAD, count=2)
NEEDS_TWO_SQUADMATES = frozenset({
    "Citadel: Jahleed's Fears: Started", "Citadel: Jahleed's Fears: Completed",
    "Citadel: Rita's Sister: Started", "Citadel: Rita's Sister: Completed",
})
# Quests whose setting line is only spoken by a squadmate in the party (one line variant per squadmate).
NEEDS_ANY_SQUADMATE = {
    "UNC: Derelict Freighter: Started": HasFromList(*data.SQUAD),  # bool 4843, BIOA_FRE31 sp116_triggers_dlg
    "UNC: Derelict Freighter: Completed": HasFromList(*data.SQUAD),
    # bool 6255, BIOA_UNC24 sp106_trig01_supplies_dlg: no line for Tali
    "UNC: Listening Post Theta: Started": HasFromList(*(s for s in data.SQUAD if s != "Tali'Zorah nar Rayya")),
    "UNC: Listening Post Theta: Completed": HasFromList(*(s for s in data.SQUAD if s != "Tali'Zorah nar Rayya")),
}
# The grounding (regions.GROUNDED_QUESTS) needs every main mission before Ilos done.
GROUNDED = Has(data.EVENT_THERUM) & Has(data.EVENT_FEROS) & Has(data.EVENT_NOVERIA) & Has(data.EVENT_VIRMIRE)
UNCHARTED_LANDINGS = HAS_ACT1 & HAS_MAKO  # the Uncharted Worlds entrance (regions.ENTRANCES)
MISSIONS_DONE = (data.EVENT_THERUM, data.EVENT_FEROS, data.EVENT_NOVERIA)  # what squad_talk.TalkGate.missions counts
UNCHARTED_GATES = {  # landing conditions of gated uncharted planets (uncharted.py)
    uncharted.FEROS: Has(data.EVENT_FEROS),
    uncharted.NOVERIA: Has(data.EVENT_NOVERIA),
    uncharted.GARRUS: Has("Garrus Vakarian"),
}


def set_rules(world: MassEffectLE1World) -> None:
    # Entrance rules are set when regions are created (regions.ENTRANCES).
    for name, loc in data.LOCATIONS.items():
        if not location_included(world, name, loc):
            continue
        rule = None
        if loc.needs_act1:
            rule = HAS_ACT1
        if loc.needs_event is not None:
            rule = Has(loc.needs_event) if rule is None else rule & Has(loc.needs_event)
        if loc.squad is not None:  # the squadmate's own conversation
            rule = Has(loc.squad) if rule is None else rule & Has(loc.squad)
        if (needed := NEEDS_SQUADMATE.get(name)) is not None:
            rule = Has(needed) if rule is None else rule & Has(needed)
        if (any_squad := NEEDS_ANY_SQUADMATE.get(name)) is not None:
            rule = any_squad if rule is None else rule & any_squad
        if name in GROUNDED_QUESTS:
            rule = GROUNDED if rule is None else rule & GROUNDED
        if name in NEEDS_TWO_SQUADMATES or uncharted.location_needs_two_squadmates(name):
            rule = TWO_SQUADMATES if rule is None else rule & TWO_SQUADMATES
        if (gate := uncharted.location_gate(name)) is not None:
            rule = UNCHARTED_GATES[gate] if rule is None else rule & UNCHARTED_GATES[gate]
        if (talk := squad_talk.TALK_GATES.get(name)) is not None:
            if talk.uncharted:
                rule = UNCHARTED_LANDINGS if rule is None else rule & UNCHARTED_LANDINGS
            if talk.missions:
                done = HasFromList(*MISSIONS_DONE, count=talk.missions)
                rule = done if rule is None else rule & done
        if rule is not None:
            world.set_rule(world.get_location(name), rule)
    world.set_completion_rule(Has(data.EVENT_VICTORY))
