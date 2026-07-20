# Feature Branch Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions workflow that runs layered config checks on PRs targeting `master`, so broken package configs cannot merge.

**Architecture:** A new `check-packages.yml` workflow runs a single `checks` job with three fail-fast steps: JSON Schema validation (new `validate_schemas.py` script), index build with `--strict`, and live download validation. A one-line fix to `schemas/package.schema.json`'s `oci-ref` pattern is required first so the schema matches how `oci-ref` is actually used (tag appended at runtime).

**Tech Stack:** Python 3.12+, `jsonschema` (Draft7Validator), `oras`, `requests`, GitHub Actions.

## Global Constraints

- Repo layout: package JSON files live under `packages/artifacts/` (Maven-style directories); schemas under `schemas/`; scripts under `scripts/`.
- `AGENTS.md` field ordering for package JSON: `name, version, query, sources, maintainer, description, homepage, license, tags, includes`.
- ORAS `oci-ref` values must start with `ghcr.io/` and store **no tag** (tag is appended at runtime by `validate_package_downloads.py:41`).
- Exit codes: `0` = success, `1` = error (downloads/schema), `2` = validation failure (`build_index_validating.py --strict`).
- Commit message style follows the repo's informal convention: `+` prefix for additions, `*` prefix for fixes.
- No comments in package JSON files.
- Python venv at `.venv/` has `jsonschema` installed; `oras`/`requests` need `pip install` in the venv for local runs of the download validator (CI installs them fresh).

---

### Task 1: Fix the `oci-ref` pattern in `schemas/package.schema.json`

The current `oci-ref` pattern expects a `host:port/path:tag` form with a required tag. Real package files store no tag (appended at runtime), so 25 of 26 files fail schema validation. This fix unblocks Task 2.

**Files:**
- Modify: `schemas/package.schema.json:58`

**Interfaces:**
- Consumes: nothing
- Produces: a schema that all 26 current package files validate against cleanly

- [ ] **Step 1: Read the current pattern to confirm the exact line**

Run: `grep -n "oci-ref" schemas/package.schema.json`
Expected: line 58 contains `"pattern": "^([a-zA-Z0-9-]+\\.)*[a-zA-Z0-9-]+:[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$"`

- [ ] **Step 2: Apply the pattern fix**

Replace the line:
```json
                "pattern": "^([a-zA-Z0-9-]+\\.)*[a-zA-Z0-9-]+:[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$"
```
with:
```json
                "pattern": "^ghcr\\.io/[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$"
```
(Use the `edit` tool with `oldString`/`newString` to make this exact replacement in `schemas/package.schema.json`.)

- [ ] **Step 3: Verify all 26 package files pass the fixed schema**

Run from the repo root (with `.venv` activated):
```bash
source .venv/bin/activate && python3 - <<'EOF'
import json, glob
from jsonschema import Draft7Validator
schema = json.load(open("schemas/package.schema.json"))
v = Draft7Validator(schema)
files = sorted(glob.glob("packages/**/*.json", recursive=True))
errs_total = 0
for f in files:
    doc = json.load(open(f))
    errs = list(v.iter_errors(doc))
    errs_total += len(errs)
    for e in errs:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"{f}: {path}: {e.message}")
print(f"\n{len(files)} files, {errs_total} schema errors total")
EOF
```
Expected output: `26 files, 0 schema errors total` (no per-file error lines). If any errors appear, do not proceed — investigate before continuing.

- [ ] **Step 4: Commit the schema fix**

```bash
git add schemas/package.schema.json
git commit -m "* fix oci-ref pattern in package.schema.json to match runtime usage"
```

---

### Task 2: Create `scripts/validate_schemas.py`

A standalone validation script that checks every package JSON against `schemas/package.schema.json`. Matches the existing `scripts/` pattern (argparse, `rglob("*.json")`, exit-code semantics), so it runs identically in CI and locally.

**Files:**
- Create: `scripts/validate_schemas.py`

**Interfaces:**
- Consumes: `schemas/package.schema.json` (fixed in Task 1), `packages/**/*.json`
- Produces: exit `0` if all files valid, exit `1` if any file has schema errors; prints `<relpath>: <path>: <message>` per error

- [ ] **Step 1: Write the script**

Create `scripts/validate_schemas.py` with this exact content:
```python
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
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/validate_schemas.py`
Expected: no output (success).

- [ ] **Step 3: Run against the current tree — expect exit 0**

Run: `source .venv/bin/activate && python3 scripts/validate_schemas.py; echo "rc=$?"`
Expected output: ends with `Summary: 26 valid, 0 invalid, 26 total` and `rc=0`.

- [ ] **Step 4: Negative test — corrupt a package file and confirm the script catches it**

Make a temporary copy of one package file, introduce a schema violation (unknown field), run the script, confirm it reports the error and exits `1`, then restore.

