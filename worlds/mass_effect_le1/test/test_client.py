import collections
import logging
from types import SimpleNamespace
import unittest

from ..client import (
    SHOP_TRANSITIONS,
    VIRMIRE_TRANSMISSION_TRANSITION,
    sync_virmire_open,
    BridgeConnection,
    BridgeProtocolError,
    BuildMismatchError,
    DeliveryGapError,
    GameNotReadyError,
    IdentityMismatchError,
    InventoryFullError,
    ID_BASE,
    ME1CommandProcessor,
    ME1Context,
    coordinate_bool_for_item,
    FRAGMENT_ITEM_ID,
    ILOS_OPEN_PLOT_BOOL,
    plot_bool_for_item,
    sync_human_squad,
    sync_ilos_gate,
    sync_two_squadmates,
    TWO_SQUADMATES_PLOT_BOOL,
    evaluate_location_predicates,
    parse_plot_predicate,
    sync_received_items as _sync_received_items,
    sync_experience as _sync_experience,
    ExtrasConnection,
    EXPERIENCE_ITEM_ID,
    gear_level,
    upgrade_row,
    deliver_notices,
    notice_lines,
    notice_text,
    INFINITE_SPRINT_PLOT_BOOL,
    MOD_VERSION,
    SHOP_SLOTS_PLOT_INT,
    summary_pages,
    SNAP_REQUEST,
    Snapshot,
    BridgeError,
    seed_identity,
    version_key,
)
from ..data import ITEM_NAME_TO_ID, LOCATION_NAME_TO_ID, UPGRADE
from ..gear import GEAR_LINES
from ..levelup import CREDIT_REWARDS, XP_REWARDS

ID = seed_identity("S", 0, 1)  # the identity of the test contexts (server seed "S", team 0, slot 1)


def sync_received_items(*args, **kwargs):
    return _sync_received_items(*args, identity=ID, **kwargs)


def sync_experience(*args, **kwargs):
    return _sync_experience(*args, identity=ID, **kwargs)


class FakeExtras:
    """LE1APExtras XPTXN: identity, then the Experience index, then the grant and the index update in one step."""

    def __init__(self, bridge):
        self.bridge = bridge  # the same save: the extras read its identity and keep their index in its ints
        self.grants = []

    def experience_index(self):
        return self.bridge.ints.get(2010, 0)

    def experience_transaction(self, identity, index, amount):
        if identity != self.bridge.identity():
            raise IdentityMismatchError("another save was loaded")
        current = self.bridge.ints.get(2010, 0)
        if current > index:
            return False
        if current < index:
            raise DeliveryGapError(f"ERR gap {current}")
        self.grants.append(amount)
        self.bridge.ints[2010] = index + 1
        return True


class FakeBridge:
    def __init__(self, *, bools=None, ints=None, fail_give_after=None, ident=ID):
        self.bools = dict(bools or {})
        self.ints = {2004: ident[0], 2005: ident[1], **(ints or {})}
        self.transactions = []
        self.bridge_version = 13
        self.inventory_full = False  # v13: GIVE answers ERR full and the index stays
        self.mod = "0.2.12"
        self.bool_writes = []
        self.int_writes = []
        self.gives = []
        self.resources = []
        self.fail_give_after = fail_give_after
        self.notes = []
        self.fail_note = False
        self.map = "BIOA_NOR00"
        self.presses = 0
        self.v10 = True
        self.v11 = True
        self.boxes = []
        self.fail_box = False
        self.shop_labels = {}
        self.shop_clears = 0

    def transaction(self, identity, index, delivery):
        """Bridge v12+ TXN: identity, then index, then the delivery and the index update in one step."""
        if identity != self.identity():
            raise IdentityMismatchError("another save was loaded")
        current = self.ints.get(2000, 0)
        if current > index:
            return False
        if current < index:
            raise DeliveryGapError(f"ERR gap {current}")
        op, *args = delivery.split()
        args = [int(arg) for arg in args]
        if op == "FLAG":
            self.set_bool(args[0], True)
        elif op == "GIVE":
            if self.inventory_full:
                raise InventoryFullError("ERR full")
            if self.fail_give_after is not None and len(self.gives) >= self.fail_give_after:
                raise GameNotReadyError("load screen")
            self.gives.append(tuple(args))
        elif op == "RES":
            self.resources.append(tuple(args))
        self.transactions.append((index, delivery))
        self.ints[2000] = index + 1
        return True

    def identity(self):
        return self.ints.get(2004, 0), self.ints.get(2005, 0)

    def nonce(self, value):
        self.ints[2006] = value

    def bind_identity(self, identity, nonce):
        if self.identity() not in {(0, 0), identity}:
            raise BridgeProtocolError("ERR bound")
        if self.ints.get(2006) != nonce:
            raise BridgeProtocolError("ERR changed")
        self.ints[2004], self.ints[2005] = identity

    def set_session(self, identity):
        self.session = identity

    def snapshot(self):
        return Snapshot(self.identity(), collections.defaultdict(bool, self.bools), collections.defaultdict(int, self.ints))

    def ping(self):
        return self.bridge_version

    def mod_version(self):
        return self.mod

    def close(self):
        pass

    def get_bool(self, index):
        return self.bools.get(index, False)

    def get_int(self, index):
        return self.ints.get(index, 0)

    def set_bool(self, index, value):
        self.bool_writes.append((index, value))
        self.bools[index] = value

    def set_int(self, index, value):
        self.int_writes.append((index, value))
        self.ints[index] = value

    def note(self, text):
        if self.fail_note:
            raise GameNotReadyError("load screen")
        self.notes.append(text)

    def version(self):
        return 11 if self.v11 else 10

    def shop_item(self, transition, name, description):
        if not self.v11:
            raise AssertionError("SHOPITEM sent to a bridge older than v11")
        self.shop_labels[transition] = (name, description)

    def shop_clear(self):
        self.shop_clears += 1
        self.shop_labels.clear()

    def message_box(self, lines):
        if not self.v11:
            raise AssertionError("MSGBOX sent to a bridge older than v11")
        if self.fail_box:
            raise GameNotReadyError("load screen")
        self.boxes.append(list(lines))

    def run_transition(self, transition):
        if not self.v10:
            raise BridgeProtocolError(f"'XST {transition}': ERR badrequest")
        self.transitions = getattr(self, "transitions", []) + [transition]
        self.bools[transition] = True  # AP transitions 20xxx set the bool of the same number

    def map_name(self):
        if not self.v10:
            raise BridgeProtocolError("'MAP': ERR badrequest")
        return self.map

    def key_presses(self):
        if not self.v10:
            raise BridgeProtocolError("'KEYS': ERR badrequest")
        presses, closes, self.presses, self.closes = self.presses, getattr(self, "closes", 0), 0, 0
        return presses, closes


class FakePipe:
    def __init__(self, reply=b"OK 1", fail_write=False):
        self.reply = reply
        self.fail_write = fail_write
        self.writes = []
        self.closed = False

    def write(self, value):
        if self.fail_write:
            raise OSError("disconnected")
        self.writes.append(value)

    def read(self, _size):
        return self.reply

    def close(self):
        self.closed = True


class TestBridgeConnection(unittest.TestCase):
    def test_reconnects_once_after_transport_failure(self):
        broken = FakePipe(fail_write=True)
        working = FakePipe(b"OK 1")
        pipes = iter((broken, working))
        bridge = BridgeConnection(opener=lambda *_args: next(pipes))

        self.assertTrue(bridge.get_bool(2505))
        self.assertTrue(broken.closed)
        self.assertEqual(working.writes, [b"GETB 2505"])

    def test_noworld_is_game_not_ready(self):
        bridge = BridgeConnection(opener=lambda *_args: FakePipe(b"ERR noworld"))

        with self.assertRaises(GameNotReadyError):
            bridge.get_int(2000)


