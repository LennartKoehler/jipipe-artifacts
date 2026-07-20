# Feature Branch Checks for Package Configs

**Date:** 2026-07-20
**Status:** Approved (design)
**Goal:** Prevent broken package configs from merging into `master`.

## Background

The repository currently has one workflow, `.github/workflows/publish-index.yml`, which runs only on push to `master` and performs the full build + GitHub Pages deploy + (commented-out) ORAS push. There is no pre-merge gate: a broken package JSON, a malformed source block, or a dead download URL can land on `master` and only surface when the publish workflow runs.

Existing validation assets:

- `scripts/validate_package_downloads.py` — live HTTP/ORAS reachability checks; exit `0` clean, `1` on any failure.
- `scripts/build_index_validating.py` — structural validation + index assembly; exit `0` clean, `2` on validation failure when `--strict` is set.
- `schemas/package.schema.json` (draft-07) — formal JSON Schema for individual package files. Stricter than the inline validator: enforces version and tag patterns and `additionalProperties: false`. **Not currently used by any script.**
- `schemas/index.schema.json` (draft 2020-12) — schema for the generated index. **Has a pre-existing conflict with actual output** (see Follow-ups).

## Scope

**In scope:**
- A new GitHub Actions workflow that runs on PRs targeting `master`.
- Layered, fail-fast checks: JSON Schema validation → index build (`--strict`) → live downloads.
- One new validation script.
- A one-line fix to `schemas/package.schema.json`'s `oci-ref` pattern so it matches reality (see "Schema fix" below).
- Documentation of the required branch-protection settings (manual repo-admin step).

**Out of scope:**
- Extending or refactoring the existing `publish-index.yml`.
- Validating `dist/index.json` against `index.schema.json` (pre-existing schema conflict; see Follow-ups).
- Building the Hugo site in the check workflow (the publish workflow still catches template regressions post-merge).
- Caching ORAS manifests or pip deps between runs.
- Making any of the checks non-blocking / advisory.

## Design

### Workflow structure

New file: `.github/workflows/check-packages.yml`. Separate from `publish-index.yml`, which is left untouched.

**Trigger:**
```yaml
on:
  pull_request:
    branches: [ master ]
  workflow_dispatch: {}
```
No path filter — any PR that touches `packages/`, `schemas/`, or `scripts/` should be checked, and PRs touching only docs/CI are cheap to validate.

**Single job `checks`, ordered fail-fast steps:**

1. `actions/checkout@v4`
2. Install Python deps: `pip install oras requests jsonschema`
3. **JSON Schema validation** — runs `scripts/validate_schemas.py`
4. **Build index** — runs `scripts/build_index_validating.py --strict`
5. **Live download validation** — runs `scripts/validate_package_downloads.py`

Ordering is fast/deterministic → slow/network. No `continue-on-error`: a failure in an earlier step fails the job immediately, so contributors fix schema errors before chasing download errors. The job's overall status (`checks`) is the single required status check for branch protection.

**Permissions:** read-only.
```yaml
permissions:
  contents: read
```
No `pages: write`, no `packages: write` — this workflow never publishes. The default `GITHUB_TOKEN` is available for unauthenticated public ghcr.io reads, matching local dev behavior.

**Runner:** `ubuntu-latest`. System Python on the runner satisfies `requires-python = ">=3.12"`.

### New script: `scripts/validate_schemas.py`

Validates every package JSON file against `schemas/package.schema.json`.

### Schema fix: `schemas/package.schema.json`

The current `oci-ref` pattern on line 58 is:
```
"^([a-zA-Z0-9-]+\\.)*[a-zA-Z0-9-]+:[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$"
```
This expects a `host:port/path:tag` form with a required `:tag` segment. Actual package files store `oci-ref` **without a tag** (e.g., `ghcr.io/applied-systems-biology/jipipe/artifacts/ai/ggml/llamacpp`) because `validate_package_downloads.py:41` appends the tag at runtime (`oci-ref + ":" + tag`). The existing inline validator (`build_index_validating.py:65`) only requires the `ghcr.io/` prefix. As a result, 25 of 26 current package files fail schema validation today.

