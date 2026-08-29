import unittest
from unittest.mock import patch

from apps.telegram_bot.translator import traduzir_se_necessario


class TranslatorTest(unittest.TestCase):
    def test_texto_vazio_retorna_original(self):
        self.assertEqual(traduzir_se_necessario(""), "")
        self.assertEqual(traduzir_se_necessario("   "), "   ")

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="pt")
    def test_texto_em_portugues_nao_e_traduzido(self, mock_detect):
        original = "Isso é um teste em português #tag"
        self.assertEqual(traduzir_se_necessario(original), original)

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value=None)
    def test_deteccao_incerta_nao_traduz(self, mock_detect):
        original = "texto sem idioma claro"
        self.assertEqual(traduzir_se_necessario(original), original)

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="en")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_texto_em_ingles_e_traduzido(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Confira esta atualização"
        resultado = traduzir_se_necessario("Check out this update")
        self.assertEqual(resultado, "Confira esta atualização")
        mock_gt.assert_called_once_with(source="auto", target="pt")

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="en")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_falha_na_traducao_retorna_original(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = None
        original = "Check out this update"
        self.assertEqual(traduzir_se_necessario(original), original)


if __name__ == "__main__":
    unittest.main()