class TestPredicates(unittest.TestCase):
    def test_parse_supported_forms(self):
        self.assertEqual(parse_plot_predicate("bool 2505 Prologue_Done").indices, (2505,))
        self.assertEqual(parse_plot_predicate("bool 3798 or 4133").indices, (3798, 4133))
        predicate = parse_plot_predicate("int 39 >= 20 (keeper note)")
        self.assertEqual((predicate.kind, predicate.indices, predicate.threshold), ("int", (39,), 20))

    def test_rejects_unsupported_forms(self):
        self.assertIsNone(parse_plot_predicate(None))
        self.assertIsNone(parse_plot_predicate("AP check flag (P8) at recruitment"))

    def test_evaluate_bool_or_and_int(self):
        bridge = FakeBridge(bools={4133: True}, ints={39: 20})

        self.assertTrue(parse_plot_predicate("bool 3798 or 4133").evaluate(bridge))
        self.assertTrue(parse_plot_predicate("int 39 >= 20").evaluate(bridge))
        self.assertFalse(parse_plot_predicate("int 39 >= 21").evaluate(bridge))

    def test_location_evaluation_uses_data_predicates(self):
        bridge = FakeBridge(bools={3569: True, 3798: True})

        result = evaluate_location_predicates(bridge)

        self.assertIn(LOCATION_NAME_TO_ID["Eden Prime: Beacon Vision"], result)
        self.assertIn(LOCATION_NAME_TO_ID["Feros: Confront Shiala"], result)
        self.assertNotIn(LOCATION_NAME_TO_ID["Citadel: Recruit Garrus"], result)

    def test_recruits_read_the_mod_flags_not_inparty(self):
        garrus, wrex = LOCATION_NAME_TO_ID["Citadel: Recruit Garrus"], LOCATION_NAME_TO_ID["Citadel: Recruit Wrex"]
        # InParty is also written by item delivery, so it must not count as a recruitment.
        self.assertFalse({garrus, wrex} & evaluate_location_predicates(FakeBridge(bools={3941: True, 3942: True})))
        # The mod's recruit transitions 20101/20102 set these flags.
        self.assertTrue({garrus, wrex} <= evaluate_location_predicates(FakeBridge(bools={20101: True, 20102: True})))


class TestReceivedItems(unittest.TestCase):
    def test_coordinate_item_mapping(self):
        self.assertEqual(coordinate_bool_for_item(ID_BASE + 0x10), 20000)
        self.assertEqual(coordinate_bool_for_item(ID_BASE + 0x15), 20005)
        self.assertIsNone(coordinate_bool_for_item(ID_BASE + 0x16))
        self.assertIsNone(coordinate_bool_for_item(ID_BASE + 0x00))

    def test_resumes_at_saved_index_and_advances_past_unsupported_items(self):
        bridge = FakeBridge(ints={2000: 1})
        items = [
            SimpleNamespace(item=ID_BASE + 0x10),
            SimpleNamespace(item=ID_BASE + 0x20),  # fragment: no game write
            SimpleNamespace(item=ID_BASE + 0x12),
        ]

        result = sync_received_items(bridge, items, logging.getLogger("test"))

        self.assertEqual(result, 3)
        self.assertEqual(bridge.bool_writes, [(20002, True)])
        self.assertEqual(bridge.transactions, [(1, "NONE"), (2, "FLAG 20002")])
        self.assertEqual(bridge.ints[2000], 3)
        self.assertEqual(bridge.int_writes, [])  # the index moves only inside TXN

    def test_older_save_replays_items_without_duplicate_progress(self):
        bridge = FakeBridge(ints={2000: 0})
        items = [SimpleNamespace(item=ID_BASE + 0x10), SimpleNamespace(item=ID_BASE + 0x11)]

        sync_received_items(bridge, items)
        sync_received_items(bridge, items)

        self.assertEqual(bridge.bool_writes, [(20000, True), (20001, True)])
        self.assertEqual(bridge.ints[2000], 2)

    def test_index_ahead_of_server_is_not_lowered(self):
        bridge = FakeBridge(ints={2000: 7})

        result = sync_received_items(bridge, [SimpleNamespace(item=ID_BASE + 0x10)])

        self.assertEqual(result, 7)
        self.assertEqual(bridge.bool_writes, [])
        self.assertEqual(bridge.int_writes, [])


class TestGearDelivery(unittest.TestCase):
    PISTOL = next(line for line in GEAR_LINES if line.category == "Pistol" and not line.spectre)
    SPECTRE = next(line for line in GEAR_LINES if line.spectre)

    def test_gear_levels_climb_from_one_to_ten_over_the_seed(self):
        self.assertEqual([gear_level(seen, 4) for seen in range(4)], [1, 4, 7, 10])
        self.assertEqual(gear_level(0, 1), 1)
        self.assertEqual(gear_level(50, 10), 10)

    def test_gear_spectre_upgrade_and_credits_reach_the_bridge(self):
        bridge = FakeBridge()
        items = [SimpleNamespace(item=ITEM_NAME_TO_ID[name]) for name in
                 (self.PISTOL.name, self.SPECTRE.name, UPGRADE, "Credits", "Omni-Gel")]

        sync_received_items(bridge, items, gear_total=3, seed_name="S")

        self.assertEqual(bridge.gives[0], (self.PISTOL.item_row, 1, self.PISTOL.manufacturer_row))
        self.assertEqual(bridge.gives[1], (self.SPECTRE.item_row, 10, self.SPECTRE.manufacturer_row))
        self.assertEqual(bridge.gives[2], (upgrade_row("S", 2), 10, -1))  # two of three gear lines seen
        self.assertEqual(bridge.resources, [(0, 5000), (2, 10)])
        self.assertEqual(bridge.ints[2000], 5)

    def test_replay_from_an_older_save_gives_the_same_items_at_the_same_levels(self):
        items = [SimpleNamespace(item=ITEM_NAME_TO_ID[name]) for name in (self.PISTOL.name, UPGRADE, self.PISTOL.name)]
        first, replay = FakeBridge(), FakeBridge()
        sync_received_items(first, items, gear_total=2, seed_name="S")
        sync_received_items(replay, items, gear_total=2, seed_name="S")
        self.assertEqual(first.gives, replay.gives)
        self.assertEqual([give[1] for give in first.gives], [1, 10, 10])

    def test_failed_delivery_resumes_at_the_failed_item(self):
        bridge = FakeBridge(fail_give_after=1)
        items = [SimpleNamespace(item=ITEM_NAME_TO_ID[self.PISTOL.name])] * 3

        with self.assertRaises(GameNotReadyError):
            sync_received_items(bridge, items, gear_total=3)
        self.assertEqual(bridge.ints[2000], 1)
        bridge.fail_give_after = None
        sync_received_items(bridge, items, gear_total=3)
        self.assertEqual([give[1] for give in bridge.gives], [1, 5, 10])


