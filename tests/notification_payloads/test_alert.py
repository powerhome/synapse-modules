"""Integration tests for Alert APNs payloads."""

import unittest

from notification_payloads.alert import Alert
from notification_payloads.alert_strategies import FALLBACK_BODY


class AlertIntegrationTestSuite(unittest.TestCase):
    def test_msg_from_user_with_content_dm(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "content": {"body": "hello there"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "[dev] John Doe",
                    "body": "hello there",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_msg_from_user_in_room_with_content(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "room_name": "Project Updates",
            "content": {"body": "Hey team, meeting at 3pm!"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "Alice in Project Updates",
                    "body": "Hey team, meeting at 3pm!",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_msg_from_user_dm_no_content(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "John Doe is Connecting with you",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_msg_from_user_in_room_no_content(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "[dev] Alice",
            "sender_raw_name": "Alice",
            "room_name": "General",
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Alice wrote a message in General",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_image_from_user_dm(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "content": {"msgtype": "m.image", "body": "vacation-photo.jpg"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "[dev] John Doe",
                    "body": "vacation-photo.jpg",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_image_from_user_in_room(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "room_name": "Photos",
            "content": {"msgtype": "m.image", "body": "sunset.png"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "Alice in Photos",
                    "body": "sunset.png",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_action_from_user_dm(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "content": {"msgtype": "m.emote", "body": "waves hello"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "[dev] John Doe",
                    "body": "waves hello",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_action_from_user_in_room(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "room_name": "General",
            "content": {
                "msgtype": "m.emote",
                "body": "is excited about the new feature",
            },
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "Alice in General",
                    "body": "is excited about the new feature",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_video_call_from_user(self):
        content = {
            "type": "m.call.invite",
            "sender_display_name": "[dev] Alice",
            "sender_raw_name": "Alice",
            "content": {
                "offer": {
                    "sdp": "v=0\no=- 123456 0 IN IP4 192.168.1.1\nm=video 5004 RTP/AVP 96\na=rtpmap:96 H264/90000"
                }
            },
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Alice wants to Video Call",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_voice_call_from_user(self):
        content = {
            "type": "m.call.invite",
            "sender_display_name": "[dev] Bob",
            "sender_raw_name": "Bob",
            "content": {
                "offer": {
                    "sdp": "v=0\no=- 123456 0 IN IP4 192.168.1.1\nm=audio 5004 RTP/AVP 0\na=rtpmap:0 PCMU/8000"
                }
            },
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Bob wants to Voice Call",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_encrypted_message_dm(self):
        content = {
            "type": "m.room.encrypted",
            "sender_display_name": "Bob",
            "sender_raw_name": "Bob",
            "content": {},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Bob is Connecting with you",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_encrypted_message_room(self):
        content = {
            "type": "m.room.encrypted",
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "room_name": "Secret Room",
            "content": {},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Alice wrote a message in Secret Room",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_missing_sender_fallback(self):
        content = {
            "type": "m.room.message",
            "content": {"body": "hello"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "hello",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_missing_sender_call_fallback(self):
        content = {
            "type": "m.call.invite",
            "content": {
                "offer": {
                    "sdp": "v=0\no=- 123456 0 IN IP4 192.168.1.1\nm=video 5004 RTP/AVP 96"
                }
            },
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": FALLBACK_BODY,
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_extends_default_payload(self):
        content = {
            "type": "m.room.message",
            "sender_display_name": "Alice",
            "content": {"body": "Test"},
            "room_id": "test_room_id",
        }
        default_payload = {"existing": "data", "aps": {"sound": "default", "badge": 1}}
        alert = Alert(content)
        result = alert.apns_dict(default_payload)

        self.assertEqual(result["existing"], "data")
        self.assertEqual(result["aps"]["sound"], "default")
        self.assertEqual(result["aps"]["badge"], 1)
        self.assertEqual(result["aps"]["alert"]["title"], "Alice")
        self.assertEqual(result["aps"]["alert"]["body"], "Test")
        self.assertEqual(result["aps"]["thread-id"], "test_room_id")
        self.assertEqual(result["aps"]["category"], "LIKE_REPLY_CATEGORY")

    def test_call_malformed_offer(self):
        content = {
            "type": "m.call.invite",
            "sender_display_name": "Charlie",
            "sender_raw_name": "Charlie",
            "content": {},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "body": "Charlie wants to Voice Call",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_unknown_event_type(self):
        content = {
            "type": "m.room.unknown",
            "sender_display_name": "Alice",
            "content": {"body": "test"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "Alice",
                    "body": "test",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_gif_message(self):
        content = {
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "type": "m.room.message",
            "content": {
                "body": "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExZnQwaXNtbXZhZWRlNXV2bTl3MHl4ejJ6MGtlcHZvd200ZXV6M2JpaCZlcD12MV9naWZzX3RyZW5kaW5nJmN0PWc/9gkwDaCB4pmoOe73x2/giphy.gif"
            },
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "[dev] John Doe",
                    "body": "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExZnQwaXNtbXZhZWRlNXV2bTl3MHl4ejJ6MGtlcHZvd200ZXV6M2JpaCZlcD12MV9naWZzX3RyZW5kaW5nJmN0PWc/9gkwDaCB4pmoOe73x2/giphy.gif",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)

    def test_url_message(self):
        content = {
            "sender_display_name": "[dev] John Doe",
            "sender_raw_name": "John Doe",
            "type": "m.room.message",
            "content": {"body": "http://google.com"},
            "room_id": "test_room_id",
        }
        alert = Alert(content)
        result = alert.apns_dict()

        expected = {
            "aps": {
                "alert": {
                    "title": "[dev] John Doe",
                    "body": "http://google.com",
                },
                "category": "LIKE_REPLY_CATEGORY",
                "thread-id": "test_room_id",
                "sound": "default",
            }
        }
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
