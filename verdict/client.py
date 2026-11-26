"""Flagsmith API client, stdlib only. Every method maps to a verified endpoint."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"{status} {path}: {body[:300]}")
        self.status = status
        self.body = body
        self.path = path


@dataclass
class Flagsmith:
    base: str
    token: str = ""
    timeout: int = 30

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")

    # -- transport -------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None,
              headers: dict | None = None, retries: int = 2) -> Any:
        url = f"{self.base}/api/v1{path}" if path.startswith("/") else f"{self.base}/{path}"
        data = None if body is None else json.dumps(body).encode()
        last: Exception | None = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            if self.token:
                req.add_header("Authorization", f"Token {self.token}")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                body_text = e.read().decode(errors="replace")
                # Retry only server-side failures; 4xx are answers, not accidents.
                if e.code >= 500 and attempt < retries:
                    last = ApiError(e.code, body_text, path)
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise ApiError(e.code, body_text, path) from None
            except urllib.error.URLError as e:
                last = e
                if attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
        raise ApiError(0, str(last), path)

    # -- auth ------------------------------------------------------------
    @classmethod
    def login(cls, base: str, email: str, password: str, timeout: int = 30) -> "Flagsmith":
        tmp = cls(base=base, token="", timeout=timeout)
        data = tmp._call("POST", "/auth/login/", {"email": email, "password": password})
        return cls(base=base, token=data["key"], timeout=timeout)

    # -- reads -----------------------------------------------------------
    def experiments(self, env_key: str) -> list[dict]:
        return self._call("GET", f"/environments/{env_key}/experiments/").get("results", [])

    def experiment(self, env_key: str, experiment_id: int) -> dict:
        return self._call("GET", f"/environments/{env_key}/experiments/{experiment_id}/")

    def results(self, env_key: str, experiment_id: int) -> dict:
        return self._call("GET", f"/environments/{env_key}/experiments/{experiment_id}/results/")

    def refresh_results(self, env_key: str, experiment_id: int) -> int:
        self._call("POST", f"/environments/{env_key}/experiments/{experiment_id}/results/refresh/")
        return 202

    def exposures(self, env_key: str, experiment_id: int) -> dict:
        return self._call("GET", f"/environments/{env_key}/experiments/{experiment_id}/exposures/")

    def feature(self, project_id: int, feature_id: int) -> dict:
        return self._call("GET", f"/projects/{project_id}/features/{feature_id}/")

    def feature_state(self, env_key: str, feature_id: int) -> dict:
        rows = self._call("GET", f"/environments/{env_key}/featurestates/?feature={feature_id}")
        results = rows.get("results", rows)
        if not results:
            raise ApiError(404, "no feature state", f"/featurestates/?feature={feature_id}")
        return results[0]

    def warehouse_connection(self, env_key: str) -> dict | None:
        rows = self._call("GET", f"/environments/{env_key}/warehouse-connections/")
        items = rows.get("results", rows) if isinstance(rows, dict) else rows
        return items[0] if items else None

    def warehouse_events(self, env_key: str, connection_id: int) -> list[str]:
        data = self._call("GET", f"/environments/{env_key}/warehouse-connections/{connection_id}/events/")
        return list(data.get("events") or [])

    # -- writes ----------------------------------------------------------
    def complete_experiment(self, env_key: str, experiment_id: int) -> dict:
        return self._call("POST", f"/environments/{env_key}/experiments/{experiment_id}/complete/")

    def set_winner(self, env_key: str, state_id: int, winner: str,
                   winner_option_id: int, winner_value: str | None = None) -> dict:
        state = self._call("GET", f"/environments/{env_key}/featurestates/{state_id}/")
        allocations = []
        for mv in state.get("multivariate_feature_state_values") or []:
            allocations.append({
                "id": mv["id"],
                "multivariate_feature_option": mv["multivariate_feature_option"],
                "percentage_allocation": 100 if mv["multivariate_feature_option"] == winner_option_id else 0,
            })
        body = {
            "enabled": True,
            "feature_state_value": winner_value if winner_value is not None else winner,
            "multivariate_feature_state_values": allocations,
        }
        return self._call("PUT", f"/environments/{env_key}/featurestates/{state_id}/", body)
