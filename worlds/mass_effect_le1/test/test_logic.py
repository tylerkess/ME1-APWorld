from ..data import COORDINATES, EVENT_ACT1, EXPERIENCE, EVENT_FEROS, EVENT_THERUM, EVENT_NOVERIA, EVENT_VIRMIRE, FILLER, FRAGMENT, GEAR_BY_NAME, HUMAN_SQUAD, LOCATIONS, MAKO, NO_RETURN_REGIONS, Region, SHOPS, SQUAD, UPGRADE, shop_bool
from BaseClasses import Item, ItemClassification, LocationProgressType
from ..options import options_presets
from ..items import EXTRA_GEAR_MANUFACTURERS, GEAR_MAX, gear_lines, gear_max
from ..squad_talk import TALK_GATES
from ..regions import AUTOMATIC_CODEX, GROUNDED_QUESTS, MISSABLE_CODEX
from . import MassEffectLE1TestBase


def takes_own_progression(test: MassEffectLE1TestBase, name: str) -> bool:
    """Not filler-only, and its item rule accepts this world's progression (a Beacon Fragment)."""
    location = test.multiworld.get_location(name, test.player)
    return (location.progress_type != LocationProgressType.EXCLUDED
            and location.item_rule(test.world.create_item(FRAGMENT)))


class TestDefaults(MassEffectLE1TestBase):
    def test_citadel_is_open_from_start(self) -> None:
        self.assertTrue(self.can_reach_region(Region.CITADEL.value))
        self.assertTrue(self.can_reach_location("Citadel: Recruit Garrus"))

    def collect_event(self, name: str) -> None:
        self.collect(self.multiworld.get_location(name, self.player).item)

    def test_act1_gates_citadel_side_content(self) -> None:
        self.assertFalse(self.can_reach_location("Citadel: Asari Consort: Started"))
        self.collect_event(EVENT_ACT1)
        self.assertTrue(self.can_reach_location("Citadel: Asari Consort: Started"))

    def test_eden_prime_needs_only_coordinates(self) -> None:
        self.assertFalse(self.can_reach_region(Region.EDEN_PRIME.value))
        self.collect_by_name(COORDINATES[Region.EDEN_PRIME])
        self.assertTrue(self.can_reach_region(Region.EDEN_PRIME.value))

    def test_mission_needs_coordinates_and_two_squadmates(self) -> None:
        self.collect_by_name(COORDINATES[Region.FEROS])
        self.assertFalse(self.can_reach_region(Region.FEROS.value))
        self.collect_by_name(SQUAD[2])
        self.assertFalse(self.can_reach_region(Region.FEROS.value))
        self.collect_by_name(SQUAD[3])
        self.assertTrue(self.can_reach_region(Region.FEROS.value))

    def test_virmire_needs_kaidan_and_ashley(self) -> None:
        self.collect_by_name([COORDINATES[Region.VIRMIRE], "Garrus Vakarian", "Urdnot Wrex"])
        self.assertFalse(self.can_reach_region(Region.VIRMIRE.value))
        self.collect_by_name("Kaidan Alenko")
        self.assertFalse(self.can_reach_region(Region.VIRMIRE.value))
        self.collect_by_name("Ashley Williams")
        self.assertTrue(self.can_reach_region(Region.VIRMIRE.value))

    def test_ilos_needs_required_fragments(self) -> None:
        self.collect_by_name(["Garrus Vakarian", "Urdnot Wrex"])
        self.collect_event(EVENT_VIRMIRE)
        fragments = self.get_items_by_name(FRAGMENT)
        self.assertEqual(len(fragments), 6)
        self.collect(fragments[:3])
        self.assertFalse(self.can_reach_region(Region.ILOS.value))
        self.collect(fragments[3:4])
        self.assertTrue(self.can_reach_region(Region.ILOS.value))

    def test_ilos_needs_virmire_complete(self) -> None:
        self.collect_by_name(["Garrus Vakarian", "Urdnot Wrex"])
        self.collect(self.get_items_by_name(FRAGMENT))
        self.assertFalse(self.can_reach_region(Region.ILOS.value))
        self.collect_event(EVENT_VIRMIRE)
        self.assertTrue(self.can_reach_region(Region.ILOS.value))

    def test_beatable_with_everything(self) -> None:
        self.collect_all_but([])
        self.assertBeatable(True)


