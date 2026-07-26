import unittest

from apps.telegram_bot.media_utils import detectar_extensao
from apps.telegram_bot.text_utils import dividir_texto_longo, limpar_texto, montar_legenda


class TextAndMediaUtilsTest(unittest.TestCase):
    def test_detectar_extensao_prioriza_content_type(self):
        url = "https://cdn.instagram.com/t51.2885-15/arquivo_sem_extensao"
        self.assertEqual(detectar_extensao(url, "video/mp4; charset=binary"), "mp4")

    def test_limpar_texto_remove_hashtags(self):
        self.assertEqual(limpar_texto("texto #tag\n\n\nfim"), "texto \n\nfim")

    def test_montar_legenda_respeita_limite(self):
        legenda = montar_legenda("x" * 500, "Autor", "Usuario", limite=120)
        self.assertLessEqual(len(legenda), 123)

    def test_dividir_texto_longo(self):
        partes = dividir_texto_longo("a " * 3000, limite=1000)
        self.assertGreater(len(partes), 1)
        self.assertTrue(all(len(parte) <= 1000 for parte in partes))


if __name__ == "__main__":
    unittest.main()
