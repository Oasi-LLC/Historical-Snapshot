# Local data (not in git)

Place property CSV exports here:

```
data/
  lafave/
    lafave_main_data.csv
```

The API discovers folders via `GET /properties?data_root=data`.

Tests use sample files in `tests/fixtures/` only.