class TestUnchartedGates(MassEffectLE1TestBase):
    """Uncharted planets whose landing waits on a story flag (uncharted.py)."""
    options = {"containers_no_progression": False}  # these tests use containers for the planets' own rules

    def collect_event(self, name: str) -> None:
        self.collect(self.multiworld.get_location(name, self.player).item)

    def test_open_planets_need_only_act1(self) -> None:
        self.assertFalse(self.can_reach_location("Solcrum: Ammo Pack 1"))
        self.collect_event(EVENT_ACT1)
        self.assertTrue(self.can_reach_location("Solcrum: Ammo Pack 1"))
        self.assertTrue(self.can_reach_location("Luna: Locked Secure Crate 1"))  # opened by the mod's open start

    def test_feros_planets_need_feros_complete(self) -> None:
        self.collect_event(EVENT_ACT1)
        self.assertFalse(self.can_reach_location("Chasca: Aid Station 1"))
        self.assertFalse(self.can_reach_location("Investigate Samples: Completed"))
        self.collect_event(EVENT_FEROS)
        self.assertTrue(self.can_reach_location("Investigate Samples: Completed"))
        self.assertTrue(self.can_reach_location("Chasca: Aid Station 1"))

    def test_noveria_planets_need_noveria_complete(self) -> None:
        names = ("Altahe: Aid Station 1", "Unidentified Space Facility: Aid Station 1", "UNC: Listening Post Alpha: Started")
        self.collect_event(EVENT_ACT1)
        for name in names:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_event(EVENT_NOVERIA)
        for name in names:
            self.assertTrue(self.can_reach_location(name), name)

    def test_fedele_needs_garrus(self) -> None:
        self.collect_event(EVENT_ACT1)
        self.assertFalse(self.can_reach_location("MSV Fedele: Malfunctioning Object 1"))
        self.collect_by_name("Garrus Vakarian")
        self.assertTrue(self.can_reach_location("MSV Fedele: Malfunctioning Object 1"))

    def test_presrop_holds_filler_only(self) -> None:
        location = self.multiworld.get_location("Presrop: Aid Station 1", self.player)
        self.assertEqual(location.progress_type, LocationProgressType.EXCLUDED)

    def test_finished_missions_hold_no_progression_by_default(self) -> None:
        for name in ("Therum: Ammo Pack 1", "Codex: Aliens: Council Races: Salarians: Culture", "Eden Prime: Ammo Pack 1",
                     "Codex: Aliens: Non-Council Races: Geth", "Asteroid X57: Grenade Storage Box 13"):
            self.assertFalse(takes_own_progression(self, name), name)
        for name in ("Therum: Rescue Liara", "Virmire: The Survivor", "Eden Prime: Beacon Vision",
                     "Asteroid X57: Bring Down the Sky"):  # on the way: normal
            self.assertTrue(takes_own_progression(self, name), name)

    def test_finished_missions_take_gear_but_not_others_useful_items(self) -> None:
        location = self.multiworld.get_location("Therum: Ammo Pack 1", self.player)
        self.assertEqual(location.progress_type, LocationProgressType.DEFAULT)
        self.assertTrue(location.item_rule(self.world.create_item(next(iter(GEAR_BY_NAME)))))
        self.assertTrue(location.item_rule(self.world.create_item(FILLER[0])))
        other = self.player + 1
        self.assertFalse(location.item_rule(Item("Other Key", ItemClassification.progression, None, other)))
        self.assertFalse(location.item_rule(Item("Other Sword", ItemClassification.useful, None, other)))
        self.assertTrue(location.item_rule(Item("Other Rupee", ItemClassification.filler, None, other)))

    def test_feros_and_noveria_close_in_part(self) -> None:
        for name, closed in (("Feros: Ammo Pack 1", True),  # BIOA_WAR20_03C: not streamed after the Thorian
                               ("Feros: Ammo Pack 2", False),  # Zhu's Hope
                               ("Feros: Locked Secure Crate 1", True),  # ExoGeni (WAR50)
                               ("Noveria: Ammo Pack 3", True),  # the road to Peak 15 (ICE25)
                               ("Noveria: Aid Station 1", True),  # Peak 15 (ICE60)
                               ("Noveria: Ammo Pack 1", False),  # Port Hanshan
                               ("Feros: Zhu's Hope Merchant Slot 1", False)):
            self.assertEqual(takes_own_progression(self, name), not closed, name)

    def test_morality_planets_hold_no_progression(self) -> None:
        """Chohe and Nonuel open only with 75% Paragon or Renegade (their star systems' conditional 1669)."""
        for name, missable in (("Chohe: Technician Kit 1", True), ("Nonuel: Aid Station 1", True),
                               ("Klensal: Locked Secure Weapon Locker 1", False)):
            self.assertEqual(takes_own_progression(self, name), not missable, name)
        location = self.multiworld.get_location("Chohe: Technician Kit 1", self.player)
        self.assertTrue(location.item_rule(self.world.create_item(next(iter(GEAR_BY_NAME)))))

    def test_joker_prologue_pages_hold_no_progression(self) -> None:
        for name in ("Codex: Humanity and the Systems Alliance: Systems Alliance",
                     "Codex: Humanity and the Systems Alliance: Timeline"):
            location = self.multiworld.get_location(name, self.player)
            self.assertFalse(takes_own_progression(self, name), name)
            self.assertTrue(location.item_rule(self.world.create_item(next(iter(GEAR_BY_NAME)))), name)

    def test_genophage_needs_wrex(self) -> None:
        name = "Virmire: Wrex and the Genophage: Started"
        self.collect_by_name([COORDINATES[Region.VIRMIRE], "Kaidan Alenko", "Ashley Williams"])
        self.assertTrue(self.can_reach_region(Region.VIRMIRE.value))
        self.assertFalse(self.can_reach_location(name))
        self.collect_by_name("Urdnot Wrex")
        self.assertTrue(self.can_reach_location(name))


