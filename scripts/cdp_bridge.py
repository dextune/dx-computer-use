#!/usr/bin/env python3
"""Minimal Chromium CDP client (stdlib only). Runs inside the sandbox."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import urllib.parse
import urllib.request
from typing import Any, Optional

CDP_HOST = "127.0.0.1"
CDP_PORT = 9321


def _http(path: str, method: str = "GET") -> Any:
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def list_pages() -> list[dict]:
    tabs = _http("/json") or []
    return [tab for tab in tabs if tab.get("type") == "page"]


def navigate(url: str) -> dict:
    encoded = urllib.parse.quote(url, safe="")
    created = _http(f"/json/new?{encoded}", method="PUT")
    if not isinstance(created, dict):
        created = _http(f"/json/new?{encoded}", method="GET")
    if not isinstance(created, dict):
        raise RuntimeError("CDP could not open a tab")
    return created


class _CdpSocket:
    def __init__(self, page: dict):
        ws_url = page["webSocketDebuggerUrl"]
        parsed = urllib.parse.urlparse(ws_url)
        self._sock = socket.create_connection((parsed.hostname, parsed.port or CDP_PORT), timeout=30)
        self._id = 0
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or CDP_PORT}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(handshake.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP websocket handshake closed")
            header += chunk
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"CDP websocket handshake failed: {header[:80]!r}")

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        self._id += 1
        payload = {"id": self._id, "method": method, "params": params or {}}
        self._send(json.dumps(payload))
        while True:
            message = json.loads(self._recv())
            if message.get("id") == self._id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result")

    def _send(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray()
        header.append(0x81)
        length = len(data)
        mask = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(header + masked)

    def _recv(self) -> str:
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        payload = self._read_exact(length)
        if opcode == 0x8:
            raise RuntimeError("CDP websocket closed")
        return payload.decode("utf-8")

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            piece = self._sock.recv(size - len(chunks))
            if not piece:
                raise RuntimeError("CDP socket closed")
            chunks.extend(piece)
        return bytes(chunks)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def page_matching(url_substr: str) -> Optional[dict]:
    for page in list_pages():
        if url_substr in (page.get("url") or ""):
            return page
    return None


def evaluate(expression: str, page: Optional[dict] = None, url_substr: str = "") -> Any:
    pages = list_pages()
    target = page
    if target is None and url_substr:
        target = page_matching(url_substr)
    if target is None:
        target = pages[0] if pages else None
    if target is None:
        raise RuntimeError("no CDP page target")
    client = _CdpSocket(target)
    try:
        client.call("Runtime.enable")
        result = client.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise RuntimeError(remote.get("description") or "evaluate error")
        return remote.get("value")
    finally:
        client.close()


def wait_ready(timeout_s: float = 20.0) -> None:
    import time

    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            evaluate("document.readyState")
            return
        except Exception as exc:  # noqa: BLE001 — retry until ready
            last_error = exc
            time.sleep(0.4)
    raise RuntimeError(f"page not ready: {last_error}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"error": "usage: cdp_bridge.py list|navigate|eval"}))
        return 2
    command = argv[1]
    if command == "list":
        print(json.dumps(list_pages(), ensure_ascii=False))
        return 0
    if command == "navigate":
        print(json.dumps(navigate(argv[2]), ensure_ascii=False))
        return 0
    if command == "eval":
        url_substr = argv[3] if len(argv) > 3 else ""
        print(json.dumps(evaluate(argv[2], url_substr=url_substr), ensure_ascii=False))
        return 0
    print(json.dumps({"error": f"unknown command {command}"}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
