"""Provisions media (uploaded files sent as messages) through the media repo."""

import binascii
import json
import struct
import zlib
from http import HTTPStatus
from urllib.parse import quote

from synapse.api.constants import EventTypes
from synapse.api.errors import Codes, SynapseError
from synapse.logging.context import make_deferred_yieldable
from synapse.types import JsonDict
from twisted.web.client import readBody
from twisted.web.http_headers import Headers

from connect.fixtures.refs import resolve_room_ref, resolve_user_ref
from connect.fixtures.sending import send_once
from connect.helpers import media_repo


def _png_1x1() -> bytes:
    # A valid 1x1 transparent PNG, built rather than embedded so the bytes are
    # self-evidently deterministic.
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", binascii.crc32(body))
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixel = zlib.compress(b"\x00\x00\x00\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixel)
        + chunk(b"IEND", b"")
    )


PNG_BYTES = _png_1x1()

# Per `kind`: the message msgtype, the uploaded mimetype, and a default extension.
KINDS = {
    "image": ("m.image", "image/png", "png"),
    "file": ("m.file", "text/plain", "txt"),
}


class MediaProvisioner:
    """Uploads a small file to the media repo and sends it as a message.

    The upload goes to the media-repo service with the shared-secret token (the
    path Connect's avatars module uses), and the returned ``mxc://`` URI is sent
    as an ``m.image`` (a 1x1 PNG) or ``m.file`` (a short text file) message
    through the same in-process event-creation handler as ``EventProvisioner``.
    Because it is a message, a media item takes part in the scenario's events: a
    reaction, redaction or receipt can name it by ``ref``. The message lives on
    its room, so purging the room (run teardown) removes it; the uploaded bytes
    stay in the media repo, which has no per-run teardown.
    """

    def __init__(self, hs, media_repo_token: str):
        self.hs = hs
        self._token = media_repo_token
        self.event_creation_handler = hs.get_event_creation_handler()

    async def provision(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        rooms: dict[str, str],
        *,
        txn_id: str,
    ) -> JsonDict:
        """Upload the file described by ``spec`` and send it as a message.

        Args:
            spec (JsonDict): The media spec — ``room`` (a room ref), ``sender`` (a user ref), plus the optional ``ref`` (so later events can target it), ``kind`` ("image" default / "file"), ``filename`` (defaults to ``<ref>.<ext>``) and ``ts`` (origin_server_ts in ms).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``sender``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            txn_id (str): Unique to this deployment step; makes a repeated send return the original.

        Returns:
            JsonDict: ``ref`` (may be ``None``) and the created message's ``event_id``.

        Raises:
            SynapseError: If the media repo is not configured, a ref is missing/unknown, ``kind`` is unknown, or the upload fails.
        """
        if not self._token:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "media provisioning is not configured for this environment",
                Codes.UNKNOWN,
            )

        room_id = resolve_room_ref(spec.get("room"), rooms, "media room")
        sender = resolve_user_ref(spec.get("sender"), users, "media sender")

        kind = spec.get("kind", "image")
        if kind not in KINDS:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"unknown media kind '{kind}'",
                Codes.INVALID_PARAM,
            )
        msgtype, mimetype, extension = KINDS[kind]
        ref = spec.get("ref")
        filename = spec.get("filename") or f"{ref or 'fixture'}.{extension}"
        data = PNG_BYTES if kind == "image" else f"fixture file {filename}\n".encode()

        mxc_uri = await self._upload(data, mimetype, filename)

        content: JsonDict = {
            "msgtype": msgtype,
            "body": filename,
            "url": mxc_uri,
            "info": {"mimetype": mimetype, "size": len(data)},
        }
        if kind == "image":
            content["info"].update({"w": 1, "h": 1})
        else:
            content["filename"] = filename

        event_dict: JsonDict = {
            "type": EventTypes.Message,
            "room_id": room_id,
            "sender": sender,
            "content": content,
        }
        ts = spec.get("ts")
        if ts is not None:
            event_dict["origin_server_ts"] = ts

        event = await send_once(self.event_creation_handler, sender, event_dict, txn_id)
        return {"ref": ref, "event_id": event.event_id}

    async def _upload(self, data: bytes, mimetype: str, filename: str) -> str:
        headers = Headers()
        headers.addRawHeader("Authorization", f"Bearer {self._token}")
        headers.addRawHeader("Host", media_repo.HOST)
        headers.addRawHeader("Content-Type", mimetype)

        response = await self.hs.get_simple_http_client().request(
            "POST",
            f"{media_repo.UPLOAD_URL}?filename={quote(filename)}",
            data=data,
            headers=headers,
        )
        # Read the body on every path, so a failed upload still releases its
        # pooled connection.
        body = await make_deferred_yieldable(readBody(response))
        if response.code != HTTPStatus.OK:
            raise SynapseError(
                HTTPStatus.BAD_GATEWAY,
                f"media repo rejected the upload ({response.code})",
                Codes.UNKNOWN,
            )
        return json.loads(body)["content_uri"]
