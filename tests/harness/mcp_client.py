"""Minimaler MCP-stdio-Client (JSON-RPC 2.0, newline-delimited, Standardbibliothek)."""

from __future__ import annotations

import json
import queue
import shlex
import subprocess
import threading

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 20.0


class McpError(RuntimeError):
    """Protokoll-, Start- oder Timeoutfehler des Clients."""


def response_text(response: object) -> str:
    """Sichtbarer Text einer tools/call-Antwort (JSON-RPC-Fehler inklusive)."""
    if not isinstance(response, dict):
        return ""
    parts: list[str] = []
    error = response.get("error")
    if error:
        parts.append(json.dumps(error, ensure_ascii=False))
    result = response.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
        if result.get("isError"):
            parts.append("isError")
    return "\n".join(part for part in parts if part)


class McpClient:
    """Startet einen MCP-Server über stdio und spricht JSON-RPC 2.0."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = float(timeout)
        self.proc: subprocess.Popen | None = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.stray_lines: list[str] = []
        self.notifications: list[dict] = []
        self.responses: list[dict] = []
        self._pending: dict[object, queue.Queue] = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = False

    def start(self, cmd, env: dict[str, str] | None = None) -> "McpClient":
        command = shlex.split(cmd) if isinstance(cmd, str) else [str(part) for part in cmd]
        if not command:
            raise McpError("leerer Befehl")
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="mcp-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="mcp-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def stderr_text(self) -> str:
        return "\n".join(self.stderr_lines)

    @property
    def stray(self) -> list[str]:
        """stdout-Zeilen ohne gültiges JSON-RPC."""
        return self.stray_lines

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            self.stdout_lines.append(line)
            self._route(line)
        self._fail_pending()

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self.stderr_lines.append(line)

    def _route(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.stray_lines.append(line)
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self.stray_lines.append(line)
            return
        self.responses.append(message)
        if (
            "id" in message
            and message.get("id") is not None
            and ("result" in message or "error" in message)
        ):
            with self._lock:
                pending = self._pending.get(message["id"])
            if pending is not None:
                pending.put(message)
            return
        if "method" in message:
            self.notifications.append(message)
            return
        self.stray_lines.append(line)

    def _fail_pending(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
        for item in pending:
            item.put(None)

    def _write(self, message: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise McpError("Client nicht gestartet")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"Server nimmt keine Eingaben mehr an: {exc}") from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def raw_request(
        self, method: str, params: dict | None = None, timeout: float | None = None
    ) -> dict:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            pending: queue.Queue = queue.Queue()
            self._pending[request_id] = pending
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._write(message)
            response = pending.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty as exc:
            raise McpError(
                f"Timeout nach {self.timeout if timeout is None else timeout} s "
                f"bei {method}"
            ) from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if response is None:
            raise McpError(f"Server beendete sich vor der Antwort auf {method}")
        return response

    def request(
        self, method: str, params: dict | None = None, timeout: float | None = None
    ) -> dict:
        response = self.raw_request(method, params, timeout)
        if "error" in response:
            raise McpError(
                f"JSON-RPC-Fehler bei {method}: "
                + json.dumps(response["error"], ensure_ascii=False)
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-gate", "version": "1.0"},
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            result = self.request("tools/list", params)
            page = result.get("tools", [])
            if isinstance(page, list):
                tools.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.raw_request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc is not None:
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except OSError:
                pass
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
            for thread in (self._stdout_thread, self._stderr_thread):
                if thread is not None and thread.is_alive():
                    thread.join(timeout=5)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
