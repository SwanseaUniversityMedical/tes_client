from __future__ import annotations

from typing import Any

import httpx

from .auth import KeycloakTokenManager
from .models import TesState, TesTask


class TesClient:
    """REST client for a GA4GH TES endpoint secured with Keycloak OIDC."""

    def __init__(
        self,
        tes_url: str,
        token_manager: KeycloakTokenManager,
        timeout: float = 60.0,
    ) -> None:
        self._base = tes_url.rstrip("/")
        self._auth = token_manager
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = httpx.get(
            f"{self._base}{path}",
            params=params,
            headers=self._auth.auth_header(),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        resp = httpx.post(
            f"{self._base}{path}",
            json=body,
            headers={**self._auth.auth_header(), "Content-Type": "application/json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def submit(self, task: TesTask) -> str:
        """Submit a task and return its server-assigned ID."""
        data = self._post("/v1/tasks", task.submission_dict())
        return data["id"]

    def get(self, task_id: str, *, full: bool = False) -> TesTask:
        """Fetch a task by ID.  Pass ``full=True`` to include executor logs."""
        view = "FULL" if full else "MINIMAL"
        data = self._get(f"/v1/tasks/{task_id}", params={"view": view})
        return TesTask.model_validate(data)

    def list_tasks(
        self,
        *,
        name_prefix: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
        view: str = "MINIMAL",
    ) -> list[TesTask]:
        """Return a page of tasks.  Paginates automatically if ``page_token`` is given."""
        params: dict[str, Any] = {"view": view}
        if name_prefix:
            params["name_prefix"] = name_prefix
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token
        data = self._get("/v1/tasks", params=params)
        return [TesTask.model_validate(t) for t in data.get("tasks", [])]

    def cancel(self, task_id: str) -> None:
        """Request cancellation of a running task."""
        self._post(f"/v1/tasks/{task_id}:cancel", {})

    def service_info(self) -> dict[str, Any]:
        return self._get("/v1/service-info")

    def state(self, task_id: str) -> TesState:
        task = self.get(task_id)
        return task.state or TesState.UNKNOWN