class TestLevelScaledRewards(unittest.TestCase):
    XP = SimpleNamespace(item=EXPERIENCE_ITEM_ID)
    CREDITS = SimpleNamespace(item=ITEM_NAME_TO_ID["Credits"])

    def test_credits_follow_the_squad_level(self):
        bridge = FakeBridge()
        sync_received_items(bridge, [self.CREDITS, self.CREDITS], squad_level=20)
        self.assertEqual(bridge.resources, [(0, CREDIT_REWARDS[19][3])] * 2)  # LargeReward at level 20
        unknown = FakeBridge()
        sync_received_items(unknown, [self.CREDITS], gear_total=3)
        self.assertEqual(unknown.resources, [(0, 500)])  # no squad level: gear level I

    def test_experience_is_a_bridge_none_and_granted_once_by_the_extras(self):
        bridge = FakeBridge()
        extras = FakeExtras(bridge)
        items = [self.XP, self.CREDITS, self.XP]
        delivered = sync_received_items(bridge, items, squad_level=10)
        self.assertEqual([delivery for _, delivery in bridge.transactions][0::2], ["NONE", "NONE"])
        self.assertEqual(sync_experience(extras, items, delivered, 10, 2), 0)
        self.assertEqual(extras.grants, [XP_REWARDS[9][3]] * 2)  # MajorQuest at level 10
        sync_experience(extras, items, delivered, 10, 2)
        self.assertEqual(len(extras.grants), 2)  # nothing granted twice

    def test_only_delivered_experience_is_granted(self):
        bridge = FakeBridge()
        extras = FakeExtras(bridge)
        items = [self.XP, self.XP, self.XP]
        sync_experience(extras, items, 1, 5, 3)
        self.assertEqual(extras.grants, [XP_REWARDS[4][4]])  # MainPlot at level 5, one delivered

    def test_waiting_experience_is_granted_at_the_later_level(self):
        bridge = FakeBridge()
        extras = FakeExtras(bridge)
        items = [self.XP]
        sync_received_items(bridge, items)  # no extras yet: the bridge's index moves, the grant waits
        sync_experience(extras, items, 1, 30, 0)
        self.assertEqual(extras.grants, [XP_REWARDS[29][1]])

    def test_gap_pauses_experience(self):
        bridge = FakeBridge()
        extras = FakeExtras(bridge)
        extras.experience_index = lambda: 1  # read before another save (index 0) was loaded
        waiting = sync_experience(extras, [self.XP, self.XP], 2, 1, 0)
        self.assertEqual(waiting, 1)
        self.assertEqual(extras.grants, [])

    def test_levels_outside_the_table_are_clamped(self):
        bridge = FakeBridge()
        sync_received_items(bridge, [self.CREDITS], squad_level=99)
        self.assertEqual(bridge.resources, [(0, CREDIT_REWARDS[-1][3])])

    def test_extras_requests(self):
        replies = {b"LEVEL": b"OK 12 34567", b"XPIDX": b"OK 3", b"XPTXN 1 2 3 400": b"OK applied"}

        class Pipe:
            def __init__(self):
                self.last = b""

            def write(self, data):
                self.last = data

            def read(self, _size):
                return replies[self.last]

            def close(self):
                pass

        extras = ExtrasConnection(opener=lambda *_args: Pipe())
        self.assertEqual(extras.squad_level(), 12)
        self.assertEqual(extras.experience_index(), 3)
        self.assertTrue(extras.experience_transaction((1, 2), 3, 400))


class TestContextPolling(unittest.IsolatedAsyncioTestCase):
    async def test_poll_sends_new_checks_and_goal(self):
        bridge = FakeBridge(bools={3569: True, 6530: True}, ints={2000: 0})
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.infinite_sprint, ctx.notices, ctx.notices_enabled, ctx._fragments_noticed = False, collections.deque(), True, None
        ctx.sends_pending = collections.deque()
        ctx.locations_info = {}
        ctx.items_received = []
        ctx.checked_locations = set()
        ctx.locations_checked = set()
        ctx.server_locations = set()
        ctx.finished_game = False
        ctx.slot = 1
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        ctx.fragments_required = None
        sent = []

        async def capture(messages):
            sent.extend(messages)

        ctx.send_msgs = capture
        await ctx.poll_game()

        beacon = LOCATION_NAME_TO_ID["Eden Prime: Beacon Vision"]
        location_packets = [packet for packet in sent if packet["cmd"] == "LocationChecks"]
        goal_packets = [packet for packet in sent if packet["cmd"] == "StatusUpdate"]
        self.assertIn(beacon, location_packets[0]["locations"])
        self.assertEqual(len(goal_packets), 1)
        self.assertTrue(ctx.finished_game)

        await ctx.poll_game()
        self.assertEqual(len([packet for packet in sent if packet["cmd"] == "StatusUpdate"]), 1)

    async def test_poll_without_server_slot_neither_writes_nor_sends(self):
        bridge = FakeBridge(bools={3569: True, 6530: True}, ints={2000: 0})
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.infinite_sprint, ctx.notices, ctx.notices_enabled, ctx._fragments_noticed = False, collections.deque(), True, None
        ctx.sends_pending = collections.deque()
        ctx.locations_info = {}
        ctx.items_received = [SimpleNamespace(item=ID_BASE + 0x13)]
        ctx.checked_locations = set()
        ctx.locations_checked = set()
        ctx.server_locations = set()
        ctx.finished_game = False
        ctx.slot = None
        ctx.server = None
        sent = []

        async def capture(messages):
            sent.extend(messages)

        ctx.send_msgs = capture
        await ctx.poll_game()

        self.assertEqual(sent, [])
        self.assertEqual(bridge.bool_writes, [])
        self.assertEqual(bridge.int_writes, [])
        self.assertFalse(ctx.finished_game)
        self.assertEqual(ctx.locations_checked, set())


class TestSquadAndIlos(unittest.TestCase):
    def test_squad_items_map_to_inparty_bools(self):
        self.assertEqual(plot_bool_for_item(ITEM_NAME_TO_ID["Kaidan Alenko"]), 3939)
        self.assertEqual(plot_bool_for_item(ITEM_NAME_TO_ID["Liara T'Soni"]), 3943)
        self.assertEqual(plot_bool_for_item(ITEM_NAME_TO_ID["Tali'Zorah nar Rayya"]), 3944)
        self.assertEqual(plot_bool_for_item(ITEM_NAME_TO_ID["Feros Coordinates"]), 20002)
        self.assertIsNone(plot_bool_for_item(FRAGMENT_ITEM_ID))

    def test_received_squad_item_sets_inparty(self):
        bridge = FakeBridge(ints={2000: 0})
        sync_received_items(bridge, [SimpleNamespace(item=ITEM_NAME_TO_ID["Liara T'Soni"])])
        self.assertEqual(bridge.bool_writes, [(3943, True)])
        self.assertEqual(bridge.ints[2000], 1)

    def test_ilos_gate(self):
        fragment = SimpleNamespace(item=FRAGMENT_ITEM_ID)
        other = SimpleNamespace(item=ITEM_NAME_TO_ID["Feros Coordinates"])
        cases = [
            ("unknown requirement", [fragment] * 4, None, True, False),
            ("too few fragments", [fragment, other], 2, True, False),
            ("virmire not done", [fragment] * 2, 2, False, False),
            ("gate met", [fragment, other, fragment], 2, True, True),
        ]
        for name, items, required, virmire, opens in cases:
            with self.subTest(name):
                bridge = FakeBridge(bools={2509: virmire})
                self.assertEqual(sync_ilos_gate(bridge, items, required), opens)
                self.assertEqual(bridge.bool_writes, [(ILOS_OPEN_PLOT_BOOL, True)] if opens else [])

    def test_human_squad_joins_until_the_virmire_choice(self):
        bridge = FakeBridge(bools={3939: False, 3940: True})
        self.assertTrue(sync_human_squad(bridge))
        self.assertEqual(bridge.bool_writes, [(3939, True)])
        self.assertFalse(sync_human_squad(bridge))  # already in the party: nothing written
        for survivor in (3827, 3828):
            with self.subTest(survivor=survivor):
                after = FakeBridge(bools={3939: False, 3940: False, survivor: True})
                self.assertFalse(sync_human_squad(after))
                self.assertEqual(after.bool_writes, [])

    def test_two_squadmates_bool_follows_the_party(self):
        cases = [((), False), ((3941,), False), ((3941, 3943), True), ((3939, 3940, 3944), True)]
        for in_party, value in cases:
            with self.subTest(in_party=in_party):
                bridge = FakeBridge(bools={index: index in in_party for index in range(3939, 3945)})
                self.assertEqual(sync_two_squadmates(bridge, bridge), value)  # written only when it changes
                self.assertEqual(bridge.bool_writes, [(TWO_SQUADMATES_PLOT_BOOL, True)] if value else [])
        bridge = FakeBridge(bools={3941: True, TWO_SQUADMATES_PLOT_BOOL: True})  # back to one (an older save)
        self.assertTrue(sync_two_squadmates(bridge, bridge))
        self.assertEqual(bridge.bool_writes, [(TWO_SQUADMATES_PLOT_BOOL, False)])

    def test_virmire_transmission_runs_once_the_coordinates_arrive(self):
        cases = [
            ("no coordinates", {20004: False}, False),
            ("coordinates", {20004: True}, True),
            ("already open (the game's own transmission)", {20004: True, 4104: True}, False),
            ("virmire done", {20004: True, 2509: True}, False),
        ]
        for name, bools, runs in cases:
            with self.subTest(name):
                bridge = FakeBridge(bools=bools)
                self.assertEqual(sync_virmire_open(bridge), runs)
                self.assertEqual(getattr(bridge, "transitions", []), [VIRMIRE_TRANSMISSION_TRANSITION] if runs else [])
                self.assertEqual(bridge.bool_writes, [])

    def test_ilos_gate_does_not_rewrite_when_open(self):
        bridge = FakeBridge(bools={2509: True, ILOS_OPEN_PLOT_BOOL: True})
        self.assertFalse(sync_ilos_gate(bridge, [SimpleNamespace(item=FRAGMENT_ITEM_ID)], 1))
        self.assertEqual(bridge.bool_writes, [])


