import tempfile
import unittest
from pathlib import Path

from apps.telegram_bot.services.media_filenames import ensure_photo_upload_extension


class MediaFilenameTests(unittest.IsolatedAsyncioTestCase):
    async def test_extensionless_jpeg_is_renamed_before_telegram_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            original = directory / "remote_000.bin"
            original.write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg")

            prepared = await ensure_photo_upload_extension(
                original, "image/jpeg", directory, 0
            )

            self.assertEqual(prepared.name, "photo_000.jpg")
            self.assertEqual(prepared.read_bytes(), original.read_bytes())
