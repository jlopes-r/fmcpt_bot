import unittest
from unittest.mock import patch

from apps.telegram_bot.twitter import traduzir_texto_tweet


class TwitterTranslationTest(unittest.TestCase):
    # Textos e idioma verificados nas APIs públicas VXTwitter e FXTwitter.
    TWEETS_REPORTADOS = (
        {"tweetID": "2095582977946173730", "text": "VOU FAZER", "lang": "pt"},
        {
            "tweetID": "2095833224756805697",
            "text": "BOM DIA!\n\nMAS PQP!!!!",
            "lang": "pt",
        },
    )
    TEXTO_ALEMAO = "Das ist eine wichtige Nachricht für alle Menschen."
    TEXTO_TRADUZIDO = "Esta é uma mensagem importante para todas as pessoas."

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_tweets_reportados_preservam_portugues_sem_consultar_tradutor(self, translator):
        for tweet in self.TWEETS_REPORTADOS:
            with self.subTest(tweet_id=tweet["tweetID"]):
                self.assertEqual(traduzir_texto_tweet(tweet), tweet["text"])
        translator.assert_not_called()

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_tweets_reportados_tambem_sao_preservados_sem_idioma_da_api(self, translator):
        for tweet in self.TWEETS_REPORTADOS:
            with self.subTest(tweet_id=tweet["tweetID"]):
                self.assertEqual(
                    traduzir_texto_tweet({"text": tweet["text"]}), tweet["text"]
                )
        translator.assert_not_called()

    @patch("apps.telegram_bot.translator._detectar_idioma", return_value="de")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_idioma_da_fonte_protege_texto_mesmo_com_deteccao_errada(self, translator, detector):
        for field, language in (
            ("lang", "pt"),
            ("language", "PT_BR"),
            ("lang", "en-US"),
            ("language", "und"),
        ):
            with self.subTest(field=field, language=language):
                tweet = {"text": self.TEXTO_ALEMAO, field: language}
                self.assertEqual(traduzir_texto_tweet(tweet), self.TEXTO_ALEMAO)
        translator.assert_not_called()
        detector.assert_not_called()

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_idioma_ingles_sem_metadado_permanece_original(self, translator):
        text = "This is an important message for everyone in the community."
        self.assertEqual(traduzir_texto_tweet({"text": text}), text)
        translator.assert_not_called()

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_portugues_longo_em_caixa_alta_sem_metadado_permanece_original(self, translator):
        for text in ("BOM DIA PARA TODOS VOCÊS", "EU GOSTARIA DE SABER MAIS SOBRE ISSO"):
            with self.subTest(text=text):
                self.assertEqual(traduzir_texto_tweet({"text": text}), text)
        translator.assert_not_called()

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_chines_usa_idioma_generico_da_fonte_com_detector_regional(self, translator):
        translator.return_value.translate.return_value = "Esta é uma mensagem muito importante."
        tweet = {"text": "这是一个非常重要的消息，请大家注意安全。", "lang": "zh"}
        self.assertEqual(
            traduzir_texto_tweet(tweet),
            "Esta é uma mensagem muito importante.\n\n---\n🔎 Traduzido do chinês",
        )
        translator.assert_called_once_with(source="zh-CN", target="pt")

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_hebraico_usa_codigo_aceito_pelo_provedor(self, translator):
        translator.return_value.translate.return_value = self.TEXTO_TRADUZIDO
        tweet = {"text": "זוהי הודעה חשובה מאוד לכל האנשים בקהילה שלנו.", "lang": "he"}
        self.assertEqual(
            traduzir_texto_tweet(tweet),
            self.TEXTO_TRADUZIDO + "\n\n---\n🔎 Traduzido do hebraico",
        )
        translator.assert_called_once_with(source="iw", target="pt")

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_idioma_da_fonte_em_desacordo_preserva_original(self, translator):
        tweet = {"text": self.TEXTO_ALEMAO, "lang": "es"}
        self.assertEqual(traduzir_texto_tweet(tweet), self.TEXTO_ALEMAO)
        translator.assert_not_called()

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_tweet_e_quote_usam_seus_proprios_textos_e_idiomas(self, translator):
        translator.return_value.translate.return_value = self.TEXTO_TRADUZIDO
        quote = {
            "text": self.TEXTO_ALEMAO,
            "language": "de",
            "user_name": "Autor do quote",
        }
        tweet = {
            "text": "VOU FAZER",
            "lang": "pt",
            "user_name": "Chef Dan Galhardo",
            "qrt": quote,
        }

        self.assertEqual(traduzir_texto_tweet(tweet), "VOU FAZER")
        self.assertEqual(
            traduzir_texto_tweet(quote),
            self.TEXTO_TRADUZIDO + "\n\n---\n🔎 Traduzido do alemão",
        )
        translator.assert_called_once_with(source="de", target="pt")
        translator.return_value.translate.assert_called_once_with(self.TEXTO_ALEMAO)

    @patch("apps.telegram_bot.translator.time.sleep")
    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_falha_na_traducao_preserva_original_sem_aviso(self, translator, sleep):
        translator.return_value.translate.return_value = "<html>Server Error</html>"
        tweet = {"text": self.TEXTO_ALEMAO, "lang": "de"}
        self.assertEqual(traduzir_texto_tweet(tweet), self.TEXTO_ALEMAO)
        self.assertEqual(translator.return_value.translate.call_count, 3)

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_resposta_identica_ao_original_nao_recebe_aviso(self, translator):
        translator.return_value.translate.return_value = self.TEXTO_ALEMAO
        tweet = {"text": self.TEXTO_ALEMAO, "lang": "de"}
        self.assertEqual(traduzir_texto_tweet(tweet), self.TEXTO_ALEMAO)

    @patch("apps.telegram_bot.translator.GoogleTranslator")
    def test_texto_ausente_ou_nulo_nao_inventa_legenda(self, translator):
        for tweet in ({}, {"text": None, "lang": "de"}, {"text": "", "lang": "de"}):
            with self.subTest(tweet=tweet):
                self.assertEqual(traduzir_texto_tweet(tweet), "")
        translator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
