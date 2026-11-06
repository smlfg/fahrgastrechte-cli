"""Antrags-Vorbereitung als CSV oder PDF (ANFORDERUNGEN F8/F9).

Das Tool versendet nichts. Die Dateien landen im aktuellen Verzeichnis und
werden vom Menschen selbst weiterverwendet. PDF wird ohne Zusatzabhängigkeit
mit einem kleinen, stdlib-basierten Writer erzeugt (Helvetica, WinAnsi).
"""

from __future__ import annotations

import csv
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CSV_HEADERS = (
    "id",
    "datum",
    "zug",
    "linie",
    "von_eva",
    "nach_eva",
    "verspaetung_min",
    "status",
    "ticket_type",
    "erwartet_cent",
)


def _cell(case: Mapping[str, Any], key: str, default: str = "") -> str:
    value = case.get(key)
    if value is None:
        return default
    return str(value)


def export_csv(
    cases: Sequence[Mapping[str, Any]],
    path: Path,
) -> Path:
    """Schreibt die Fälle als RFC-4180-CSV (UTF-8, CRLF)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(CSV_HEADERS)
        for case in cases:
            writer.writerow(
                [
                    _cell(case, "id"),
                    _cell(case, "service_date"),
                    _cell(case, "train_number"),
                    _cell(case, "line"),
                    _cell(case, "start_eva"),
                    _cell(case, "dest_eva"),
                    _cell(case, "delay_min_arr"),
                    _cell(case, "current_status", "offen"),
                    _cell(case, "ticket_type"),
                    _cell(case, "expected_cents"),
                ]
            )
    return path


def _ascii(text: str) -> str:
    """PDF-Standardschrift kann kein UTF-8: Umlaute transliterieren."""

    return (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "replace")
        .decode("ascii")
    )


def _pdf_escape(text: str) -> str:
    return (
        _ascii(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _pdf_bytes(lines: Iterable[str]) -> bytes:
    text_lines = list(lines)
    commands = ["BT", "/F1 10 Tf", "50 800 Td", "14 TL"]
    for index, line in enumerate(text_lines):
        if index:
            commands.append("T*")
        commands.append(f"({_pdf_escape(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", "replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def export_pdf(
    cases: Sequence[Mapping[str, Any]],
    path: Path,
    *,
    profile: Mapping[str, str] | None = None,
) -> Path:
    """Schreibt ein einfaches, druckbares Antrags-PDF."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = profile or {}
    lines: list[str] = [
        "Fahrgastrechte - Antragsvorbereitung",
        "",
    ]
    for key, label in (
        ("name", "Name"),
        ("address", "Adresse"),
        ("iban", "IBAN"),
        ("ticket_type", "Ticket-Typ"),
        ("dticket_id", "D-Ticket-ID"),
    ):
        value = profile.get(key)
        if value:
            lines.append(f"{label}: {value}")
    lines.append("")
    lines.append("Faelle:")
    if not cases:
        lines.append("- keine Faelle ausgewaehlt")
    for case in cases:
        lines.append(
            "- #{} {} {} {} -> {} | {} min | {} | {} Cent".format(
                _cell(case, "id"),
                _cell(case, "service_date"),
                _cell(case, "train_number"),
                _cell(case, "start_eva"),
                _cell(case, "dest_eva"),
                _cell(case, "delay_min_arr", "-"),
                _cell(case, "current_status", "offen"),
                _cell(case, "expected_cents"),
            )
        )
    lines.extend(
        [
            "",
            "Den Antrag reicht der Mensch selbst ein (DB-Kundenkonto oder Post).",
        ]
    )
    path.write_bytes(_pdf_bytes(lines))
    return path


__all__ = ["CSV_HEADERS", "export_csv", "export_pdf"]
