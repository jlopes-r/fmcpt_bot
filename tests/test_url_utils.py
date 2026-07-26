from packages.url_utils import normalizar_link_social, normalizar_url
import unittest


class UrlUtilsTest(unittest.TestCase):
    def test_normalizar_url_twitter_status_ignora_usuario(self):
        self.assertEqual(normalizar_url("https://x.com/alguem/status/12345?s=20"), "tweet:12345")

    def test_normalizar_link_social_remove_query(self):
        self.assertEqual(
            normalizar_link_social("https://instagram.com/p/ABC/?utm_source=x"),
            "https://instagram.com/p/abc",
        )

    def test_normalizar_link_social_x_padroniza_status(self):
        url = "https://twitter.com/usuario/status/98765?s=20"
        self.assertEqual(normalizar_link_social(url), "https://x.com/i/status/98765")


if __name__ == "__main__":
    unittest.main()
