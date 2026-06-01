# Local data (not in git)

Place property CSV exports here:

```
data/
  lafave/
    lafave_main_data.csv
  flohom/
    flohom_main_data.csv
```

The API discovers folders via `GET /properties?data_root=data`.

PMS column mapping and property defaults live in `config/pms/` and `config/properties/` (committed). Flohom uses the Hostaway profile with `rentalRevenue`, payment-status filtering, and per-period active listing counts for occupancy.

Tests use sample files in `tests/fixtures/` only.
