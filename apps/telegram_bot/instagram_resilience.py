"""Safety and resilience primitives for Instagram extraction.

This module deliberately has no dependency on the Telegram application.  It
keeps URL resolution, account health and rotating GraphQL document identifiers
testable without importing the large bot module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import json
import os
from pathlib import Path
import time
from typing import Callable, Mapping
from urllib.parse import urljoin, urlparse

import httpx


INSTAGRAM_WEB_HOSTS = frozenset({
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class InstagramFailure(str, Enum):
    """Failure classes that need different retry/account handling."""

    COOKIE_INVALID = "cookie_invalid"
    CHALLENGE = "challenge"
    IP_RATE_LIMITED = "ip_rate_limited"
    TRANSIENT = "transient"


def _response_text(response: object) -> str:
    try:
        return str(getattr(response, "text", "") or "")[:4000].lower()
    except Exception:
        return ""


def classify_instagram_response(response: object) -> InstagramFailure | None:
    """Classify authentication and throttling responses conservatively.

    A 429 is treated as an IP-level throttle: rotating cookie accounts usually
    makes this worse.  A checkpoint/challenge is kept distinct from an expired
    or logged-out cookie so operators know whether renewal or manual review is
    needed.
    """

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        return InstagramFailure.IP_RATE_LIMITED

    final_url = str(getattr(response, "url", "") or "").lower()
    body = _response_text(response)
    if (
        "/challenge/" in final_url
        or "challenge_required" in body
        or "checkpoint_required" in body
        or "checkpoint_challenge_required" in body
    ):
        return InstagramFailure.CHALLENGE
    if (
        "/accounts/login/" in final_url
        or "login_required" in body
        or 'id="loginform"' in body
        or status in {401, 403}
    ):
        return InstagramFailure.COOKIE_INVALID
    if status >= 500:
        return InstagramFailure.TRANSIENT
    return None


def validate_instagram_url(url: str) -> str:
    """Return *url* when it is an HTTP(S) Instagram URL, otherwise raise."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or host not in INSTAGRAM_WEB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("URL fora dos hosts oficiais do Instagram")
    return url


