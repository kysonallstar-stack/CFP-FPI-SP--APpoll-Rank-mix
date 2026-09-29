"""Thin client for the CollegeFootballData API.

Only does HTTP: auth header, retries, JSON decoding. Caching decisions live in
fetch.py so they can be tested without the network (tests pass a fake client).
"""
import os
import time

import requests


class CFBDClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "CFBDClient":
        api = cfg["api"]
        key = os.environ.get(api["key_env"])
        if not key:
            raise SystemExit(f"Set the {api['key_env']} environment variable to your CFBD API key.")
        return cls(api["base_url"], key, api["timeout_seconds"], api["max_retries"])

    def get(self, endpoint: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        for attempt in range(1, self.max_retries + 1):
            self.calls += 1
            resp = self.session.get(url, params=params, timeout=self.timeout)
            # Retry only on rate limiting and server errors; 4xx means our request is wrong.
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
