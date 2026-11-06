#!/usr/bin/env python3
"""Maschinelles MCP-Gate für stdio-Server (Prüfungen MCP00/01/04/05)."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from fake_server import FakeServer
    from leak_check import find_secret_in_text
    from mcp_client import DEFAULT_TIMEOUT, McpClient, response_text
    from mcp_spec import McpSpec, parse
    from ux_session import build_env, free_local_port, install_fresh
except ImportError:  # pragma: no cover - Paketimport
    from .fake_server import FakeServer
    from .leak_check import find_secret_in_text
    from .mcp_client import DEFAULT_TIMEOUT, McpClient, response_text
    from .mcp_spec import McpSpec, parse
    from .ux_session import build_env, free_local_port, install_fresh

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
CHECK_IDS = ["MCP00", "MCP01", "MCP04", "MCP05"]
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TRACEBACK = "Traceback (most recent call last)"
UNKNOWN_TOOL = "__gate_unknown_tool__"


@dataclass
class CheckRow:
    id: str
    result: str
    detail: str


@dataclass
class Context:
    repo: Path
    spec: McpSpec
    command: list[str]
    bin_dir: Path
    env: dict[str, str]
    canaries: dict[str, str]
    clients: list[McpClient] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    main_client: McpClient | None = None


def _row(rows: dict[str, CheckRow], cid: str, ok: bool, detail: str) -> None:
    rows[cid] = CheckRow(cid, PASS if ok else FAIL, detail or ("OK" if ok else "FAIL"))


def _skip(rows: dict[str, CheckRow], cid: str, detail: str) -> None:
    rows[cid] = CheckRow(cid, SKIP, detail)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_report(rows: dict[str, CheckRow], ctx: Context | None) -> str:
    ordered = [rows[cid] for cid in CHECK_IDS if cid in rows]
    failed = any(row.result == FAIL for row in ordered)
    lines = ["# MCP-Gate Bericht", ""]
    if ctx is not None:
        lines.extend(
            [
                f"- Repo: `{ctx.repo}`",
                f"- Befehl: `{' '.join(ctx.command)}`",
                f"- Ergebnis: {FAIL if failed else PASS}",
                "",
            ]
        )
    lines.extend(["| ID | PASS/FAIL/SKIP | Detail |", "|---|---|---|"])
    for row in ordered:
        lines.append(f"| {row.id} | {row.result} | {_cell(row.detail)} |")
    return "\n".join(lines) + "\n"


def _finish(rows: dict[str, CheckRow], ctx: Context | None, report_path: str | None) -> int:
    report = _format_report(rows, ctx)
    print(report, end="")
    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
    return 0 if not any(row.result == FAIL for row in rows.values()) else 1


def _scratch_tmp() -> Path:
    path = Path.cwd() / "scratch" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _response_is_error(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("error"):
        return True
    result = response.get("result")
    return isinstance(result, dict) and result.get("isError") is True


def _client_texts(client: McpClient) -> list[str]:
    texts = list(client.stdout_lines)
    if client.stderr_text:
        texts.append(client.stderr_text)
    texts.extend(response_text(response) for response in client.responses)
    return texts


def _check_mcp00(rows: dict[str, CheckRow], ctx: Context) -> None:
    client = McpClient(timeout=DEFAULT_TIMEOUT)
    ctx.main_client = client
    ctx.clients.append(client)
    try:
        client.start(ctx.command, ctx.env)
        init = client.initialize()
    except Exception as exc:  # noqa: BLE001 - Fehler landet im Bericht
        _row(rows, "MCP00", False, f"Start/initialize fehlgeschlagen: {type(exc).__name__}: {exc}")
        return
    problems: list[str] = []
    server_info = init.get("serverInfo") if isinstance(init, dict) else None
    if not isinstance(server_info, dict) or not str(server_info.get("name", "")).strip():
        problems.append("serverInfo.name fehlt")
    capabilities = init.get("capabilities") if isinstance(init, dict) else None
    if not isinstance(capabilities, dict) or "tools" not in capabilities:
        problems.append("capabilities.tools fehlt")
    if client.stray_lines:
        problems.append(
            "stray stdout: " + repr(client.stray_lines[:3])
        )
    if problems:
        _row(rows, "MCP00", False, "; ".join(problems))
    else:
        _row(rows, "MCP00", True, "initialize < 20 s, serverInfo.name und capabilities.tools vorhanden")


def _check_mcp01(rows: dict[str, CheckRow], ctx: Context) -> None:
    client = ctx.main_client
    if client is None or not client.alive:
        _row(rows, "MCP01", False, "Server läuft nach MCP00 nicht mehr")
        return
    try:
        tools = client.list_tools()
    except Exception as exc:  # noqa: BLE001
        _row(rows, "MCP01", False, f"tools/list fehlgeschlagen: {type(exc).__name__}: {exc}")
        return
    ctx.tools = tools
    problems: list[str] = []
    if not tools:
        problems.append("tools/list liefert kein Tool")
    names: set[str] = set()
    for tool in tools:
        name = str(tool.get("name", ""))
        if not TOOL_NAME_RE.fullmatch(name):
            problems.append(f"ungültiger Toolname {name!r}")
        if name in names:
            problems.append(f"Toolname {name!r} doppelt")
        names.add(name)
        description = str(tool.get("description", "")).strip()
        if len(description) < 20:
            problems.append(f"{name!r}: description fehlt oder < 20 Zeichen")
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            problems.append(f"{name!r}: inputSchema ist nicht type=object")
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            problems.append(f"{name!r}: inputSchema.properties fehlt")
            continue
        for prop, prop_schema in properties.items():
            if not isinstance(prop_schema, dict) or not (
                str(prop_schema.get("description", "")).strip()
                or str(prop_schema.get("title", "")).strip()
            ):
                problems.append(f"{name!r}.{prop}: description/title fehlt")
        annotations = tool.get("annotations")
        if not isinstance(annotations, dict) or not isinstance(
            annotations.get("readOnlyHint"), bool
        ):
            problems.append(f"{name!r}: annotations.readOnlyHint fehlt")
    if problems:
        _row(rows, "MCP01", False, "; ".join(problems))
    else:
        _row(rows, "MCP01", True, f"{len(tools)} Tool(s), Namen/Schemata/Annotations gültig")


def _check_mcp04(rows: dict[str, CheckRow], ctx: Context) -> None:
    client = ctx.main_client
    if client is None or not client.alive:
        _row(rows, "MCP04", False, "Server läuft nach MCP00 nicht mehr")
        return
    problems: list[str] = []

    try:
        unknown = client.call_tool(UNKNOWN_TOOL, {})
    except Exception as exc:  # noqa: BLE001
        problems.append(f"(a) unbekanntes Tool: Aufruf fehlgeschlagen: {exc}")
    else:
        if not _response_is_error(unknown):
            problems.append("(a) unbekanntes Tool liefert weder JSON-RPC-Fehler noch isError")

    if not ctx.tools:
        problems.append("(b) kein Tool aus MCP01 für falsche Argumente")
    else:
        first = ctx.tools[0]
        name = str(first.get("name", ""))
        schema = first.get("inputSchema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        arguments = {
            str(prop): {"x": [1]}
            for prop in (properties or {})
        } or {"__gate_bad_arg__": {"x": [1]}}
        try:
            bad = client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"(b) {name!r} mit falschen Typen: Aufruf fehlgeschlagen: {exc}")
        else:
            if not _response_is_error(bad):
                problems.append(f"(b) {name!r} mit falschen Typen liefert keinen Fehler")

    if not ctx.spec.calls:
        problems.append("(c) mcp-calls ist leer, Netzwerkfehler nicht prüfbar")
    else:
        base_env = str(ctx.spec.meta.get("base_url_env", "")).strip()
        if not base_env:
            problems.append("(c) base_url_env fehlt, Netzwerkfehler nicht prüfbar")
        else:
            dead_env = dict(ctx.env)
            dead_env[base_env] = f"http://127.0.0.1:{free_local_port()}"
            call = ctx.spec.calls[0]
            dead = McpClient(timeout=DEFAULT_TIMEOUT)
            ctx.clients.append(dead)
            try:
                dead.start(ctx.command, dead_env)
                dead.initialize()
                response = dead.call_tool(call.tool, call.arguments)
                text = response_text(response)
                if not _response_is_error(response) or response.get("error"):
                    problems.append(
                        f"(c) {call.tool!r} bei unerreichbarer URL liefert kein result.isError"
                    )
                if len(text.strip()) < 5:
                    problems.append("(c) Fehlertext ist leer oder unlesbar")
                if TRACEBACK in text or any(
                    TRACEBACK in line for line in dead.stdout_lines
                ):
                    problems.append("(c) Traceback im Antworttext oder auf stdout")
                try:
                    tools = dead.list_tools()
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"(c) tools/list nach Fehler schlägt fehl: {exc}")
                else:
                    if not tools:
                        problems.append("(c) tools/list nach Fehler liefert kein Tool")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"(c) Netzwerkfehler-Prüfung fehlgeschlagen: {exc}")

    for item in ctx.clients:
        if any(TRACEBACK in line for line in item.stdout_lines):
            problems.append("Traceback auf stdout")
            break
        if any(TRACEBACK in response_text(response) for response in item.responses):
            problems.append("Traceback im Antworttext")
            break

    if problems:
        _row(rows, "MCP04", False, "; ".join(dict.fromkeys(problems)))
    else:
        _row(
            rows,
            "MCP04",
            True,
            "unbekanntes Tool, falsche Typen und Netzwerkfehler sauber; Server lebt weiter",
        )


def _check_mcp05(rows: dict[str, CheckRow], ctx: Context) -> None:
    if not ctx.canaries:
        _row(rows, "MCP05", True, "keine secret_env, nichts zu prüfen")
        return
    hits: list[str] = []
    for name, secret in ctx.canaries.items():
        for client in ctx.clients:
            if find_secret_in_text(secret, "\n".join(client.stdout_lines)):
                hits.append(f"{name} auf stdout")
            if find_secret_in_text(secret, client.stderr_text):
                hits.append(f"{name} auf stderr")
            for response in client.responses:
                if find_secret_in_text(secret, response_text(response)):
                    hits.append(f"{name} im Antworttext")
    if hits:
        _row(rows, "MCP05", False, "Secret-Leak: " + ", ".join(dict.fromkeys(hits)))
    else:
        _row(
            rows,
            "MCP05",
            True,
            f"{len(ctx.canaries)} Kanarienwert(e) in stdout/stderr/Antworten nicht gefunden",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP-Gate für stdio-Server")
    parser.add_argument("repo_dir")
    parser.add_argument("--venv", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--cmd", default=None)
    args = parser.parse_args(argv)

    repo = Path(args.repo_dir).resolve()
    if not repo.is_dir():
        print(f"Repo-Verzeichnis fehlt: {repo}", file=sys.stderr)
        return 2

    spec = parse(repo / "docs" / "MCP.md")
    rows: dict[str, CheckRow] = {}
    if spec.errors:
        _row(rows, "MCP00", False, "docs/MCP.md ungültig: " + "; ".join(spec.errors))
        for cid in CHECK_IDS[1:]:
            _skip(rows, cid, "MCP.md ungültig")
        return _finish(rows, None, args.report)

    command_text = (args.cmd or str(spec.meta.get("command", ""))).strip()
    if not command_text:
        _row(rows, "MCP00", False, "mcp-meta.command fehlt und --cmd nicht gesetzt")
        for cid in CHECK_IDS[1:]:
            _skip(rows, cid, "kein Serverbefehl")
        return _finish(rows, None, args.report)
    command = shlex.split(command_text)
    if not command:
        _row(rows, "MCP00", False, "Serverbefehl ist leer")
        for cid in CHECK_IDS[1:]:
            _skip(rows, cid, "kein Serverbefehl")
        return _finish(rows, None, args.report)

    if args.venv:
        venv = Path(args.venv).resolve()
    else:
        venv = Path(tempfile.mkdtemp(prefix="mcp-venv-", dir=_scratch_tmp())) / "venv"
    try:
        bin_dir = install_fresh(repo, venv)
    except Exception as exc:  # noqa: BLE001 - Fehler landet im Bericht
        _row(rows, "MCP00", False, f"Installation fehlgeschlagen: {exc}")
        for cid in CHECK_IDS[1:]:
            _skip(rows, cid, "Installation fehlgeschlagen")
        return _finish(rows, None, args.report)

    executable = bin_dir / command[0]
    if executable.is_file():
        command[0] = str(executable)

    home = Path(tempfile.mkdtemp(prefix="mcp-check-home-", dir=_scratch_tmp()))
    canaries = {
        str(name): "CANARY-" + secrets.token_hex(8)
        for name in (spec.meta.get("secret_env", []) or [])
    }
    port = free_local_port()
    ux_spec = spec.ux_spec()

    with FakeServer(ux_spec, repo, port=port) as server:
        env = build_env(ux_spec, server, home, bin_dir, repo)
        env.update(canaries)
        ctx = Context(
            repo=repo,
            spec=spec,
            command=command,
            bin_dir=bin_dir,
            env=env,
            canaries=canaries,
        )
        try:
            _check_mcp00(rows, ctx)
            _check_mcp01(rows, ctx)
            _check_mcp04(rows, ctx)
            _check_mcp05(rows, ctx)
        finally:
            for client in ctx.clients:
                client.close()

    return _finish(rows, ctx, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
