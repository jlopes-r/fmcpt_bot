import unittest
from unittest.mock import patch

from apps.telegram_bot.translator import nome_idioma, traduzir_com_detalhes, traduzir_se_necessario


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


class TraduzirComDetalhesTest(unittest.TestCase):
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="pt")
    def test_em_portugues_nao_traduz(self, mock_detect):
        original = "Já estou em português"
        r = traduzir_com_detalhes(original)
        self.assertEqual(r["original"], original)
        self.assertIsNone(r["traduzido"])
        self.assertIsNone(r["idioma_origem"])
        self.assertFalse(r["foi_traduzido"])

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="en")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_traduz_com_detalhes(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Confira esta atualização"
        r = traduzir_com_detalhes("Check out this update")
        self.assertEqual(r["original"], "Check out this update")
        self.assertEqual(r["traduzido"], "Confira esta atualização")
        self.assertEqual(r["idioma_origem"], "en")
        self.assertTrue(r["foi_traduzido"])

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="es")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_traducao_null_nao_marca_como_traduzido(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = None
        r = traduzir_com_detalhes("Hola mundo")
        self.assertFalse(r["foi_traduzido"])
        self.assertIsNone(r["traduzido"])


class NomeIdiomaTest(unittest.TestCase):
    def test_codigos_conhecidos(self):
        self.assertEqual(nome_idioma("en"), "inglês")
        self.assertEqual(nome_idioma("es"), "espanhol")
        self.assertEqual(nome_idioma("pt"), "português")
        self.assertEqual(nome_idioma("ja"), "japonês")

    def test_codigo_com_sufixo_regiao(self):
        self.assertEqual(nome_idioma("pt-br"), "português")
        self.assertEqual(nome_idioma("zh-cn"), "chinês")

    def test_fallback_retorna_codigo(self):
        self.assertEqual(nome_idioma("xx"), "xx")

    def test_none_retorna_texto_generico(self):
        self.assertEqual(nome_idioma(None), "outro idioma")


if __name__ == "__main__":
    unittest.main()
