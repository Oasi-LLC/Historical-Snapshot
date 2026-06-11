# Historical Snapshot

CLI and web dashboard for historical property performance (revenue management snapshots).

**Repository:** [github.com/Oasi-LLC/Historical-Snapshot](https://github.com/Oasi-LLC/Historical-Snapshot)

## Features

- **Stay-overlap filtering** (default): bookings with occupied nights in your date range; revenue prorated to those nights
- Portfolio KPIs: ADR, Occupancy %, RevPAR, LOS, pickup by booking-window band
- Breakdown by **listing** or **grouping**
- YoY compare mode in the Streamlit dashboard
- **CLI**, **REST API**, and **Streamlit dashboard**

## Prerequisites

- **Python 3.10+** (3.11 recommended)
- **git**
- Property booking export as CSV (see [Data](#data-not-in-git))
- Optional: **Docker** for containerized runs

## Quick start (after clone)

```bash
git clone https://github.com/Oasi-LLC/Historical-Snapshot.git
cd Historical-Snapshot

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install -e .

# Add your CSV (not in the repo — see data/README.md)
mkdir -p data/lafave
# copy your export to: data/lafave/lafave_main_data.csv

# Smoke test (uses committed test fixtures only)
PYTHONPATH=src pytest -q
```

### Run the dashboard (two terminals)

**Terminal 1 — API** (must be running before the UI):

```bash
source .venv/bin/activate
cd /path/to/Historical-Snapshot
PYTHONPATH=src uvicorn api.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — Streamlit:**

```bash
source .venv/bin/activate
cd /path/to/Historical-Snapshot
./scripts/run_dashboard.sh
```

Open **http://localhost:8502**, choose property and dates in the sidebar, then click **Run snapshot**.

For teammates on the same Wi‑Fi/LAN, bind to all interfaces:

```bash
PYTHONPATH=src uvicorn api.main:app --host 0.0.0.0 --port 8000
SNAPSHOT_API_URL=http://127.0.0.1:8000 streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8502
```

Then use `http://<your-machine-ip>:8502` from another device.

## Project layout

```
Historical-Snapshot/
  src/historical_snapshot/   # Core metrics, CSV ingest, CLI logic
  api/main.py                # FastAPI REST API
  dashboard/app.py           # Streamlit UI (calls API)
  tests/fixtures/            # Small sample CSVs for pytest
  data/                      # Your CSVs locally (gitignored)
  docker-compose.yml
```

## Install options

**Recommended** (matches `requirements.txt` — API, dashboard, and tests):

```bash
pip install -r requirements.txt
pip install -e .
```

**Optional extras** via `pyproject.toml`:

```bash
pip install -e ".[all]"    # api + dashboard + dev (pytest, httpx)
pip install -e ".[api]"    # FastAPI only
pip install -e ".[dashboard]"  # Streamlit only
```

## CLI

Works without the API. Point `--csv` at your file or a test fixture:

```bash
source .venv/bin/activate

# Example with local Lafave data
PYTHONPATH=src snapshot \
  --csv data/lafave/lafave_main_data.csv \
  --start-date 2025-07-04 \
  --end-date 2025-07-05 \
  --date-basis stay \
  --format text

# Example with repo test fixture (no private data needed)
PYTHONPATH=src snapshot \
  --csv tests/fixtures/bookings_sample.csv \
  --start-date 2025-01-01 \
  --end-date 2025-01-31 \
  --format json
```

Common flags: `--date-basis` (`stay` | `arrival` | `reservation`), `--breakdown-by` (`listing` | `grouping`), `--inventory-listings`, `--bands`.

## API

```bash
PYTHONPATH=src uvicorn api.main:app --host 127.0.0.1 --port 8000
```

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness check |
| `GET /properties?data_root=data` | List properties under `data/` |
| `GET /listings?csv_path=...` | Listings in a CSV |
| `GET /snapshot?...` | Full snapshot JSON |

Example:

```bash
curl "http://127.0.0.1:8000/health"
curl "http://127.0.0.1:8000/properties?data_root=data"
curl "http://127.0.0.1:8000/snapshot?csv_path=data/lafave/lafave_main_data.csv&start_date=2025-07-04&end_date=2025-07-05&property_id=lafave&property_name=Lafave&date_basis=stay&breakdown_by=listing&bands=0-7,8-14,15-30,31-60,61+&inventory_listings=30"
```

Interactive docs: **http://127.0.0.1:8000/docs**

## Streamlit dashboard

The UI does **not** compute metrics itself; it calls the API. Always start the API first.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SNAPSHOT_API_URL` | `http://127.0.0.1:8000` | Base URL for API requests |

In the sidebar you can change API URL, data root, date range, date basis, breakdown, bands, inventory count, and YoY compare.

## Docker (optional)

Requires CSVs mounted or copied into `data/` on the host:

```bash
docker compose up --build
```

- API: http://localhost:8000  
- Dashboard: http://localhost:8502  

The compose file sets `SNAPSHOT_API_URL=http://api:8000` for the dashboard container.

## Data (not in git)

Portfolio CSVs are **not committed** (booking/revenue data stays local). After clone:

```
data/
  lafave/
    lafave_main_data.csv
```

Add more properties as `data/<property_id>/*.csv`. Column mapping and defaults live in `config/properties/<folder>.json` and `config/pms/`. The API discovers folders via `GET /properties?data_root=data`.

See `data/README.md` for details. Tests use only `tests/fixtures/`.

## Tests

```bash
source .venv/bin/activate
PYTHONPATH=src pytest -q
```

No `data/` CSV required — fixtures cover unit and API tests.

## Canonical CSV columns

LaFave-style exports use the `lafave` PMS profile (`config/pms/lafave.json` and `config/properties/lafave.json`).

**Required (or mapped aliases):** arrival/check-in, departure/check-out, amount, nights, listing name.

**Optional:** reservation date, booking window, channel, grouping, status.

Internal names after ingest include `booking_date`, `check_in`, `check_out`, `amount`, `nights`, `listing_name`, etc.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Dashboard: “Cannot reach API” | Start uvicorn on port 8000; check `SNAPSHOT_API_URL` |
| No properties in sidebar | Add CSV under `data/<id>/`; confirm `data_root` is `data` |
| `snapshot: command not found` | Run `pip install -e .` and activate `.venv` |
| Port already in use | Change port, e.g. `--port 8001` and update `SNAPSHOT_API_URL` |

## License

Internal use — Oasi LLC. Contact the repo owners for access and data handling policy.
