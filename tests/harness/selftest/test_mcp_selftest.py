import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
REPO = HARNESS.parents[1]
MCP_CHECK = HARNESS / "mcp_check.py"
GOOD = HARNESS / "selftest" / "good_mcp"
BAD = HARNESS / "selftest" / "bad_mcp"
SCRATCH_TMP = REPO / "scratch" / "tmp"


def _workdir() -> Path:
    SCRATCH_TMP.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="mcp-check-selftest-", dir=SCRATCH_TMP))


def _run_check(repo: Path):
    root = _workdir()
    report = root / "report.md"
    command = [
        sys.executable,
        str(MCP_CHECK),
        str(repo),
        "--venv",
        str(root / "venv"),
        "--report",
        str(report),
    ]
    result = subprocess.run(
        command,
        cwd=str(REPO),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )
    text = report.read_text(encoding="utf-8") if report.is_file() else result.stdout
    return result.returncode, result.stdout, text


def _rows(report: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in report.splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) >= 3 and parts[0].startswith("MCP"):
            rows[parts[0]] = parts[1]
    return rows


def test_good_mcp_passes():
    code, stdout, report = _run_check(GOOD)
    rows = _rows(report)
    assert code == 0, stdout
    assert "FAIL" not in rows.values(), report
    for check in ("MCP00", "MCP01", "MCP04", "MCP05"):
        assert rows.get(check) == "PASS", f"{check} sollte PASS sein:\n{report}"


def test_bad_mcp_fails_expected_checks():
    code, stdout, report = _run_check(BAD)
    rows = _rows(report)
    assert code != 0, stdout
    for check in ("MCP00", "MCP01", "MCP04", "MCP05"):
        assert rows.get(check) == "FAIL", f"{check} sollte FAIL sein:\n{report}"
    assert "stray stdout" in report
    assert "Traceback" in report
    assert "Secret-Leak" in report
