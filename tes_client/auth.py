from __future__ import annotations

import base64
import hashlib
import http.server
import os
import threading
import time
import urllib.parse
import webbrowser
from abc import ABC, abstractmethod
from typing import Any

import httpx


# ------------------------------------------------------------------ #
# Abstract base — shared caching + refresh-token logic                #
# ------------------------------------------------------------------ #

class _CachingTokenManager(ABC):
    """Token cache with automatic refresh-token promotion.

    Subclasses implement ``_do_fetch`` (full grant) and optionally
    ``_do_refresh`` (refresh-token grant).  The ``token`` property calls
    them in the right order so callers never manage token lifecycle.
    """

    _REFRESH_BUFFER = 30

    def __init__(self) -> None:
        self._access_token = ""
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0

    @property
    def token(self) -> str:
        if time.monotonic() < self._expires_at - self._REFRESH_BUFFER:
            return self._access_token
        if self._refresh_token:
            try:
                self._store(self._do_refresh())
                return self._access_token
            except (httpx.HTTPStatusError, NotImplementedError):
                self._refresh_token = None
        self._store(self._do_fetch())
        return self._access_token

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def is_valid(self) -> bool:
        """Return True if the cached token is still usable without a network call."""
        return bool(self._access_token) and time.monotonic() < self._expires_at - self._REFRESH_BUFFER

    def ensure_valid(self) -> None:
        """Trigger re-authentication now if the token is expired or missing.

        Useful in long-running scripts to proactively refresh before a batch of
        calls rather than letting the first call fail mid-flight.
        """
        _ = self.token  # delegate to existing refresh/fetch logic

    def _store(self, payload: dict[str, Any]) -> None:
        self._access_token = payload["access_token"]
        self._refresh_token = payload.get("refresh_token")
        self._expires_at = time.monotonic() + int(payload.get("expires_in", 300))

    def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        resp = httpx.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    @abstractmethod
    def _do_fetch(self) -> dict[str, Any]:
        """Perform a full grant exchange (called when no valid token exists)."""
        ...

    def _do_refresh(self) -> dict[str, Any]:
        """Perform a refresh-token exchange.  Subclasses that receive refresh
        tokens must override this; the base raises ``NotImplementedError`` so
        the caller falls back to ``_do_fetch``."""
        raise NotImplementedError


# ------------------------------------------------------------------ #
# No auth (open / dev endpoints)                                       #
# ------------------------------------------------------------------ #

class NoAuth:
    """Pass-through for TES endpoints that require no authentication."""

    def auth_header(self) -> dict[str, str]:
        return {}

    def is_valid(self) -> bool:
        return True

    def ensure_valid(self) -> None:
        pass


# ------------------------------------------------------------------ #
# Client credentials                                                   #
# ------------------------------------------------------------------ #

