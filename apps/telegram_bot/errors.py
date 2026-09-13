"""Erros tipados do pipeline social, com contexto seguro para logs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(eq=False)
class SocialMediaError(Exception):
    error_code: ClassVar[str] = "social_media_error"
    retryable: ClassVar[bool] = False
    public_message: ClassVar[str] = "Nao foi possivel processar essa midia."

    message: str
    platform: str = ""
    stage: str = ""
    account: str = ""
    status_code: int | None = None
    retry_after: float | None = None

    def __str__(self) -> str:
        return self.message

    def log_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "error_code": self.error_code,
            "error_type": type(self).__name__,
            "retryable": self.retryable,
        }
        for key in ("platform", "stage", "account", "status_code", "retry_after"):
            value = getattr(self, key)
            if value not in (None, ""):
                fields[key] = value
        return fields


class ContentUnavailable(SocialMediaError):
    error_code = "content_unavailable"
    public_message = "O conteudo nao esta disponivel ou foi removido."


class AuthenticationRequired(SocialMediaError):
    error_code = "authentication_required"
    public_message = "A rede social exigiu uma conta valida para abrir esse conteudo."


class RateLimited(SocialMediaError):
    error_code = "rate_limited"
    retryable = True
    public_message = "A rede social limitou os acessos. Tente novamente mais tarde."


class MediaTooLarge(SocialMediaError):
    error_code = "media_too_large"
    public_message = "A midia excede o limite aceito para envio."


class UnsupportedUrl(SocialMediaError):
    error_code = "unsupported_url"
    public_message = "Esse tipo de link ainda nao e suportado."


class DownloadTimeout(SocialMediaError):
    error_code = "download_timeout"
    retryable = True
    public_message = "O download demorou demais. Tente novamente."


class TelegramUploadFailed(SocialMediaError):
    error_code = "telegram_upload_failed"
    retryable = True
    public_message = "O Telegram recusou o envio da midia. Tente novamente."


class MediaTooLong(SocialMediaError):
    error_code = "media_too_long"
    public_message = "A midia excede a duracao configurada."


class StorageUnavailable(SocialMediaError):
    error_code = "storage_unavailable"
    retryable = True
    public_message = "O armazenamento temporario esta sem espaco suficiente."
