"""Run regression tests without outbound network access; allow local HTTP fixtures."""

import ipaddress
import sys

import pytest


def block_external_network(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event == "socket.connect":
        host = args[1][0]
    else:
        return
    if host in (None, "localhost", b"localhost"):
        return
    if isinstance(host, bytes):
        host = host.decode("ascii")
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("Offline tests attempted an external network connection")


sys.addaudithook(block_external_network)
raise SystemExit(pytest.main(sys.argv[1:]))
