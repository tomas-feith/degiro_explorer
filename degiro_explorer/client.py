"""Thin wrapper around degiro-connector for authenticated, read-only access."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from degiro_connector.core.exceptions import DeGiroConnectionError
from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.credentials import Credentials

from config import settings

logger = logging.getLogger(__name__)

# In-app 2FA approval: how long to wait for the user to tap "Yes" in the DEGIRO app.
IN_APP_POLL_SECONDS = 4
IN_APP_MAX_ATTEMPTS = 30  # ~2 minutes


@dataclass
class Session:
    """An authenticated DEGIRO session plus resolved account context."""

    api: TradingAPI
    int_account: int
    base_currency: str


def connect() -> Session:
    """Log in to DEGIRO and resolve int_account + base currency.

    Handles two 2FA styles automatically:
      * TOTP secret (set DEGIRO_TOTP_SECRET in .env), or
      * in-app approval — DEGIRO pushes a prompt to your phone (status 12); we then
        poll while you tap "Yes" in the DEGIRO mobile app.

    Credentials come from the .env file (see config.py). Nothing here ever places an
    order — read access only.
    """
    settings.require_credentials()

    api = TradingAPI(credentials=_build_credentials())
    logger.info("Connecting to DEGIRO...")
    try:
        api.connect()
    except DeGiroConnectionError as exc:
        status = getattr(exc.error_details, "status", None)
        if status == 12:
            _connect_in_app(api, getattr(exc.error_details, "in_app_token", None))
        else:
            raise

    int_account = settings.int_account or _resolve_int_account(api)
    # Write it back so later calls (get_update, get_account_info) build valid URLs.
    api.credentials.int_account = int_account
    base_currency = _resolve_base_currency(api)
    logger.info("Connected (int_account=%s, base_currency=%s)", int_account, base_currency)

    return Session(api=api, int_account=int_account, base_currency=base_currency)


def _build_credentials() -> Credentials:
    return Credentials(
        username=settings.username,
        password=settings.password,
        int_account=settings.int_account,
        totp_secret_key=settings.totp_secret or None,
    )


def _connect_in_app(api: TradingAPI, in_app_token: str | None) -> None:
    """Retry login using DEGIRO's in-app approval, polling until the user approves.

    Reuses the SAME api object/session (rebuilding breaks the approval linkage) and
    just swaps the credentials to the in-app endpoint. While waiting for the user to
    tap "Yes", DEGIRO returns status 12 (in-app needed) or status 3 (still pending);
    both mean "keep polling".
    """
    logger.warning(
        "DEGIRO in-app 2FA required. Open the DEGIRO mobile app and tap 'Yes' to "
        "approve this login (waiting up to %d seconds)...",
        IN_APP_POLL_SECONDS * IN_APP_MAX_ATTEMPTS,
    )
    # Switch the existing credentials to the in-app flow (TOTP must not be sent now).
    api.credentials.totp_secret_key = None
    api.credentials.one_time_password = None
    api.credentials.in_app_token = in_app_token

    for attempt in range(1, IN_APP_MAX_ATTEMPTS + 1):
        try:
            api.connect()
            logger.info("In-app approval confirmed.")
            return
        except DeGiroConnectionError as exc:
            status = getattr(exc.error_details, "status", None)
            if status not in (3, 12):
                raise
            new_token = getattr(exc.error_details, "in_app_token", None)
            if new_token:
                api.credentials.in_app_token = new_token
            logger.info("Waiting for in-app approval... (%d/%d)", attempt, IN_APP_MAX_ATTEMPTS)
            time.sleep(IN_APP_POLL_SECONDS)
    raise TimeoutError(
        "Timed out waiting for DEGIRO in-app approval. Re-run sync and approve the "
        "prompt in the DEGIRO app more quickly."
    )


def _resolve_int_account(api: TradingAPI) -> int:
    details = api.get_client_details() or {}
    data = details.get("data", details)
    int_account = data.get("intAccount")
    if int_account is None:
        raise RuntimeError(
            "Could not resolve intAccount from client details. Set DEGIRO_INT_ACCOUNT in your .env file."
        )
    return int(int_account)


def _resolve_base_currency(api: TradingAPI) -> str:
    """Best-effort base-currency lookup; defaults to EUR."""
    try:
        info = api.get_account_info() or {}
        data = info.get("data", info)
        for key in ("baseCurrency", "currency"):
            if data.get(key):
                return str(data[key])
    except Exception:  # noqa: BLE001 - non-fatal, we fall back to EUR
        logger.warning("Could not determine base currency; defaulting to EUR", exc_info=True)
    return "EUR"