```bash
cp packages/artifacts/ai/ggml/llamacpp/llamacpp-0.10043.0.1000.json /tmp/llamacpp-backup.json
```
Then use the `edit` tool on `packages/artifacts/ai/ggml/llamacpp/llamacpp-0.10043.0.1000.json` to add an unknown top-level field. Replace:
```json
{
    "name": "llamacpp",
```
with:
```json
{
    "bogus_field": "should_not_be_here",
    "name": "llamacpp",
```

Run: `source .venv/bin/activate && python3 scripts/validate_schemas.py; echo "rc=$?"`
Expected: output includes a line like `.../llamacpp-0.10043.0.1000.json: <root>: Additional properties are not allowed ('bogus_field' was unexpected)` and `rc=1`.

- [ ] **Step 5: Restore the file and re-confirm exit 0**

```bash
cp /tmp/llamacpp-backup.json packages/artifacts/ai/ggml/llamacpp/llamacpp-0.10043.0.1000.json
```
Run: `source .venv/bin/activate && python3 scripts/validate_schemas.py; echo "rc=$?"`
Expected: `rc=0` and `Summary: 26 valid, 0 invalid, 26 total`.

Verify the restore is clean: `git diff --exit-code packages/artifacts/ai/ggml/llamacpp/llamacpp-0.10043.0.1000.json`
Expected: no output, exit `0` (file matches HEAD).

- [ ] **Step 6: Commit the new script**

```bash
git add scripts/validate_schemas.py
git commit -m "+ add validate_schemas.py for JSON Schema validation of package files"
```

---

### Task 3: Create `.github/workflows/check-packages.yml`

The new workflow that gates PRs. Separate from `publish-index.yml` (which stays unchanged). Single `checks` job, three fail-fast steps.

**Files:**
- Create: `.github/workflows/check-packages.yml`

**Interfaces:**
- Consumes: `scripts/validate_schemas.py` (Task 2), `scripts/build_index_validating.py`, `scripts/validate_package_downloads.py`
- Produces: a `checks` status check on PRs to `master`

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/check-packages.yml` with this exact content:
```yaml
name: Check Package Configs

on:
  pull_request:
    branches: [ master ]
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Python dependencies
        run: |
          python3 -m pip install --upgrade pip
          python3 -m pip install oras requests jsonschema

      - name: Validate package JSON against schema
        run: |
          python3 scripts/validate_schemas.py \
            --packages-dir packages \
            --schema schemas/package.schema.json

      - name: Build validated index (strict)
        run: |
          python3 scripts/build_index_validating.py \
            --packages-dir packages \
            --out dist/index.json \
            --owner applied-systems-biology \
            --repo jipipe \
            --prefix artifacts \
            --strict

      - name: Validate package downloads
        run: |
          python3 scripts/validate_package_downloads.py --packages-dir packages
```

- [ ] **Step 2: Verify the YAML is syntactically valid**

If `actionlint` is available:
```bash
actionlint .github/workflows/check-packages.yml
```
Expected: no output, exit `0`.

If `actionlint` is not installed, verify YAML parse with Python:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/check-packages.yml')); print('YAML OK')"
```
Expected: `YAML OK`.

- [ ] **Step 3: Confirm the three script invocations match the existing publish workflow**

Run: `grep -n "python3 scripts/" .github/workflows/publish-index.yml`
Expected: see `validate_package_downloads.py` and `build_index_validating.py` lines in `publish-index.yml`. Confirm the args in `check-packages.yml` for those two scripts match `publish-index.yml` exactly (same `--packages-dir`, `--out`, `--owner`, `--repo`, `--prefix`, `--strict` values). This ensures the PR check and the publish gate validate against the same criteria.

- [ ] **Step 4: Commit the workflow**

```bash
git add .github/workflows/check-packages.yml
git commit -m "+ add check-packages workflow for PR config validation"
```

---

## Post-implementation: manual branch protection (out of code, documented for the PR description)

After the workflow is merged to `master`, a repo admin must:
1. Settings → Branches → Branch protection rules → add/edit rule for `master`.
2. Enable "Require status checks to pass before merging".
3. Select `checks` as a required status check.
4. (Recommended) enable "Require branches to be up to date before merging".

This cannot be enforced from within the workflow itself.

## Verification summary (run all three before opening the PR)

1. `source .venv/bin/activate && python3 scripts/validate_schemas.py` → exit `0`.
2. `source .venv/bin/activate && python3 scripts/build_index_validating.py --packages-dir packages --out /tmp/index.json --owner applied-systems-biology --repo jipipe --prefix artifacts --strict` → exit `0`.
3. `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/check-packages.yml')); print('YAML OK')"` → `YAML OK`.

(The live-download validator is intentionally not run as a local verification step here because it makes network calls to ghcr.io and may be slow; it will run in CI on the PR.)
