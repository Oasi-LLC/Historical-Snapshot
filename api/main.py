from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from historical_snapshot.service import DEFAULT_DATA_ROOT, discover_properties, list_listings, run_snapshot

app = FastAPI(
    title="Historical Snapshot API",
    description="Property performance snapshots for revenue management.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/properties")
def get_properties(data_root: str = Query(default=str(DEFAULT_DATA_ROOT))) -> dict:
    properties = discover_properties(data_root)
    return {"data_root": data_root, "properties": properties}


@app.get("/listings")
def get_listings(
    csv_path: str = Query(..., description="Path to property CSV file"),
) -> dict:
    path = Path(csv_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"CSV not found: {csv_path}")
    return {"csv_path": csv_path, "listings": list_listings(path)}


@app.get("/snapshot")
def get_snapshot(
    csv_path: str = Query(..., description="Path to property CSV file"),
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    property_id: str = Query(default="LAFAVE"),
    property_name: str = Query(default="LaFave"),
    date_basis: str = Query(default="stay", pattern="^(stay|arrival|reservation)$"),
    breakdown_by: str = Query(default="listing", pattern="^(listing|grouping)$"),
    bands: str = Query(default="0-7,8-15,16-30,31-60,61+"),
    inventory_listings: int = Query(default=30, ge=1),
    inventory_mode: Optional[str] = Query(
        default=None,
        pattern="^(manual|active_listings)$",
        description="Override property config inventory mode",
    ),
) -> dict:
    path = Path(csv_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"CSV not found: {csv_path}")
    try:
        result = run_snapshot(
            csv_path=path,
            start_date=start_date,
            end_date=end_date,
            property_id=property_id,
            property_name=property_name,
            date_basis=date_basis,  # type: ignore[arg-type]
            breakdown_by=breakdown_by,
            bands=bands,
            inventory_listings=inventory_listings,
            inventory_mode=inventory_mode,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict()
