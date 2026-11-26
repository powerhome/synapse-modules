"""Tests for the fixtures orchestrator: planning, running, resuming, rollback."""

import copy
import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import HttpResponseException, SynapseError

from fixtures.checkpoint import Checkpoint
from fixtures.journal import Journal
from fixtures.provisioner import Deployment, Provisioner


class FakeWorld:
    """An in-memory homeserver behind fake provisioners.

    Each fake honours the same repeat contract as the real provisioner it
    stands in for: a user is adopted rather than registered twice, a
    message-like send is deduplicated by its txn_id, memberships and
    departures are no-ops when already in place — and, like Synapse, creating
    a room is NOT deduplicated. So a resumed run that re-ran the wrong steps,
    or skipped clearing a half-made room, leaves duplicates these tests catch.
    """

    def __init__(self):
        self.accounts = {}  # mxid -> {"deployment": ..., "active": bool}
        self.rooms = {}  # room_id -> {"marker": (deployment, ref), "members": set}
        self.sent = {}  # txn_id -> event_id
        self.receipts = set()
        self.contexts = {}
        self.archived = set()
        self.log = []
        self.room_counter = 0
        self.fail = {}  # (kind, ref) -> exception to raise once

    def maybe_fail(self, kind, ref):
        error = self.fail.pop((kind, ref), None)
        if error is not None:
            raise error


def make_provisioner(world=None, secret="s3cret"):  # noqa: S107
    world = world or FakeWorld()
    hs = MagicMock()
    hs.hostname = "hs"
    hs.get_clock.return_value.sleep = AsyncMock()
    provisioner = Provisioner(hs, secret)
    provisioner.store.get_global_account_data_by_type_for_user = AsyncMock(
        side_effect=lambda mxid, _type: (
            {"deployment_id": world.accounts[mxid]["deployment"]}
            if mxid in world.accounts
            else None
        )
    )

    async def provision_user(spec, run_id, run_prefix, deployment_id, admitted_ms):
        ref = spec["ref"]
        mxid = f"@{ref}:hs"
        if mxid not in world.accounts:
            world.accounts[mxid] = {"deployment": deployment_id, "active": True}
            world.log.append(("user", ref))
        world.maybe_fail("user", ref)
        return {"ref": ref, "mxid": mxid, "localpart": ref}

    async def deactivate(mxid):
        world.accounts[mxid]["active"] = False
        world.log.append(("deactivate", mxid))

    async def issue_credentials(spec, mxid):
        return {"password": f"pw-{mxid}"} if spec.get("auth") == "password" else {}

    provisioner.users = MagicMock()
    provisioner.users.provision = AsyncMock(side_effect=provision_user)
    provisioner.users.deactivate = AsyncMock(side_effect=deactivate)
    provisioner.users.issue_credentials = AsyncMock(side_effect=issue_credentials)
    provisioner.users.random_localpart = MagicMock(
        side_effect=lambda prefix, deployment_id, ref: ref
    )

    async def provision_room(spec, users, run_id, deployment_id):
        world.room_counter += 1
        room_id = f"!r{world.room_counter}:hs"
        world.rooms[room_id] = {
            "marker": (deployment_id, spec["ref"]),
            "members": set(),
        }
        world.log.append(("room", spec["ref"]))
        world.maybe_fail("room", spec["ref"])
        return {"ref": spec["ref"], "room_id": room_id}

    async def created_by(creator, deployment_id, ref):
        return [
            room_id
            for room_id, room in world.rooms.items()
            if room["marker"] == (deployment_id, ref)
        ]

    provisioner.rooms = MagicMock()
    provisioner.rooms.provision = AsyncMock(side_effect=provision_room)
    provisioner.rooms.created_by = AsyncMock(side_effect=created_by)

    async def join_members(spec, users, room_id):
        for ref in [*spec.get("members", []), *spec.get("left", [])]:
            world.rooms[room_id]["members"].add(users[ref]["mxid"])

    async def leave(spec, users, room_id):
        for ref in spec.get("left", []):
            world.rooms[room_id]["members"].discard(users[ref]["mxid"])
        world.log.append(("leave", spec["ref"]))

    provisioner.members = MagicMock()
    provisioner.members.provision = AsyncMock(side_effect=join_members)
    provisioner.members.leave = AsyncMock(side_effect=leave)

    def sender(kind):
        async def send(spec, users, rooms, *events, txn_id):
            world.maybe_fail(kind, spec.get("ref"))
            if txn_id not in world.sent:
                world.sent[txn_id] = f"${kind}{len(world.sent)}"
                world.log.append((kind, spec.get("ref")))
            return {"ref": spec.get("ref"), "event_id": world.sent[txn_id]}

        return send

    provisioner.events = MagicMock(provision=AsyncMock(side_effect=sender("event")))
    provisioner.media = MagicMock(provision=AsyncMock(side_effect=sender("media")))
    provisioner.reactions = MagicMock(
        provision=AsyncMock(side_effect=sender("reaction"))
    )
    provisioner.redactions = MagicMock(
        provision=AsyncMock(side_effect=sender("redaction"))
    )

    async def receipt(spec, users, rooms, events):
        world.receipts.add((spec["user"], spec["event"]))

    provisioner.receipts = MagicMock(provision=AsyncMock(side_effect=receipt))

    async def audience(spec, users, room_id, departed):
        world.contexts[room_id] = spec.get("audience")
        world.log.append(("audience", spec["ref"]))

    provisioner.audience = MagicMock()
    provisioner.audience.provision = AsyncMock(side_effect=audience)

    async def archive(room_id, creator):
        world.archived.add(room_id)
        world.log.append(("archive", room_id))

    provisioner.archive = MagicMock(archive=AsyncMock(side_effect=archive))

    async def purge(room_ids):
        for room_id in room_ids:
            world.rooms.pop(room_id, None)
        return len(room_ids)

    async def deactivate_erase(mxids):
        for mxid in mxids:
            world.accounts.pop(mxid, None)
        return len(mxids)

    provisioner.room_cleanup = MagicMock(purge=AsyncMock(side_effect=purge))
    provisioner.cleanup = MagicMock(deactivate=AsyncMock(side_effect=deactivate_erase))
    return provisioner, world


