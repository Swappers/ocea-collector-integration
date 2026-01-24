from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import requests

AUTHORITY = "https://osbespaceresident.b2clogin.com/osbespaceresident.onmicrosoft.com"
POLICY = "b2c_1a_signup_signin"
AUTHORIZE_URL = f"{AUTHORITY}/{POLICY}/oauth2/v2.0/authorize"
TOKEN_URL = f"{AUTHORITY}/{POLICY}/oauth2/v2.0/token"
CLIENT_ID = "1cacfb15-0b3c-42cc-a662-736e4737e7d9"
REDIRECT_URI = "https://espace-resident.ocea-sb.com"
SCOPE = (
    "https://osbespaceresident.onmicrosoft.com/"
    "app-imago-espace-resident-back-prod/user_impersonation "
    "openid profile offline_access"
)

API_BASE = "https://espace-resident-api.ocea-sb.com/api/v1"

FLUIDS = {
    "eau_froide": {
        "api_name": "EauFroide",
        "unit": "L",
        "label": "Eau froide",
    },
    "eau_chaude": {
        "api_name": "EauChaude",
        "unit": "L",
        "label": "Eau chaude",
    },
    "cetc": {
        "api_name": "Cetc",
        "unit": "kWh",
        "label": "CETC",
    },
}

LOGGER = logging.getLogger(__name__)


class OceaAuthError(RuntimeError):
    pass


def _build_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_settings(html: str) -> dict:
    match = re.search(r"var SETTINGS = (\{.*?\})\s*;", html, re.S)
    if not match:
        raise OceaAuthError("Unable to parse B2C settings.")
    return json.loads(match.group(1))


def _extract_code(location: str) -> str | None:
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    return qs.get("code", [None])[0]


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("oui", "yes", "true"):
            return 1.0
        if lowered in ("non", "no", "false", "pas de fuite", "aucune fuite"):
            return 0.0
        value = value.replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_conso(payload: dict) -> dict:
    consos = payload.get("consommations", [])
    consos_sorted = sorted(consos, key=lambda x: x.get("date", ""))
    latest = consos_sorted[-1] if consos_sorted else {}
    unit = payload.get("unite")
    factor = 1000 if unit == "m3" else 1
    latest_value = _to_float(latest.get("valeur"))
    if latest_value is not None:
        latest_value *= factor
    leak_raw = latest.get("fuiteEstimee")
    leak_estimate = None if leak_raw is None else str(leak_raw)
    latest_date = latest.get("date")

    daily = None
    if len(consos_sorted) >= 2:
        last_val = _to_float(consos_sorted[-1].get("valeur"))
        prev_val = _to_float(consos_sorted[-2].get("valeur"))
        if last_val is not None and prev_val is not None:
            daily = (last_val - prev_val) * factor
            daily = round(daily, 3)
            if daily < 0:
                daily = None

    return {
        "latest_value": latest_value,
        "latest_date": latest_date,
        "leak_estimate": leak_estimate,
        "unit": "L" if unit == "m3" else unit,
        "daily": daily,
    }