class ClientCredentialsAuth(_CachingTokenManager):
    """Machine-to-machine OIDC client-credentials grant.

    Keycloak does not return a refresh token for this grant type, so every
    expiry triggers a fresh ``client_credentials`` POST.
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        token_url: str | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self._token_url = (
            token_url
            or f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"
        )

    def _do_fetch(self) -> dict[str, Any]:
        return self._post(
            self._token_url,
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )


# Backward-compatible alias — existing code using KeycloakTokenManager still works.
KeycloakTokenManager = ClientCredentialsAuth


# ------------------------------------------------------------------ #
# Resource-owner password credentials                                  #
# ------------------------------------------------------------------ #

class PasswordAuth(_CachingTokenManager):
    """OIDC resource-owner password credentials (ROPC) grant.

    Keycloak returns a refresh token for this grant, so subsequent calls after
    the first only POST if the refresh token itself expires.

    Note: ROPC is disabled by default in newer Keycloak realms; enable it under
    Realm Settings → Client Authentication.
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        username: str,
        password: str,
        client_secret: str | None = None,
        scope: str = "openid",
        token_url: str | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.scope = scope
        self._token_url = (
            token_url
            or f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"
        )

    def _do_fetch(self) -> dict[str, Any]:
        data: dict[str, str] = {
            "grant_type": "password",
            "client_id": self.client_id,
            "username": self.username,
            "password": self.password,
            "scope": self.scope,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return self._post(self._token_url, data)

    def _do_refresh(self) -> dict[str, Any]:
        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self._refresh_token,  # type: ignore[assignment]
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return self._post(self._token_url, data)


# ------------------------------------------------------------------ #
# Authorization code + PKCE                                            #
# ------------------------------------------------------------------ #

class AuthorizationCodeAuth(_CachingTokenManager):
    """Interactive OIDC authorization-code flow with PKCE (RFC 7636).

    On first use (or when the refresh token expires), opens the Keycloak login
    page in the system browser and starts a one-shot local HTTP server on
    ``redirect_port`` to receive the callback.  After the user authenticates,
    the refresh token is used silently until it too expires.

    Example::

        auth = AuthorizationCodeAuth(
            base_url="https://keycloak.example.org",
            realm="my-realm",
            client_id="tes-public-client",
            redirect_port=8080,
        )
        client = TesClient("https://tes.example.org", token_manager=auth)
        # first call opens browser; subsequent calls are silent
        task_id = client.submit(task)
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str | None = None,
        redirect_port: int = 8080,
        scope: str = "openid",
        token_url: str | None = None,
        auth_url: str | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_port = redirect_port
        self.scope = scope
        self._token_url = (
            token_url
            or f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"
        )
        self._auth_url = (
            auth_url
            or f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/auth"
        )

    def _do_fetch(self) -> dict[str, Any]:
        verifier, challenge = _pkce_pair()
        redirect_uri = f"http://localhost:{self.redirect_port}/callback"

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        login_url = f"{self._auth_url}?{urllib.parse.urlencode(params)}"
        code = _receive_auth_code(login_url, self.redirect_port)

        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return self._post(self._token_url, data)

    def _do_refresh(self) -> dict[str, Any]:
        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self._refresh_token,  # type: ignore[assignment]
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return self._post(self._token_url, data)


# ------------------------------------------------------------------ #
# PKCE helpers                                                         #
# ------------------------------------------------------------------ #

def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256 challenge) as unpadded URL-safe base64 strings."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ------------------------------------------------------------------ #
# One-shot local callback server                                        #
# ------------------------------------------------------------------ #

_SUCCESS_HTML = b"""\
<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:4em">
<h2>Authorization complete</h2>
<p>You can close this tab and return to your terminal.</p>
</body></html>"""

_ERROR_HTML = """\
<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:4em">
<h2>Authorization failed</h2><p>{error}: {description}</p>
</body></html>"""


def _receive_auth_code(login_url: str, port: int, timeout: float = 300.0) -> str:
    """Open *login_url* in the default browser, wait for the auth-code callback.

    Starts a temporary ``HTTPServer`` on ``localhost:{port}`` that handles
    exactly one request (the Keycloak redirect).  Returns the ``code`` query
    parameter or raises ``RuntimeError`` on error or timeout.
    """
    received: dict[str, str] = {}
    ready = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "error" in qs:
                received["error"] = qs["error"][0]
                received["error_description"] = qs.get("error_description", [""])[0]
                body = _ERROR_HTML.format(
                    error=received["error"],
                    description=received["error_description"],
                ).encode()
            else:
                received["code"] = qs.get("code", [""])[0]
                body = _SUCCESS_HTML
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            ready.set()

        def log_message(self, *_: object) -> None:
            pass  # suppress default access log noise

    server = http.server.HTTPServer(("localhost", port), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Opening browser for login…\n  {login_url}")
    if not webbrowser.open(login_url):
        print("Could not open browser automatically — visit the URL above manually.")

    if not ready.wait(timeout=timeout):
        server.server_close()
        raise RuntimeError(f"Authorization timed out after {timeout}s (no callback received).")

    server.server_close()
    thread.join(timeout=5)

    if "error" in received:
        raise RuntimeError(
            f"Authorization failed: {received['error']} — {received.get('error_description', '')}"
        )
    if not received.get("code"):
        raise RuntimeError("No authorization code received in callback.")

    return received["code"]