def deployment(scenario, deployment_id="dep-1"):
    return Deployment(
        id=deployment_id,
        run_id=scenario["run_id"],
        scenario=scenario,
        admitted_ms=0,
    )


def full_scenario():
    return {
        "run_id": "run-a",
        "users": [
            {"ref": "alice", "auth": "password"},
            {"ref": "bob"},
            {"ref": "dave", "deactivated": True},
        ],
        "rooms": [
            {
                "ref": "r1",
                "creator": "alice",
                "members": ["bob", "dave"],
                "left": ["bob"],
            },
            {"ref": "r2", "creator": "alice", "members": ["bob"], "archived": True},
        ],
        "events": [
            {"ref": "e1", "room": "r1", "sender": "alice", "body": "hi"},
            {
                "ref": "e2",
                "room": "r1",
                "sender": "bob",
                "body": "yo",
                "reply_to": "e1",
            },
            {"room": "r2", "sender": "dave", "body": "untracked"},
        ],
        "media": [{"ref": "m1", "room": "r1", "sender": "alice"}],
        "reactions": [{"room": "r1", "sender": "bob", "event": "m1", "key": "+"}],
        "redactions": [{"room": "r1", "sender": "alice", "event": "e2"}],
        "receipts": [{"room": "r1", "user": "bob", "event": "e1"}],
    }


class SavingJournal(Journal):
    """Saves checkpoints the way the runner does, and can kill the process.

    A copy is saved after every structural step and every `every`-th step
    otherwise. ``crash_after`` simulates the process dying right after that
    step's side effects, before its checkpoint is saved.
    """

    def __init__(self, crash_after=None, every=3):
        self.saved = None
        self._crash_after = crash_after
        self._every = every
        self.crashed = False

    async def after_step(self, step, checkpoint):
        if step.index == self._crash_after:
            self.crashed = True
            raise ProcessDiedError()
        if step.is_structural or step.index % self._every == 0:
            self.saved = copy.deepcopy(checkpoint)

    def may_roll_back(self):
        # A dying process never gets to roll back.
        return not self.crashed


class ProcessDiedError(Exception):
    pass


class PlanTestSuite(unittest.TestCase):
    def test_orders_steps_so_later_ones_can_name_earlier_ones(self):
        kinds = [step.kind for step in Provisioner.plan(full_scenario())]

        self.assertEqual(
            kinds,
            ["user"] * 3
            + ["room"] * 2
            + ["event"] * 3
            + ["media", "reaction", "redaction", "receipt"]
            + ["audience"] * 2
            + ["leave"] * 2
            + ["deactivate", "archive"],
        )

    def test_indexes_steps_from_zero(self):
        plan = Provisioner.plan(full_scenario())

        self.assertEqual([step.index for step in plan], list(range(len(plan))))

    def test_counts_each_phases_steps(self):
        self.assertEqual(
            Provisioner.phases(full_scenario()),
            {
                "users": 3,
                "rooms": 2,
                "events": 3,
                "media": 1,
                "reactions": 1,
                "redactions": 1,
                "receipts": 1,
                "audiences": 2,
                "departures": 4,
            },
        )


