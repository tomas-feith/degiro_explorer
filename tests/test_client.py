"""Tests for the in-app 2FA login state machine.

The real flow needs a phone tap, so these drive a fake API. They lock in the rules that
were painful to discover: status 3 means "still pending", the api object must be reused
so DEGIRO keeps the approval linkage, and TOTP must not be sent during in-app approval.
"""

import pytest

from degiro_explorer import client


class _Details:
    def __init__(self, status, in_app_token=None):
        self.status = status
        self.in_app_token = in_app_token


class _ConnError(Exception):
    def __init__(self, status, in_app_token=None):
        super().__init__(f"status {status}")
        self.error_details = _Details(status, in_app_token)


class _Credentials:
    def __init__(self):
        self.totp_secret_key = "SOMETOTPSECRET"
        self.one_time_password = "123456"
        self.in_app_token = None
        self.int_account = None


class _FakeAPI:
    """Fails with the given statuses in order, then succeeds."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.credentials = _Credentials()
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        if self.statuses:
            raise _ConnError(self.statuses.pop(0), in_app_token=f"tok{self.connect_calls}")


@pytest.fixture(autouse=True)
def _no_sleep_and_real_exception(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    # The module catches DeGiroConnectionError; point that at our fake.
    monkeypatch.setattr(client, "DeGiroConnectionError", _ConnError)


def test_status_3_is_pending_not_failure():
    """status 3 during polling means 'still waiting for the tap', NOT an error."""
    api = _FakeAPI([3, 3, 12])
    client._connect_in_app(api, "initial")
    assert api.connect_calls == 4  # three pending, then success


def test_reuses_same_api_object_and_clears_totp():
    """Rebuilding the api per retry breaks DEGIRO's approval linkage."""
    api = _FakeAPI([3])
    client._connect_in_app(api, "initial")
    assert api.credentials.totp_secret_key is None
    assert api.credentials.one_time_password is None


def test_refreshes_in_app_token_between_polls():
    api = _FakeAPI([12, 12])
    client._connect_in_app(api, "initial")
    # Each failure hands back a new token that must be carried into the next attempt.
    assert api.credentials.in_app_token == "tok2"


def test_unexpected_status_propagates():
    """A real auth failure must surface, not be swallowed by the polling loop."""
    api = _FakeAPI([5])
    with pytest.raises(_ConnError):
        client._connect_in_app(api, "initial")


def test_times_out_after_max_attempts(monkeypatch):
    monkeypatch.setattr(client, "IN_APP_MAX_ATTEMPTS", 3)
    api = _FakeAPI([3] * 10)
    with pytest.raises(TimeoutError, match="Timed out"):
        client._connect_in_app(api, "initial")
    assert api.connect_calls == 3
