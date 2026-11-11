"""HTTP client for the Vorwerk Kobold cloud (MyKobold app backend)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

import aiohttp

from .const import (
    APP_BUILD,
    APP_OS,
    APP_OS_VERSION,
    APP_USER_AGENT,
    APP_VERSION,
    AUTH_CLIENT_ID,
    AUTH_HOST,
    COMPANION_HOST,
    ORBITAL_HOST,
    VENDOR_ID,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)


class KoboldError(Exception):
    """Base error."""


class KoboldConnectionError(KoboldError):
    """The cloud could not be reached."""


class KoboldAuthError(KoboldError):
    """The credentials are invalid or expired; the user has to log in again."""


class KoboldApiError(KoboldError):
    """The cloud answered with an unexpected error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message[:200]}")
        self.status = status


def accept_language(market: str) -> str:
    market = (market or "de").lower()
    return {"en": "en-GB"}.get(market, f"{market}-{market.upper()}")


def _app_headers(market: str) -> dict[str, str]:
    return {
        "Accept-Language": accept_language(market),
        "mobile-app-version": APP_VERSION,
        "mobile-app-build": APP_BUILD,
        "mobile-app-os": APP_OS,
        "mobile-app-os-version": APP_OS_VERSION,
        "User-Agent": APP_USER_AGENT,
    }


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: Any = None,
    want: str = "json",
) -> Any:
    try:
        async with session.request(
            method, url, headers=headers, json=json, timeout=TIMEOUT
        ) as resp:
            text = await resp.text()
            if resp.status in (401, 403):
                raise KoboldAuthError(f"HTTP {resp.status}: {text[:200]}")
            if resp.status >= 400:
                raise KoboldApiError(resp.status, text)
            if want == "response":
                return resp, text
            if not text:
                return None
            try:
                return await resp.json(content_type=None)
            except ValueError:
                return text
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise KoboldConnectionError(str(err) or type(err).__name__) from err


class KoboldAuth:
    """Passwordless (e-mail code) login against the MyKobold Auth0 tenant."""

    def __init__(self, session: aiohttp.ClientSession, market: str) -> None:
        self._session = session
        self._market = market

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept-Language": accept_language(self._market),
            "User-Agent": APP_USER_AGENT,
        }

    async def send_code(self, email: str) -> None:
        """Ask the cloud to e-mail a one-time code."""
        await _request(
            self._session,
            "POST",
            f"{AUTH_HOST}/passwordless/start",
            headers=self._headers(),
            json={
                "client_id": AUTH_CLIENT_ID,
                "email": email,
                "send": "code",
                "connection": "email",
            },
        )

    async def verify_code(self, email: str, code: str) -> dict[str, Any]:
        """Exchange the e-mailed code for tokens."""
        result = await _request(
            self._session,
            "POST",
            f"{AUTH_HOST}/oauth/token",
            headers=self._headers(),
            json={
                "client_id": AUTH_CLIENT_ID,
                "grant_type": "http://auth0.com/oauth/grant-type/passwordless/otp",
                "realm": "email",
                "username": email,
                "otp": code.strip(),
                "scope": "openid profile email offline_access",
                "platform": APP_OS,
                "locale": self._market,
                "source": "vorwerk_auth0" if self._market == "de" else "vorwerk_auth0_international",
            },
        )
        if not isinstance(result, dict) or not result.get("id_token"):
            raise KoboldAuthError("Login response contained no id_token")
        return result

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Get a new id_token with a refresh token."""
        result = await _request(
            self._session,
            "POST",
            f"{AUTH_HOST}/oauth/token",
            headers=self._headers(),
            json={
                "client_id": AUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if not isinstance(result, dict) or not result.get("id_token"):
            raise KoboldAuthError("Refresh response contained no id_token")
        return result


TokenProvider = Callable[[], Awaitable[str]]


class KoboldApi:
    """REST calls against the Orbital (robots) and Companion (socket login) APIs."""

    def __init__(
        self, session: aiohttp.ClientSession, token: TokenProvider, market: str
    ) -> None:
        self._session = session
        self._token = token
        self._market = market

    async def _orbital(self, method: str, path: str, json: Any = None, app: bool = False) -> Any:
        headers = {
            "Authorization": f"Auth0Bearer {await self._token()}",
            "Accept": "application/vnd.neato.orbital-http.v1+json",
            "Content-Type": "application/json",
            "User-Agent": APP_USER_AGENT,
        }
        if app:
            headers.update(_app_headers(self._market))
        return await _request(
            self._session, method, f"{ORBITAL_HOST}{path}", headers=headers, json=json
        )

    async def robots(self) -> list[dict[str, Any]]:
        result = await self._orbital("GET", "/users/me/robots")
        return [r for r in result or [] if isinstance(r, dict) and r.get("id")]

    async def floorplans(self, robot_id: str) -> list[dict[str, Any]]:
        result = await self._orbital(
            "GET", f"/robots/{robot_id}/floorplans?sort_by=promoted_at&sort_order=asc"
        )
        return [m for m in result or [] if isinstance(m, dict)]

    async def tracks(self, floorplan_uuid: str) -> list[dict[str, Any]]:
        result = await self._orbital("GET", f"/maps/floorplans/{floorplan_uuid}/tracks")
        return [t for t in result or [] if isinstance(t, dict)]

    async def cleaning_maps(self, robot_id: str) -> list[dict[str, Any]]:
        result = await self._orbital(
            "GET", f"/robots/{robot_id}/cleaningmaps?cleaning_types[]=persistent"
        )
        if isinstance(result, dict):
            result = result.get("cleaning_maps") or result.get("maps") or [result]
        return [m for m in result or [] if isinstance(m, dict)]

    async def start_cleaning(self, robot_id: str, body: dict[str, Any]) -> Any:
        return await self._orbital(
            "POST", f"/robots/{robot_id}/cleaning/v2", json=body, app=True
        )

    async def send_ability(self, serial: str, ability: str) -> Any:
        return await self._orbital(
            "POST",
            f"/vendors/{VENDOR_ID}/robots/{serial}/messages",
            json={"ability": ability},
        )

    async def probe(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        """Call an endpoint and describe the answer (diagnostics only)."""
        try:
            result = await self._orbital(method, path, json=json)
        except KoboldApiError as err:
            return {"status": err.status, "error": str(err)[:160]}
        except KoboldError as err:
            return {"error": type(err).__name__}
        info: dict[str, Any] = {"status": "ok", "type": type(result).__name__}
        if isinstance(result, list):
            info["len"] = len(result)
            if result and isinstance(result[0], dict):
                info["item_keys"] = sorted(result[0])[:40]
        elif isinstance(result, dict):
            info["keys"] = sorted(result)[:40]
        text = repr(result)
        if len(text) <= 1500:
            info["value"] = result
        return info

    async def socket_authorization(self) -> str:
        """Log in to Companion and return the Authorization header for the socket."""
        headers = {"Authorization": f"Bearer {await self._token()}", **_app_headers(self._market)}
        resp, _text = await _request(
            self._session,
            "POST",
            f"{COMPANION_HOST}/api/v1/profile/login",
            headers=headers,
            want="response",
        )
        authorization = resp.headers.get("Authorization")
        if not authorization:
            raise KoboldApiError(resp.status, "Companion login returned no Authorization header")
        return authorization

    def socket_headers(self, authorization: str) -> dict[str, str]:
        return {"Authorization": authorization, **_app_headers(self._market)}
