"""Tests for the port picker.

freeport exists specifically because of Windows bind semantics, so these matter
most on the Windows CI job. Stdlib only, matching the module under test.
"""

import socket

import freeport  # top-level: scripts/ is on sys.path via conftest, and is not a package


def _listener(reuse: bool) -> socket.socket:
    """A real listening socket on an ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def test_actively_served_port_is_not_free_even_with_so_reuseaddr():
    """The regression this module exists for.

    Streamlit's server binds with SO_REUSEADDR. On Windows that lets a *second*
    process bind the same port, so a bind-only check reports an actively-served
    port as free and you get two servers on one port. Only the connect probe
    catches it.

    The holder MUST set SO_REUSEADDR: without it, a bind-only implementation
    also passes this test on Windows, which is exactly the false confidence the
    earlier version of this file gave. (On Linux it passes either way, so the
    Windows CI job is the one that makes this test meaningful.)
    """
    sock = _listener(reuse=True)
    try:
        port = sock.getsockname()[1]
        assert freeport.is_free(port) is False
    finally:
        sock.close()


def test_plain_listener_is_not_free():
    sock = _listener(reuse=False)
    try:
        assert freeport.is_free(sock.getsockname()[1]) is False
    finally:
        sock.close()


def test_port_is_free_again_once_released():
    sock = _listener(reuse=False)
    port = sock.getsockname()[1]
    sock.close()
    assert freeport.is_free(port) is True


def test_find_free_port_walks_up_past_a_taken_port():
    sock = _listener(reuse=True)
    try:
        taken = sock.getsockname()[1]
        found = freeport.find_free_port(taken, limit=20)
        assert found > taken
        assert freeport.is_free(found)
    finally:
        sock.close()


def test_find_free_port_returns_preferred_when_available():
    sock = _listener(reuse=False)
    port = sock.getsockname()[1]
    sock.close()
    assert freeport.find_free_port(port, limit=20) == port


def test_main_prints_port_to_stdout_and_notice_to_stderr(capsys):
    """The launch scripts capture stdout, so the notice must not contaminate it."""
    sock = _listener(reuse=True)
    try:
        taken = sock.getsockname()[1]
        assert freeport.main([str(taken)]) == 0
        captured = capsys.readouterr()
    finally:
        sock.close()

    assert captured.out.strip().isdigit()
    assert int(captured.out.strip()) > taken
    assert "in use" in captured.err


def test_main_fails_when_no_port_is_free(monkeypatch, capsys):
    monkeypatch.setattr(freeport, "is_free", lambda port: False)
    assert freeport.main(["9000"]) == 1
    assert "error:" in capsys.readouterr().err
