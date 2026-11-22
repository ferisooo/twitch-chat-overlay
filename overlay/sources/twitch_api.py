"""Small helpers for Twitch's web API (login check, streams, users, device login)."""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"

# What the overlay asks Twitch for: read + send chat, and approve AutoMod.
SCOPES = ("chat:read", "chat:edit", "moderator:manage:automod")
AUTOMOD_SCOPE = "moderator:manage:automod"
TOKEN_GENERATOR_URL = (
    "https://twitchtokengenerator.com/?scope=chat:read+chat:edit+moderator:manage:automod"
)


class TwitchAuthError(RuntimeError):
    """A login problem with a message that is safe to show the user."""


def normalize_oauth(raw: Any) -> str:
    token = str(raw or "").strip()
    if token.lower().startswith("oauth:"):
        token = token[6:].strip()
    # Pasted web pages / multi-line junk must not reach HTTP headers.
    token = token.splitlines()[0].strip() if token else ""
    return "".join(ch for ch in token if ch.isprintable() and ch not in " \t")


def looks_like_oauth_token(token: str) -> bool:
    if len(token) < 20 or len(token) > 128:
        return False
    low = token.lower()
    if "discontinued" in low or "twitchapps" in low or "http://" in low or "https://" in low:
        return False
    cleaned = token
    for ch in "-_+/=":
        cleaned = cleaned.replace(ch, "")
    return cleaned.isalnum()


def _json(req: Request, timeout: float = 12.0) -> Any:
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def validate(token: str) -> dict[str, Any]:
    """Ask Twitch about a token. Raises HTTPError(401) when it's dead."""
    req = Request(VALIDATE_URL, headers={"Authorization": f"OAuth {token}", "Accept": "application/json"})
    data = _json(req, timeout=10)
    return data if isinstance(data, dict) else {}


def check_pasted_token(raw: str) -> dict[str, Any]:
    """Validate a pasted token and return Twitch's info about it."""
    token = normalize_oauth(raw)
    if not token:
        raise TwitchAuthError("Paste a Twitch token first.")
    if not looks_like_oauth_token(token):
        raise TwitchAuthError(
            "That is not an access token. Click Get token, authorize, then paste only the Access token."
        )
    try:
        info = validate(token)
    except HTTPError as exc:
        raise TwitchAuthError("That token is invalid or expired.") from exc
    except (URLError, OSError, ValueError, UnicodeEncodeError) as exc:
        raise TwitchAuthError("Couldn't reach Twitch to check the token. Try again.") from exc
    login = str(info.get("login") or "").strip().lower()
    if not login:
        raise TwitchAuthError("Token is valid but has no username.")
    scopes = {str(s).lower() for s in (info.get("scopes") or [])}
    if "chat:edit" not in scopes and "chat_login" not in scopes:
        raise TwitchAuthError("This token cannot send chat. Make a new one with chat:read and chat:edit.")
    info["token"] = token
    return info


def helix(method: str, path: str, token: str, client_id: str, body: Any = None) -> tuple[int, Any]:
    """Call the Twitch Helix API. Returns (HTTP status, JSON or None)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(HELIX + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=12) as resp:
            raw, status = resp.read(), resp.status
    except HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except OSError:
            raw = b""
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        parsed = None
    return status, parsed


def user_id(login: str, token: str, client_id: str) -> str:
    status, data = helix("GET", f"/users?login={quote(login, safe='')}", token, client_id)
    users = (data.get("data") or []) if status == 200 and isinstance(data, dict) else []
    return str(users[0].get("id") or "") if users else ""


def stream_info(channel: str, token: str, client_id: str) -> Optional[dict[str, str]]:
    """The live stream's id, game and title, or None when offline."""
    login = quote(channel.strip().lstrip("#").lower(), safe="")
    status, data = helix("GET", f"/streams?user_login={login}", token, client_id)
    if status in (401, 403):
        raise TwitchAuthError("login rejected")
    if status != 200 or not isinstance(data, dict):
        raise OSError(f"streams returned {status}")
    streams = data.get("data") or []
    if not streams:
        return None
    s = streams[0]
    return {
        "id": str(s.get("id") or ""),
        "game": str(s.get("game_name") or ""),
        "title": str(s.get("title") or ""),
    }


# ----- Device login (needs your own app's Client ID from dev.twitch.tv) -----

def _post_form(url: str, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
    req = Request(
        url,
        data=urlencode(fields).encode("ascii"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=12) as resp:
            status, raw = resp.status, resp.read()
    except HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except OSError:
            raw = b""
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        data = {}
    return status, data if isinstance(data, dict) else {}


def device_start(client_id: str) -> dict[str, Any]:
    status, data = _post_form(DEVICE_URL, {"client_id": client_id, "scopes": " ".join(SCOPES)})
    if status != 200 or not data.get("device_code"):
        raise TwitchAuthError(
            "Twitch didn't accept that Client ID. Check it, and that the app's Client Type is Public."
        )
    return data


def device_poll(client_id: str, device_code: str) -> tuple[str, dict[str, Any]]:
    """("ok", tokens), ("pending", {}), ("slow_down", {}) or ("failed", {message})."""
    status, data = _post_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "scopes": " ".join(SCOPES),
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    if status == 200 and data.get("access_token"):
        return "ok", data
    message = str(data.get("message") or "").lower()
    if "authorization_pending" in message:
        return "pending", {}
    if "slow_down" in message:
        return "slow_down", {}
    return "failed", {"message": message or f"HTTP {status}"}


def refresh(client_id: str, refresh_token: str) -> dict[str, Any]:
    status, data = _post_form(
        TOKEN_URL,
        {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if status != 200 or not data.get("access_token"):
        raise TwitchAuthError("Twitch login expired. Log in again.")
    return data
