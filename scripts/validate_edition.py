#!/usr/bin/env python3
"""
Validate a single edition JSON file against schema/edition.schema.json.

Usage:
    python scripts/validate_edition.py data/editions/2026-08-10.json

Exits 0 and prints "VALID" on success; exits 1 and prints the validation
error on failure. Useful as a quick check before committing a new edition
(e.g. from a Power Automate flow via a lightweight HTTP-triggered Action,
or just run by hand).
"""
import json
import sys
from pathlib import Path

from jsonschema import validate, ValidationError

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "edition.schema.json"


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_edition.py <path-to-edition.json>")
        sys.exit(2)

    target = Path(sys.argv[1])
    schema = json.loads(SCHEMA_PATH.read_text())

    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        print(f"INVALID JSON: {e}")
        sys.exit(1)

    try:
        validate(instance=data, schema=schema)
    except ValidationError as e:
        print(f"SCHEMA VIOLATION at {'/'.join(str(p) for p in e.path)}: {e.message}")
        sys.exit(1)

    print("VALID")
    sys.exit(0)


if __name__ == "__main__":
    main()
