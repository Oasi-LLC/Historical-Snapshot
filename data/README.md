# Local data (not in git)

Booking data is synced from the master Google Spreadsheet into a local cache. A small set of fixed legacy CSVs remain for hybrid properties.

## Google Sheets cache (primary)

Synced tabs are written to:

```
data/.cache/sheets/
  lafave.csv
  onera.csv
  wmb.csv
  flohom.csv
  atx.csv
```

Refresh with:

```bash
snapshot sync
# or one property:
snapshot sync --property-folder lafave
```

## Fixed legacy files (hybrid properties only)

These are **not** synced from Google Sheets. Keep them in place; they are merged into the cache on sync.

```
data/Onera/
  historical_data.csv    # old PMS data — manual, fixed

data/ATX/
  track_data.csv         # old Track PMS data — manual, fixed
```

- **Onera**: `historical_data.csv` + live `FBG_data` tab → `onera.csv` cache
- **ATX**: `track_data.csv` + live `ATX_hostaway_data` tab → `atx.csv` cache

## Setup

1. Copy `historical_data.csv` and `track_data.csv` into the folders above (one-time)
2. Set Google Sheets env vars (see root `README.md` and `.env.example`)
3. Run `snapshot sync`

Property configs live in `config/properties/`. Per-tab sheet schemas are in `config/sheets/schemas.json`.