class TestFinishedMissionsAnyItem(MassEffectLE1TestBase):
    options = {"finished_mission_checks": "any_item", "containers_no_progression": False}

    def test_finished_mission_checks_are_normal(self) -> None:
        for name in ("Therum: Ammo Pack 1", "Feros: Ammo Pack 1", "Noveria: Aid Station 1"):
            self.assertTrue(takes_own_progression(self, name), name)


class TestFinishedMissionsFillerOnly(MassEffectLE1TestBase):
    options = {"finished_mission_checks": "filler_only", "containers_no_progression": False}

    def test_finished_mission_checks_hold_filler_only(self) -> None:
        for name in ("Therum: Ammo Pack 1", "Feros: Ammo Pack 1", "Noveria: Aid Station 1", "Virmire: Salarian Camp Slot 1"):
            self.assertEqual(self.multiworld.get_location(name, self.player).progress_type, LocationProgressType.EXCLUDED, name)
        self.assertTrue(takes_own_progression(self, "Noveria: Ammo Pack 1"))


class TestMakoShuffled(MassEffectLE1TestBase):
    options = {"mako_shuffled": True}

    def test_missions_need_the_mako(self) -> None:
        self.collect_by_name([COORDINATES[Region.THERUM], "Garrus Vakarian", "Urdnot Wrex"])
        self.assertFalse(self.can_reach_region(Region.THERUM.value))
        self.collect_by_name("M35 Mako")
        self.assertTrue(self.can_reach_region(Region.THERUM.value))


class TestSoloSquad(MassEffectLE1TestBase):
    options = {"required_squad_size": 0}

    def test_missions_need_only_coordinates(self) -> None:
        self.collect_by_name(COORDINATES[Region.NOVERIA])
        self.assertTrue(self.can_reach_region(Region.NOVERIA.value))


class TestNoBringDownTheSky(MassEffectLE1TestBase):
    options = {"bring_down_the_sky": False}

    def test_x57_removed(self) -> None:
        self.assertNotIn("Asteroid X57: Bring Down the Sky", self.multiworld.regions.location_cache[self.player])
        self.assertEqual(self.get_items_by_name(COORDINATES[Region.ASTEROID_X57]), [])


class TestFragmentClamping(MassEffectLE1TestBase):
    options = {"fragments_total": 12, "fragments_required": 12, "mako_shuffled": True, "codexsanity": "off", "journalsanity": "off", "containersanity": "off",
               "aid_stations": False, "shop_slots": 0}

    def test_fragments_clamped_to_location_room(self) -> None:
        # 17 locations - 1 on Ilos (no own progression) - 6 squad - 6 coordinates - 1 Mako - 4 reserved filler slots < 0: none
        self.assertEqual(len(self.get_items_by_name(FRAGMENT)), 0)
        self.assertEqual(self.world.fragments_required, 0)


class TestHumanSquadAtCitadel(MassEffectLE1TestBase):
    options = {"human_squad_at_citadel": True, "fragments_total": 12, "fragments_required": 12, "mako_shuffled": True,
               "codexsanity": "off", "journalsanity": "off", "containersanity": "off",
               "aid_stations": False, "shop_slots": 0}

    def test_kaidan_and_ashley_are_not_items(self) -> None:
        for name in HUMAN_SQUAD:
            self.assertEqual(self.get_items_by_name(name), [])
        for name in SQUAD[2:]:
            self.assertEqual(len(self.get_items_by_name(name)), 1)

    def test_citadel_arrival_gives_the_pair(self) -> None:
        for name in HUMAN_SQUAD:  # events (no ID): the client never sets their InParty
            self.assertIsNone(self.multiworld.get_location(f"Citadel: {name} Joins (arrival)", self.player).address)
        self.assertFalse(self.can_reach_region(Region.VIRMIRE.value))
        # Citadel is open from the start, so its arrival events count: no squad items needed (squad of two, D4 pair)
        self.collect_by_name([COORDINATES[Region.VIRMIRE], "M35 Mako"])
        self.assertTrue(self.can_reach_region(Region.VIRMIRE.value))

    def test_freed_slots_go_to_fragments_room(self) -> None:
        # 17 locations - 1 on Ilos (no own progression) - 4 squad - 6 coordinates - 1 Mako - 4 reserved filler slots = 1
        self.assertEqual(len(self.get_items_by_name(FRAGMENT)), 1)


