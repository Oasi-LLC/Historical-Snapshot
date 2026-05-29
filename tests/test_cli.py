from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "historical_snapshot.cli", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_cli_text_output_contains_key_metrics():
    result = run_cli(
        "--csv",
        str(FIXTURES / "bookings_sample.csv"),
        "--property-id",
        "P001",
        "--start-date",
        "2025-01-01",
        "--end-date",
        "2025-01-31",
    )

    assert "ADR: 167.50" in result.stdout
    assert "RevPAR: 1.44" in result.stdout
    assert "Average LOS: 2.00" in result.stdout


def test_cli_json_output_is_valid():
    result = run_cli(
        "--csv",
        str(FIXTURES / "bookings_sample.csv"),
        "--property-id",
        "P001",
        "--start-date",
        "2025-01-01",
        "--end-date",
        "2025-01-31",
        "--format",
        "json",
    )
    payload = json.loads(result.stdout)
    assert payload["property"]["id"] == "P001"
    assert payload["portfolio_snapshot"]["adr"] == 167.5
