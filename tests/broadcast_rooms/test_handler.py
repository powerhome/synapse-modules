"""Tests for handler."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import Codes, StoreError, SynapseError

from connect.broadcast_rooms.handler import BroadcastRoomHandler
from connect.broadcast_rooms.store import BroadcastRoomStore

room_id = "!abc:localhost"
a_id = "@a:localhost"
b_id = "@b:localhost"
c_id = "@c:localhost"
admin_id = "@admin:localhost"
bot_id = "@connect_bot:localhost"

HANDLER_LOGGER = "connect.broadcast_rooms.handler"
JOIN_RULES_KEY = ("m.room.join_rules", "")


def make_requester(mxid: str):
    requester = MagicMock()
    requester.user.__str__ = lambda _self: mxid
    requester.user.localpart = mxid.lstrip("@").split(":")[0]
    return requester


def join_rules_state(join_rule: str):
    event = MagicMock()
    event.content = {"join_rule": join_rule}
    return {JOIN_RULES_KEY: event}


class BroadcastRoomHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    """Builds a handler whose collaborators are all mocks.

    By default the room is a private room that is not a broadcast room yet.
    """

    def make_handler(self, bridge=None, **config):
        self.store = MagicMock(spec=BroadcastRoomStore)
        self.store.get_broadcasters = AsyncMock(return_value=None)

        self.hs = MagicMock()
        self.hs.hostname = "localhost"
        self.hs.get_clock.return_value.time_msec.return_value = 1730000000000
        self.room_member_handler = self.hs.get_room_member_handler.return_value
        self.room_member_handler.update_membership = AsyncMock()

        self.api = MagicMock()
        self.api.get_room_state = AsyncMock(return_value=join_rules_state("invite"))
        self.api.create_and_send_event_into_room = AsyncMock()

        handler = BroadcastRoomHandler(
            self.hs,
            self.api,
            self.store,
            {"allowed_localparts": ["bridge", "connect_bot"], **config},
            bridge,
        )
        handler.people_conversation_store = MagicMock()
        handler.people_conversation_store.is_people_conversation = AsyncMock(
            return_value=False
        )
        return handler

    def setUp(self):
        self.handler = self.make_handler()
        self.admin = make_requester(admin_id)
        self.bot = make_requester(bot_id)


class HandleGetTestSuite(BroadcastRoomHandlerTestCase):
    async def test__handle_get__not_broadcast_room(self):
        code, _ = await self.handler.handle_get(room_id)
        self.assertEqual(code, 404)
        self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__handle_get__no_broadcasters(self):
        self.store.get_broadcasters = AsyncMock(return_value=[])

        code, body = await self.handler.handle_get(room_id)
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [])
        self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__handle_get__ok(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        code, body = await self.handler.handle_get(room_id)
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id, b_id])
        self.store.get_broadcasters.assert_called_once_with(room_id)


class HandlePutTestSuite(BroadcastRoomHandlerTestCase):
    async def test__handle_put__missing_key(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {}, self.admin)

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__not_list(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": 123}, self.admin)

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__not_only_strings(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": [123]}, self.admin)

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__has_duplicates(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id, a_id]}, self.admin
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id])
        self.store.set_broadcasters.assert_called_once_with(room_id, [a_id])

    async def test__handle_put__ok(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id, b_id]}, self.admin
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id, b_id])
        self.store.set_broadcasters.assert_called_once_with(room_id, [a_id, b_id])

    async def test__handle_put__store_error_propagates_without_side_effects(self):
        self.store.set_broadcasters = AsyncMock(
            side_effect=StoreError(HTTPStatus.NOT_FOUND, "nope", Codes.NOT_FOUND)
        )

        with self.assertRaises(StoreError):
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.api.create_and_send_event_into_room.assert_not_called()
        self.room_member_handler.update_membership.assert_not_called()

    async def test__handle_put__logs_the_change(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        with self.assertLogs(HANDLER_LOGGER, level="INFO") as logs:
            await self.handler.handle_put(
                room_id, {"broadcasters": [a_id, b_id]}, self.admin
            )

        self.assertIn(
            f"{admin_id} set broadcasters for {room_id}: "
            f"['{a_id}'] -> ['{a_id}', '{b_id}']",
            logs.output[0],
        )

    async def test__handle_put__logs_a_room_becoming_a_broadcast_room(self):
        with self.assertLogs(HANDLER_LOGGER, level="INFO") as logs:
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.assertIn(f"not a broadcast room -> ['{a_id}']", logs.output[0])


class HandlePutEmptyListTestSuite(BroadcastRoomHandlerTestCase):
    """Connect v2 has no broadcast room without broadcasters."""

    async def assert_refused(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": []}, self.admin)

        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(
            ctx.exception.msg, "A broadcast room needs at least one broadcaster"
        )
        self.store.set_broadcasters.assert_not_called()
        self.api.create_and_send_event_into_room.assert_not_called()

    async def test__admin__cannot_turn_broadcasting_on_with_nobody(self):
        await self.assert_refused()

    async def test__admin__cannot_remove_the_last_broadcaster(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.assert_refused()

    async def test__admin__refused_before_the_room_is_looked_at(self):
        await self.assert_refused()

        self.api.get_room_state.assert_not_called()
        self.store.get_broadcasters.assert_not_called()

    async def test__bridge__may_still_store_an_empty_list(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": []}, self.bot
        )

        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [])
        self.store.set_broadcasters.assert_called_once_with(room_id, [])


class HandlePutUnchangedTestSuite(BroadcastRoomHandlerTestCase):
    """The bridge re-sends the list on every v2 room update, changed or not."""

    def setUp(self):
        self.handler = self.make_handler()
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])
        self.admin = make_requester(admin_id)

    async def test__same_broadcasters__has_no_side_effects(self):
        with self.assertNoLogs(HANDLER_LOGGER, level="INFO"):
            code, body = await self.handler.handle_put(
                room_id, {"broadcasters": [b_id, a_id]}, self.admin
            )

        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [b_id, a_id])
        self.store.set_broadcasters.assert_called_once_with(room_id, [b_id, a_id])
        self.api.create_and_send_event_into_room.assert_not_called()

    async def test__empty_list_on_a_regular_room__is_a_change(self):
        # None (not a broadcast room) and [] (broadcast room with nobody) differ.
        self.store.get_broadcasters = AsyncMock(return_value=None)

        await self.handler.handle_put(
            room_id, {"broadcasters": []}, make_requester(bot_id)
        )

        self.api.create_and_send_event_into_room.assert_awaited_once()


class HandlePutRoomKindTestSuite(BroadcastRoomHandlerTestCase):
    async def test__admin__public_room__is_refused(self):
        self.api.get_room_state = AsyncMock(return_value=join_rules_state("public"))

        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(ctx.exception.msg, "Only private rooms can be broadcast rooms")
        self.store.set_broadcasters.assert_not_called()

    async def test__admin__people_conversation__is_refused(self):
        self.handler.people_conversation_store.is_people_conversation = AsyncMock(
            return_value=True
        )

        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(
            ctx.exception.msg, "People conversations cannot be broadcast rooms"
        )
        self.store.set_broadcasters.assert_not_called()

    async def test__admin__room_without_join_rules__is_allowed(self):
        self.api.get_room_state = AsyncMock(return_value={})

        code, _ = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id]}, self.admin
        )

        self.assertEqual(code, 200)

    async def test__bridge__public_room__is_allowed(self):
        self.api.get_room_state = AsyncMock(return_value=join_rules_state("public"))

        code, _ = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id]}, self.bot
        )

        self.assertEqual(code, 200)
        self.store.set_broadcasters.assert_called_once_with(room_id, [a_id])
        self.handler.people_conversation_store.is_people_conversation.assert_not_called()


class MembershipTestSuite(BroadcastRoomHandlerTestCase):
    """Naming broadcasters never changes who is in the room."""

    async def test__adding_a_broadcaster_who_is_not_in_the_room__invites_nobody(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": [c_id]}, self.admin
        )

        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [c_id])
        self.room_member_handler.update_membership.assert_not_called()

    async def test__removing_a_broadcaster__removes_nobody(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.room_member_handler.update_membership.assert_not_called()


class UpdatedEventTestSuite(BroadcastRoomHandlerTestCase):
    def sent_event(self):
        self.api.create_and_send_event_into_room.assert_awaited_once()
        return self.api.create_and_send_event_into_room.await_args.args[0]

    async def test__put__sends_the_state_event_as_the_requester(self):
        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.assertEqual(
            self.sent_event(),
            {
                "type": "com.powerhrg.broadcast.updated",
                "room_id": room_id,
                "sender": admin_id,
                "state_key": "",
                "content": {"origin": "localhost", "timestamp": 1730000000000},
            },
        )

    async def test__put__uses_the_configured_event_type(self):
        self.handler = self.make_handler(broadcast_updated_event_type="com.test.type")

        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.assertEqual(self.sent_event()["type"], "com.test.type")

    async def test__put__by_the_bridge__also_sends_it(self):
        # A change made in Connect v2 must reach v3 clients too.
        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.bot)

        self.assertEqual(self.sent_event()["sender"], bot_id)

    async def test__put__event_failure_does_not_fail_the_request(self):
        self.api.create_and_send_event_into_room = AsyncMock(
            side_effect=Exception("sender is not in the room")
        )

        with self.assertLogs(HANDLER_LOGGER, level="ERROR"):
            code, body = await self.handler.handle_put(
                room_id, {"broadcasters": [a_id]}, self.admin
            )

        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id])

    async def test__delete__sends_the_state_event(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.handler.handle_delete(room_id, self.admin)

        self.assertEqual(self.sent_event()["sender"], admin_id)


class HandleDeleteTestSuite(BroadcastRoomHandlerTestCase):
    async def test__handle_delete__ok(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        code, body = await self.handler.handle_delete(room_id, self.admin)
        self.assertEqual(code, 200)
        self.assertEqual(body, {})
        self.store.unbroadcast_room.assert_called_once_with(room_id)

    async def test__handle_delete__logs_the_change(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        with self.assertLogs(HANDLER_LOGGER, level="INFO") as logs:
            await self.handler.handle_delete(room_id, self.admin)

        self.assertIn(
            f"{admin_id} turned off broadcasting for {room_id}: "
            f"['{a_id}'] -> not a broadcast room",
            logs.output[0],
        )

    async def test__handle_delete__not_a_broadcast_room__has_no_side_effects(self):
        self.store.unbroadcast_room = AsyncMock(
            side_effect=StoreError(
                HTTPStatus.NOT_FOUND,
                f"{room_id} is not a broadcast room",
                Codes.NOT_FOUND,
            )
        )

        with self.assertRaises(StoreError) as ctx:
            await self.handler.handle_delete(room_id, self.admin)

        self.assertEqual(ctx.exception.code, 404)
        self.api.create_and_send_event_into_room.assert_not_called()

    async def test__handle_delete__never_touches_membership(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.handler.handle_delete(room_id, self.admin)

        self.room_member_handler.update_membership.assert_not_called()


class NotifyBridgeTestSuite(BroadcastRoomHandlerTestCase):
    """A person's change is sent to Connect v2; the bridge's own changes are not."""

    def setUp(self):
        self.bridge = MagicMock()
        self.handler = self.make_handler(bridge=self.bridge)
        self.admin = make_requester(admin_id)
        self.bot = make_requester(bot_id)

    async def test__put__by_a_person__sends_the_new_broadcasters(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.handler.handle_put(
            room_id, {"broadcasters": [a_id, b_id]}, self.admin
        )

        self.bridge.broadcasters_changed.assert_called_once_with(
            room_id, [a_id, b_id], admin_id
        )

    async def test__put__making_a_broadcast_room__sends_it(self):
        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.bridge.broadcasters_changed.assert_called_once_with(
            room_id, [a_id], admin_id
        )

    async def test__put__unchanged__sends_nothing(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        await self.handler.handle_put(
            room_id, {"broadcasters": [b_id, a_id]}, self.admin
        )

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__put__by_the_bridge__is_not_sent_back(self):
        await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.bot)

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__put__refused__sends_nothing(self):
        self.api.get_room_state = AsyncMock(return_value=join_rules_state("public"))

        with self.assertRaises(SynapseError):
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__put__store_error__sends_nothing(self):
        self.store.set_broadcasters = AsyncMock(side_effect=StoreError(500, "db down"))

        with self.assertRaises(StoreError):
            await self.handler.handle_put(room_id, {"broadcasters": [a_id]}, self.admin)

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__delete__by_a_person__sends_no_broadcasters(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.handler.handle_delete(room_id, self.admin)

        self.bridge.broadcasters_changed.assert_called_once_with(room_id, [], admin_id)

    async def test__delete__by_the_bridge__is_not_sent_back(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id])

        await self.handler.handle_delete(room_id, self.bot)

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__delete__not_a_broadcast_room__sends_nothing(self):
        self.store.unbroadcast_room = AsyncMock(
            side_effect=StoreError(HTTPStatus.NOT_FOUND, "not a broadcast room")
        )

        with self.assertRaises(StoreError):
            await self.handler.handle_delete(room_id, self.admin)

        self.bridge.broadcasters_changed.assert_not_called()

    async def test__without_a_bridge__a_change_still_succeeds(self):
        handler = self.make_handler(bridge=None)

        code, body = await handler.handle_put(
            room_id, {"broadcasters": [a_id]}, self.admin
        )

        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id])


if __name__ == "__main__":
    unittest.main()
