from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

from worlds.AutoWorld import World

from . import data, items, regions, rules, web_world
from . import options as me_options  # renamed: World.options is an attribute

logger = logging.getLogger(__name__)

# Spare slots kept for filler when clamping fragments_total. An all-progression pool made restrictive fill fail on
# about 1 in 200 seeds; with 4 spare slots, 1000/1000 seeds filled in every option combination tried (2026-10-08).
MIN_FILLER_SLOTS = 4


class MassEffectLE1World(World):
    """
    Commander Shepard starts aboard the Normandy with no squad and no leads. Recover the coordinates, rebuild the
    team, and gather enough Prothean beacon fragments to reach Ilos and stop Saren.
    """

    game = data.GAME_NAME
    web = web_world.MassEffectLE1WebWorld()
    options_dataclass = me_options.MassEffectLE1Options
    options: me_options.MassEffectLE1Options
    origin_region_name = data.Region.NORMANDY.value

    item_name_to_id: ClassVar[dict[str, int]] = data.ITEM_NAME_TO_ID
    location_name_to_id: ClassVar[dict[str, int]] = data.LOCATION_NAME_TO_ID
    item_name_groups = data.ITEM_NAME_GROUPS
    location_name_groups = data.LOCATION_NAME_GROUPS

    fragments_required: int
    gear_total: int = 0  # gear lines drawn into the pool (the client scales gear levels over this many)

    def generate_early(self) -> None:
        bdts = bool(self.options.bring_down_the_sky)
        # Locations that never take this world's progression (regions.holds_no_progression, and Ilos, the point of no
        # return) don't count as room for it.
        location_count = sum(1 for name, loc in data.LOCATIONS.items()
                             if regions.location_included(self, name, loc)
                             and not regions.holds_no_progression(self, name, loc)
                             and loc.region not in data.NO_RETURN_REGIONS)
        squad_items = len(data.SQUAD) - (len(data.HUMAN_SQUAD) if self.options.human_squad_at_citadel else 0)
        fixed_items = squad_items + len(data.COORDINATES) - (0 if bdts else 1) + int(bool(self.options.mako_shuffled))
        room = max(0, location_count - fixed_items - MIN_FILLER_SLOTS)

        total = self.options.fragments_total
        if total.value > room:
            logger.warning(f"{self.player_name}: fragments_total {total.value} lowered to {room} (location count).")
            total.value = room
        required = self.options.fragments_required
        if required.value > total.value:
            logger.warning(f"{self.player_name}: fragments_required {required.value} lowered to {total.value}.")
            required.value = total.value
        self.fragments_required = required.value

    def create_regions(self) -> None:
        regions.create_regions(self)

    def create_items(self) -> None:
        items.create_items(self)

    def create_item(self, name: str) -> items.MassEffectLE1Item:
        return items.create_item(self, name)

    def get_filler_item_name(self) -> str:
        return self.random.choices(data.FILLER, data.FILLER_WEIGHTS)[0]

    def set_rules(self) -> None:
        rules.set_rules(self)

    def fill_slot_data(self) -> Mapping[str, Any]:
        return {
            **self.options.as_dict(
                "fragments_total", "fragments_required", "required_squad_size", "mako_shuffled", "human_squad_at_citadel",
                "bring_down_the_sky", "useful_share", "unlock_containers", "infinite_sprint",
                "shop_slots", "experience_size",
            ),
            "gear_total": self.gear_total,
        }
