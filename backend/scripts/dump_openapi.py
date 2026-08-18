"""Write the API's OpenAPI spec to frontend/openapi.json.

Run: python -m scripts.dump_openapi
Then regenerate the frontend types: cd ../frontend && npm run gen:types
"""

import json
from pathlib import Path

from app.main import app

OUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"

if __name__ == "__main__":
    OUT.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"wrote {OUT}")
