"""Export von Fällen als CSV/PDF (ANFORDERUNGEN §3 F8/F9)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from . import cases as fc_cases
from . import config as fc_config


CSV_COLUMNS: tuple[str, ...] = (
    "case_id",
    "service_date",
    "train_number",
    "line",
    "start_eva",
    "dest_eva",
    "delay_min_arr",
    "expected_cents",
    "current_status",
    "reason_text",
)


def export_csv(
    conn: sqlite3.Connection,
    out_path: Path,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    """Schreibt Fälle als RFC-4180-CSV."""

    rows = fc_cases.list_cases(conn, status=status)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(list(CSV_COLUMNS))
        for row in rows:
            writer.writerow(
                [
                    row.get("id") if col == "case_id" else row.get(col)
                    for col in CSV_COLUMNS
                ]
            )
    return {"path": str(out_path), "rows": len(rows)}


def export_pdf(
    conn: sqlite3.Connection,
    out_path: Path,
    case_ids: Sequence[int],
) -> dict[str, Any]:
    """Erzeugt ein druckfertiges PDF mit Stammdaten und Fall-Tabelle."""

    if not case_ids:
        raise ValueError("Mindestens eine Fall-ID (--ids) angeben.")

    selected: list[dict[str, Any]] = []
    for cid in case_ids:
        selected.append(fc_cases.show_case(conn, int(cid)))

    cfg = fc_config.list_config(conn)
    name = cfg.get("name", "")
    address = cfg.get("address", "")
    iban = cfg.get("iban", "")
    dticket_id = cfg.get("dticket_id", "")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=A4, pageCompression=0)
    width, height = A4
    y = height - 72

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, "Fahrgastrechte — Antragsvorbereitung")
    y -= 28
    c.setFont("Helvetica", 11)

    for label, value in (
        ("Name", name),
        ("Adresse", address),
        ("IBAN", iban),
        ("D-Ticket-ID", dticket_id),
    ):
        if value:
            c.drawString(72, y, f"{label}: {value}")
            y -= 16

    y -= 12
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y, "Fall  Datum       Zug     Versp.  Erwartet  Status")
    y -= 14
    c.setFont("Helvetica", 10)
    for row in selected:
        cents = int(row.get("expected_cents") or 0)
        eur = f"{cents / 100:.2f}".replace(".", ",")
        line = (
            f"#{row.get('id')}  {row.get('service_date', ''):<10} "
            f"{str(row.get('train_number', '')):<6} "
            f"{str(row.get('delay_min_arr') or '-'):>4}    "
            f"{eur:>6} €  {row.get('current_status', '')}"
        )
        c.drawString(72, y, line)
        y -= 14
        if y < 72:
            c.showPage()
            y = height - 72
            c.setFont("Helvetica", 10)

    c.save()
    size = out_path.stat().st_size
    return {"path": str(out_path), "size": size, "cases": len(selected)}


__all__ = ["export_csv", "export_pdf", "CSV_COLUMNS"]
