import socket

import pytest

from couch import net


def test_magic_packet_exact_bytes():
    pkt = net.magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(pkt) == 102
    assert pkt == bytes.fromhex("ffffffffffff" + "aabbccddeeff" * 16)
    assert net.magic_packet("aa-bb-cc-dd-ee-ff") == pkt


@pytest.mark.parametrize("bad", ["", "AA:BB:CC:DD:EE", "GG:BB:CC:DD:EE:FF", "AABBCCDDEEFF", "AA:BB:CC:DD:EE:FF:00"])
def test_bad_mac_rejected(bad):
    with pytest.raises(ValueError):
        net.magic_packet(bad)


def test_send_wol_broadcast():
    sent = {}

    class Sock:
        def __init__(self, *a):
            pass

        def setsockopt(self, *a):
            sent["opt"] = a

        def sendto(self, data, addr):
            sent["data"], sent["addr"] = data, addr

        def close(self):
            pass
    net.send_wol("AA:BB:CC:DD:EE:FF", sock_factory=Sock)
    assert sent["addr"] == ("255.255.255.255", 9) and sent["opt"][1] == socket.SO_BROADCAST
    assert sent["data"] == net.magic_packet("AA:BB:CC:DD:EE:FF")


def test_tcp_probe_open_and_refused():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert net.tcp_probe("127.0.0.1", port) == "open"
    srv.close()
    assert net.tcp_probe("127.0.0.1", port) == "refused"


def test_resolve4_localhost_and_garbage():
    assert net.resolve4("localhost") == "127.0.0.1"
    assert net.resolve4("no-such-host.invalid") is None
