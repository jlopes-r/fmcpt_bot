import unittest
import threading
from pathlib import Path

from apps.telegram_bot.downloaders import (
    DownloadCancelled,
    _progresso_ytdlp_sync,
    video_exige_confirmacao,
)
from packages.url_utils import preparar_url_download_generico


class SocialDownloadRoutingTest(unittest.TestCase):
    def test_dependencia_de_impersonacao_do_tiktok_declarada(self):
        requirements = (Path(__file__).parents[1] / "apps" / "telegram_bot" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("curl-cffi", requirements)

    def test_query_que_identifica_video_do_facebook_e_preservada(self):
        url = "https://www.facebook.com/video.php?v=123456&ref=sharing"
        self.assertEqual(preparar_url_download_generico(url), url)

    def test_youtube_continua_sendo_normalizado(self):
        self.assertEqual(
            preparar_url_download_generico("https://youtu.be/abc_123?t=20"),
            "https://www.youtube.com/watch?v=abc_123",
        )

    def test_video_acima_de_dez_minutos_pede_confirmacao(self):
        self.assertFalse(video_exige_confirmacao(600, 600))
        self.assertTrue(video_exige_confirmacao(601, 600))

    def test_cancelamento_interrompe_hook_do_ytdlp(self):
        cancel_event = threading.Event()
        cancel_event.set()
        hook = _progresso_ytdlp_sync(None, None, cancel_event)

        with self.assertRaises(DownloadCancelled):
            hook({"status": "downloading"})


if __name__ == "__main__":
    unittest.main()
