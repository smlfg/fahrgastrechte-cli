"""Pydantic-Modelle für das Verspätungskonto (ANFORDERUNGEN §4).

Diese Modelle sind die einzige strukturierte Schnittstelle zwischen DB,
Geschäftslogik, CLI und (Phase 2) MCP-Server. Sie spiegeln das SQLite-Schema
in :mod:`fahrgastrechte_core.db` eins zu eins wider, geben aber zusätzlich
Komfort: Validierung (z. B. ISO-8601, Cent-Beträge, EVA-Nummer-Format),
``to_dict()`` für ``--json`` und ``from_row()`` für DB-Lesezugriffe.

Felder, die in §4 als optional markiert sind, werden in Python optional
gemacht. SQLite kennt NULL; die Modelle tolerieren beides (``None``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------- Enums
class TicketType(StrEnum):
    """Ticket-Typ, der die Entschädigungslogik aus ANFORDERUNGEN §8 speist."""

    D_TICKET = "d_ticket"
    NV_ZEITKARTE = "nv_zeitkarte"
    FV_ZEITKARTE = "fv_zeitkarte"
    NV_EINZEL = "nv_einzel"
    FV_EINZEL = "fv_einzel"
    BC100 = "bc100"


class TripCategory(StrEnum):
    """Produktkategorie des Zuges / Verkehrsmittels."""

    RE = "RE"
    RB = "RB"
    S = "S"
    IC = "IC"
    ICE = "ICE"
    BUS = "Bus"
    STRAB = "Strab"
    OTHER = "Other"


class DataSource(StrEnum):
    """Woher die Trip-Daten stammen."""

    TIMETABLES_API = "timetables_api"
    DB_VENDO = "db_vendo"
    MANUAL = "manual"


class DataQuality(StrEnum):
    """Belastbarkeit der Trip-Daten (ANFORDERUNGEN N3)."""

    VERIFIED = "verified"
    BEST_EFFORT = "best_effort"
    MANUAL_ONLY = "manual_only"


class CaseStatusName(StrEnum):
    """Status-Werte laut §4 / F7."""

    OFFEN = "offen"
    EINGEREICHT = "eingereicht"
    RUECKFRAGE = "rueckfrage"
    NACHGEREICHT = "nachgereicht"
    BEWILLIGT = "bewilligt"
    AUSGEZAHLT = "ausgezahlt"
    ABGELEHNT = "abgelehnt"


class StationSource(StrEnum):
    """Herkunft eines Stations-Datensatzes."""

    TIMETABLES_API = "timetables_api"
    MANUAL = "manual"


# ----------------------------------------------------------- Basismodelle
class _Base(BaseModel):
    """Gemeinsame Konfiguration: ``from_attributes`` für ORM-Lesungen,
    ``frozen`` ist bewusst NICHT gesetzt — Modelle sind Wert-Container,
    werden aber nicht in-place mutiert."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# ----------------------------------------------------------------- Stations
class Station(_Base):
    """Eine Haltestelle mit EVA-Nummer als Primärschlüssel (§4)."""

    eva_no: int = Field(..., ge=1_000_000, le=9_999_999, description="7-stellige EVA-Nummer")
    name: str
    ds100: str | None = None
    lat: float | None = None
    lon: float | None = None
    source: StationSource = StationSource.MANUAL

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name darf nicht leer sein")
        return value


# --------------------------------------------------------------------- Trips
class Trip(_Base):
    """Eine rekonstruierte (oder manuell erfasste) Fahrt (§4)."""

    id: int | None = None
    service_date: str = Field(..., description="ISO 8601 Datum, z. B. 2026-10-12")
    planned_dep: str | None = None
    planned_arr: str | None = None
    actual_dep: str | None = None
    actual_arr: str | None = None
    delay_min_arr: int | None = None
    start_eva: int | None = Field(default=None, ge=1_000_000, le=9_999_999)
    dest_eva: int | None = Field(default=None, ge=1_000_000, le=9_999_999)
    category: TripCategory | None = None
    line: str | None = None
    train_number: str | None = None
    evu: str | None = None
    trip_ref: str | None = None
    data_source: DataSource = DataSource.MANUAL
    data_quality: DataQuality = DataQuality.MANUAL_ONLY
    reconstructed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Stabile Repräsentation für ``--json``/MCP-Tool-Output."""

        return self.model_dump(mode="json")


# ------------------------------------------------------------------- Cases
class Case(_Base):
    """Ein Verspätungsfall mit berechneter Entschädigung (§4)."""

    id: int | None = None
    trip_id: int | None = None
    ticket_type: TicketType
    ticket_class: int | None = Field(default=None, ge=1, le=2)
    reason_text: str | None = None
    evidence_path: str | None = None
    expected_cents: int = Field(..., ge=0, description="Berechnete Entschädigung in Cent")
    cap_applied: int | None = Field(default=None, ge=0, description="0 wenn irrelevant")
    notes: str | None = None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ------------------------------------------------------------- CaseStatus
class CaseStatusEntry(_Base):
    """Eine Status-Zeile im Workflow (§4 / F7).

    Der zusammengesetzte Primärschlüssel ``(case_id, status, at)`` wird in
    Pydantic als zusammengesetztes Feld abgebildet, damit ``to_dict`` ihn
    flach ausgeben kann (MCP-Tools brauchen kein Spezial-Schema).
    """

    case_id: int
    status: CaseStatusName
    at: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "TicketType",
    "TripCategory",
    "DataSource",
    "DataQuality",
    "CaseStatusName",
    "StationSource",
    "Station",
    "Trip",
    "Case",
    "CaseStatusEntry",
]
