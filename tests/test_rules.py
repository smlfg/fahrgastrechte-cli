"""Unit-Tests für Entschädigungs-Regeln (MVP-Schritt 5, ANFORDERUNGEN §8).

Werte gegen bahn.de: D-Ticket 1,50 €/Fall ab 60 min, Bagatellgrenze 4 €,
Deckel 25 % des Ticketpreises (58 € → 14,50 €).
"""

from __future__ import annotations

from fahrgastrechte_core import cases as fc_cases, db, stats as fc_stats
from fahrgastrechte_core.models import TicketType
from fahrgastrechte_core.rules import (
    BAGATELLGRENZE_CENTS,
    RULES_LAST_VERIFIED,
    compute_compensation,
)


def test_rules_last_verified_is_set() -> None:
    assert RULES_LAST_VERIFIED == "2026-10-12"


def test_d_ticket_60_min_is_150_cents() -> None:
    r = compute_compensation(
        TicketType.D_TICKET,
        60,
        ticket_price_cents=4900,
        travel_class=2,
    )
    assert r.cents == 150
    assert r.below_threshold is True
    assert r.cap_applied == 1225


def test_d_ticket_59_min_no_claim() -> None:
    r = compute_compensation(TicketType.D_TICKET, 59, ticket_price_cents=4900)
    assert r.cents == 0
    assert r.below_threshold is False


def test_five_cases_sum_750_above_bagatellgrenze() -> None:
    saldo = 0
    for _ in range(5):
        r = compute_compensation(
            TicketType.D_TICKET,
            65,
            ticket_price_cents=4900,
            monthly_total_cents=saldo,
        )
        assert r.cents == 150
        saldo += r.cents
    assert saldo == 750
    assert saldo >= BAGATELLGRENZE_CENTS


def test_d_ticket_58_euro_cap_10_cases() -> None:
    """10 × 60 min à 1,50 €, Deckel 25 % von 58 € = 14,50 € (1450 Cent)."""

    price = 5800
    cap = (price * 25) // 100
    assert cap == 1450
    saldo = 0
    for i in range(9):
        r = compute_compensation(
            TicketType.D_TICKET,
            60,
            ticket_price_cents=price,
            monthly_total_cents=saldo,
        )
        assert r.cents == 150, f"Fall {i + 1}"
        saldo += r.cents
    last = compute_compensation(
        TicketType.D_TICKET,
        60,
        ticket_price_cents=price,
        monthly_total_cents=saldo,
    )
    assert last.cents == 100
    assert saldo + last.cents == 1450


def test_aggregate_cases_five_in_month(tmp_path) -> None:
    db_path = tmp_path / "konto.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        for n in range(5):
            fc_cases.add_case(
                conn,
                fc_cases.AddCaseInput(
                    service_date="2026-10-12",
                    train_number=str(10000 + n),
                    start_eva=8000096,
                    dest_eva=8000105,
                    ticket_type=TicketType.D_TICKET.value,
                    reason_text="test",
                    expected_cents=0,
                    delay_min_arr=65,
                ),
            )
        buckets = fc_stats.aggregate_cases(
            fc_stats._case_rows(conn),
            ticket_price_cents=4900,
            month="2026-10",
        )
    assert buckets["2026-10"]["case_count"] == 5
    assert buckets["2026-10"]["expected_cents"] == 750
    assert buckets["2026-10"]["below_threshold"] is False


def test_calc_cli_json(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from fahrgastrechte_cli.main import app

    db_path = tmp_path / "konto.db"
    monkeypatch.setenv("FAHRGASTRECHTE_DB", str(db_path))
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "add",
                "--service-date",
                "2026-10-12",
                "--train",
                "12345",
                "--start-eva",
                "8000096",
                "--dest-eva",
                "8000105",
                "--reason",
                "verspätet",
                "--delay-min",
                "65",
            ],
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["calc", "1", "--json"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.stdout)
    assert payload["cents"] == 150
    assert payload["below_threshold"] is True
