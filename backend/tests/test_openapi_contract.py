"""The checked-in OpenAPI schema must match the app the frontend types come from.

`frontend/openapi.json` is a snapshot, not a live read. Editing a response model
updates `app.openapi()` immediately but leaves the snapshot -- and the TypeScript
generated from it -- stale until someone regenerates them. Nothing else notices,
because stale types fail at runtime rather than at build.
"""

import json
from pathlib import Path

from app.main import app

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"

REGENERATE = (
    "frontend/openapi.json is out of date with app/schemas.py. Regenerate it and "
    "the TypeScript types:\n    cd frontend && npm run gen:types"
)


def test_checked_in_schema_matches_the_app():
    live = app.openapi()["components"]["schemas"]
    saved = json.loads(SCHEMA_PATH.read_text())["components"]["schemas"]
.
    drifted = sorted(
        name for name in set(live) | set(saved) if live.get(name) != saved.get(name)
    )
    assert not drifted, f"{REGENERATE}\n\nOut of date: {', '.join(drifted)}"
