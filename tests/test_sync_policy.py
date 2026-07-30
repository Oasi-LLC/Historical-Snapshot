from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from historical_snapshot.config import PropertyConfig, PropertyDataSource
from historical_snapshot.io.google_sheets import SyncResult, write_sync_metadata
from historical_snapshot.io.sync_policy import (
    ensure_daily_sync,
    needs_daily_sync,
    synced_on_date,
    sync_timezone,
)


def _sheets_config(folder: str) -> PropertyConfig:
    return PropertyConfig(
        folder=folder,
        pms=folder,
        property_id=folder.upper(),
        property_name=folder.title(),
        data_source=PropertyDataSource(type="google_sheets", tab=f"{folder}_data"),
    )


def _write_property_cache(
    tmp_path: Path,
    folder: str,
    *,
    synced_at: str,
) -> None:
    cache_dir = tmp_path / ".cache" / "sheets"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{folder.lower()}.csv"
    cache_path.write_text("booking_date,reservation_date\n", encoding="utf-8")
    write_sync_metadata(
        cache_dir / f"{folder.lower()}.meta.json",
        tab=f"{folder}_data",
        row_count=1,
        synced_at=synced_at,
    )


LISBON = ZoneInfo("Europe/Lisbon")


def test_sync_timezone_defaults_to_lisbon():
    with patch.dict("os.environ", {}, clear=True):
        assert sync_timezone().key == "Europe/Lisbon"


def test_sync_timezone_reads_env():
    with patch.dict("os.environ", {"SYNC_TIMEZONE": "America/New_York"}):
        assert sync_timezone().key == "America/New_York"


def test_synced_on_date_lisbon_edge_case():
    assert synced_on_date(
        "2026-07-28T23:30:00+00:00",
        day=date(2026, 7, 29),
        tz=LISBON,
    )
    assert not synced_on_date(
        "2026-07-28T23:30:00+00:00",
        day=date(2026, 7, 28),
        tz=LISBON,
    )


@patch("historical_snapshot.io.sync_policy._google_sheets_property_configs")
def test_needs_daily_sync_false_when_all_synced_today(mock_configs, tmp_path: Path):
    mock_configs.return_value = [_sheets_config("wmb"), _sheets_config("onera")]
    today = date(2026, 7, 29)
    synced_at = "2026-07-29T08:00:00+00:00"
    _write_property_cache(tmp_path, "wmb", synced_at=synced_at)
    _write_property_cache(tmp_path, "onera", synced_at=synced_at)

    needed, reason = needs_daily_sync(tmp_path, tz=LISBON, today=today)

    assert needed is False
    assert "all 2 properties synced today" in reason


@patch("historical_snapshot.io.sync_policy._google_sheets_property_configs")
def test_needs_daily_sync_true_when_one_property_stale(mock_configs, tmp_path: Path):
    mock_configs.return_value = [_sheets_config("wmb"), _sheets_config("onera")]
    today = date(2026, 7, 29)
    _write_property_cache(tmp_path, "wmb", synced_at="2026-07-29T08:00:00+00:00")
    _write_property_cache(tmp_path, "onera", synced_at="2026-07-27T08:00:00+00:00")

    needed, reason = needs_daily_sync(tmp_path, tz=LISBON, today=today)

    assert needed is True
    assert "onera" in reason


@patch("historical_snapshot.io.sync_policy._google_sheets_property_configs")
def test_needs_daily_sync_true_when_meta_missing(mock_configs, tmp_path: Path):
    mock_configs.return_value = [_sheets_config("wmb")]
    cache_dir = tmp_path / ".cache" / "sheets"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "wmb.csv").write_text("x\n", encoding="utf-8")

    needed, reason = needs_daily_sync(
        tmp_path,
        tz=LISBON,
        today=date(2026, 7, 29),
    )

    assert needed is True
    assert "missing sync metadata" in reason


@patch("historical_snapshot.io.sync_policy.sync_all_properties")
@patch("historical_snapshot.io.sync_policy._google_sheets_property_configs")
@patch("historical_snapshot.io.sync_policy._sheets_env_configured", return_value=True)
def test_ensure_daily_sync_skips_when_fresh(
    mock_env,
    mock_configs,
    mock_sync_all,
    tmp_path: Path,
):
    mock_configs.return_value = [_sheets_config("wmb")]
    _write_property_cache(
        tmp_path,
        "wmb",
        synced_at="2026-07-29T08:00:00+00:00",
    )

    result = ensure_daily_sync(
        tmp_path,
        tz=LISBON,
        today=date(2026, 7, 29),
    )

    assert result is None
    mock_sync_all.assert_not_called()


@patch("historical_snapshot.io.sync_policy.sync_all_properties")
@patch("historical_snapshot.io.sync_policy._google_sheets_property_configs")
@patch("historical_snapshot.io.sync_policy._sheets_env_configured", return_value=True)
def test_ensure_daily_sync_runs_when_stale(
    mock_env,
    mock_configs,
    mock_sync_all,
    tmp_path: Path,
):
    mock_configs.return_value = [_sheets_config("wmb")]
    _write_property_cache(
        tmp_path,
        "wmb",
        synced_at="2026-07-27T08:00:00+00:00",
    )
    mock_sync_all.return_value = SyncResult(properties=(), errors=())

    result = ensure_daily_sync(
        tmp_path,
        tz=LISBON,
        today=date(2026, 7, 29),
    )

    assert result is not None
    mock_sync_all.assert_called_once_with(data_root=tmp_path)


@patch("historical_snapshot.io.sync_policy.sync_all_properties")
@patch("historical_snapshot.io.sync_policy._sheets_env_configured", return_value=False)
def test_ensure_daily_sync_skips_without_credentials(mock_env, mock_sync_all, tmp_path: Path):
    result = ensure_daily_sync(tmp_path, tz=LISBON, today=date(2026, 7, 29))

    assert result is None
    mock_sync_all.assert_not_called()


def test_service_ensure_daily_sync_wrapper(tmp_path: Path):
    from historical_snapshot.service import ensure_daily_sync as service_ensure

    with patch(
        "historical_snapshot.service._ensure_daily_sync",
        return_value=None,
    ) as mock_policy:
        assert service_ensure(data_root=tmp_path) is None
        mock_policy.assert_called_once()