class ValidateTestSuite(unittest.TestCase):
    def test_requires_a_run_id(self):
        provisioner, _ = make_provisioner()
        provisioner.users.validate = MagicMock()

        with self.assertRaises(SynapseError) as ctx:
            provisioner.validate({"users": []})

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    def test_rejects_an_undeclared_ref(self):
        provisioner, _ = make_provisioner()
        provisioner.users.validate = MagicMock()

        with self.assertRaises(SynapseError):
            provisioner.validate(
                {"run_id": "run-a", "rooms": [{"ref": "r", "creator": "ghost"}]}
            )

    def test_rejects_an_archived_room_whose_creator_departs(self):
        provisioner, _ = make_provisioner()
        provisioner.users.validate = MagicMock()

        with self.assertRaises(SynapseError):
            provisioner.validate(
                {
                    "run_id": "run-a",
                    "users": [{"ref": "a"}],
                    "rooms": [
                        {"ref": "r", "creator": "a", "left": ["a"], "archived": True}
                    ],
                }
            )

    def test_validates_each_user_spec(self):
        provisioner, _ = make_provisioner()
        provisioner.users.validate = MagicMock(
            side_effect=SynapseError(HTTPStatus.BAD_REQUEST, "bad auth")
        )

        with self.assertRaises(SynapseError):
            provisioner.validate({"run_id": "run-a", "users": [{"ref": "a"}]})


class ExecuteTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_provisions_the_whole_scenario_and_maps_refs(self):
        provisioner, world = make_provisioner()
        checkpoint = Checkpoint()

        await provisioner.execute(deployment(full_scenario()), checkpoint, Journal())

        graph = provisioner.graph(deployment(full_scenario()), checkpoint)
        self.assertEqual(set(graph["users"]), {"alice", "bob", "dave"})
        self.assertEqual(set(graph["rooms"]), {"r1", "r2"})
        self.assertEqual(set(graph["events"]), {"e1", "e2", "m1"})
        self.assertEqual(checkpoint.next_index, len(Provisioner.plan(full_scenario())))
        self.assertEqual(len(world.rooms), 2)

    async def test_departures_and_audiences_come_after_every_message(self):
        provisioner, world = make_provisioner()

        await provisioner.execute(deployment(full_scenario()), Checkpoint(), Journal())

        kinds = [kind for kind, _ in world.log]
        last_message = max(
            i
            for i, kind in enumerate(kinds)
            if kind in ("event", "media", "reaction", "redaction")
        )
        first_departure = min(
            i
            for i, kind in enumerate(kinds)
            if kind in ("audience", "leave", "deactivate", "archive")
        )
        self.assertLess(last_message, first_departure)
        self.assertEqual(kinds[-1], "archive")

    async def test_sends_each_message_with_a_txn_id_unique_to_its_step(self):
        provisioner, _ = make_provisioner()

        await provisioner.execute(deployment(full_scenario()), Checkpoint(), Journal())

        txn_ids = [
            call.kwargs["txn_id"]
            for call in provisioner.events.provision.await_args_list
        ]
        self.assertEqual(len(txn_ids), len(set(txn_ids)))
        self.assertTrue(all(txn.startswith("fx:dep-1:") for txn in txn_ids))

    async def test_validates_audiences_before_creating_any_room(self):
        provisioner, world = make_provisioner()
        provisioner.audience.validate = MagicMock(
            side_effect=SynapseError(HTTPStatus.BAD_REQUEST, "needs a directory")
        )

        with self.assertRaises(SynapseError):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), Journal()
            )

        self.assertEqual(world.rooms, {})

    async def test_issues_credentials_for_each_provisioned_user(self):
        provisioner, _ = make_provisioner()
        checkpoint = Checkpoint()
        await provisioner.execute(deployment(full_scenario()), checkpoint, Journal())

        credentials = await provisioner.issue_credentials(full_scenario(), checkpoint)

        self.assertEqual(credentials["alice"], {"password": "pw-@alice:hs"})
        self.assertEqual(credentials["bob"], {})


class RollbackTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_step_rolls_back_exactly_what_was_created(self):
        provisioner, world = make_provisioner()
        world.accounts["@other:hs"] = {"deployment": "dep-9", "active": True}
        world.fail[("event", "e2")] = SynapseError(HTTPStatus.BAD_REQUEST, "boom")

        with self.assertRaises(SynapseError):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), Journal()
            )

        self.assertEqual(world.rooms, {})
        self.assertEqual(set(world.accounts), {"@other:hs"})

    async def test_a_room_that_failed_part_way_is_rolled_back_too(self):
        provisioner, world = make_provisioner()
        world.fail[("room", "r2")] = SynapseError(HTTPStatus.BAD_REQUEST, "boom")

        with self.assertRaises(SynapseError):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), Journal()
            )

        self.assertEqual(world.rooms, {})

    async def test_the_original_error_propagates_even_if_rollback_fails(self):
        provisioner, world = make_provisioner()
        world.fail[("event", "e1")] = SynapseError(HTTPStatus.BAD_REQUEST, "boom")
        provisioner.room_cleanup.purge = AsyncMock(side_effect=Exception("purge"))

        with self.assertLogs(level="ERROR"):
            with self.assertRaises(SynapseError) as ctx:
                await provisioner.execute(
                    deployment(full_scenario()), Checkpoint(), Journal()
                )

        self.assertEqual(ctx.exception.msg, "boom")

    async def test_a_shutdown_leaves_everything_for_the_resume(self):
        provisioner, world = make_provisioner()
        journal = SavingJournal(crash_after=6)

        with self.assertRaises(ProcessDiedError):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), journal
            )

        self.assertEqual(len(world.rooms), 2)
        self.assertEqual(len(world.accounts), 3)

    async def test_deprovision_removes_exactly_what_was_created(self):
        provisioner, world = make_provisioner()
        world.accounts["@other:hs"] = {"deployment": "dep-9", "active": True}
        checkpoint = Checkpoint()
        await provisioner.execute(deployment(full_scenario()), checkpoint, Journal())

        await provisioner.deprovision(checkpoint)

        self.assertEqual(world.rooms, {})
        self.assertEqual(set(world.accounts), {"@other:hs"})


class RetryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_retries_a_step_through_a_dependency_that_is_down(self):
        provisioner, world = make_provisioner()
        world.fail[("event", "e1")] = HttpResponseException(503, "down", b"")

        await provisioner.execute(deployment(full_scenario()), Checkpoint(), Journal())

        self.assertIn(("event", "e1"), world.log)
        provisioner.clock.sleep.assert_awaited()

    async def test_does_not_retry_a_real_error(self):
        provisioner, world = make_provisioner()
        world.fail[("event", "e1")] = HttpResponseException(400, "bad", b"")

        with self.assertRaises(HttpResponseException):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), Journal()
            )

        provisioner.clock.sleep.assert_not_awaited()

    async def test_gives_up_once_the_retry_budget_is_spent(self):
        provisioner, world = make_provisioner()
        provisioner._retry_budget_s = 3
        provisioner.events.provision = AsyncMock(
            side_effect=HttpResponseException(503, "down", b"")
        )

        with self.assertRaises(HttpResponseException):
            await provisioner.execute(
                deployment(full_scenario()), Checkpoint(), Journal()
            )

        self.assertEqual(world.rooms, {})


