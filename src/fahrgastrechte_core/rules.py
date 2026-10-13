"""Entschädigungs-Regeln für das Verspätungskonto (ANFORDERUNGEN §8 / F5 / F11).

Stichtag der Regeln: :data:`RULES_LAST_VERIFIED` (manuell gegen bahn.de
abgeglichen, Quellen-URLs pro Eintrag). Bei Änderungen der politisch
festgelegten Bagatellgrenze (4 €) oder der Pauschalen den Stichtag mit
aktualisieren — das Tool warnt beim Start, wenn der Stichtag > 90 Tage
zurückliegt (ANFORDERUNGEN §11 / Risiko "Bagatellgrenze ändert sich").

Designprinzipien
=================

* **Keine erfundenen Beträge.** Jeder Cent-Wert stammt aus der Quell-Tabelle
  in §8; unbekannte Ticket-Typen oder unzureichende Inputs (z. B.
  Einzelticket ohne Preisangabe) liefern ``cents == 0`` und eine
  ``reason``-Erklärung, niemals Fantasie-Beträge.
* **Ganzzahlen.** Alle Geldwerte in Cent (``int``), Berechnungen ohne
  ``float`` (Division durch 100 würde Rundungsfehler erzeugen).
* **Deterministisch.** ``compute_compensation`` ist eine reine Funktion
  derselben Eingaben — kein DB-Zugriff, kein Logging, keine versteckte
  Zeitabhängigkeit.
* **Strikt typisiert** über :class:`dataclass` + :class:`enum` — die
  Schritt-6-MCP-Tools (``compute_compensation``) übernehmen den
  Rückgabewert 1:1.

Periodenlogik
=============

Die Bagatellgrenze (4 €) und der Deckel (25 % des Ticketwerts pro
Zeitraum) sind Eigenschaften des Antrag-Submissions, nicht des Einzelfalls.
Damit sie auch in Mehrfachläufen konsistent bleiben, kennt
``compute_compensation`` den Parameter ``monthly_total_cents``: der
bereits in diesem Monat angesammelte Betrag. Der Rückgabe-Cent-Wert ist
bereits auf den verbleibenden Capped-Raum begrenzt; Aufruferinnen müssen
ihnweise also nur noch aufsummieren.

Beispiel: D-Ticket 58 €, 10 Fälle mit je 60 min Verspätung:

* Fall 1–9 → je 150 Cent (unter Deckel 1450).
* Fall 10 → 100 Cent (1350 angesammelt, Rest bis 1450 = 100).
* Summe = 1450 (= 25 % von 5800 Cent).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .models import TicketType


# Stichtag der zuletzt manuell gegen bahn.de abgeglichenen Regel-Tabelle
# (ANFORDERUNGEN §11 / §9 N7). Format: ISO-Datum.
RULES_LAST_VERIFIED: str = "2026-10-12"

# Bagatellgrenze in Cent (4,00 €). Beträge darunter werden nicht ausgezahlt
# (Quelle: bahn.de/service/.../rechtliche-regelungen).
BAGATELLGRENZE_CENTS: int = 400

# Deckel pro Periode in Prozent des Ticketwerts (25 %).
DECKEL_PROZENT: int = 25

# Quell-URLs aus ANFORDERUNGEN §8.
SRC_BAHN_DE_FAQ_DT = (
    "https://www.bahn.de/faq/deutschlandticket-verspaetung-erstattung"
)
SRC_BAHN_DE_RECHTLICH = (
    "https://www.bahn.de/service/informationen-buchung/fahrgastrechte/"
    "rechtliche-regelungen"
)
SRC_BAHN_DE_LETZTE_VERBINDUNG = (
    "https://www.bahn.de/faq/gk/angebot/zeitkarten/"
    "deutschland-ticket/fahrgastrechte"
)


class ReasonCode(str, Enum):
    """Kurze, stabile Kennung der Regel-Begründung (für Tests + JSON)."""

    NO_DELAY = "no_delay"
    UNKNOWN_TICKET = "unknown_ticket"
    MISSING_PRICE = "missing_price"
    PAUSCHALE = "pauschale"
    PERCENT_25 = "percent_25"
    PERCENT_50 = "percent_50"
    BELOW_THRESHOLD = "below_threshold"
    CAP_REACHED = "cap_reached"


# --------------------------------------------------------------- Regel-Tabelle
# Werte in Cent, Ganzzahlen. Die Klassen-Logik (1. Kl. vs 2. Kl.) wird
# in :func:`_flat_for` ausgewertet; die Quelle ist je Zeile dokumentiert.
# Die ``_KEY``-Strings sind stabil für Tests/JSON-Output.
#
# Format: ticket_type -> {
#   "source": Quell-URL,
#   "classes": { 1: cents_kl_1, 2: cents_kl_2 },   # für Pauschalen
# }
_PAUSCHALEN: Mapping[TicketType, Mapping[str, object]] = {
    TicketType.D_TICKET: {
        "source": SRC_BAHN_DE_FAQ_DT,
        # §3 EVO "erheblich ermäßigtes Beförderungsentgelt" — nur 2. Klasse.
        "classes": {2: 150},
        "label": "D-Ticket: 1,50 € pro Fall (2. Kl.)",
    },
    TicketType.NV_ZEITKARTE: {
        "source": SRC_BAHN_DE_RECHTLICH,
        "classes": {2: 150, 1: 225},
        "label": "NV-Zeitkarte: 1,50 € (2. Kl.) / 2,25 € (1. Kl.) pro Fall",
    },
    TicketType.FV_ZEITKARTE: {
        "source": SRC_BAHN_DE_RECHTLICH,
        "classes": {2: 500, 1: 750},
        "label": "FV-Zeitkarte: 5,00 € (2. Kl.) / 7,50 € (1. Kl.) pro Fall",
    },
    TicketType.BC100: {
        "source": SRC_BAHN_DE_RECHTLICH,
        "classes": {2: 1000, 1: 1500},
        "label": "BahnCard 100: 10,00 € (2. Kl.) / 15,00 € (1. Kl.) pro Fall",
    },
}


# --------------------------------------------------------------- Datencontainer
@dataclass(frozen=True, slots=True)
class CompensationResult:
    """Ergebnis von :func:`compute_compensation`.

    Felder
    ------

    * ``cents`` — Cent-Betrag für diesen Einzelfall (bereits auf den
      verbleibenden Capped-Raum begrenzt, siehe ``monthly_total_cents``).
    * ``reason`` — menschliche Erklärung der Regel auf Deutsch.
    * ``reason_code`` — stabile :class:`ReasonCode`-Kennung für Tests/JSON.
    * ``cap_applied`` — 25-%-Deckel des Ticketwerts in Cent (für den
      Hinweis "Deckel erreicht/nicht erreicht"), 0 wenn nicht anwendbar.
    * ``below_threshold`` — ``True`` wenn ``cents < BAGATELLGRENZE_CENTS``
      (Einzelfall-Hinweis "unter Bagatellgrenze, sammeln").
    """

    cents: int
    reason: str
    reason_code: ReasonCode
    cap_applied: int
    below_threshold: bool

    def to_dict(self) -> dict[str, object]:
        """Stabile Repräsentation für ``--json`` und MCP-Tools."""

        return {
            "cents": self.cents,
            "reason": self.reason,
            "reason_code": self.reason_code.value,
            "cap_applied": self.cap_applied,
            "below_threshold": self.below_threshold,
        }


# --------------------------------------------------------------- Helfer
def _ticket_type(value: TicketType | str) -> TicketType:
    """Akzeptiert :class:`TicketType` oder String, normalisiert auf Enum."""

    if isinstance(value, TicketType):
        return value
    try:
        return TicketType(str(value))
    except ValueError as exc:
        raise ValueError(
            f"Unbekannter ticket_type {value!r}; "
            f"erlaubt: {', '.join(t.value for t in TicketType)}"
        ) from exc


def _cap_cents(ticket_price_cents: int | None) -> int:
    """25-%-Deckel des Ticketwerts in Cent (0 wenn kein Preiswert ``kannt)."""

    if ticket_price_cents is None:
        return 0
    if ticket_price_cents < 0:
        return 0
    # Ganzzahl-Mathematik, damit z. B. 49 € → 1225 Cent, 58 € → 1450 Cent.
    return (int(ticket_price_cents) * DECKEL_PROZENT) // 100


def _percent_cents(ticket_price_cents: int | None, percent: int) -> int:
    """``percent`` Prozent von ``ticket_price_cents`` in Cent.

    Wirft ``ValueError`` bei fehlendem Preiswert — Einzelticket ohne Preis
    darf keinen erfundenen Betrag liefern.
    """

    if ticket_price_cents is None:
        raise ValueError(
            "ticket_price_cents ist Pflicht für Einzelticket-Berechnung"
        )
    if ticket_price_cents < 0:
        raise ValueError(
            f"ticket_price_cents darf nicht negativ sein, "
            f"bekommen: {ticket_price_cents!r}"
        )
    return (int(ticket_price_cents) * int(percent)) // 100


def _class_or_default(travel_class: int | None) -> int:
    """Default 2. Kl., wenn nicht gesetzteingebunden ("exception class").

    Wirft ``ValueError`` bei offensichtlich falschen Werten (0, 4, …).
    """

    if travel_class is None:
        return 2
    if travel_class not in (1, 2):
        raise ValueError(
            f"travel_class muss 1 oder 2 sein, nicht {travel_class!r}"
        )
    return int(travel_class)


# --------------------------------------------------------------- Public API
def compute_compensation(
    ticket_type: TicketType | str,
    delay_min: int,
    *,
    ticket_price_cents: int | None = None,
    travel_class: int = 2,
    monthly_total_cents: int = 0,
) -> CompensationResult:
    """Berechnet die Entschädigung für **einen** Verspätungsfall (F5).

    Parameter
    ---------

    * ``ticket_type`` — :class:`TicketType` oder String (Wert werden über
      :class:`TicketType` validiert).
    * ``delay_min`` — Verspätung am Ziel in Minuten. Werte unter 60 min
        liefern grundsätzlich ``0`` Cent (§8: "≥ 60 min").
    * ``ticket_price_cents`` — Monatspreis des Tickets in Cent (Pflicht
        für Einzeltickets; Pauschalen verwenden den Preis nur für den
        Deckel-Hinweis).
    * ``travel_class`` — 1 oder 2 (Default 2).
    * ``monthly_total_cents`` — bereits in diesem Monat angesammelte
        Cent-Beträge. Verhindert, dass die Summe den 25-%-Deckel
        überschreitet: das ``cents``-Ergebnis ist auf den verbleibenden
        Raum begrenzt.

    Verhalten
    ---------

    * ``delay_min < 60`` → ``cents == 0``, ``reason = "kein Anspruch"``.
    * Pauschalen (D-Ticket, NV-/FV-Zeitkarte, BC100) → fester Betrag je
      Klasse, ggf. durch Deckel begrenzt.
    * Einzeltickets → 25 % (≥ 60 min) bzw. 50 % (≥ 120 min) des
      Ticketpreises; ohne Preisangabe ``ValueError`` (kein erfundenen Betrag).
    * ``cap_applied`` ist immer 25 % von ``ticket_price_cents`` (oder 0,
      wenn nicht angegeben).
    * ``below_threshold`` ist ``True`` wenn ``cents < BAGATELLGRENZE_CENTS``.
    """

    tt = _ticket_type(ticket_type)
    delay = int(delay_min)
    klass = _class_or_default(travel_class)
    cap = _cap_cents(ticket_price_cents)

    # 1) Unter 60 min → kein Anspruch, kein Cap-Effekt.
    if delay < 60:
        return CompensationResult(
            cents=0,
            reason=(
                f"Verspätung {delay} min am Ziel < 60 min — "
                "kein Entschädigungs-Anspruch (§8)."
            ),
            reason_code=ReasonCode.NO_DELAY,
            cap_applied=cap,
            below_threshold=False,
        )

    # 2) Pauschalen.
    if tt in _PAUSCHALEN:
        rule = _PAUSCHALEN[tt]
        classes: Mapping[int, int] = rule["classes"]  # type: ignore[assignment]
        label: str = rule["label"]  # type: ignore[assignment]
        flat = int(classes[klass])
        # Klassen-spezifische Quelle (für Tests/JSON-Audit).
        source = str(rule["source"])

        # Cap-Anwendung: wenn der Monats-Deckel bereits überschritten ist,
        # gibt es für diesen Fall nichts mehr; sonst auf Rest begrenzen.
        if cap > 0:
            remaining = max(0, cap - max(0, int(monthly_total_cents)))
            granted = min(flat, remaining)
            cap_hit = granted < flat
        else:
            granted = flat
            cap_hit = False

        if granted == 0 and cap_hit:
            reason = (
                f"{label} — Monats-Deckel von {cap / 100:.2f} € bereits "
                "ausgeschöpft; kein weiterer Betrag für diesen Fall."
            )
            code = ReasonCode.CAP_REACHED
        else:
            reason = (
                f"{label} → {granted / 100:.2f} € "
                f"({granted} Cent × 1 Fall)."
            )
            if cap_hit:
                reason += (
                    f" Hinweis: Monats-Deckel {cap / 100:.2f} € "
                    "(25 % des Ticketwerts) fast erreicht."
                )
                code = ReasonCode.CAP_REACHED
            else:
                code = ReasonCode.PAUSCHALE
            # Quell-URL nur in die reason schreiben, wenn kein Cap-Hit —
            # sonst ist der Hinweis wichtiger.
            reason += f" [Quelle: {source}]"

        return CompensationResult(
            cents=int(granted),
            reason=reason,
            reason_code=code,
            cap_applied=cap,
            below_threshold=int(granted) < BAGATELLGRENZE_CENTS,
        )

    # 3) Einzeltickets (NV oder FV).
    if tt in (TicketType.NV_EINZEL, TicketType.FV_EINZEL):
        try:
            if delay >= 120:
                amount = _percent_cents(ticket_price_cents, 50)
                code = ReasonCode.PERCENT_50
                pct_text = "50 %"
            else:
                amount = _percent_cents(ticket_price_cents, 25)
                code = ReasonCode.PERCENT_25
                pct_text = "25 %"
        except ValueError as exc:
            # Ohne Preis → kein erfundenen Betrag, klarer Hinweis.
            return CompensationResult(
                cents=0,
                reason=(
                    f"Einzelticket ({tt.value}) ohne Preisangabe — "
                    "Betrag kann nicht berechnet werden. "
                    "Bitte ticket_price_cents angeben. "
                    f"[Quelle: {SRC_BAHN_DE_RECHTLICH}]"
                ),
                reason_code=ReasonCode.MISSING_PRICE,
                cap_applied=cap,
                below_threshold=True,
            )
        # Einzeltickets: kein Monats-Deckel-Cap (der ganze Fall ist ein
        # Ticket). Aber Bagatellgrenze greift für die Summe.
        label = "NV-Einzelticket" if tt is TicketType.NV_EINZEL else "FV-Einzelticket"
        return CompensationResult(
            cents=int(amount),
            reason=(
                f"{label}: {pct_text} des Fahrpreises "
                f"({amount} Cent = {amount / 100:.2f} €). "
                f"[Quelle: {SRC_BAHN_DE_RECHTLICH}]"
            ),
            reason_code=code,
            cap_applied=cap,
            below_threshold=int(amount) < BAGATELLGRENZE_CENTS,
        )

    # 4) Unbekannter Typ (sollte nicht passieren, _ticket_type wirft
    # bereits ValueError, aber doppelte Absicherung).
    return CompensationResult(
        cents=0,
        reason=f"Unbekannter ticket_type {tt!r}; kein Betrag berechnet.",
        reason_code=ReasonCode.UNKNOWN_TICKET,
        cap_applied=cap,
        below_threshold=True,
    )


# --------------------------------------------------------------- Re-export
__all__ = [
    "BAGATELLGRENZE_CENTS",
    "DECKEL_PROZENT",
    "CompensationResult",
    "RULES_LAST_VERIFIED",
    "ReasonCode",
    "SRC_BAHN_DE_FAQ_DT",
    "SRC_BAHN_DE_RECHTLICH",
    "SRC_BAHN_DE_LETZTE_VERBINDUNG",
    "compute_compensation",
]