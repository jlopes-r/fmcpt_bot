import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.errors import ContentUnavailable
from apps.telegram_bot.models.media import MediaBundle, MediaItem
from apps.telegram_bot.services.media_sender import MediaSender, sniff_media


class MediaModelTests(unittest.TestCase):
    def test_bundle_preserves_explicit_source_order(self):
        bundle = MediaBundle(
            "x",
            (
                MediaItem("third.jpg", "photo", index=2),
                MediaItem("first.mp4", "video", index=0),
            ),
            expected_items=3,
        )
        self.assertEqual([item.source for item in bundle.items], ["first.mp4", "third.jpg"])
        self.assertTrue(bundle.is_partial)

    def test_legacy_result_becomes_typed_bundle(self):
        bundle = MediaBundle.from_legacy_result(
            {"files": ["one.jpg", "two.mp4"], "title": "Post", "uploader": "Ada"},
            platform="instagram",
        )
        self.assertEqual([item.kind for item in bundle.items], ["photo", "video"])
        self.assertEqual(bundle.author, "Ada")

    def test_magic_bytes_reject_html_with_image_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fake.jpg"
            path.write_text("<html>login</html>", encoding="utf-8")
            with self.assertRaises(ContentUnavailable):
                sniff_media(path)


class MediaSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_item_uses_individual_send(self):
        client = SimpleNamespace(
            send_photo=AsyncMock(),
            send_video=AsyncMock(),
            send_media_group=AsyncMock(),
        )
        sender = MediaSender(client)
        prepared = MediaBundle("x", (MediaItem("photo.jpg", "photo"),))
        with patch.object(sender, "prepare", new=AsyncMock(return_value=prepared)):
            await sender.send(
                SimpleNamespace(chat=SimpleNamespace(id=1), id=2),
                prepared,
                caption="caption",
            )
        client.send_photo.assert_awaited_once()
        client.send_media_group.assert_not_awaited()

    async def test_mixed_album_preserves_order_and_caption(self):
        client = SimpleNamespace(
            send_photo=AsyncMock(),
            send_video=AsyncMock(),
            send_media_group=AsyncMock(),
        )
        sender = MediaSender(client)
        prepared = MediaBundle(
            "x",
            (
                MediaItem("one.jpg", "photo", index=0),
                MediaItem("two.mp4", "video", index=1, width=10, height=20, duration=3),
                MediaItem("three.jpg", "photo", index=2),
            ),
        )
        with patch.object(sender, "prepare", new=AsyncMock(return_value=prepared)):
            await sender.send(
                SimpleNamespace(chat=SimpleNamespace(id=1), id=2),
                prepared,
                caption="caption",
            )
        sent = client.send_media_group.await_args.args[1]
        self.assertEqual([item.media for item in sent], ["one.jpg", "two.mp4", "three.jpg"])
        self.assertEqual([item.caption for item in sent], ["caption", "", ""])

    async def test_rejected_album_falls_back_in_original_order(self):
        client = SimpleNamespace(
            send_photo=AsyncMock(),
            send_video=AsyncMock(),
            send_media_group=AsyncMock(side_effect=RuntimeError("MEDIA_EMPTY")),
        )
        sender = MediaSender(client)
        prepared = MediaBundle(
            "x",
            (
                MediaItem("one.jpg", "photo", index=0),
                MediaItem("two.mp4", "video", index=1, width=10, height=20, duration=3),
            ),
        )
        with patch.object(sender, "prepare", new=AsyncMock(return_value=prepared)):
            await sender.send(
                SimpleNamespace(chat=SimpleNamespace(id=1), id=2),
                prepared,
                caption="caption",
            )
        self.assertEqual(client.send_photo.await_count, 1)
        self.assertEqual(client.send_video.await_count, 1)
        self.assertEqual(
            [client.send_photo.await_args.args[1], client.send_video.await_args.args[1]],
            ["one.jpg", "two.mp4"],
        )
