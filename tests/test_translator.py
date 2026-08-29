import unittest
from unittest.mock import patch

from apps.telegram_bot.translator import (
    _parece_erro_traducao,
    nome_idioma,
    traduzir_com_detalhes,
    traduzir_se_necessario,
)


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
    def test_texto_em_ingles_nao_e_traduzido(self, mock_detect):
        original = "Check out this update from our team"
        self.assertEqual(traduzir_se_necessario(original), original)

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="de")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_texto_em_outro_idioma_e_traduzido(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Confira esta atualização"
        resultado = traduzir_se_necessario("Schauen Sie sich dieses Update an")
        self.assertEqual(resultado, "Confira esta atualização")
        mock_gt.assert_called_once_with(source="auto", target="pt")

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="de")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_falha_na_traducao_retorna_original(self, mock_gt, mock_detect, mock_sleep):
        instancia = mock_gt.return_value
        instancia.translate.return_value = None
        original = "Schauen Sie sich dieses Update an"
        self.assertEqual(traduzir_se_necessario(original), original)

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="de")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_resposta_de_erro_nao_marca_como_traduzido(self, mock_gt, mock_detect, mock_sleep):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Error 500 (Server Error)!!1500.That's an error."
        original = "Schauen Sie sich dieses Update an"
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
    def test_em_ingles_nao_traduz(self, mock_detect):
        original = "This stays in English"
        r = traduzir_com_detalhes(original)
        self.assertFalse(r["foi_traduzido"])
        self.assertIsNone(r["traduzido"])
        self.assertEqual(r["original"], original)

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="es")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_traduz_com_detalhes(self, mock_gt, mock_detect):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Confira esta atualização"
        r = traduzir_com_detalhes("Mira esta actualización")
        self.assertEqual(r["original"], "Mira esta actualización")
        self.assertEqual(r["traduzido"], "Confira esta atualização")
        self.assertEqual(r["idioma_origem"], "es")
        self.assertTrue(r["foi_traduzido"])

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="es")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_traducao_null_nao_marca_como_traduzido(self, mock_gt, mock_detect, mock_sleep):
        instancia = mock_gt.return_value
        instancia.translate.return_value = None
        r = traduzir_com_detalhes("Hola mundo")
        self.assertFalse(r["foi_traduzido"])
        self.assertIsNone(r["traduzido"])

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="es")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_retry_apos_erro_500(self, mock_gt, mock_detect, mock_sleep):
        # Primeira chamada devolve página de erro; a segunda traduz normalmente.
        instancia = mock_gt.return_value
        instancia.translate.side_effect = [
            "Error 500 (Server Error)!!1500.That's an error.",
            "Confira esta atualização",
        ]
        r = traduzir_com_detalhes("Mira esta actualización")
        self.assertTrue(r["foi_traduzido"])
        self.assertEqual(r["traduzido"], "Confira esta atualização")
        self.assertEqual(r["idioma_origem"], "es")
        # backoff exponencial: 2s (somente 1 espera antes da 2ª tentativa)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="es")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_todas_tentativas_falham_nao_traduz(self, mock_gt, mock_detect, mock_sleep):
        instancia = mock_gt.return_value
        instancia.translate.return_value = "Error 500 (Server Error)!!1500.That's an error."
        r = traduzir_com_detalhes("Mira esta actualización")
        self.assertFalse(r["foi_traduzido"])
        self.assertIsNone(r["traduzido"])
        # 3 tentativas -> 2 esperas (2s e 4s de backoff)
        self.assertEqual(instancia.translate.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


class PareceErroTraducaoTest(unittest.TestCase):
    def test_detecta_pagina_de_erro_http(self):
        self.assertTrue(_parece_erro_traducao("Error 500 (Server Error)!!1500.That's an error."))
        self.assertTrue(_parece_erro_traducao("<html><head><title>Error</title></head>"))
        self.assertTrue(_parece_erro_traducao("server error, please try again later"))

    def test_texto_valido_nao_e_erro(self):
        self.assertFalse(_parece_erro_traducao("Esta é uma tradução normal em português."))
        self.assertFalse(_parece_erro_traducao("Check this out"))

    def test_texto_vazio_e_erro(self):
        self.assertTrue(_parece_erro_traducao(""))
        self.assertTrue(_parece_erro_traducao("   "))


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
