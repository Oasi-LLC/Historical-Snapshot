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
- Google Sheets access (service account + shared spreadsheet — see [Google Sheets data source](#google-sheets-data-source))
- Optional: **Docker** for containerized runs

## Quick start (after clone)

```bash
git clone https://github.com/Oasi-LLC/Historical-Snapshot.git
cd Historical-Snapshot

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install -e .

# Google Sheets (see .env.example)
export GOOGLE_SHEETS_SPREADSHEET_ID="your-spreadsheet-id"
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/historical-snapshot/sheets-sa.json"

# One-time: place fixed legacy CSVs for hybrid properties (see data/README.md)
#   data/Onera/historical_data.csv
#   data/ATX/track_data.csv

snapshot sync
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
  config/                    # Property + PMS profiles
  data/                      # Sheets cache + fixed legacy CSVs (gitignored)
  docker-compose.yml
```

## Install options

**Recommended** (matches `requirements.txt` — API and dashboard):

```bash
pip install -r requirements.txt
pip install -e .
```

**Optional extras** via `pyproject.toml`:

```bash
pip install -e ".[all]"    # api + dashboard
pip install -e ".[api]"    # FastAPI only
pip install -e ".[dashboard]"  # Streamlit only
```

## CLI

Works without the API. Use `--property-folder` after `snapshot sync` (reads from the Sheets cache):

```bash
source .venv/bin/activate

PYTHONPATH=src snapshot \
  --property-folder lafave \
  --start-date 2025-07-04 \
  --end-date 2025-07-05 \
  --date-basis stay \
  --format text
```

Common flags: `--date-basis` (`stay` | `arrival` | `reservation`), `--breakdown-by` (`listing` | `grouping`), `--inventory-listings`, `--bands`.

## Google Sheets data source

Properties can pull booking data from a master Google Spreadsheet (one tab per property) instead of manual CSV paste.

### One-time setup

1. Create a Google Cloud project and enable the **Google Sheets API**
2. Create a **service account**, download the JSON key, and store it outside the repo
3. Share the spreadsheet with the service account email
4. Set environment variables (see `.env.example`):

```bash
export GOOGLE_SHEETS_SPREADSHEET_ID="your-spreadsheet-id"
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/historical-snapshot/sheets-sa.json"
```

### Sync and run

```bash
# Pull all configured property tabs into data/.cache/sheets/
snapshot sync

# Or sync one property
snapshot sync --property-folder lafave

# Run snapshot from synced cache (no --csv needed)
snapshot --property-folder lafave --start-date 2025-07-04 --end-date 2025-07-05
```

In the dashboard, use **Refresh from Google Sheets** in the sidebar after selecting a property.

Per-tab column schemas are documented in `config/sheets/schemas.json`. Property configs declare the sheet tab via `data_source` in `config/properties/<folder>.json`.

## API

```bash
PYTHONPATH=src uvicorn api.main:app --host 127.0.0.1 --port 8000
```

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness check |
| `GET /properties?data_root=data` | List configured properties |
| `POST /sync?data_root=data` | Sync Google Sheets tabs to local cache |
| `GET /listings?property_folder=...` | Listings for a property |
| `GET /snapshot?property_folder=...` | Full snapshot JSON |

Example:

```bash
curl "http://127.0.0.1:8000/health"
curl "http://127.0.0.1:8000/properties?data_root=data"
curl -X POST "http://127.0.0.1:8000/sync?data_root=data"
curl "http://127.0.0.1:8000/snapshot?property_folder=lafave&start_date=2025-07-04&end_date=2025-07-05&property_id=LAFAVE&property_name=LaFave&date_basis=stay&breakdown_by=listing&bands=0-7,8-14,15-30,31-60,61+&inventory_listings=30"
```

Interactive docs: **http://127.0.0.1:8000/docs**

## Streamlit dashboard

The UI does **not** compute metrics itself; it calls the API. Always start the API first.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SNAPSHOT_API_URL` | `http://127.0.0.1:8000` | Base URL for API requests |

In the sidebar you can change API URL, data root, date range, date basis, breakdown, bands, inventory count, and YoY compare.

## Docker (optional)

Requires Google Sheets credentials and (for hybrid properties) legacy CSVs under `data/` — see `data/README.md`:

```bash
docker compose up --build
```

- API: http://localhost:8000  
- Dashboard: http://localhost:8502  

The compose file sets `SNAPSHOT_API_URL=http://api:8000` for the dashboard container.

## Data (not in git)

Portfolio data is **not committed**. After clone:

1. Configure Google Sheets env vars (`.env.example`)
2. Place fixed legacy files for hybrid properties if needed (`data/README.md`)
3. Run `snapshot sync` to populate `data/.cache/sheets/`

```
data/
  .cache/sheets/          # synced from Google Sheets
  Onera/historical_data.csv   # fixed legacy (hybrid)
  ATX/track_data.csv          # fixed legacy (hybrid)
```

Property configs in `config/properties/` drive tab names and ingest rules. The API discovers properties via `GET /properties?data_root=data`.

See `data/README.md` for details.

## Canonical CSV columns

LaFave-style exports use the `lafave` PMS profile (`config/pms/lafave.json` and `config/properties/lafave.json`).

**Required (or mapped aliases):** arrival/check-in, departure/check-out, amount, nights, listing name.

**Optional:** reservation date, booking window, channel, grouping, status.

Internal names after ingest include `booking_date`, `check_in`, `check_out`, `amount`, `nights`, `listing_name`, etc.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Dashboard: “Cannot reach API” | Start uvicorn on port 8000; check `SNAPSHOT_API_URL` |
| No properties in sidebar | Run `snapshot sync`; check Google Sheets env vars |
| Google Sheets sync fails | Check `GOOGLE_SHEETS_SPREADSHEET_ID` and `GOOGLE_APPLICATION_CREDENTIALS`; share sheet with service account |
| `snapshot: command not found` | Run `pip install -e .` and activate `.venv` |
| Port already in use | Change port, e.g. `--port 8001` and update `SNAPSHOT_API_URL` |

## License

Internal use — Oasi LLC. Contact the repo owners for access and data handling policy.