**Fix:** change the pattern to:
```
"^ghcr\\.io/[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$"
```
This matches the `ghcr.io/` prefix requirement enforced elsewhere, allows the path-segment structure used by all existing packages, and omits any required `:tag` (since tags are appended at runtime). Verified: with this pattern, all 26 current package files pass `Draft7Validator.iter_errors` cleanly.



**Behavior:**
- Discovers all `*.json` under `packages/` via `rglob` (consistent with the other two scripts).
- Loads `schemas/package.schema.json` once.
- Uses the `jsonschema` library with a `Draft7Validator` (the schema declares draft-07).
- Collects errors across **all** files before exiting — one file's failure does not hide others. Each error is reported as `<relpath>: <json-pointer-path>: <message>`.
- Exit `0` if clean, `1` if any errors.

**CLI:**
```
python3 scripts/validate_schemas.py \
  --packages-dir packages \
  --schema schemas/package.schema.json
```
Defaults match the repo layout, so `python3 scripts/validate_schemas.py` works with no args.

**Why a dedicated script (vs. inline Python in the workflow):** matches the existing `scripts/` pattern, runs identically locally so developers can reproduce CI failures, and is reusable if we later add more schema-based checks.

### Index build step (structural)

Exact reuse of the publish workflow's invocation:
```
python3 scripts/build_index_validating.py \
  --packages-dir packages \
  --out dist/index.json \
  --owner applied-systems-biology \
  --repo jipipe \
  --prefix artifacts \
  --strict
```
Exit `0` clean, `2` on validation failure → fails the job. The generated `dist/index.json` is discarded (this workflow never publishes); the step is purely a gate.

### Live downloads step (network)

Exact reuse:
```
python3 scripts/validate_package_downloads.py --packages-dir packages
```
Exit `0` clean, `1` on any failed download → fails the job. All ORAS packages are public, so no auth is passed to `oras`; the script's unauthenticated `oras.client.OrasClient()` already works for public ghcr.io reads.

### Branch protection (post-merge manual step)

The workflow produces one status check named `checks`. After the workflow is merged, a repo admin must:
1. Settings → Branches → Branch protection rules → add/edit rule for `master`.
2. Enable "Require status checks to pass before merging".
3. Select `checks` as a required status check.
4. (Recommended) enable "Require branches to be up to date before merging".

This cannot be enforced from within the workflow itself; it will be documented in the PR description.

## Follow-ups (not in scope)

1. **`index.schema.json` conflict.** The schema requires tags to match `^version-[0-9]+(\.[0-9]+)*$` and gives the example `["version-0.5.5.1000"]`, but `build_index_validating.py` emits tags verbatim from package files (e.g., `0.10043.0.1000-linux_amd64`), and `package.schema.json`'s tag pattern (`^[0-9]+(\.[0-9]+)*-.*$`) matches that reality. Validating the generated index against `index.schema.json` would fail on every package today. Resolving this (decide which representation is canonical, fix schema and/or builder) is a separate change.
2. **Field-order enforcement.** `AGENTS.md` mandates strict field ordering (`name, version, query, sources, maintainer, description, homepage, license, tags, includes`), but no script or schema currently checks it. Could be added to `validate_schemas.py` later.

## Testing

No traditional test framework in this repo; the validation scripts serve as tests. Verification of this change:

1. `python3 scripts/validate_schemas.py` exits `0` on the current `packages/` tree (after the schema fix).
2. `python3 scripts/build_index_validating.py --packages-dir packages --out /tmp/index.json --owner applied-systems-biology --repo jipipe --prefix artifacts --strict` exits `0`.
3. Temporarily corrupt a package file (e.g., remove a required field, add an unknown field, break a tag pattern) and confirm `validate_schemas.py` reports the error and exits `1`.
4. Restore the file and confirm the workflow YAML is syntactically valid (e.g., `actionlint` if available, or push to a PR and observe the run).

## Files added/changed

- **New:** `.github/workflows/check-packages.yml`
- **New:** `scripts/validate_schemas.py`
- **Modified:** `schemas/package.schema.json` (one-line `oci-ref` pattern fix; see "Schema fix")
- **New:** `docs/superpowers/specs/2026-07-20-feature-branch-checks-design.md` (this file)
- **Unchanged:** `.github/workflows/publish-index.yml`, existing scripts, packages.
