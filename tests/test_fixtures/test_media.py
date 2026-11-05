"""Tests for the media provisioner."""

import json
import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.constants import EventTypes
from synapse.api.errors import SynapseError
from twisted.internet import defer

from fixtures.media import PNG_BYTES, MediaProvisioner

MXC = "mxc://synapse:8008/abc123"


def make_provisioner(token="media-secret", code=200):  # noqa: S107
    hs = MagicMock()
    http = MagicMock()
    response = MagicMock()
    response.code = code
    http.request = AsyncMock(return_value=response)
    hs.get_simple_http_client.return_value = http
    creation_handler = MagicMock()
    event = MagicMock()
    event.event_id = "$media:localhost"
    creation_handler.create_and_send_nonmember_event = AsyncMock(
        return_value=(event, 1)
    )
    hs.get_event_creation_handler.return_value = creation_handler
    return MediaProvisioner(hs, token), http, creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}}
ROOMS = {"r1": "!room:localhost"}


def read_body_returning(content_uri=MXC):
    body = json.dumps({"content_uri": content_uri}).encode()
    return patch("fixtures.media.readBody", return_value=defer.succeed(body))


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_uploads_an_image_to_the_media_repo_with_the_shared_secret(self):
        provisioner, http, _ = make_provisioner()

        with read_body_returning():
            await provisioner.provision(
                {"ref": "i1", "room": "r1", "sender": "alice", "filename": "a.png"},
                USERS,
                ROOMS,
                txn_id="txn",
            )

        args = http.request.await_args
        self.assertEqual(args.args[0], "POST")
        self.assertIn("/_matrix/media/v3/upload", args.args[1])
        self.assertIn("filename=a.png", args.args[1])
        self.assertEqual(args.kwargs["data"], PNG_BYTES)
        headers = args.kwargs["headers"]
        self.assertEqual(
            headers.getRawHeaders("Authorization"), ["Bearer media-secret"]
        )
        self.assertEqual(headers.getRawHeaders("Content-Type"), ["image/png"])

    async def test_url_encodes_the_filename_in_the_upload_query(self):
        provisioner, http, _ = make_provisioner()

        with read_body_returning():
            await provisioner.provision(
                {
                    "room": "r1",
                    "sender": "alice",
                    "kind": "file",
                    "filename": "Q3 report & notes#1.txt",
                },
                USERS,
                ROOMS,
                txn_id="txn",
            )

        self.assertTrue(
            http.request.await_args.args[1].endswith(
                "?filename=Q3%20report%20%26%20notes%231.txt"
            )
        )

    async def test_sends_an_image_message_pointing_at_the_uploaded_content(self):
        provisioner, _, handler = make_provisioner()

        with read_body_returning():
            result = await provisioner.provision(
                {"ref": "i1", "room": "r1", "sender": "alice", "filename": "a.png"},
                USERS,
                ROOMS,
                txn_id="txn",
            )

        requester, event_dict = handler.create_and_send_nonmember_event.await_args.args
        self.assertEqual(requester.user.to_string(), "@alice:localhost")
        self.assertEqual(event_dict["type"], EventTypes.Message)
        self.assertEqual(event_dict["room_id"], "!room:localhost")
        content = event_dict["content"]
        self.assertEqual(content["msgtype"], "m.image")
        self.assertEqual(content["body"], "a.png")
        self.assertEqual(content["url"], MXC)
        self.assertEqual(content["info"]["mimetype"], "image/png")
        self.assertEqual(content["info"]["size"], len(PNG_BYTES))
        self.assertEqual(result, {"ref": "i1", "event_id": "$media:localhost"})

    async def test_a_file_kind_uploads_and_sends_an_m_file_message(self):
        provisioner, http, handler = make_provisioner()

        with read_body_returning():
            await provisioner.provision(
                {
                    "ref": "f1",
                    "room": "r1",
                    "sender": "alice",
                    "kind": "file",
                    "filename": "notes.txt",
                },
                USERS,
                ROOMS,
                txn_id="txn",
            )

        self.assertEqual(
            http.request.await_args.kwargs["headers"].getRawHeaders("Content-Type"),
            ["text/plain"],
        )
        content = handler.create_and_send_nonmember_event.await_args.args[1]["content"]
        self.assertEqual(content["msgtype"], "m.file")
        self.assertEqual(content["filename"], "notes.txt")
        self.assertEqual(content["info"]["mimetype"], "text/plain")

    async def test_defaults_the_filename_from_the_ref(self):
        provisioner, _, handler = make_provisioner()

        with read_body_returning():
            await provisioner.provision(
                {"ref": "i1", "room": "r1", "sender": "alice"},
                USERS,
                ROOMS,
                txn_id="txn",
            )

        content = handler.create_and_send_nonmember_event.await_args.args[1]["content"]
        self.assertEqual(content["body"], "i1.png")

    async def test_pins_origin_server_ts_when_ts_is_given(self):
        provisioner, _, handler = make_provisioner()

        with read_body_returning():
            await provisioner.provision(
                {"room": "r1", "sender": "alice", "ts": 1700000000000},
                USERS,
                ROOMS,
                txn_id="txn",
            )

        event_dict = handler.create_and_send_nonmember_event.await_args.args[1]
        self.assertEqual(event_dict["origin_server_ts"], 1700000000000)

    async def test_media_is_a_bad_request_when_the_media_repo_is_not_configured(self):
        provisioner, http, _ = make_provisioner(token="")  # noqa: S106

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "alice"}, USERS, ROOMS, txn_id="txn"
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        http.request.assert_not_awaited()

    async def test_an_unknown_kind_is_a_bad_request(self):
        provisioner, _, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "alice", "kind": "video"},
                USERS,
                ROOMS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_a_failed_upload_is_a_bad_gateway(self):
        provisioner, _, handler = make_provisioner(code=500)

        with read_body_returning() as read_body:
            with self.assertRaises(SynapseError) as ctx:
                await provisioner.provision(
                    {"room": "r1", "sender": "alice"}, USERS, ROOMS, txn_id="txn"
                )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_GATEWAY)
        handler.create_and_send_nonmember_event.assert_not_awaited()
        # The body is still read, releasing the pooled connection.
        read_body.assert_called_once()

    async def test_unknown_sender_ref_is_a_bad_request(self):
        provisioner, http, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "ghost"}, USERS, ROOMS, txn_id="txn"
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        http.request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