class TestMultiBoolPredicate(unittest.TestCase):
    def test_any_of_many_bools(self):
        predicate = parse_plot_predicate("bool 10 or 20 or 30")
        self.assertEqual(predicate.indices, (10, 20, 30))
        self.assertTrue(predicate.evaluate(FakeBridge(bools={30: True})))
        self.assertEqual(parse_plot_predicate("bool 3941 InParty.Turian (note)").indices, (3941,))


class TestNotices(unittest.TestCase):
    def test_notice_text_is_ascii_single_line(self):
        self.assertEqual(notice_text("Received Tali'Zorah from José"), "Received Tali'Zorah from Jose")
        self.assertEqual(notice_text("a\nb\tc"), "a b c")
        self.assertEqual(notice_text("x" * 500), "x" * 500)  # never cut: notice_lines wraps it
        self.assertEqual(notice_text("☃"), "?")

    def test_notice_lines_wrap_without_dropping_text(self):
        text = "Received Aldrin Labs Onyx Light Armor (Turian) from Nic_Elden"
        lines = notice_lines(text)
        self.assertTrue(all(len(line) <= 18 for line in lines))
        self.assertEqual(" ".join(lines), text)
        self.assertEqual(notice_lines("Found Medi-Gel"), ["Found Medi-Gel"])
        self.assertEqual(notice_lines("Hint: " + "x" * 40), ["Hint:", "x" * 18, "x" * 18, "x" * 4])
        self.assertEqual(notice_lines("☃"), ["?"])

    def test_deliver_keeps_the_rest_queued(self):
        bridge = FakeBridge()
        notices = collections.deque(f"n{i}" for i in range(5))
        deliver_notices(bridge, notices, limit=3)
        self.assertEqual(bridge.notes, ["n0", "n1", "n2"])
        self.assertEqual(list(notices), ["n3", "n4"])
        bridge.fail_note = True
        with self.assertRaises(GameNotReadyError):
            deliver_notices(bridge, notices)
        self.assertEqual(list(notices), ["n3", "n4"])  # nothing lost on a load screen

    def test_item_send_texts(self):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.slot, ctx.slot_info = 1, {}
        ctx.player_names = {1: "Shepard", 2: "Link"}
        ctx.item_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"item{code}@{slot}")
        self.assertEqual(ctx.item_send_notice(SimpleNamespace(item=5, player=2), 1), "Received item5@1 from Link")
        self.assertEqual(ctx.item_send_notice(SimpleNamespace(item=5, player=1), 2), "Sent item5@2 to Link")
        self.assertEqual(ctx.item_send_notice(SimpleNamespace(item=5, player=1), 1), "Found item5@1")


class TestNoticeLevels(unittest.TestCase):
    def make_ctx(self):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.slot, ctx.slot_info = 1, {}
        ctx.player_names = {1: "Shepard", 2: "Link"}
        ctx.item_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"item{code}")
        ctx.location_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"loc{code}")
        ctx.notices, ctx.sends_pending = collections.deque(), collections.deque()
        ctx.locations_info = {}
        ctx.notices_enabled, ctx.notice_level = True, "all"
        return ctx

    @staticmethod
    def send(ctx, receiving, sender, item=5):
        ME1Context.notice_for_print(ctx, {"type": "ItemSend", "receiving": receiving,
                                          "item": SimpleNamespace(item=item, player=sender)})

    def test_sends_are_batched_per_poll(self):
        ctx = self.make_ctx()
        self.send(ctx, 2, 1)
        self.send(ctx, 2, 1)
        self.send(ctx, 1, 2, item=7)
        self.assertEqual(list(ctx.notices), ["Received item7", "from Link"])  # items for this slot: right away
        ctx.notices.clear()
        ctx._flush_sends()
        self.assertEqual(list(ctx.notices), ["Sent 2 items"])
        ctx.notices.clear()
        self.send(ctx, 2, 1)
        ctx._flush_sends()
        self.assertEqual(list(ctx.notices), ["Sent item5 to Link"])
        self.assertFalse(ctx.sends_pending)

    def test_levels(self):
        ctx = self.make_ctx()
        ctx.notify("Normandy: 3 checks in logic", important=False)
        ctx.notify("Found Medi-Gel")
        self.assertEqual(list(ctx.notices), ["Normandy: 3 checks", "in logic", "Found Medi-Gel"])
        ctx.notices.clear()
        ctx.notice_level = "important"
        ctx.notify("Normandy: 3 checks in logic", important=False)
        self.send(ctx, 2, 1)
        ctx.notify("Found Medi-Gel")
        self.assertEqual((list(ctx.notices), list(ctx.sends_pending)), (["Found Medi-Gel"], []))
        ctx.notices.clear()
        ctx.notice_level = "off"
        ctx.notify("Found Medi-Gel")
        self.assertFalse(ctx.notices)

    def test_notices_command(self):
        ctx = self.make_ctx()
        ctx.notices.append("old")
        processor = ME1Context.command_processor(ctx)
        outputs = []
        processor.output = outputs.append
        self.assertTrue(processor._cmd_notices("important"))
        self.assertEqual(ctx.notice_level, "important")
        self.assertEqual(list(ctx.notices), ["old"])
        self.assertTrue(processor._cmd_notices("OFF"))
        self.assertEqual((ctx.notice_level, ctx.notices_reset), ("off", True))  # cleared by the next poll
        self.assertFalse(processor._cmd_notices("loud"))
        self.assertEqual(ctx.notice_level, "off")
        self.assertTrue(processor._cmd_notices())
        self.assertEqual(outputs[-1], "In-game notices: off")


class TestQualityOfLifeSync(unittest.TestCase):
    def make_ctx(self, bridge, items, infinite_sprint):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.items_received = items
        ctx.slot = 1
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        ctx.fragments_required = 4
        ctx.gear_total = 0
        ctx.unlock_containers = False
        ctx.infinite_sprint, ctx.notices, ctx.notices_enabled, ctx._fragments_noticed = infinite_sprint, collections.deque(), True, None
        ctx.sends_pending = collections.deque()
        ctx.locations_info = {}
        return ctx

    def test_sprint_option_is_mirrored_both_ways(self):
        bridge = FakeBridge(ints={2000: 0})
        self.make_ctx(bridge, [], True)._read_game_state(True)
        self.assertTrue(bridge.bools[INFINITE_SPRINT_PLOT_BOOL])
        self.make_ctx(bridge, [], False)._read_game_state(True)
        self.assertFalse(bridge.bools[INFINITE_SPRINT_PLOT_BOOL])

    def test_shop_slot_count_is_written_once_connected(self):
        bridge = FakeBridge(ints={2000: 0, SHOP_SLOTS_PLOT_INT: 0})
        ctx = self.make_ctx(bridge, [], False)
        ctx._read_game_state(True)
        self.assertEqual(bridge.ints[SHOP_SLOTS_PLOT_INT], 0)  # no slot data yet: the mod shows every slot
        ctx.shop_slots = 3
        ctx._read_game_state(True)
        self.assertEqual(bridge.ints[SHOP_SLOTS_PLOT_INT], 4)

    def test_fragment_notice_only_for_new_fragments(self):
        bridge = FakeBridge(ints={2000: 0})
        items = [SimpleNamespace(item=FRAGMENT_ITEM_ID)]
        ctx = self.make_ctx(bridge, items, False)
        ctx._read_game_state(True)  # first poll after connecting: baseline, no notice
        self.assertEqual(bridge.notes, [])
        items.append(SimpleNamespace(item=FRAGMENT_ITEM_ID))
        ctx._read_game_state(True)
        self.assertEqual(bridge.notes, ["Fragments: 2/4"])