def _format_utc(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class OceaClient:
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._session = requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    def _try_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        data = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "scope": SCOPE,
        }
        resp = self._session.post(TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            LOGGER.warning(
                "Token refresh failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        token = resp.json()
        self._access_token = token.get("access_token")
        self._refresh_token = token.get("refresh_token", self._refresh_token)
        LOGGER.debug("Refreshed access token.")
        return bool(self._access_token)

    def _try_ropc(self) -> bool:
        data = {
            "client_id": CLIENT_ID,
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
            "scope": SCOPE,
        }
        resp = self._session.post(TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            LOGGER.warning(
                "ROPC auth failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        token = resp.json()
        self._access_token = token.get("access_token")
        self._refresh_token = token.get("refresh_token")
        LOGGER.debug("Authenticated with ROPC flow.")
        return bool(self._access_token)

    def _auth_pkce(self) -> None:
        code_verifier, code_challenge = _build_pkce_pair()
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "response_mode": "query",
            "scope": SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
        }

        auth_resp = self._session.get(AUTHORIZE_URL, params=params, timeout=30)
        if auth_resp.status_code >= 400:
            raise OceaAuthError(
                f"Auth start failed: status={auth_resp.status_code}."
            )
        settings = _parse_settings(auth_resp.text)

        trans_id = settings.get("transId")
        csrf = settings.get("csrf")
        tenant = settings.get("hosts", {}).get("tenant")
        policy = settings.get("hosts", {}).get("policy")
        if not trans_id or not tenant or not policy:
            raise OceaAuthError("Missing B2C settings fields.")

        self_asserted = f"https://osbespaceresident.b2clogin.com{tenant}/SelfAsserted"
        sa_params = {"tx": trans_id, "p": policy}
        payload = {
            "request_type": "RESPONSE",
            "signInName": self._username,
            "logonIdentifier": self._username,
            "email": self._username,
            "password": self._password,
        }
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if csrf:
            payload["csrf_token"] = csrf
            headers["X-CSRF-TOKEN"] = csrf

        sa_resp = self._session.post(
            self_asserted,
            params=sa_params,
            data=payload,
            headers=headers,
            timeout=30,
        )
        if sa_resp.status_code >= 400:
            LOGGER.warning("SelfAsserted failed: status=%s", sa_resp.status_code)
            raise OceaAuthError(f"B2C SelfAsserted failed ({sa_resp.status_code}).")
        content_type = sa_resp.headers.get("content-type", "")
        if "json" in content_type:
            data = sa_resp.json()
            status = str(data.get("status"))
            if status not in ("200", "ok", "OK"):
                message = data.get("message") or "B2C SelfAsserted returned an error."
                raise OceaAuthError(message)

        confirm = f"https://osbespaceresident.b2clogin.com{tenant}/api/CombinedSigninAndSignup/confirmed"
        confirm_params = {"tx": trans_id, "p": policy}
        if csrf:
            confirm_params["csrf_token"] = csrf

        confirm_headers = {"X-Requested-With": "XMLHttpRequest"}
        if csrf:
            confirm_headers["X-CSRF-TOKEN"] = csrf

        confirm_resp = self._session.get(
            confirm,
            params=confirm_params,
            headers=confirm_headers,
            allow_redirects=False,
            timeout=30,
        )

        code = None
        if confirm_resp.status_code in (302, 303):
            location = confirm_resp.headers.get("location", "")
            code = _extract_code(location)
        elif confirm_resp.status_code == 200:
            content_type = confirm_resp.headers.get("content-type", "")
            if "application/json" in content_type:
                data = confirm_resp.json()
                redirect_url = data.get("redirectUrl") or data.get("redirect_uri")
                if redirect_url:
                    code = _extract_code(redirect_url)
            if not code:
                code = _extract_code(confirm_resp.text)

        if not code:
            follow_resp = self._session.get(
                confirm,
                params=confirm_params,
                headers=confirm_headers,
                allow_redirects=True,
                timeout=30,
            )
            code = _extract_code(follow_resp.url)

        if not code:
            raise OceaAuthError("Authorization code not found.")

        token_data = {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
        }
        token_resp = self._session.post(TOKEN_URL, data=token_data, timeout=30)
        if token_resp.status_code != 200:
            LOGGER.warning(
                "Token exchange failed: status=%s body=%s",
                token_resp.status_code,
                token_resp.text[:200],
            )
            raise OceaAuthError("Token exchange failed.")
        token = token_resp.json()
        self._access_token = token.get("access_token")
        self._refresh_token = token.get("refresh_token")
        LOGGER.debug("Authenticated with PKCE flow.")

    def _handle_unauthorized(self) -> bool:
        LOGGER.warning("HTTP 401 received; attempting token refresh.")
        if self._try_refresh():
            return True
        LOGGER.warning("Token refresh failed; retrying full authentication.")
        self._access_token = None
        self._refresh_token = None
        try:
            self._auth_pkce()
        except OceaAuthError as err:
            LOGGER.error("Full authentication failed after 401: %s", err)
            return False
        return bool(self._access_token)

    def _ensure_token(self) -> None:
        if self._access_token:
            return
        if self._try_refresh():
            return
        if self._try_ropc():
            return
        LOGGER.debug("ROPC failed or not supported, trying PKCE flow.")
        self._auth_pkce()

    def _get(self, path: str) -> dict:
        self._ensure_token()
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = self._session.get(url, headers=headers, timeout=30)
        if resp.status_code == 401 and self._handle_unauthorized():
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = self._session.get(url, headers=headers, timeout=30)
        if resp.status_code >= 400:
            raise OceaAuthError(f"HTTP {resp.status_code} for {url}")
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        self._ensure_token()
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = self._session.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 401 and self._handle_unauthorized():
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = self._session.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code >= 400:
            raise OceaAuthError(f"HTTP {resp.status_code} for {url}")
        return resp.json()

    def fetch(self) -> dict:
        resident = self._get("/resident")
        occupations = resident.get("occupations", [])
        if not occupations:
            raise OceaAuthError("No occupations found for this account.")
        local_id = occupations[0].get("logementId")
        if not local_id:
            raise OceaAuthError("Unable to determine local ID.")

        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=30)
        conso_payload = {
            "debut": _format_utc(start),
            "fin": _format_utc(end),
            "granularity": "Month",
        }

        results: dict[str, dict] = {}
        for key, meta in FLUIDS.items():
            api_name = meta["api_name"]
            conso = self._post(f"/local/{local_id}/conso/{api_name}", conso_payload)
            results[key] = _parse_conso(conso)

        return results