async def resolve_instagram_share_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_redirects: int = 5,
) -> str:
    """Resolve ``/share/...`` without ever following an external redirect.

    Redirects are followed manually, validating every hop first.  This avoids
    turning a user-controlled link into an SSRF/open-redirect primitive.
    Non-share Instagram URLs are returned without a network request.
    """

    current = validate_instagram_url(url)
    parsed = urlparse(current)
    if not parsed.path.startswith("/share/"):
        return current

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=12.0, follow_redirects=False)
    try:
        for _ in range(max(1, min(max_redirects, 10))):
            response = await client.get(
                current,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            if response.status_code not in _REDIRECT_STATUSES:
                # httpx exposes the effective response URL even when a custom
                # transport rewrites it.  It is still validated before use.
                effective = str(getattr(response, "url", "") or current)
                return validate_instagram_url(effective)

            location = response.headers.get("location")
            if not location:
                raise ValueError("Redirect do Instagram sem cabeçalho Location")
            current = validate_instagram_url(urljoin(current, location))
        raise ValueError("Instagram excedeu o limite de redirects")
    finally:
        if owns_client:
            await client.aclose()


@dataclass(frozen=True)
class AccountHealth:
    account_id: str
    label: str
    state: str = "healthy"
    reason: str = ""
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    blocked_until: float = 0.0
    consecutive_failures: int = 0


@dataclass
class _EndpointCircuit:
    failures: int = 0
    open_until: float = 0.0
    last_failure: InstagramFailure | None = None


class InstagramAccountPool:
    """Tracks independent cookie accounts and endpoint circuit breakers."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        failure_threshold: int = 2,
        circuit_seconds: float = 300.0,
        invalid_cookie_seconds: float = 1800.0,
        challenge_seconds: float = 3600.0,
        ip_rate_limit_seconds: float = 120.0,
    ) -> None:
        self._clock = clock
        self.failure_threshold = max(1, failure_threshold)
        self.circuit_seconds = max(1.0, circuit_seconds)
        self.invalid_cookie_seconds = max(1.0, invalid_cookie_seconds)
        self.challenge_seconds = max(1.0, challenge_seconds)
        self.ip_rate_limit_seconds = max(1.0, ip_rate_limit_seconds)
        self._accounts: dict[str, AccountHealth] = {}
        self._circuits: dict[tuple[str, str], _EndpointCircuit] = {}
        self._ip_blocked_until = 0.0
        self._ip_reason = ""

    @staticmethod
    def account_id(label: str, cookie_path: str) -> str:
        normalized = str(Path(cookie_path).expanduser().resolve()).casefold()
        return normalized

    def register(self, label: str, cookie_path: str) -> str:
        account_id = self.account_id(label, cookie_path)
        if account_id not in self._accounts:
            self._accounts[account_id] = AccountHealth(account_id, label)
        elif label != "health" and self._accounts[account_id].label != label:
            self._accounts[account_id] = replace(self._accounts[account_id], label=label)
        return account_id

    @property
    def ip_cooldown_remaining(self) -> float:
        return max(0.0, self._ip_blocked_until - self._clock())

    def can_attempt(self, account_id: str, endpoint: str) -> bool:
        now = self._clock()
        account = self._accounts.get(account_id)
        if account is None:
            return False
        if account.blocked_until > now:
            return False
        if endpoint != "ytdlp" and self._ip_blocked_until > now:
            return False
        circuit = self._circuits.get((account_id, endpoint))
        return circuit is None or circuit.open_until <= now

    def report_success(self, account_id: str, endpoint: str) -> None:
        now = self._clock()
        account = self._accounts[account_id]
        self._accounts[account_id] = replace(
            account,
            state="healthy",
            reason="",
            blocked_until=0.0,
            last_success_at=now,
            consecutive_failures=0,
        )
        self._circuits.pop((account_id, endpoint), None)

    def report_failure(
        self,
        account_id: str,
        endpoint: str,
        failure: InstagramFailure,
        reason: str = "",
    ) -> None:
        now = self._clock()
        account = self._accounts[account_id]
        blocked_until = account.blocked_until
        state = failure.value
        if failure is InstagramFailure.COOKIE_INVALID:
            blocked_until = now + self.invalid_cookie_seconds
        elif failure is InstagramFailure.CHALLENGE:
            blocked_until = now + self.challenge_seconds
        elif failure is InstagramFailure.IP_RATE_LIMITED:
            self._ip_blocked_until = max(
                self._ip_blocked_until, now + self.ip_rate_limit_seconds
            )
            self._ip_reason = reason or "Instagram retornou 429"

        self._accounts[account_id] = replace(
            account,
            state=state,
            reason=reason or failure.value,
            last_failure_at=now,
            blocked_until=blocked_until,
            consecutive_failures=account.consecutive_failures + 1,
        )

        circuit = self._circuits.setdefault((account_id, endpoint), _EndpointCircuit())
        circuit.failures += 1
        circuit.last_failure = failure
        if (
            circuit.failures >= self.failure_threshold
            or failure in {
                InstagramFailure.COOKIE_INVALID,
                InstagramFailure.CHALLENGE,
                InstagramFailure.IP_RATE_LIMITED,
            }
        ):
            circuit.open_until = max(circuit.open_until, now + self.circuit_seconds)

    def health(self, account_id: str) -> AccountHealth:
        return self._accounts[account_id]

    def snapshot(self) -> dict[str, object]:
        return {
            "accounts": {
                account_id: health
                for account_id, health in self._accounts.items()
            },
            "ip_blocked_until": self._ip_blocked_until,
            "ip_reason": self._ip_reason,
        }

    def reset(self, account_id: str | None = None) -> None:
        if account_id is None:
            self._accounts.clear()
            self._circuits.clear()
            self._ip_blocked_until = 0.0
            self._ip_reason = ""
            return
        current = self._accounts.get(account_id)
        if current:
            self._accounts[account_id] = AccountHealth(account_id, current.label)
        for key in [key for key in self._circuits if key[0] == account_id]:
            self._circuits.pop(key, None)


@dataclass(frozen=True)
class GraphQLDocuments:
    version: str
    modern: str
    legacy: tuple[str, ...] = field(default_factory=tuple)


GRAPHQL_DOCUMENT_SETS: Mapping[str, GraphQLDocuments] = {
    "polaris-2026-08": GraphQLDocuments(
        version="polaris-2026-08",
        modern="27130156389949648",
        legacy=("8845758582119845", "17991233890457762"),
    ),
    "polaris-2025": GraphQLDocuments(
        version="polaris-2025",
        modern="27130156389949648",
        legacy=("8845758582119845", "17991233890457762"),
    ),
}


def _valid_doc_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text.isdigit() and 8 <= len(text) <= 32 else None


def load_graphql_documents(environ: Mapping[str, str] | None = None) -> GraphQLDocuments:
    """Load a named built-in document set with validated environment overrides."""

    env = os.environ if environ is None else environ
    version = env.get("IG_GRAPHQL_DOCSET", "polaris-2026-08").strip()
    selected = GRAPHQL_DOCUMENT_SETS.get(version, GRAPHQL_DOCUMENT_SETS["polaris-2026-08"])

    modern = _valid_doc_id(env.get("IG_GRAPHQL_MODERN_DOC_ID")) or selected.modern
    legacy_raw = env.get("IG_GRAPHQL_LEGACY_DOC_IDS", "")
    if legacy_raw.strip():
        try:
            decoded = json.loads(legacy_raw)
            values = decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            values = legacy_raw.split(",")
        legacy = tuple(
            doc_id
            for doc_id in (_valid_doc_id(value) for value in values)
            if doc_id and doc_id != modern
        )
    else:
        legacy = tuple(doc_id for doc_id in selected.legacy if doc_id != modern)
    return GraphQLDocuments(version=version, modern=modern, legacy=legacy)
