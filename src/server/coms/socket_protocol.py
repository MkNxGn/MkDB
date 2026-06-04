"""
Socket message framing helpers.

Wire format: [4-byte big-endian uint32 length][UTF-8 JSON payload bytes]
"""

import json
import socket
import struct

_MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10 MB hard limit


def encode_message(payload: dict) -> bytes:
    """Serialize dict to JSON, prefix with 4-byte big-endian length."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def decode_message(data: bytes) -> dict:
    """Strip 4-byte length prefix, parse JSON payload."""
    return json.loads(data[4:].decode("utf-8"))


def read_frame(sock: socket.socket) -> dict:
    """
    Read exactly 4 bytes for the length, then exactly N bytes for the payload.
    Raises ConnectionError on premature close.
    Raises ValueError if the frame exceeds 10 MB.
    """
    length_bytes = _recv_exact(sock, 4)
    length = struct.unpack(">I", length_bytes)[0]
    if length == 0:
        return {}
    if length > _MAX_FRAME_SIZE:
        raise ValueError(f"Frame too large: {length} bytes (max {_MAX_FRAME_SIZE})")
    payload_bytes = _recv_exact(sock, length)
    return json.loads(payload_bytes.decode("utf-8"))


def write_frame(sock: socket.socket, payload: dict) -> None:
    """Encode and send one frame."""
    sock.sendall(encode_message(payload))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from sock; raises ConnectionError if connection closes early."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed before all bytes received")
        buf += chunk
    return buf
