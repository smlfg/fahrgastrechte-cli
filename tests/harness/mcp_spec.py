"""Parser für maschinenlesbare docs/MCP.md-Dateien (nur Standardbibliothek)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from ux_spec import Route, UxSpec, _blocks, _json_block, _parse_routes
except ImportError:  # pragma: no cover - Paketimport
    from .ux_spec import Route, UxSpec, _blocks, _json_block, _parse_routes


@dataclass
class Call:
    tool: str
    arguments: dict = field(default_factory=dict)
    expect: list[str] = field(default_factory=list)
    expect_no_write: bool = False


@dataclass
class McpSpec:
    meta: dict
    routes: list[Route] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def ux_spec(self) -> UxSpec:
        """FakeServer erwartet ein UxSpec; Sessions/Formsteps bleiben leer."""
        return UxSpec(
            meta=self.meta,
            sessions=[],
            routes=self.routes,
            formsteps=[],
            errors=list(self.errors),
        )

    def to_ux_spec(self) -> UxSpec:
        return self.ux_spec()


def _parse_meta(data: object, start: int, errors: list[str]) -> dict:
    if not isinstance(data, dict):
        errors.append(f"Zeile {start}: mcp-meta muss ein JSON-Objekt sein")
        return {}
    command = str(data.get("command", "")).strip()
    if not command:
        errors.append(f"Zeile {start}: mcp-meta.command fehlt")
    secret_env = data.get("secret_env", [])
    if not isinstance(secret_env, list) or not all(
        isinstance(name, str) and name for name in secret_env
    ):
        errors.append(f"Zeile {start}: mcp-meta.secret_env muss eine Liste von Namen sein")
    env = data.get("env", {})
    if not isinstance(env, dict):
        errors.append(f"Zeile {start}: mcp-meta.env muss ein JSON-Objekt sein")
    base = data.get("base_url_env")
    if base is not None and not isinstance(base, str):
        errors.append(f"Zeile {start}: mcp-meta.base_url_env muss ein String sein")
    return data


def _parse_calls(data: object, start: int, errors: list[str]) -> list[Call]:
    calls: list[Call] = []
    if not isinstance(data, list):
        errors.append(f"Zeile {start}: mcp-calls muss eine JSON-Liste sein")
        return calls
    for item in data:
        if not isinstance(item, dict):
            errors.append(f"Zeile {start}: mcp-calls-Eintrag ist kein Objekt")
            continue
        tool = str(item.get("tool", "")).strip()
        if not tool:
            errors.append(f"Zeile {start}: mcp-calls-Eintrag ohne tool")
            continue
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            errors.append(f"Zeile {start}: arguments von {tool!r} muss ein Objekt sein")
            arguments = {}
        expect = item.get("expect", [])
        if not isinstance(expect, list) or not all(
            isinstance(value, str) for value in expect
        ):
            errors.append(f"Zeile {start}: expect von {tool!r} muss eine Liste von Strings sein")
            expect = []
        calls.append(
            Call(
                tool=tool,
                arguments=arguments,
                expect=list(expect),
                expect_no_write=bool(item.get("expect_no_write", False)),
            )
        )
    return calls


def parse(path: str | Path) -> McpSpec:
    """Liest eine MCP.md und validiert sie. Fehler landen in ``spec.errors``."""
    mcp_path = Path(path)
    errors: list[str] = []
    try:
        lines = mcp_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return McpSpec(meta={}, errors=[f"Zeile 0: MCP.md nicht lesbar: {exc}"])

    repo_dir = mcp_path.resolve().parent.parent
    spec = McpSpec(meta={}, errors=errors)
    meta_seen = False
    meta_block_seen = False
    routes_seen = False

    for info, start, body, _heading in _blocks(lines):
        if info == "mcp-meta":
            meta_block_seen = True
            data = _json_block(info, start, body, errors)
            if data is None:
                continue
            spec.meta = _parse_meta(data, start, errors)
            meta_seen = True
        elif info == "mcp-routes":
            routes_seen = True
            data = _json_block(info, start, body, errors)
            if data is not None:
                spec.routes.extend(_parse_routes(data, start, repo_dir, errors))
        elif info == "mcp-calls":
            data = _json_block(info, start, body, errors)
            if data is not None:
                spec.calls.extend(_parse_calls(data, start, errors))

    if not meta_seen and not meta_block_seen:
        errors.append("Zeile 0: mcp-meta fehlt")
    if routes_seen and not spec.meta.get("base_url_env"):
        errors.append(
            "Zeile 0: mcp-routes vorhanden, aber mcp-meta.base_url_env fehlt"
        )
    return spec


def summary(spec: McpSpec) -> str:
    command = spec.meta.get("command", "?") if spec.meta else "?"
    lines = [
        f"command: {command}",
        f"routes: {len(spec.routes)}",
        f"calls: {len(spec.calls)}",
        f"errors: {len(spec.errors)}",
    ]
    lines.extend(f"  - {error}" for error in spec.errors)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python mcp_spec.py <MCP.md>", file=sys.stderr)
        return 2
    spec = parse(args[0])
    print(summary(spec))
    return 1 if spec.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
