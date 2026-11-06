"""Akzeptanztests aus docs/UX.md (R2).

Jede UX-Sitzung läuft als echter Subprozess (PTY) gegen den Fake-Server aus
``tests/harness``. ``FAHRGASTRECHTE_TIMETABLES_BASE_URL`` zeigt dabei auf den
lokalen Server; das Tool darf keinen anderen Host erreichen.

Die Tests dürfen bis zum Abschluss der R3-Nachrüstung rot sein.
"""

from __future__ import annotations

import shlex
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
HARNESS = REPO / "tests" / "harness"

for path in (HARNESS,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fake_server import FakeServer  # noqa: E402
from ux_session import build_env, run_session  # noqa: E402
from ux_spec import Session, UxSpec, parse  # noqa: E402

LOGIN_TITLE = "Installation, Login und Stationssuche"
TRIP_TITLE = "Fahrt aus den Timetables prüfen"
ADD_TITLE = "Verspätungsfall verbindlich anlegen"


@pytest.fixture(scope="module")
def spec() -> UxSpec:
    parsed = parse(REPO / "docs" / "UX.md")
    assert not parsed.errors, parsed.errors
    return parsed


def _session(spec: UxSpec, title: str) -> Session:
    for session in spec.sessions:
        if session.title == title:
            return session
    raise AssertionError(f"UX-Sitzung {title!r} fehlt in docs/UX.md")


def _write_launcher(bin_dir: Path) -> None:
    """Konsolen-Skript ohne pip-Installation, aber als eigener Prozess."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "fahrgastrechte"
    pythonpath = f"{SRC}{':'}{HARNESS}"
    launcher.write_text(
        "#!/bin/sh\n"
        f"export PYTHONPATH={shlex.quote(pythonpath)}\n"
        f"exec {shlex.quote(sys.executable)} -c "
        "'from fahrgastrechte_cli.main import main; main()' \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def _environment(spec: UxSpec, tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_launcher(bin_dir)
    env = build_env(spec, None, home, bin_dir, REPO)
    # conftest autouse setzt FAHRGASTRECHTE_DB auf tmp_path/konto.db und
    # session_env vererbt os.environ; "" macht den Override ungültig, sodass
    # die Akzeptanzsitzungen ihre konto.db nur im Fake-Home wählen.
    env["FAHRGASTRECHTE_DB"] = ""
    return env, home


def _with_server_env(spec: UxSpec, server: FakeServer, env: dict[str, str]):
    base_env = str((spec.meta or {}).get("base_url_env", ""))
    merged = dict(env)
    if base_env:
        merged[base_env] = server.base_url
    return merged


def _login(spec: UxSpec, env: dict[str, str], tmp_path: Path) -> None:
    session = _session(spec, LOGIN_TITLE)
    with FakeServer(spec, REPO) as server:
        result = run_session(
            spec,
            session,
            server,
            _with_server_env(spec, server, env),
            run_label="login-vorbereitung",
        )
    assert result.ok, result.fails


def _assert_no_traceback(result) -> None:
    assert "Traceback (most recent call last)" not in result.transcript


def test_login_und_station_gegen_fake_server(spec: UxSpec, tmp_path: Path) -> None:
    env, _home = _environment(spec, tmp_path)
    session = _session(spec, LOGIN_TITLE)

    with FakeServer(spec, REPO) as server:
        result = run_session(
            spec,
            session,
            server,
            _with_server_env(spec, server, env),
        )
        paths = [request.path for request in server.requests]

    assert result.ok, result.fails
    _assert_no_traceback(result)
    assert "/station/Stuttgart%20Hbf" in paths
    session_file = (
        Path(env["XDG_CONFIG_HOME"]) / "fahrgastrechte" / "session.json"
    )
    assert session_file.is_file()
    assert session_file.stat().st_mode & 0o777 == 0o600


def test_trip_liest_timetables_gegen_fake_server(
    spec: UxSpec, tmp_path: Path
) -> None:
    env, _home = _environment(spec, tmp_path)
    _login(spec, env, tmp_path)
    session = _session(spec, TRIP_TITLE)

    with FakeServer(spec, REPO) as server:
        result = run_session(
            spec,
            session,
            server,
            _with_server_env(spec, server, env),
        )
        paths = [request.path for request in server.requests]

    assert result.ok, result.fails
    _assert_no_traceback(result)
    assert "/station/Stuttgart%20Hbf" in paths
    assert "/station/Mannheim%20Hbf" in paths
    assert "/rchg/8000244" in paths


def test_add_abbruch_mit_n_sendet_keine_echtzeit(
    spec: UxSpec, tmp_path: Path
) -> None:
    env, _home = _environment(spec, tmp_path)
    _login(spec, env, tmp_path)
    session = _session(spec, ADD_TITLE)

    with FakeServer(spec, REPO) as server:
        result = run_session(
            spec,
            session,
            server,
            _with_server_env(spec, server, env),
            answer_override="N",
            run_label="N",
        )
        paths = [request.path for request in server.requests]
        sent = server.sent_formsteps()
        steps = {step.id: step for step in spec.formsteps}

    assert result.ok, result.fails
    _assert_no_traceback(result)
    assert "/rchg/8000235" not in paths
    for step_id, _record in sent:
        assert steps[step_id].before_confirm, (
            f"formstep {step_id!r} wurde vor der Bestätigung gesendet"
        )


def test_add_mit_j_sendet_alle_formsteps_und_legt_fall_an(
    spec: UxSpec, tmp_path: Path
) -> None:
    env, home = _environment(spec, tmp_path)
    _login(spec, env, tmp_path)
    session = _session(spec, ADD_TITLE)

    with FakeServer(spec, REPO) as server:
        result = run_session(
            spec,
            session,
            server,
            _with_server_env(spec, server, env),
            run_label="j",
        )
        paths = [request.path for request in server.requests]
        missing = server.missing_formsteps()
        sent_ids = [step_id for step_id, _record in server.sent_formsteps()]

    assert result.ok, result.fails
    _assert_no_traceback(result)
    assert missing == []
    assert sent_ids == [step.id for step in spec.formsteps]
    assert "/rchg/8000235" in paths

    databases = list(home.rglob("konto.db"))
    assert databases, "add hat keine lokale konto.db angelegt"
    with sqlite3.connect(databases[0]) as connection:
        count = connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    assert count == 1
