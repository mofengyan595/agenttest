"""The entire default suite must succeed without opening outbound connections."""

import socket
import sys

import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch) -> None:
    original_connect = socket.socket.connect

    def blocked(*args, **kwargs):
        raise AssertionError("Default tests may not use DNS or network connections")

    def connect(sock, address):
        # Windows asyncio uses the stdlib fallback socketpair as its internal wakeup pipe.
        # Permit that exact caller only; even ordinary loopback HTTP remains forbidden.
        fallback = getattr(socket, "_fallback_socketpair", None)
        if fallback and sys._getframe(1).f_code is fallback.__code__:
            return original_connect(sock, address)
        return blocked()

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