class TestGearDraw(MassEffectLE1TestBase):
    options = {"fragments_total": 1, "fragments_required": 1, "useful_share": 100, "upgrade_weight": 0, "experience_weight": 0,
               "codexsanity": "off", "journalsanity": "off",
               "containersanity": "off", "aid_stations": False, "shop_slots": 0}

    def test_gear_fills_the_free_room_with_distinct_lines_and_a_spectre(self) -> None:
        gear = [item.name for item in self.multiworld.itempool if item.name in GEAR_BY_NAME]
        self.assertEqual(len(gear), len(set(gear)))
        self.assertEqual(len(gear), self.world.gear_total)
        self.assertGreater(len(gear), 0)
        self.assertTrue(any(GEAR_BY_NAME[name].spectre for name in gear))
        self.assertEqual(self.world.fill_slot_data()["gear_total"], len(gear))

    def test_categories_are_spread_before_any_repeats(self) -> None:
        gear = [GEAR_BY_NAME[item.name] for item in self.multiworld.itempool if item.name in GEAR_BY_NAME]
        categories = [line.category for line in gear if not line.spectre]
        self.assertEqual(len(categories), len(set(categories)))  # fewer slots than the 12 categories here


class TestNoUsefulItems(MassEffectLE1TestBase):
    options = {"useful_share": 0}

    def test_only_progression_and_filler(self) -> None:
        own = [item for item in self.multiworld.itempool if item.player == self.player]
        self.assertFalse(any(item.useful for item in own))
        self.assertEqual(self.world.gear_total, 0)


class TestZeroWeightsGiveFiller(MassEffectLE1TestBase):
    options = {"gear_weight": 0, "upgrade_weight": 0, "experience_weight": 0}

    def test_no_useful_items(self) -> None:
        self.assertFalse(any(item.useful for item in self.multiworld.itempool if item.player == self.player))


class TestGearOnly(MassEffectLE1TestBase):
    options = {"useful_share": 100, "upgrade_weight": 0, "experience_weight": 0}

    def test_gear_caps_and_nothing_else_is_useful(self) -> None:
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        self.assertEqual(names.count(UPGRADE) + names.count(EXPERIENCE), 0)
        self.assertLessEqual(self.world.gear_total, GEAR_MAX)
        self.assertEqual(len(set(name for name in names if name in GEAR_BY_NAME)), self.world.gear_total)


class TestExtraGearOff(MassEffectLE1TestBase):
    options = {"useful_share": 100, "upgrade_weight": 0, "experience_weight": 0}

    def test_extra_gear_stays_out_by_default(self) -> None:
        gear = [GEAR_BY_NAME[item.name] for item in self.multiworld.itempool if item.name in GEAR_BY_NAME]
        self.assertTrue(gear)
        self.assertFalse(any(line.manufacturer_row in EXTRA_GEAR_MANUFACTURERS for line in gear))
        self.assertLessEqual(self.world.gear_total, gear_max(gear_lines(extra=False)))

    def test_extra_gear_is_forty_lines(self) -> None:
        self.assertEqual(len(gear_lines(extra=True)) - len(gear_lines(extra=False)), 40)
        self.assertEqual((gear_max(gear_lines(extra=False)), GEAR_MAX), (185, 227))


class TestExtraGearOn(MassEffectLE1TestBase):
    options = {"useful_share": 100, "upgrade_weight": 0, "experience_weight": 0, "extra_gear": True}

    def test_extra_gear_can_be_drawn(self) -> None:
        gear = [GEAR_BY_NAME[item.name] for item in self.multiworld.itempool if item.name in GEAR_BY_NAME]
        self.assertTrue(any(line.manufacturer_row in EXTRA_GEAR_MANUFACTURERS for line in gear))


class TestGearSpread(MassEffectLE1TestBase):
    options = {"useful_share": 10, "upgrade_weight": 0, "experience_weight": 0}  # about 64 lines: under 12 x 6

    def test_round_robin_keeps_categories_within_one(self) -> None:
        counts: dict[str, int] = {}
        for item in self.multiworld.itempool:
            if item.name in GEAR_BY_NAME and not GEAR_BY_NAME[item.name].spectre:
                counts[GEAR_BY_NAME[item.name].category] = counts.get(GEAR_BY_NAME[item.name].category, 0) + 1
        self.assertEqual(len(counts), 12)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)


