"""Tests for the port picker.

freeport exists specifically because of Windows bind semantics, so these run on both
CI platforms. Stdlib only, matching the module under test.
"""

import socket

import freeport  # top-level: scripts/ is on sys.path via conftest, and is not a package


def test_is_free_reports_taken_port():
    """A port actively held by a listening socket must NOT be reported free.

    This is the property that breaks if SO_REUSEADDR is ever added to is_free(): on
    Windows that option lets you bind a port another process holds, which would make
    every port look free and hand Streamlit a port it cannot use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert freeport.is_free(port) is False
    # Released again once the holder closes.
    assert freeport.is_free(port) is True


def test_find_free_port_walks_up_past_a_taken_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        found = freeport.find_free_port(taken, limit=20)
        assert found > taken
        assert freeport.is_free(found)


def test_find_free_port_returns_preferred_when_available():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        port = probe.getsockname()[1]
    assert freeport.find_free_port(port, limit=20) == port


def test_main_prints_port_to_stdout_and_notice_to_stderr(capsys):
    """The launch scripts capture stdout, so the notice must not contaminate it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        assert freeport.main([str(taken)]) == 0
        captured = capsys.readouterr()

    assert captured.out.strip().isdigit()
    assert int(captured.out.strip()) > taken
    assert "in use" in captured.err


def test_main_fails_when_no_port_is_free(monkeypatch, capsys):
    monkeypatch.setattr(freeport, "is_free", lambda port: False)
    assert freeport.main(["9000"]) == 1
    assert "error:" in capsys.readouterr().err
