# Historical Snapshot

CLI and web dashboard for historical property performance (revenue management snapshots).

## Features

- **Stay-overlap filtering** (default): bookings with occupied nights in your date range; revenue prorated to those nights
- Portfolio KPIs: ADR, Occupancy %, RevPAR, LOS, pickup by booking-window band
- Breakdown by **listing** or **grouping**
- **CLI**, **REST API**, and **Streamlit dashboard**

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## CLI

```bash
PYTHONPATH=src snapshot \
  --csv data/lafave/lafave_main_data.csv \
  --start-date 2025-07-04 \
  --end-date 2025-07-05 \
  --date-basis stay \
  --format text
```

## API (local / LAN)

Start the API (listens on all interfaces for team access):

```bash
PYTHONPATH=src uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `GET /health`
- `GET /properties?data_root=data`
- `GET /listings?csv_path=...`
- `GET /snapshot?csv_path=...&start_date=...&end_date=...&date_basis=stay&breakdown_by=listing`

Example:

```bash
curl "http://127.0.0.1:8000/snapshot?csv_path=data/lafave/lafave_main_data.csv&start_date=2025-07-04&end_date=2025-07-05"
```

## Streamlit dashboard

Terminal 1 — API:

```bash
PYTHONPATH=src uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 — dashboard:

```bash
streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
```

Open `http://localhost:8501` (or `http://<your-machine-ip>:8501` from another device on the same network).

Set `SNAPSHOT_API_URL` if the API is not on `http://127.0.0.1:8000`.

## Docker (optional)

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- Dashboard: `http://localhost:8501`

## Data layout

Portfolio CSVs are **not committed** (see `data/README.md`). After clone, add exports locally:

```
data/
  lafave/
    lafave_main_data.csv
```

Add more properties as `data/<property_id>/*.csv`. The API discovers them via `GET /properties`.

## Tests

```bash
PYTHONPATH=src pytest -q
```

## Canonical CSV columns

Required: `booking_date`, `check_in`/`arrival`, `check_out`/`departure`, `amount`, `# nights`, `listing name` (LaFave exports).

Optional: `grouping`, `channel`, `status`.
