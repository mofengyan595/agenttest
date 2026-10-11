import socket

import pytest


def test_ordinary_loopback_and_outbound_connections_stay_blocked():
    with socket.socket() as client:
        for address in (("127.0.0.1", 8080), ("8.8.8.8", 80)):
            with pytest.raises(AssertionError, match="Default tests"):
                client.connect(address)
            with pytest.raises(AssertionError, match="Default tests"):
                client.connect_ex(address)
    with pytest.raises(AssertionError, match="Default tests"):
        socket.getaddrinfo("example.test", 80)


def test_stdlib_internal_socket_pair_remains_usable():
    left, right = socket.socketpair()
    try:
        left.sendall(b"internal wakeup")
        assert right.recv(15) == b"internal wakeup"
    finally:
        left.close()
        right.close()