class TestCodexsanity(MassEffectLE1TestBase):
    def test_full_codex_is_in_and_squad_pages_need_the_squadmate(self) -> None:
        codex = [loc for loc in self.multiworld.get_locations(self.player) if loc.name.startswith("Codex: ")]
        self.assertEqual(len(codex), 104)  # 120 pages, less BDtS's and the 15 the story gives on its own
        page = next(name for name, loc in LOCATIONS.items() if loc.squad == "Liara T'Soni" and name not in TALK_GATES)
        self.assertFalse(self.can_reach_location(page))
        self.collect_by_name(["Liara T'Soni"])
        self.assertTrue(self.can_reach_location(page))

    def test_uncharted_waits_for_act1(self) -> None:
        self.assertFalse(self.can_reach_region(Region.UNCHARTED.value))

    def test_automatic_pages_are_not_locations(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        for name in AUTOMATIC_CODEX:
            self.assertIn(name, LOCATIONS)
            self.assertNotIn(name, names)

    def test_missable_pages_hold_filler_or_useful_of_any_game(self) -> None:
        useful = Item("Other Useful", ItemClassification.useful, None, self.player + 1)
        progression = Item("Other Progression", ItemClassification.progression, None, self.player + 1)
        for name in MISSABLE_CODEX:
            location = self.multiworld.get_location(name, self.player)
            self.assertFalse(takes_own_progression(self, name), name)
            self.assertTrue(location.item_rule(self.world.create_item(next(iter(GEAR_BY_NAME)))), name)
            self.assertTrue(location.item_rule(useful), name)
            self.assertFalse(location.item_rule(progression), name)

    def test_other_pages_take_progression(self) -> None:
        for location in self.multiworld.get_locations(self.player):
            if location.name.startswith("Codex: ") and location.name not in MISSABLE_CODEX:
                self.assertTrue(takes_own_progression(self, location.name), location.name)


class TestCodexsanityStory(MassEffectLE1TestBase):
    options = {"codexsanity": "story"}

    def test_only_story_pages(self) -> None:
        codex = [loc.name for loc in self.multiworld.get_locations(self.player) if loc.name.startswith("Codex: ")]
        self.assertEqual(len(codex), 79)
        self.assertTrue(all(LOCATIONS[name].codex == "story" for name in codex))


class TestContainers(MassEffectLE1TestBase):
    def test_default_has_containers_and_stations_but_no_drop_slots(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        containers = [n for n in names if LOCATIONS.get(n) and LOCATIONS[n].container == "container"]
        stations = [n for n in names if LOCATIONS.get(n) and LOCATIONS[n].container == "station"]
        self.assertEqual((len(containers), len(stations)), (226 + 158, 33 + 21))
        self.assertFalse(any(LOCATIONS[n].container == "drop" for n in names if n in LOCATIONS))

    def test_uncharted_containers_need_act1_and_x57_its_coordinates(self) -> None:
        self.assertFalse(self.can_reach_location("Chasca: Crate 1"))
        self.assertFalse(self.can_reach_location("Asteroid X57: Crate 1"))


class TestContainerDrops(MassEffectLE1TestBase):
    options = {"containersanity": "with_drops", "aid_stations": False}

    def test_drop_slots_share_their_container_flag(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        self.assertIn("Chasca: Crate 1 (Item 3)", names)
        self.assertEqual(LOCATIONS["Chasca: Crate 1 (Item 3)"].ot_plot, LOCATIONS["Chasca: Crate 1"].ot_plot)
        self.assertFalse(any(LOCATIONS[n].container == "station" for n in names if n in LOCATIONS))

    def test_agebinium_needs_two_squadmates(self) -> None:
        names = ("Agebinium: Ammo Pack 1", "Agebinium: Crate 1", "Agebinium: Crate 1 (Item 3)")
        self.collect(self.multiworld.get_location(EVENT_ACT1, self.player).item)
        self.collect([self.world.create_item(MAKO), self.world.create_item("Garrus Vakarian")])
        self.assertTrue(self.can_reach_location("Mavigon: Crate 1"))  # ungated planet: one squadmate is enough
        for name in names:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect(self.world.create_item("Liara T'Soni"))
        for name in names:
            self.assertTrue(self.can_reach_location(name), name)


class TestContainersNoProgression(MassEffectLE1TestBase):
    options = {"containersanity": "with_drops"}

    def test_containers_and_drops_hold_no_progression_of_any_game(self) -> None:
        progression = Item("Other Progression", ItemClassification.progression, None, self.player + 1)
        kinds = set()
        for location in self.multiworld.get_locations(self.player):
            kind = LOCATIONS[location.name].container if location.name in LOCATIONS else None
            if kind in ("container", "drop"):
                kinds.add(kind)
                self.assertFalse(takes_own_progression(self, location.name), location.name)
                self.assertFalse(location.item_rule(progression), location.name)
        self.assertEqual(kinds, {"container", "drop"})

    def test_plain_container_takes_any_games_useful_items(self) -> None:
        location = self.multiworld.get_location("Klensal: Locked Secure Weapon Locker 1", self.player)
        self.assertTrue(location.item_rule(Item("Other Useful", ItemClassification.useful, None, self.player + 1)))
        self.assertTrue(location.item_rule(self.world.create_item(next(iter(GEAR_BY_NAME)))))

    def test_aid_stations_are_not_affected(self) -> None:
        station = next(name for name, loc in LOCATIONS.items() if loc.container == "station" and loc.region is Region.CITADEL)
        self.assertTrue(takes_own_progression(self, station), station)


class TestContainersAnyItem(MassEffectLE1TestBase):
    options = {"containers_no_progression": False}

    def test_open_containers_take_progression(self) -> None:
        self.assertTrue(takes_own_progression(self, "Klensal: Locked Secure Weapon Locker 1"))


class TestUnlockContainers(MassEffectLE1TestBase):
    def test_locked_containers_and_stations_are_checks_by_default(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        locked = [n for n in names if n in LOCATIONS and LOCATIONS[n].locked]
        self.assertEqual(len(locked), 179)
        self.assertTrue(self.world.fill_slot_data()["unlock_containers"])


class TestKeepLocks(MassEffectLE1TestBase):
    options = {"unlock_containers": False}

    def test_locked_ones_are_left_out(self) -> None:
        self.assertFalse(any(n in LOCATIONS and LOCATIONS[n].locked for n in
                             (loc.name for loc in self.multiworld.get_locations(self.player))))


class TestDefaultItemPool(MassEffectLE1TestBase):
    def test_weighted_useful_share_and_credit_heavy_filler(self):
        own = [item for item in self.multiworld.itempool if item.player == self.player]
        useful = [item.name for item in own if item.useful]
        free = len(useful) + sum(1 for item in own if not item.useful and not item.advancement)
        self.assertAlmostEqual(len(useful) / free, 0.55, delta=0.15)  # excluded slots also hold filler
        gear = sum(1 for name in useful if name in GEAR_BY_NAME)
        upgrades, experience = useful.count(UPGRADE), useful.count(EXPERIENCE)
        self.assertEqual(gear + upgrades + experience, len(useful))
        self.assertGreater(gear, upgrades)  # weights 50 / 25 / 25
        self.assertGreater(gear, experience)
        self.assertGreater(min(upgrades, experience), 0)
        filler = [item.name for item in own if item.name in FILLER]
        self.assertGreater(filler.count("Credits"), len(filler) // 2)


class TestJournalsanity(MassEffectLE1TestBase):
    def test_journal_checks_and_excluded_completions(self) -> None:
        journal = [loc for loc in self.multiworld.get_locations(self.player) if LOCATIONS.get(loc.name) and LOCATIONS[loc.name].journal]
        self.assertEqual(len(journal), 130)
        for loc in journal:
            choice = loc.name in TALK_GATES and TALK_GATES[loc.name].choice  # e.g. Wrex: Family Armor (Wrex can die)
            self.assertEqual(loc.progress_type == LocationProgressType.EXCLUDED, loc.name.endswith(": Completed") or choice,
                             loc.name)

    def test_grounded_quests_need_every_mission_and_hold_no_progression(self) -> None:
        useful = Item("Other Useful", ItemClassification.useful, None, self.player + 1)
        for name in GROUNDED_QUESTS:
            self.assertFalse(self.can_reach_location(name), name)
            self.assertFalse(takes_own_progression(self, name), name)
            self.assertTrue(self.multiworld.get_location(name, self.player).item_rule(useful), name)
        events = {loc.item.name: loc.item for loc in self.multiworld.get_locations(self.player)
                  if loc.item is not None and loc.address is None}
        for event in (EVENT_THERUM, EVENT_FEROS, EVENT_NOVERIA):
            self.collect(events[event])
        self.assertFalse(self.can_reach_location("Citadel: Our Own Worst Enemy: Started"))
        self.collect(events[EVENT_VIRMIRE])
        for name in GROUNDED_QUESTS:
            self.assertTrue(self.can_reach_location(name), name)

    def test_squad_quest_needs_its_squadmate(self) -> None:
        self.assertFalse(self.can_reach_location("Wrex: Family Armor: Started"))
        self.collect_by_name(["Urdnot Wrex"])
        self.assertTrue(self.can_reach_location("Wrex: Family Armor: Started"))

    def test_jahleed_and_rita_need_two_squadmates(self) -> None:
        names = ("Citadel: Jahleed's Fears: Started", "Citadel: Jahleed's Fears: Completed",
                 "Citadel: Rita's Sister: Started", "Citadel: Rita's Sister: Completed")
        self.collect(self.multiworld.get_location(EVENT_ACT1, self.player).item)
        self.collect_by_name("Garrus Vakarian")
        for name in names:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_by_name("Liara T'Soni")
        for name in names:
            self.assertTrue(self.can_reach_location(name), name)

    def test_squadmate_line_quests_need_a_squadmate(self) -> None:
        freighter = ("UNC: Derelict Freighter: Started", "UNC: Derelict Freighter: Completed")
        theta = ("UNC: Listening Post Theta: Started", "UNC: Listening Post Theta: Completed")
        self.collect_all_but(list(SQUAD))
        for name in freighter + theta:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_by_name("Tali'Zorah nar Rayya")  # no Listening Post Theta line
        for name in freighter:
            self.assertTrue(self.can_reach_location(name), name)
        for name in theta:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_by_name("Garrus Vakarian")
        for name in theta:
            self.assertTrue(self.can_reach_location(name), name)


class TestShops(MassEffectLE1TestBase):
    def collect_event(self, name: str) -> None:
        self.collect(self.multiworld.get_location(name, self.player).item)

    def location(self, name: str):
        return self.multiworld.get_location(name, self.player)

    def test_default_has_three_slots_per_merchant(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player) if LOCATIONS.get(loc.name) and LOCATIONS[loc.name].shop_slot is not None}
        self.assertEqual(len(names), 3 * len(SHOPS))
        self.assertIn("Citadel: Morlan Slot 3", names)
        self.assertNotIn("Citadel: Morlan Slot 4", names)

    def test_slot_bools_follow_the_mod_ledger(self) -> None:
        # tools/le1mod/Shops.cs: bool 20700 + 5 * merchant + slot, merchants in SHOPS order.
        self.assertEqual(LOCATIONS["Normandy: Requisitions Officer Slot 1"].ot_plot, "bool 20700")
        self.assertEqual(LOCATIONS["Virmire: Salarian Camp Slot 5"].ot_plot, "bool 20749")
        self.assertEqual(shop_bool(7, 2), 20737)

    def test_requisitions_officer_is_open_from_start(self) -> None:
        self.assertTrue(self.can_reach_location("Normandy: Requisitions Officer Slot 1"))

    def test_citadel_shops_wait_for_act1(self) -> None:
        self.assertFalse(self.can_reach_location("Citadel: Expat Slot 1"))
        self.collect_event(EVENT_ACT1)
        self.assertTrue(self.can_reach_location("Citadel: Expat Slot 1"))

    def test_rentola_needs_virmire_and_holds_filler_only(self) -> None:
        self.assertEqual(LOCATIONS["Normandy: Commander Rentola Slot 1"].needs_event, EVENT_VIRMIRE)
        self.assertFalse(self.can_reach_location("Normandy: Commander Rentola Slot 1"))
        self.assertEqual(self.location("Normandy: Commander Rentola Slot 1").progress_type, LocationProgressType.EXCLUDED)

    def test_virmire_camp_follows_finished_mission_checks(self) -> None:
        self.assertFalse(takes_own_progression(self, "Virmire: Salarian Camp Slot 1"))
        self.assertTrue(takes_own_progression(self, "Noveria: Opold and Petozi Slot 1"))


class TestNoShops(MassEffectLE1TestBase):
    options = {"shop_slots": 0}

    def test_no_shop_locations(self) -> None:
        self.assertFalse(any(LOCATIONS.get(loc.name) and LOCATIONS[loc.name].shop_slot is not None
                             for loc in self.multiworld.get_locations(self.player)))


class TestAllShopSlots(MassEffectLE1TestBase):
    options = {"shop_slots": 5, "finished_mission_checks": "any_item"}

    def test_five_slots_and_virmire_camp_normal(self) -> None:
        self.assertTrue(self.can_reach_location("Normandy: Requisitions Officer Slot 5"))
        self.assertNotEqual(self.multiworld.get_location("Virmire: Salarian Camp Slot 5", self.player).progress_type,
                            LocationProgressType.EXCLUDED)


class TestPresetStoryFocus(MassEffectLE1TestBase):
    options = options_presets["Story Focus"]


class TestPresetCompletionist(MassEffectLE1TestBase):
    options = options_presets["Completionist"]


class TestIlosNoReturn(MassEffectLE1TestBase):
    """Ilos is a point of no return: never this world's progression; its gear by default; other games' items always."""
    options = {"containers_no_progression": False}  # Ilos containers would otherwise refuse other games' progression

    def no_return_locations(self) -> list:
        return [location for region in NO_RETURN_REGIONS
                for location in self.multiworld.get_region(region.value, self.player).locations
                if location.address is not None]

    def assert_allowed(self, item: Item, allowed: bool) -> None:
        locations = self.no_return_locations()
        self.assertGreater(len(locations), 1)  # the Conduit story check and Ilos containers
        for location in locations:
            self.assertEqual(location.item_rule(item), allowed, f"{item.name} at {location.name}")

    def test_own_progression_never_on_ilos(self) -> None:
        for name in (FRAGMENT, COORDINATES[Region.EDEN_PRIME], "Garrus Vakarian"):
            self.assert_allowed(self.world.create_item(name), False)

    def test_own_gear_and_filler(self) -> None:
        for name in (next(iter(GEAR_BY_NAME)), UPGRADE, FILLER[0]):
            self.assert_allowed(self.world.create_item(name), True)

    def test_other_games_items_allowed(self) -> None:
        self.assert_allowed(Item("Other Game Key", ItemClassification.progression, None, self.player + 1), True)

    def test_other_locations_take_own_progression(self) -> None:
        location = self.multiworld.get_location("Citadel: Recruit Garrus", self.player)
        self.assertTrue(location.item_rule(self.world.create_item(FRAGMENT)))


class TestIlosNoGear(TestIlosNoReturn):
    options = {"ilos_useful_items": False, "containers_no_progression": False}

    def test_own_gear_and_filler(self) -> None:
        for name in (next(iter(GEAR_BY_NAME)), UPGRADE):
            self.assert_allowed(self.world.create_item(name), False)
        self.assert_allowed(self.world.create_item(FILLER[0]), True)

    def test_ilos_keeps_filler_room(self) -> None:
        unfilled = self.multiworld.get_unfilled_locations(self.player)
        reserved = sum(1 for location in unfilled if location.progress_type == LocationProgressType.EXCLUDED
                       or location in self.no_return_locations())
        useful = sum(1 for item in self.multiworld.itempool if item.player == self.player and item.useful)
        self.assertLessEqual(useful, len(unfilled) - reserved - sum(
            1 for item in self.multiworld.itempool if item.player == self.player and item.advancement))


class TestSquadTalk(MassEffectLE1TestBase):
    """Squadmate conversation checks (squad_talk.TALK_GATES) on top of the squadmate item."""

    def collect_event(self, name: str) -> None:
        self.collect(self.multiworld.get_location(name, self.player).item)

    def test_gates_name_squad_locations(self) -> None:
        for name in TALK_GATES:
            self.assertIn(name, LOCATIONS)
            self.assertIsNotNone(LOCATIONS[name].squad, name)

    def test_first_talk_needs_only_the_squadmate(self) -> None:
        self.collect_by_name(["Liara T'Soni"])
        self.assertTrue(self.can_reach_location("Codex: Aliens: Extinct Races: Protheans: Beacon"))

    def test_finished_missions_are_counted(self) -> None:
        page = "Codex: Technology: Biotics: Training"
        self.collect_by_name(["Kaidan Alenko"])
        self.assertFalse(self.can_reach_location(page))
        self.collect_by_name([COORDINATES[Region.THERUM], "Garrus Vakarian", "Urdnot Wrex"])
        self.assertTrue(self.can_reach_region(Region.THERUM.value))
        self.collect_event(EVENT_THERUM)
        self.assertTrue(self.can_reach_location(page))

    def test_virmire_survivor_pages(self) -> None:
        """Pages only Kaidan, Ashley or Wrex give are lost if they die on Virmire; pages with another source are not."""
        for name, missable in (("Codex: Technology: Biotics: Training", True),
                               ("Codex: Ships and Vehicles: Space Combat: Planetary Assaults", True),
                               ("Codex: Aliens: Non-Council Races: Krogan: Culture", True),
                               ("Codex: Technology: Mass Effect Fields", False),
                               ("Codex: Technology: Biotics", False),  # also assignment transmissions
                               ("Codex: Humanity and the Systems Alliance: Human Diplomatic Relations", False),
                               ("Codex: Weapons, Armor and Equipment: Body Armor", False),
                               ("Codex: Aliens: Non-Council Races: Krogan: Genophage", False)):
            self.assertEqual(takes_own_progression(self, name), not missable, name)

    def test_choice_quests_are_filler_only(self) -> None:
        for name, gate in TALK_GATES.items():
            if gate.choice:
                self.assertTrue(LOCATIONS[name].journal, name)
                location = self.multiworld.get_location(name, self.player)
                self.assertEqual(location.progress_type, LocationProgressType.EXCLUDED, name)


class TestSquadTalkMakoShuffled(MassEffectLE1TestBase):
    options = {"mako_shuffled": True}

    def test_later_talk_needs_the_mako(self) -> None:
        """Uncharted landings need Act 1 (open from the start: collecting sweeps it) and the Mako."""
        later = ("Codex: Aliens: Council Races: Turians: Biology", "Wrex: Family Armor: Started")
        self.collect_by_name(["Garrus Vakarian", "Urdnot Wrex"])
        for name in later:
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_by_name([MAKO])
        for name in later:
            self.assertTrue(self.can_reach_location(name), name)
