#!/usr/bin/env python3
"""Write the OpenAPI document of AIfred's API to a file, for the frontend's generated types.

The Vue app's TypeScript types come from the server's Pydantic models (frontend: npm run api-types);
no server needs to run for it.

Usage (from the tree root):
    AIFRED_CLI_MODE=1 PYTHONPATH=. venv/bin/python scripts/export_openapi.py frontend/openapi.json
"""

import json
import sys
from pathlib import Path

from aifred.lib.api import api_app


def main() -> None:
    target = Path(sys.argv[1])
    target.write_text(json.dumps(api_app.openapi(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
