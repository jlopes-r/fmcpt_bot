import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from apps.telegram_bot import instagram_profile_card as card


class InstagramProfileCardTest(unittest.TestCase):
    def test_avatar_has_alpha_and_center_crops_without_distortion(self):
        source = Image.new("RGB", (300, 100), "red")
        ImageDraw.Draw(source).rectangle((100, 0, 199, 99), fill="green")
        avatar = card._avatar_circular(source, 100)
        self.assertEqual(avatar.mode, "RGBA")
        self.assertEqual(avatar.getpixel((0, 0))[3], 0)
        self.assertEqual(avatar.getpixel((50, 50)), (0, 128, 0, 255))
        self.assertEqual(avatar.getpixel((15, 50)), (0, 128, 0, 255))

    def test_downloaded_avatar_is_actually_pasted_into_card(self):
        buffer = io.BytesIO()
        Image.new("RGB", (120, 120), (210, 25, 50)).save(buffer, format="PNG")
        output = card.montar_card({"username": "ada", "biography": ""}, buffer.getvalue())
        with Image.open(io.BytesIO(output)) as result:
            center = card.MARGEM + card.AVATAR_TAM // 2
            self.assertEqual(result.getpixel((center, center)), (210, 25, 50))
            self.assertNotEqual(result.getpixel((card.MARGEM, card.MARGEM)), (210, 25, 50))

    def test_invalid_avatar_keeps_valid_card(self):
        output = card.montar_card({"username": "ada"}, b"not an image")
        with Image.open(io.BytesIO(output)) as result:
            result.verify()
            self.assertEqual(result.width, 720)

    def test_missing_windows_fonts_do_not_prevent_linux_font_selection(self):
        expected = ImageFont.load_default(size=38)
        attempted = []

        def truetype(path, size):
            attempted.append(path)
            if path == "/fonts/DejaVuSans-Bold.ttf":
                return expected
            raise OSError("font not installed")

        with (
            patch.dict(card._FONTE_CACHE, {}, clear=True),
            patch.object(card, "_caminho_fonte", side_effect=lambda name: "/fonts/" + name),
            patch.object(card.ImageFont, "truetype", side_effect=truetype),
            patch.object(card.ImageFont, "load_default") as fallback,
        ):
            self.assertIs(card._get_font("bold", 38), expected)
            self.assertIn("/fonts/DejaVuSans-Bold.ttf", attempted)
            fallback.assert_not_called()

    def test_last_resort_font_preserves_requested_size(self):
        expected = ImageFont.load_default(size=38)
        with (
            patch.dict(card._FONTE_CACHE, {}, clear=True),
            patch.object(card.ImageFont, "truetype", side_effect=OSError),
            patch.object(card.ImageFont, "load_default", return_value=expected) as fallback,
        ):
            self.assertIs(card._get_font("bold", 38), expected)
            fallback.assert_called_once_with(size=38)

    def test_long_url_wraps_within_width_and_truncates_visibly(self):
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        font = card._get_font("regular", 26)
        lines = card._quebrar_texto(draw, "https://example.com/" + "long" * 100, font, 200, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(draw.textlength(line, font=font) <= 200 for line in lines))
        self.assertEqual(card._quebrar_texto(draw, "Primeira\nSegunda", font, 600), ["Primeira", "Segunda"])

    def test_height_tracks_content_and_long_fields_stay_inside_card(self):
        short = {"username": "ada", "biography": ""}
        long = {
            "username": "a_very_long_username" * 10,
            "full_name": "Um nome de perfil muito longo " * 20,
            "biography": "Uma bio com várias informações e frases.\n" * 80,
            "external_url": "https://example.com/" + "path" * 200,
            "category": "Uma categoria muito longa " * 20,
            "is_verified": True,
            "is_private": True,
            "is_business": True,
            "partial": True,
            "posts": 1481,
            "followers": 3_000_000,
            "following": 8,
        }
        short_image = Image.open(io.BytesIO(card.montar_card(short, None)))
        rendered_text = []
        original_text = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            rendered_text.append((draw.textbbox(xy, text, font=kwargs["font"], anchor=kwargs.get("anchor")), text))
            return original_text(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", capture):
            long_image = Image.open(io.BytesIO(card.montar_card(long, None)))
        self.assertLess(short_image.height, 520)
        self.assertGreater(long_image.height, short_image.height)
        self.assertLess(long_image.height, 900)
        for (left, top, right, bottom), text in rendered_text:
            with self.subTest(text=text):
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(right, long_image.width)
                self.assertLessEqual(bottom, long_image.height)

    def test_unknown_bio_is_not_reported_as_empty(self):
        original_text = ImageDraw.ImageDraw.text
        for value, expected in ((None, "Bio indisponível."), ("", "Sem bio."), ("Minha bio", "Minha bio")):
            rendered = []

            def capture(draw, xy, text, *args, **kwargs):
                rendered.append(text)
                return original_text(draw, xy, text, *args, **kwargs)

            with self.subTest(value=value), patch.object(ImageDraw.ImageDraw, "text", capture):
                card.montar_card({"username": "ada", "biography": value}, None)
                self.assertIn(expected, rendered)

    def test_unknown_stats_remain_distinct_from_zero(self):
        self.assertEqual(card._formatar(None), "—")
        self.assertEqual(card._formatar(0), "0")
        self.assertEqual(card._formatar(3_000_000), "3M")


if __name__ == "__main__":
    unittest.main()