class TestInGameTracker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ..tracker import Tracker
        cls.tracker = Tracker({"required_squad_size": 2})

    def make_ctx(self, bridge):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.items_received = []
        ctx.slot = 1
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        ctx.fragments_required = 4
        ctx.gear_total = 0
        ctx.unlock_containers = False
        ctx.infinite_sprint, ctx.notices, ctx.notices_enabled, ctx._fragments_noticed = False, collections.deque(), True, None
        ctx.sends_pending = collections.deque()
        ctx.locations_info = {}
        ctx.tracker = self.tracker
        ctx.missing_locations = set(LOCATION_NAME_TO_ID.values())
        ctx.locations_checked = set()
        return ctx

    def test_landing_notice_once_per_new_area(self):
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge)
        ctx._tracker_notices([])
        landed = len(ctx.notices)
        self.assertRegex(" ".join(ctx.notices), r"^Normandy: \d+ checks? in logic")  # one notice, wrapped into lines
        ctx._tracker_notices([])
        self.assertEqual(len(ctx.notices), landed)  # same area: nothing new
        bridge.map = "BIOA_WAR00"
        ctx._tracker_notices([])
        self.assertRegex(" ".join(list(ctx.notices)[landed:]), r"^Feros: 0 checks in logic, \d+ locked$")

    def test_hotkey_shows_the_summary_box(self):
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge)
        ctx._last_area = "Normandy"
        bridge.presses = 1
        ctx._tracker_notices([])
        ctx._deliver_summary()
        for _ in range(10):  # F7 on an open page shows the next one, until the last
            bridge.closes = 1
            ctx._tracker_notices([])
            ctx._deliver_summary()
        boxes = bridge.boxes
        self.assertTrue(boxes[0][0].startswith("Archipelago"))
        self.assertTrue(boxes[0][1].startswith("In logic: "))
        text = " ".join(line for box in boxes for line in box)
        self.assertIn("Locked: ", text)
        self.assertNotIn("planets)", text)  # the box lists every uncharted planet in logic
        self.assertTrue(all(len(box) <= 7 and all(len(line) <= 56 for line in box) for box in boxes))
        self.assertFalse(ctx.notices)
        if len(boxes) > 1:
            self.assertEqual(boxes[0][0], f"Archipelago (1/{len(boxes)})")
            self.assertEqual(boxes[0][-1], "F7: next page")
            self.assertNotEqual(boxes[-1][-1], "F7: next page")
        bridge.closes = 1  # F7 on the last page only closes it
        ctx._tracker_notices([])
        ctx._deliver_summary()
        self.assertEqual(len(bridge.boxes), len(boxes))

    def test_summary_box_waits_for_a_load_screen(self):
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge)
        ctx._last_area = "Normandy"
        bridge.presses, bridge.fail_box = 1, True
        ctx._tracker_notices([])
        ctx._deliver_summary()
        self.assertFalse(bridge.boxes)
        bridge.fail_box = False
        ctx._deliver_summary()
        self.assertEqual(len(bridge.boxes), 1)

    def test_older_bridge_sends_the_summary_as_notices(self):
        bridge = FakeBridge(ints={2000: 0})
        bridge.v11 = False
        ctx = self.make_ctx(bridge)
        ctx._last_area = "Normandy"
        bridge.presses = 1
        ctx._tracker_notices([])
        ctx._deliver_summary()
        self.assertFalse(bridge.boxes)
        text = " ".join(ctx.notices)
        self.assertTrue(text.startswith("In logic: "))
        self.assertIn(" Locked: ", text)
        self.assertTrue(all(len(line) <= 18 for line in ctx.notices))

    def test_notices_command_clears_on_the_poll_thread(self):
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge)
        ctx.notices.extend(["a", "b"])
        ctx.sends_pending.append("Sent x to y")
        processor = object.__new__(ME1CommandProcessor)
        processor.ctx = ctx
        processor.output = lambda text: None
        processor._cmd_notices("off")
        self.assertEqual(list(ctx.notices), ["a", "b"])  # untouched until the poll
        ctx._read_game_state(True)
        self.assertFalse(ctx.notices)
        self.assertFalse(ctx.sends_pending)
        self.assertFalse(ctx.notices_reset)

    def test_summary_too_long_for_the_box_goes_to_notices_whole(self):
        bridge = FakeBridge()
        ctx = self.make_ctx(bridge)
        ctx.summary_pending = (["Archipelago"] + ["x" * 90] * 100, ["x" * 90] * 100)
        ctx._deliver_summary()
        self.assertFalse(bridge.boxes)
        self.assertEqual("".join(ctx.notices), "x" * 9000)  # nothing cut

    def test_version_of_an_older_bridge(self):
        bridge = BridgeConnection(opener=lambda *args: FakePipe(b"ERR badrequest"))
        self.assertEqual(bridge.version(), 10)
        bridge = BridgeConnection(opener=lambda *args: FakePipe(b"OK 11"))
        self.assertEqual(bridge.version(), 11)

    def test_message_box_request(self):
        pipe = FakePipe(b"OK")
        bridge = BridgeConnection(opener=lambda *args: pipe)
        bridge.message_box(["Archipelago", "In logic: Feros 3", "Caf\u00e9\tok"])
        self.assertEqual(pipe.writes[-1], b"MSGBOX 0 Archipelago\nIn logic: Feros 3\nCafe ok")

    def test_older_bridge_turns_the_tracker_off(self):
        bridge = FakeBridge(ints={2000: 0})
        bridge.v10 = False
        ctx = self.make_ctx(bridge)
        ctx.tracker_enabled = True
        ctx._read_game_state(True)
        self.assertFalse(ctx.tracker_enabled)

    def test_hint_notice(self):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.slot = 1
        ctx.slot_info = {}
        ctx.player_names = {1: "Shepard", 2: "Link"}
        ctx.item_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"item{code}")
        ctx.location_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"loc{code}")
        self.assertEqual(ctx.hint_notice(SimpleNamespace(item=5, location=9, player=2), 1), "Hint: your item5 is at Link's loc9")
        self.assertEqual(ctx.hint_notice(SimpleNamespace(item=5, location=9, player=1), 1), "Hint: your item5 is at loc9")
        self.assertEqual(ctx.hint_notice(SimpleNamespace(item=5, location=9, player=1), 2), "Hint: Link's item5 is at your loc9")


