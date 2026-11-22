"""Small client for the NLB Catalogue API v2 and Library API v1.

Specs: docs/nlb-catalogue-openapi.json, docs/nlb-library-openapi.json. Auth is two headers, X-Api-Key and
X-App-Code, read from .env (NLB_API_KEY, NLB_APP_CODE). Keys are never logged
or included in exceptions.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

import httpx
from dotenv import load_dotenv

BASE_URL = "https://openweb.nlb.gov.sg/api"
CATALOGUE = "v2/Catalogue"
LIBRARY = "v1/Library"

log = logging.getLogger(__name__)

# httpx logs request lines at INFO; keep it quiet so nothing request-related leaks.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ResponseHook = Callable[[str, dict[str, Any], Any], None]


class NLBError(Exception):
    """Non-2xx response from the API. Body shape: {statusCode, error, message}."""

    def __init__(self, status: int, error: str, message: str):
        self.status = status
        self.error = error
        self.message = message
        super().__init__(f"NLB API {status} {error}: {message}")


class NLBClient:
    def __init__(
        self,
        api_key: str | None = None,
        app_code: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        on_response: ResponseHook | None = None,
        max_retries: int = 2,
        min_gap: float = 1.5,
        transport: httpx.BaseTransport | None = None,  # for tests
    ):
        """min_gap: seconds between the end of one request and the start of the next, whatever made them
        (search, availability pages, poll, Check now). The bot shares one client, so this spaces every call."""
        load_dotenv()
        api_key = api_key or os.getenv("NLB_API_KEY")
        app_code = app_code or os.getenv("NLB_APP_CODE")
        missing = [n for n, v in (("NLB_API_KEY", api_key), ("NLB_APP_CODE", app_code)) if not v]
        if missing:
            raise RuntimeError(f"Missing in environment/.env: {', '.join(missing)}")
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-Api-Key": api_key, "X-App-Code": app_code, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self.on_response = on_response
        self.max_retries = max_retries
        self.min_gap = min_gap
        self._last_request = float("-inf")  # time.monotonic() when the previous request ended

    def _wait_for_gap(self) -> None:
        wait = self._last_request + self.min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "NLBClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, endpoint: str, params: dict[str, Any], api: str = CATALOGUE) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            self._wait_for_gap()
            try:
                resp = self._http.get(f"/{api}/{endpoint}", params=params)
            finally:
                self._last_request = time.monotonic()
            if resp.status_code != 429:
                if attempt:
                    log.info("NLB %s succeeded after %d rate-limited attempt(s)", endpoint, attempt)
                break
            # Log every 429, even ones a retry recovers from, to see how close we run to NLB's limit.
            if attempt == self.max_retries:
                log.warning("NLB 429 on %s (attempt %d/%d): giving up", endpoint, attempt + 1, attempts)
                break
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after or "")
            except ValueError:
                wait = 2.0 * (attempt + 1)
            wait = min(wait, 30.0)
            # Retry-After is logged as sent: it's the only hint NLB gives about its limit.
            sent = f" (Retry-After: {retry_after})" if retry_after is not None else ""
            log.warning("NLB 429 on %s (attempt %d/%d): retrying in %.0fs%s", endpoint, attempt + 1, attempts, wait, sent)
            time.sleep(wait)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.is_error:
            body = data if isinstance(data, dict) else {}
            raise NLBError(
                resp.status_code,
                str(body.get("error") or resp.reason_phrase),
                str(body.get("message") or (resp.text[:200] if resp.text else "")),
            )
        if self.on_response:
            self.on_response(endpoint, params, data)
        return data

    def search_titles(self, keywords: str, limit: int = 20, offset: int = 0, **filters: Any) -> dict:
        """GET /SearchTitles. Filters use spec names, e.g. MaterialTypes=["bks"], Availability=True."""
        return self._get("SearchTitles", {"Keywords": keywords, "Limit": limit, "Offset": offset, **filters})

    def get_titles(self, limit: int = 20, offset: int = 0, **params: Any) -> dict:
        """GET /GetTitles. Params use spec names: Keywords, Title, Author, Subject, ISBN, ..."""
        return self._get("GetTitles", {"Limit": limit, "Offset": offset, **params})

    def get_availability(
        self,
        brn: int,
        location_codes: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """GET /GetAvailabilityInfo for one BRN (one page)."""
        return self._get(
            "GetAvailabilityInfo",
            {
                "BRN": brn,
                "LocationCode": ",".join(location_codes) if location_codes else None,
                "Limit": limit,
                "Offset": offset,
            },
        )

    def get_all_availability(self, brn: int, location_codes: list[str] | None = None) -> list[dict]:
        """All items for a BRN, following hasMoreRecords/nextRecordsOffset."""
        items: list[dict] = []
        offset = 0
        while True:
            page = self.get_availability(brn, location_codes, offset=offset)
            items.extend(page.get("items") or [])
            if not page.get("hasMoreRecords") or page.get("nextRecordsOffset") in (None, offset):
                return items
            offset = page["nextRecordsOffset"]

    def get_branches(self, list_type: str = "onsite") -> dict:
        """GET Library/GetBranches. "onsite" = active branches shown on NLB's site (includes ones closed for renovation)."""
        return self._get("GetBranches", {"ListType": list_type}, api=LIBRARY)
