"""Lokale DB-API-Zugangsdaten (Login-Session).

Es gibt keinen Web-Login. ``login`` speichert ausschließlich die beiden
Header-Werte für die DB-Timetables-API in einer Datei mit Rechten ``0600``.
Die Datei liegt unter dem XDG-Konfigurationspfad und niemals im Repo.

Priorität: Env-Variablen vor Session-Datei. Secrets werden nie geloggt und
von ``whoami`` nur maskiert ausgegeben.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .paths import ensure_private_dir, ensure_private_file

ENV_CLIENT_ID = "FAHRGASTRECHTE_DB_CLIENT_ID"
ENV_API_KEY = "FAHRGASTRECHTE_DB_API_KEY"
ENV_SESSION = "FAHRGASTRECHTE_SESSION"

APP_DIR_NAME = "fahrgastrechte"
SESSION_FILE_NAME = "session.json"


def session_path() -> Path:
    """Pfad der Session-Datei laut INTERFACE.md."""

    override = os.environ.get(ENV_SESSION)
    if override:
        return Path(override).expanduser()
    config_root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_root).expanduser() if config_root else Path.home() / ".config"
    return base / APP_DIR_NAME / SESSION_FILE_NAME


@dataclass
class CredentialStatus:
    """Aufgelöste Zugangsdaten inklusive Herkunft (ohne Secrets in Logs)."""

    client_id: str | None = None
    api_key: str | None = None
    source: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.api_key)


def _read_session_file(path: Path) -> tuple[str | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    client_id = data.get("db_client_id")
    api_key = data.get("db_api_key")
    return (
        str(client_id) if client_id else None,
        str(api_key) if api_key else None,
    )


def resolve_credentials() -> CredentialStatus:
    """Env hat Vorrang; fehlende Werte werden aus der Session ergänzt."""

    env_client_id = os.environ.get(ENV_CLIENT_ID)
    env_api_key = os.environ.get(ENV_API_KEY)
    file_client_id, file_api_key = _read_session_file(session_path())

    client_id = env_client_id or file_client_id
    api_key = env_api_key or file_api_key
    sources: list[str] = []
    if env_client_id or env_api_key:
        sources.append("env")
    if file_client_id or file_api_key:
        sources.append("session")
    source = "+".join(sources) if sources else None
    return CredentialStatus(client_id=client_id, api_key=api_key, source=source)


def save_credentials(client_id: str, api_key: str) -> Path:
    """Schreibt die Session atomar mit ``0600`` und gibt den Pfad zurück."""

    path = session_path()
    ensure_private_dir(path.parent)
    ensure_private_file(path)
    payload = json.dumps(
        {"db_client_id": client_id, "db_api_key": api_key},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload + "\n", encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:  # pragma: no cover - Windows
            pass
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    return path


def delete_session() -> bool:
    """Löscht die Session-Datei. Mehrfacher Aufruf ist erlaubt."""

    path = session_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def mask_secret(value: str | None) -> str:
    """Maskiert einen Wert für ``whoami`` (nie der vollständige Secret-Wert)."""

    if not value:
        return "—"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}…{value[-2:]}"


__all__ = [
    "ENV_CLIENT_ID",
    "ENV_API_KEY",
    "ENV_SESSION",
    "CredentialStatus",
    "session_path",
    "resolve_credentials",
    "save_credentials",
    "delete_session",
    "mask_secret",
]