class TestJournalSync(unittest.TestCase):
    def make_ctx(self, bridge, items=(), tracker=None):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.items_received = list(items)
        ctx.fragments_required = 4
        ctx.mako_shuffled = True
        ctx.tracker = tracker
        ctx.missing_locations = set(LOCATION_NAME_TO_ID.values())
        ctx.locations_checked = set()
        return ctx

    def test_values_and_activation_through_the_transition(self):
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge, [SimpleNamespace(item=FRAGMENT_ITEM_ID)] * 2)
        ctx._sync_journal(ctx.items_received)
        self.assertEqual((bridge.ints[2002], bridge.ints[2003]), (2, 4))
        self.assertFalse(bridge.bools.get(20193, False))  # Mako shuffled and not received
        self.assertEqual(bridge.transitions, [20192])
        writes = len(bridge.int_writes) + len(bridge.bool_writes)
        ctx._sync_journal(ctx.items_received)
        self.assertEqual(len(bridge.int_writes) + len(bridge.bool_writes), writes)  # nothing changed: no writes
        self.assertEqual(bridge.transitions, [20192])
        bridge.ints[2002] = 0  # an older save was loaded
        ctx._sync_journal(ctx.items_received)
        self.assertEqual(bridge.ints[2002], 2)

    def test_older_bridge_sets_the_bool(self):
        bridge = FakeBridge(ints={2000: 0})
        bridge.v10 = False
        self.make_ctx(bridge)._sync_journal([])
        self.assertTrue(bridge.bools[20192])

    def test_regions_in_logic(self):
        from ..tracker import Tracker
        bridge = FakeBridge(ints={2000: 0})
        ctx = self.make_ctx(bridge, tracker=Tracker({"required_squad_size": 2, "mako_shuffled": True}))
        ctx._sync_journal([])
        self.assertTrue(bridge.bools[20800])  # Normandy (data.Region index 0)
        self.assertFalse(bridge.bools.get(20804, False))  # Feros: no coordinates yet
        self.assertTrue(bridge.bools[20900 + 3])  # galaxy-map marker area 3 (Feros): locked checks
        self.assertFalse(bridge.bools.get(20850 + 3, False))  # ... none in logic
        self.assertTrue(bridge.bools[20850 + 0])  # Citadel: checks in logic

    def test_marker_areas_match_the_mod(self):
        from ..tracker import MARKER_AREAS, UNCHARTED_PLANETS
        self.assertEqual(len(MARKER_AREAS), 40)  # tools/le1mod/Markers.cs Areas
        self.assertEqual(MARKER_AREAS[:8], ("Citadel", "Eden Prime", "Therum", "Feros", "Noveria", "Virmire", "Asteroid X57", "Ilos"))
        self.assertEqual(MARKER_AREAS[33], "Chasca")
        self.assertEqual(set(MARKER_AREAS[8:]), set(UNCHARTED_PLANETS))


class TestShopLabels(unittest.TestCase):
    def make_ctx(self, bridge):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = bridge
        ctx.slot = 1
        ctx.slot_info = {}
        ctx.player_names = {1: "Shepard", 2: "Link"}
        ctx.item_names = SimpleNamespace(lookup_in_slot=lambda code, slot: f"item{code}")
        ctx.locations_info = {}
        return ctx

    def test_every_shop_slot_maps_to_its_transition(self):
        self.assertEqual(sorted(SHOP_TRANSITIONS.values()), list(range(20700, 20750)))
        self.assertEqual(SHOP_TRANSITIONS[LOCATION_NAME_TO_ID["Normandy: Requisitions Officer Slot 1"]], 20700)
        self.assertEqual(SHOP_TRANSITIONS[LOCATION_NAME_TO_ID["Virmire: Salarian Camp Slot 5"]], 20749)

    def test_labels_for_scouted_slots(self):
        bridge = FakeBridge()
        ctx = self.make_ctx(bridge)
        own = LOCATION_NAME_TO_ID["Normandy: Requisitions Officer Slot 1"]
        other = LOCATION_NAME_TO_ID["Feros: Zhu's Hope Merchant Slot 2"]
        ctx.locations_info = {own: SimpleNamespace(item=7, location=own, player=1, flags=1),
                              other: SimpleNamespace(item=9, location=other, player=2, flags=0)}
        ctx._send_shop_labels()
        self.assertTrue(ctx.shop_labels_sent)
        self.assertEqual(bridge.shop_clears, 1)
        self.assertEqual(bridge.shop_labels, {
            20700: ("item7", "Archipelago item for you: item7. Progression."),
            SHOP_TRANSITIONS[other]: ("Link's item9", "Archipelago item for Link: item9. Filler."),
        })

    def test_older_bridge_gets_no_labels(self):
        bridge = FakeBridge()
        bridge.v11 = False
        ctx = self.make_ctx(bridge)
        loc = LOCATION_NAME_TO_ID["Normandy: Requisitions Officer Slot 1"]
        ctx.locations_info = {loc: SimpleNamespace(item=7, location=loc, player=1, flags=2)}
        ctx._send_shop_labels()
        self.assertTrue(ctx.shop_labels_sent)
        self.assertFalse(bridge.shop_labels)

    def test_shop_item_request(self):
        pipe = FakePipe(b"OK")
        bridge = BridgeConnection(opener=lambda *args: pipe)
        bridge.shop_item(20703, "Link's Hook|shot", "Archipelago item for Link: Hookshot. Useful.")
        self.assertEqual(pipe.writes[-1], b"SHOPITEM 20703 Link's Hook/shot|Archipelago item for Link: Hookshot. Useful.")


class FakeSave:
    def __init__(self, ints=None, bools=None):
        self.ints, self.bools = dict(ints or {}), dict(bools or {})

    def identity(self):
        return self.ints.get(2004, 0), self.ints.get(2005, 0)


class FakeGame:
    """LE1APBridge v13 behind a fake pipe, following tools/le1apbridge/LE1APBridge/Plot.hpp (natively tested by
    plot_test.cpp). `timeouts` makes the next N TXNs answer ERR timeout and stay queued: like the bridge's, they run
    later, before the next request (possibly after `load` switched the save). `lost_replies` makes the next N TXNs run
    and then breaks the pipe before the reply."""

    def __init__(self, save=None, *, version=13, mod="0.2.12"):
        self.save = save or FakeSave()
        self.version, self.mod = version, mod
        self.inventory_full = False  # v13: a GIVE answers ERR full and leaves the index
        self.grants, self.requests, self.pending = [], [], []
        self.timeouts = self.lost_replies = 0
        self.session = None

    def load(self, save):
        self.save = save

    def run(self, request, session):
        save, (op, *args) = self.save, request.split()
        ints, bools = save.ints, save.bools
        if op == "GETI":
            return f"OK {ints.get(int(args[0]), 0)}"
        if op == "GETB":
            return f"OK {int(bools.get(int(args[0]), False))}"
        if op == "NONCE":
            ints[2006] = int(args[0])
            return "OK"
        if op == "IDSET":
            seed, slot, nonce = map(int, args)
            if save.identity() == (seed, slot):
                return "OK"
            if save.identity() != (0, 0):
                return "ERR bound"
            if ints.get(2006, 0) != nonce:
                return "ERR changed"
            ints[2004], ints[2005] = seed, slot
            return "OK"
        if op == "SNAP":
            parts = list(map(str, save.identity()))
            for spec in args:
                if spec[0] == "b":
                    first, count = map(int, spec[1:].split("+"))
                    parts.append("".join("0123456789abcdef"[sum(bools.get(first + i + b, False) << b for b in range(4)
                                                                  if i + b < count)] for i in range(0, count, 4)))
                else:
                    parts.append(str(ints.get(int(spec[1:]), 0)))
            return "OK " + " ".join(parts)
        if op == "TXN":
            if (int(args[0]), int(args[1])) != save.identity():
                return "ERR identity"
            index, current = int(args[2]), ints.get(2000, 0)
            if current > index:
                return "OK done"
            if current < index:
                return f"ERR gap {current}"
            if self.inventory_full and args[3] == "GIVE":
                return "ERR full"
            self.grants.append((index, " ".join(args[3:])))
            ints[2000] = index + 1
            return "OK applied"
        if op in {"SETB", "SETI", "XST"}:
            if session is None:
                return "ERR nosession"
            if session != save.identity():
                return "ERR identity"
            (bools if op != "SETI" else ints)[int(args[0])] = True if op == "XST" else int(args[1])
            return "OK"
        return "OK"

    def handle(self, request):
        for queued, session in self.pending:  # requests that timed out still run on the game thread
            self.run(queued, session)
        self.pending.clear()
        self.requests.append(request)
        op, *args = request.split()
        if op == "PING":
            return "OK pong" if self.version < 12 else f"OK pong {self.version}"
        if op == "MODVER":
            return f"OK {self.mod}" if self.mod else "ERR nomod"
        if op == "SESSION":
            self.session = (int(args[0]), int(args[1]))
            return "OK"
        if self.timeouts and op == "TXN":
            self.timeouts -= 1
            self.pending.append((request, self.session))
            return "ERR timeout"
        reply = self.run(request, self.session)
        if self.lost_replies and op == "TXN":
            self.lost_replies -= 1
            return None
        return reply


