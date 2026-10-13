"""Direct MCP transport over streamable HTTP.

Replaces the external `mcd-cli` subprocess dependency so the project can run
`--live` with nothing but the Python standard library and a personal token.

Design constraints inherited from core.py:
  * read-only: the caller is responsible for restricting tool names
  * no credential is ever logged or written to disk
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp.mcd.cn"
TOKEN_ENV = "MCD_MCP_TOKEN"


class TransportError(RuntimeError):
    """Raised for network/protocol failures. Message never contains the token."""


class McdMcpTransport:
    def __init__(self, token: str | None = None, url: str | None = None, timeout: int = 40):
        self.url = url or os.environ.get("MCD_MCP_URL") or DEFAULT_URL
        self.token = token or os.environ.get(TOKEN_ENV)
        if not self.token:
            raise TransportError(
                f"缺少 {TOKEN_ENV}。请从个人环境变量提供 Token，不要写入仓库或命令行历史。"
            )
        if self.token.lower().startswith("bearer "):
            self.token = self.token[7:]
        self.timeout = timeout
        self._req_id = 0
        self._initialized = False

    # ------------------------------------------------------------------ plumbing
    def _post(self, payload: dict) -> str:
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - response body is best-effort only
                pass
            # Never echo headers; the body may contain a diagnostic message.
            raise TransportError(f"HTTP {exc.code}：{body[:300]}") from None
        except urllib.error.URLError as exc:
            raise TransportError(f"网络请求失败：{exc.reason}") from None

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("data:"):  # SSE framing
            chunks = []
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    chunks.append(line[5:].strip())
            raw = "\n".join(chunks)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise TransportError(f"无法解析 MCP 响应（非 JSON）：{raw[:200]}") from None

    def _ensure_initialized(self) -> None:
        """Handshake once. This server also accepts session-less calls, so a
        failed handshake must not abort the run."""
        if self._initialized:
            return
        self._initialized = True
        self._req_id += 1
        try:
            self._post({
                "jsonrpc": "2.0",
                "id": -self._req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mcd-mcp-doctor", "version": "1.0.0"},
                },
            })
        except TransportError:
            pass

    # ---------------------------------------------------------------- public API
    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """Invoke a read-only tool and return its text payload."""
        self._ensure_initialized()
        self._req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        data = self._parse(self._post(payload))
        if data.get("error"):
            raise TransportError(
                "MCP 返回错误：" + json.dumps(data["error"], ensure_ascii=False)[:300]
            )
        result = data.get("result") or {}
        content = result.get("content")
        if isinstance(content, list):
            return "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return json.dumps(result, ensure_ascii=False)

    def list_tools(self) -> list | None:
        """Return the raw tools/list array, or None when unavailable."""
        self._ensure_initialized()
        self._req_id += 1
        payload = {"jsonrpc": "2.0", "id": self._req_id, "method": "tools/list", "params": {}}
        try:
            data = self._parse(self._post(payload))
        except TransportError:
            return None
        if data.get("error"):
            return None
        tools = (data.get("result") or {}).get("tools")
        return tools if isinstance(tools, list) else None
