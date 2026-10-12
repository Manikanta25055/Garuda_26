"""The systemd heartbeat: silent outside systemd, one datagram per ping inside."""
import socket

from basic_pipelines.garuda_core import watchdog


def test_notify_does_nothing_without_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert watchdog.notify("WATCHDOG=1") is False


def test_notify_sends_the_message_to_the_socket(monkeypatch, tmp_path):
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(2)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        assert watchdog.notify("WATCHDOG=1") is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()


def test_the_ping_interval_is_a_third_of_the_units_watchdog(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "90000000")
    assert watchdog.interval_s() == 30.0
    monkeypatch.delenv("WATCHDOG_USEC")
    assert watchdog.interval_s(default=20.0) == 20.0