class FakeGamePipe:
    def __init__(self, game):
        self.game, self.reply = game, None
        game.session = None  # the bridge forgets the session of a disconnected client

    def write(self, value):
        self.reply = self.game.handle(value.decode("ascii"))

    def read(self, _size):
        if self.reply is None:
            raise OSError("pipe broken before the reply")
        return self.reply.encode("ascii")

    def close(self):
        pass


def game_bridge(game):
    return BridgeConnection(opener=lambda *_args: FakeGamePipe(game))


def bound(**ints):
    return FakeSave({2004: ID[0], 2005: ID[1], **{int(k[1:]): v for k, v in ints.items()}})


class TestIdempotentDelivery(unittest.TestCase):
    ITEMS = [SimpleNamespace(item=ID_BASE + 0x10), SimpleNamespace(item=ITEM_NAME_TO_ID["Credits"]),
             SimpleNamespace(item=ID_BASE + 0x11)]

    def test_timeout_then_retry_grants_once(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        game.timeouts = 1  # TXN 0 answers ERR timeout and runs later, as the bridge's queued request does
        with self.assertRaises(GameNotReadyError):
            sync_received_items(bridge, self.ITEMS)
        self.assertEqual(sync_received_items(bridge, self.ITEMS), 3)  # next poll: rereads the index, skips TXN 0
        self.assertEqual([index for index, _ in game.grants], [0, 1, 2])

    def test_timed_out_grant_never_lands_in_another_save(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        game.timeouts = 1
        with self.assertRaises(GameNotReadyError):
            sync_received_items(bridge, self.ITEMS)
        other = FakeSave({2004: 99, 2005: 1})
        game.load(other)  # the player loads another seed's save before the queued TXN runs
        with self.assertRaises(IdentityMismatchError):
            sync_received_items(bridge, self.ITEMS)
        self.assertEqual((game.grants, other.ints.get(2000, 0)), ([], 0))

    def test_lost_reply_is_resent_once_and_skipped(self):
        game = FakeGame(bound())
        game.lost_replies = 1  # TXN 0 lands, the pipe breaks; the client reconnects and sends it again: "done"
        self.assertEqual(sync_received_items(game_bridge(game), self.ITEMS), 3)
        self.assertEqual([index for index, _ in game.grants], [0, 1, 2])
        self.assertEqual(sum(r.startswith(f"TXN {ID[0]} {ID[1]} 0 ") for r in game.requests), 2)

    def test_duplicate_index_is_done_not_granted(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        self.assertTrue(bridge.transaction(ID, 0, "FLAG 20000"))
        self.assertFalse(bridge.transaction(ID, 0, "FLAG 20000"))
        self.assertEqual(len(game.grants), 1)

    def test_gap_pauses_delivery(self):
        with self.assertRaises(DeliveryGapError):
            game_bridge(FakeGame(bound())).transaction(ID, 2, "NONE")

        class GapBridge(FakeBridge):
            def transaction(self, identity, index, delivery):
                raise DeliveryGapError("ERR gap 0")
        self.assertEqual(sync_received_items(GapBridge(ints={2000: 1}), self.ITEMS), 1)  # resumes from the save next poll

    def test_notices_are_not_resent(self):
        pipes = [FakePipe(fail_write=True), FakePipe(b"OK")]
        bridge = BridgeConnection(opener=lambda *_args: pipes.pop(0))
        with self.assertRaises(BridgeError):
            bridge.note("Found Medi-Gel")
        self.assertEqual(len(pipes), 1)  # no second attempt

    def test_reads_are_retried(self):
        pipes = [FakePipe(fail_write=True), FakePipe(b"OK 0")]
        self.assertFalse(BridgeConnection(opener=lambda *_args: pipes.pop(0)).get_bool(2505))

    def test_session_is_announced_on_every_connection(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        bridge.set_session(ID)
        bridge.set_bool(20190, True)
        bridge.close()  # e.g. the pipe broke: the next request reconnects
        bridge.set_bool(20191, True)
        self.assertEqual(game.requests.count(f"SESSION {ID[0]} {ID[1]}"), 2)
        self.assertTrue(game.save.bools[20190] and game.save.bools[20191])
        game.load(FakeSave({2004: 99, 2005: 1}))
        with self.assertRaises(IdentityMismatchError):  # writes never land in another seed's save
            bridge.set_int(2001, 4)


class TestInventoryFull(unittest.TestCase):
    """v13: a GIVE into a full inventory (300 items) waits; strict order, so later items wait behind it."""
    GEAR = GEAR_LINES[0].name
    ITEMS = [SimpleNamespace(item=ID_BASE + 0x10), SimpleNamespace(item=ITEM_NAME_TO_ID[GEAR_LINES[0].name]),
             SimpleNamespace(item=ITEM_NAME_TO_ID["Credits"]), SimpleNamespace(item=ID_BASE + 0x11)]

    def test_full_reply_is_its_own_error(self):
        bridge = BridgeConnection(opener=lambda *_args: FakePipe(b"ERR full"))
        with self.assertRaises(InventoryFullError):
            bridge.transaction(ID, 0, "GIVE 1 1 -1")
        self.assertFalse(issubclass(InventoryFullError, BridgeProtocolError))  # not a broken bridge

    def test_full_then_room_delivers_once_in_order(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        game.inventory_full = True
        pending = []
        self.assertEqual(sync_received_items(bridge, self.ITEMS, on_full=pending.append), 1)
        self.assertEqual(sync_received_items(bridge, self.ITEMS, on_full=pending.append), 1)  # next poll: still full
        self.assertEqual(pending, [3, 3])  # the gear item and the two behind it
        self.assertEqual([index for index, _ in game.grants], [0])
        game.inventory_full = False  # the player sold something
        self.assertEqual(sync_received_items(bridge, self.ITEMS, on_full=pending.append), 4)
        self.assertEqual([index for index, _ in game.grants], [0, 1, 2, 3])
        self.assertEqual(sync_received_items(bridge, self.ITEMS), 4)
        self.assertEqual(len(game.grants), 4)  # nothing twice

    def test_reload_while_pending(self):
        game = FakeGame(bound())
        bridge = game_bridge(game)
        game.inventory_full = True
        sync_received_items(bridge, self.ITEMS)
        older = FakeSave(dict(game.save.ints))  # a save made while the gear item was pending
        game.inventory_full = False
        self.assertEqual(sync_received_items(bridge, self.ITEMS), 4)
        game.load(older)  # reload it: its index is still 1, so the pending items arrive again in that save, once
        self.assertEqual(sync_received_items(bridge, self.ITEMS), 4)
        self.assertEqual([index for index, _ in game.grants], [0, 1, 2, 3, 1, 2, 3])
        self.assertEqual(older.ints[2000], 4)

    def make_ctx(self, bridge, items):
        ctx = TestQualityOfLifeSync.make_ctx(TestQualityOfLifeSync(), bridge, items, False)
        ctx.inventory_full_pending = 0
        return ctx

    def test_notice_once_per_pending_count_then_cleared(self):
        bridge = FakeBridge(ints={2000: 0})
        items = list(self.ITEMS[:2])
        ctx = self.make_ctx(bridge, items)

        def shown():
            for _ in range(5):  # the HUD takes a few lines per poll
                ctx._read_game_state(True)
            text = " ".join(bridge.notes)
            bridge.notes.clear()
            return text

        bridge.inventory_full = True
        self.assertEqual(shown(), "Inventory full 1 items waiting")  # once, not every poll; one HUD line each
        items.append(self.ITEMS[2])
        self.assertEqual(shown(), "Inventory full 2 items waiting")
        bridge.inventory_full = False
        self.assertEqual(shown(), "Got 2 items")
        self.assertEqual((ctx.inventory_full_pending, bridge.ints[2000], len(bridge.gives)), (0, 3, 1))

    def test_notices_fit_one_hud_line_at_any_count(self):
        ctx = self.make_ctx(FakeBridge(ints={2000: 0}), [])
        ctx._inventory_full_notice(1368, False)  # the most locations a seed can have
        ctx._inventory_full_notice(0, True)
        self.assertEqual(list(ctx.notices), ["Inventory full", "1368 items waiting", "Got 1368 items"])

class TestSnapshot(unittest.TestCase):
    def test_round_trip(self):
        save = bound(i2000=3)
        save.bools.update({3569: True, 6530: True, 20101: True})
        snapshot = game_bridge(FakeGame(save)).snapshot()
        self.assertEqual(snapshot.identity, ID)
        self.assertTrue(snapshot.get_bool(3569) and snapshot.get_bool(6530) and snapshot.get_bool(20101))
        self.assertFalse(snapshot.get_bool(3570))
        self.assertEqual(snapshot.get_int(2000), 3)
        self.assertIn(LOCATION_NAME_TO_ID["Eden Prime: Beacon Vision"], evaluate_location_predicates(snapshot))

    def test_request_covers_every_predicate(self):
        snapshot = game_bridge(FakeGame(bound())).snapshot()
        evaluate_location_predicates(snapshot)  # a KeyError here = a predicate SNAP does not read
        self.assertLess(len(SNAP_REQUEST), 8000)


class TestSaveIdentity(unittest.TestCase):
    def make_ctx(self, game, items=()):
        ctx = object.__new__(ME1Context)
        ctx.server_seed_name = "S"
        ctx.bridge = game_bridge(game)
        ctx.slot = 1
        ctx.items_received = list(items)
        ctx.notices_enabled = False
        return ctx

    def check(self, ctx):
        return ctx._check_identity(ID, ctx.bridge.snapshot())

    def test_fresh_save_is_bound(self):
        game = FakeGame()
        ctx = self.make_ctx(game)
        self.assertFalse(self.check(ctx))  # bound now; deliveries start next poll
        self.assertEqual(game.save.identity(), ID)
        self.assertTrue(self.check(ctx))

    def test_other_seed_is_refused_and_nothing_is_written(self):
        game = FakeGame(FakeSave({2004: 777, 2005: 1}))
        ctx = self.make_ctx(game, [SimpleNamespace(item=ID_BASE + 0x10)])
        with self.assertRaises(IdentityMismatchError):
            self.check(ctx)
        self.assertFalse(any(r.startswith(("TXN", "IDSET", "NONCE")) for r in game.requests))
        with self.assertRaises(IdentityMismatchError):  # a TXN aimed at another save is refused by the bridge too
            ctx.bridge.transaction(ID, 0, "FLAG 20000")
        self.assertEqual(game.grants, [])

    def test_save_with_progress_is_not_bound(self):
        from ..client import BIND_MAX_TRUE_LOCATIONS, LOCATION_PREDICATES
        bools = [p.indices[0] for p in LOCATION_PREDICATES.values() if p.kind == "bool"][:BIND_MAX_TRUE_LOCATIONS + 1]
        game = FakeGame(FakeSave(bools=dict.fromkeys(bools, True)))
        with self.assertRaises(IdentityMismatchError):
            self.check(self.make_ctx(game))
        self.assertEqual(game.save.identity(), (0, 0))

    def test_switch_before_idset_binds_nothing(self):
        game = FakeGame(FakeSave())
        ctx = self.make_ctx(game)
        ending = FakeSave(bools={6530: True})
        bind = ctx.bridge.bind_identity

        def switch_then_bind(identity, nonce):
            game.load(ending)  # the checked save is replaced by one past the ending just before IDSET
            bind(identity, nonce)
        ctx.bridge.bind_identity = switch_then_bind
        self.assertFalse(self.check(ctx))
        self.assertEqual(ending.identity(), (0, 0))

    def test_older_clients_save_needs_bind(self):
        game = FakeGame(FakeSave({2000: 2}))
        ctx = self.make_ctx(game, [SimpleNamespace(item=ID_BASE + 0x10)] * 2)
        with self.assertRaisesRegex(IdentityMismatchError, "/bind"):
            self.check(ctx)
        self.assertEqual(game.save.identity(), (0, 0))
        ctx.bind_requested = True
        self.assertFalse(self.check(ctx))
        self.assertEqual(game.save.identity(), ID)
        self.assertFalse(ctx.bind_requested)

    def test_no_seed_yet_means_no_reads_or_writes(self):
        game = FakeGame(bound())
        ctx = self.make_ctx(game, [SimpleNamespace(item=ID_BASE + 0x10)])
        ctx.server_seed_name = None
        ctx.build_checked = True
        self.assertEqual(ctx._read_game_state(True), (set(), False))
        self.assertEqual([r for r in game.requests if r != "PING"], [])

    def test_identity_is_never_zero(self):
        self.assertEqual(seed_identity("", 0, 0), (1, 1))
        self.assertNotEqual(seed_identity("A", 0, 1), seed_identity("B", 0, 1))
        self.assertNotEqual(seed_identity("A", 0, 1), seed_identity("A", 0, 2))


class TestBuildIdentity(unittest.TestCase):
    def check(self, game):
        ctx = object.__new__(ME1Context)
        ctx.bridge = game_bridge(game)
        ctx._check_build()
        return ctx

    def test_current_bridge_and_mod_pass(self):
        self.assertTrue(self.check(FakeGame()).build_checked)

    def test_old_bridge_is_refused(self):
        with self.assertRaisesRegex(BuildMismatchError, "bridge is v11"):
            self.check(FakeGame(version=11))
        with self.assertRaisesRegex(BuildMismatchError, "bridge is v12"):  # v12 loses gear given into a full inventory
            self.check(FakeGame(version=12))

    def test_missing_or_old_mod_is_refused(self):
        with self.assertRaisesRegex(BuildMismatchError, "missing"):
            self.check(FakeGame(mod=None))
        with self.assertRaisesRegex(BuildMismatchError, "is 0.2.3"):
            self.check(FakeGame(mod="0.2.3"))

    def test_ping_versions(self):
        self.assertEqual(game_bridge(FakeGame(version=11)).ping(), 11)
        self.assertEqual(game_bridge(FakeGame(version=13)).ping(), 13)

    def test_required_mod_is_not_newer_than_the_world(self):
        import json
        import os
        with open(os.path.join(os.path.dirname(__file__), "..", "archipelago.json"), encoding="utf-8-sig") as f:
            world = json.load(f)["world_version"]
        self.assertLessEqual(version_key(MOD_VERSION), version_key(world))




class TestSummaryPages(unittest.TestCase):
    def test_pages_keep_every_word_and_fit_the_box(self):
        lines = ["In logic: Uncharted 198, Citadel 116, Normandy 27",
                 "Uncharted: " + ", ".join(f"Planet{i} {i}" for i in range(30)),
                 "Locked: Eden Prime [coords], Therum [coords, 2 squad], Feros [coords, 2 squad]"]
        pages = summary_pages(lines)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 7 and all(len(line) <= 56 for line in page) for page in pages))
        body = [line for page in pages for line in page if not line.startswith("Archipelago") and line != "F7: next page"]
        self.assertEqual(" ".join(body).split(), " ".join(lines).split())

    def test_short_summary_is_one_page(self):
        self.assertEqual(summary_pages(["In logic: Normandy 3"]), [["Archipelago", "In logic: Normandy 3"]])
