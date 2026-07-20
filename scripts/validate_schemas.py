#!/usr/bin/env python3

from pathlib import Path
import argparse
import json
import sys
from jsonschema import Draft7Validator


def validate_all(packages_dir: Path, schema_path: Path) -> int:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)

    files = sorted(packages_dir.rglob("*.json"))
    if not files:
        print(f"[WARN] No package JSON files found under {packages_dir}")

    all_errors = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            all_errors.append(f"{f}: invalid JSON: {e}")
            continue
        for err in validator.iter_errors(doc):
            path = "/".join(str(p) for p in err.absolute_path) or "<root>"
            all_errors.append(f"{f}: {path}: {err.message}")

    for msg in all_errors:
        print(" -", msg)

    total = len(files)
    failed = len(all_errors)
    print(f"\nSummary: {total - failed} valid, {failed} invalid, {total} total")

    return 1 if all_errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Validate package JSON files against the package schema.")
    ap.add_argument("--packages-dir", default="packages", help="Root directory containing per-package JSON files.")
    ap.add_argument("--schema", default="schemas/package.schema.json", help="Path to the package JSON schema.")
    args = ap.parse_args()
    sys.exit(validate_all(Path(args.packages_dir), Path(args.schema)))
