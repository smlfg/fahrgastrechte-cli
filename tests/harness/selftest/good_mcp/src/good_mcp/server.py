"""good-mcp: minimaler MCP-Server für das Gate (nur Standardbibliothek)."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2025-06-18"


def base_url() -> str:
    return os.environ.get("GOOD_BASE_URL", "http://127.0.0.1:9").rstrip("/")


def _text(text: str, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _error(message: str) -> dict:
    return _text(message, True)


def _get(path: str, params: dict[str, str]) -> str:
    url = base_url() + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def _post(path: str, fields: dict[str, str]) -> str:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        base_url() + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def tools() -> list[dict]:
    return [
        {
            "name": "search_items",
            "description": "Sucht Artikel im Katalog anhand eines Suchbegriffs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Suchbegriff für die Artikelsuche",
                    }
                },
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "get_item",
            "description": "Liefert die Details eines Artikels anhand seiner ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Eindeutige Artikel-ID",
                    }
                },
                "required": ["id"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "book_item",
            "description": "Bucht einen Artikel verbindlich nach ausdrücklicher Bestätigung.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "ID des zu buchenden Artikels",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Buchung wirklich senden",
                    },
                },
                "required": ["item", "confirm"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
    ]


def _type_ok(value: object, expected: object) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate(tool: dict, arguments: dict) -> str | None:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    for key in schema.get("required", []):
        if key not in arguments:
            return f"Pflichtparameter '{key}' fehlt"
    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None:
            return f"Unbekannter Parameter '{key}'"
        if not _type_ok(value, prop.get("type")):
            return f"Parameter '{key}' hat den falschen Typ"
    return None


def _call(tool: dict, arguments: dict) -> dict:
    problem = _validate(tool, arguments)
    if problem is not None:
        return _error(problem)
    name = tool["name"]
    try:
        if name == "search_items":
            return _text(_get("/items", {"q": arguments["query"]}))
        if name == "get_item":
            return _text(_get("/item", {"id": arguments["id"]}))
        if name == "book_item":
            if arguments["confirm"] is not True:
                return _text("Nicht gesendet: confirm=true nötig")
            return _text(_post("/book", {"item": arguments["item"]}))
    except Exception as exc:  # noqa: BLE001 - sauberer Fehler statt Traceback
        return _error(f"Netzwerkfehler: {exc}")
    return _error(f"Unbekanntes Tool: {name}")


def _rpc_error(request_id: object, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _handle(message: dict) -> dict | None:
    method = message.get("method")
    if method is None or "id" not in message:
        return None
    request_id = message["id"]
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "good-mcp", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools()}}
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "arguments muss ein Objekt sein")
        tool = next((item for item in tools() if item["name"] == name), None)
        if tool is None:
            return _rpc_error(request_id, -32602, f"Unbekanntes Tool: {name}")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _call(tool, arguments),
        }
    return _rpc_error(request_id, -32601, f"Methode nicht gefunden: {method}")


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        response = _handle(message)
        if response is not None:
            _send(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