class ResumeTestSuite(unittest.IsolatedAsyncioTestCase):
    """A deployment resumed after its process died part-way.

    The process dies after a step's side effects but before its checkpoint is
    saved — at every step in turn — and the deployment resumes from its last
    saved checkpoint. The end state must equal an uninterrupted run's, with no
    duplicate and no leftover resource.
    """

    async def test_resuming_after_a_death_at_any_step_matches_an_uninterrupted_run(
        self,
    ):
        reference, reference_world = make_provisioner()
        expected = Checkpoint()
        await reference.execute(deployment(full_scenario()), expected, Journal())

        for crash_after in range(len(Provisioner.plan(full_scenario()))):
            with self.subTest(crash_after=crash_after):
                provisioner, world = make_provisioner()
                first = SavingJournal(crash_after=crash_after)
                with self.assertRaises(ProcessDiedError):
                    await provisioner.execute(
                        deployment(full_scenario()), Checkpoint(), first
                    )

                resumed = first.saved or Checkpoint()
                await provisioner.execute(
                    deployment(full_scenario()), resumed, SavingJournal()
                )

                self.assertEqual(resumed.next_index, expected.next_index)
                self.assertEqual(set(resumed.users), set(expected.users))
                self.assertEqual(set(resumed.rooms), set(expected.rooms))
                self.assertEqual(set(resumed.events), set(expected.events))
                # No duplicates: as many rooms, accounts and sends as a clean run.
                self.assertEqual(len(world.rooms), len(reference_world.rooms))
                self.assertEqual(set(world.rooms), set(resumed.rooms.values()))
                self.assertEqual(len(world.accounts), len(reference_world.accounts))
                self.assertEqual(len(world.sent), len(reference_world.sent))
                self.assertEqual(
                    sorted(resumed.created_rooms), sorted(resumed.rooms.values())
                )
                self.assertEqual(len(resumed.created_users), 3)

    async def test_a_room_cut_off_mid_creation_is_cleared_and_made_again(self):
        provisioner, world = make_provisioner()
        world.fail[("room", "r1")] = ProcessDiedError()
        first = SavingJournal()
        first.may_roll_back = lambda: False
        with self.assertRaises(ProcessDiedError):
            await provisioner.execute(deployment(full_scenario()), Checkpoint(), first)

        await provisioner.execute(deployment(full_scenario()), first.saved, Journal())

        self.assertEqual(len(world.rooms), 2)

    async def test_the_deadline_and_cancel_hook_runs_before_each_step(self):
        provisioner, _ = make_provisioner()
        journal = Journal()
        journal.before_step = AsyncMock()

        await provisioner.execute(deployment(full_scenario()), Checkpoint(), journal)

        self.assertEqual(
            journal.before_step.await_count, len(Provisioner.plan(full_scenario()))
        )


def request_with_auth(header_value):
    request = MagicMock()
    request.getHeader.return_value = header_value
    return request


class RunPrefixTestSuite(unittest.TestCase):
    def test_is_deterministic_for_the_same_run_id(self):
        self.assertEqual(
            Provisioner.run_prefix("run-a"), Provisioner.run_prefix("run-a")
        )

    def test_differs_between_run_ids(self):
        self.assertNotEqual(
            Provisioner.run_prefix("run-a"), Provisioner.run_prefix("run-b")
        )

    def test_has_the_tagging_shape(self):
        prefix = Provisioner.run_prefix("run-a")
        self.assertTrue(prefix.startswith("fx_"))
        self.assertTrue(prefix.endswith("_"))


class AuthenticateTestSuite(unittest.TestCase):
    def test_accepts_the_matching_bearer_secret(self):
        provisioner, _ = make_provisioner(secret="s3cret")  # noqa: S106
        provisioner.authenticate(request_with_auth("Bearer s3cret"))

    def test_rejects_a_wrong_secret(self):
        provisioner, _ = make_provisioner(secret="s3cret")  # noqa: S106
        with self.assertRaises(SynapseError) as ctx:
            provisioner.authenticate(request_with_auth("Bearer nope"))
        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    def test_rejects_a_missing_header(self):
        provisioner, _ = make_provisioner(secret="s3cret")  # noqa: S106
        with self.assertRaises(SynapseError):
            provisioner.authenticate(request_with_auth(None))


class InventoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_groups_users_and_rooms_by_owning_run(self):
        provisioner, _ = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(
            return_value=[("@a:hs", "run-1"), ("@b:hs", "run-2")]
        )
        provisioner.room_cleanup.inventory = AsyncMock(
            return_value=[("!r:hs", "run-1")]
        )

        result = await provisioner.inventory()

        self.assertEqual(
            result,
            {
                "runs": {
                    "run-1": {"users": ["@a:hs"], "rooms": ["!r:hs"]},
                    "run-2": {"users": ["@b:hs"], "rooms": []},
                }
            },
        )

    async def test_skips_a_user_whose_owning_run_is_unknown(self):
        provisioner, _ = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(
            return_value=[("@orphan:hs", None), ("@b:hs", "run-1")]
        )
        provisioner.room_cleanup.inventory = AsyncMock(return_value=[])

        result = await provisioner.inventory()

        self.assertEqual(result, {"runs": {"run-1": {"users": ["@b:hs"], "rooms": []}}})

    async def test_is_empty_when_nothing_is_provisioned(self):
        provisioner, _ = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(return_value=[])
        provisioner.room_cleanup.inventory = AsyncMock(return_value=[])

        result = await provisioner.inventory()

        self.assertEqual(result, {"runs": {}})


if __name__ == "__main__":
    unittest.main()
