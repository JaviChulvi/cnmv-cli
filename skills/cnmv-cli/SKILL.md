---
name: cnmv-cli
description: Retrieve and compare CNMV annual filings safely.
version: 0.1.0
author: Javi Chulvi (JaviChulvi), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [CNMV, filings, finance, CLI]
    related_skills: []
---

# CNMV CLI Skill

Use this repository's `cnmv` command to discover official annual filings, download an explicitly selected CNMV document, and compare two XHTML filings. It returns machine-readable source data and visible-text deltas; it does not extract financial metrics or decide materiality.

## When to Use

- A task needs official CNMV annual-filing metadata, source URLs, or document provenance.
- A task needs a reproducible visible-text delta between two chosen CNMV XHTML filings.
- A task needs to verify or extend this repository's CLI behavior.
- Do not use it for ZIP/XBRL comparison, filings outside CNMV, or a claim that a text delta is a financial-materiality analysis.

## Prerequisites

- Run commands from the repository root through `terminal`.
- Use `uv sync --all-groups` before the first local command when `.venv` is absent or dependencies changed.
- The CLI accepts an issuer NIF for discovery. Document operations accept only explicit HTTPS URLs on `cnmv.es` or its subdomains.

## Quick Reference

```text
terminal(command="uv run cnmv filing list --nif A08001851", timeout=120)
terminal(command="uv run cnmv filing download --url 'https://www.cnmv.es/…' --output downloads/filing.xhtml", timeout=360)
terminal(command="uv run cnmv filing compare --older-url 'https://www.cnmv.es/…' --newer-url 'https://www.cnmv.es/…' --database .cnmv-filing-delta", timeout=900)
```

## Procedure

1. Discover filings with `terminal(command="uv run cnmv filing list --nif <NIF>", timeout=120)`.
   - Treat stdout as JSON Lines: parse one JSON object per line, not one JSON array.
   - If stdout has no filing objects, stop and report that CNMV returned no filings for the supplied NIF. Do not construct a document URL.
   - Confirm each usable record has `registration_number`, `period_end`, `publication_date`, `auditor`, and `documents`.

2. Select source documents deliberately.
   - For a consolidated annual-report delta, choose one `documents` entry whose `role` is `consolidated_xhtml` from each of two filings.
   - Interpret CNMV `period_end` values as `DD/MM/YYYY` dates; do not sort their raw strings lexicographically. Order `--older-url` and `--newer-url` chronologically, not by website listing order.
   - Use `individual_xhtml` only when the task explicitly concerns issuer-only accounts. Never pass `consolidated_zip` to `compare`; it supports only `application/xhtml+xml` or `text/html` documents.
   - If either selected period has no document with the requested role, stop and report that no comparable official XHTML pair is available. Before comparison, retain each selected filing's `registration_number`, `period_end`, and document role alongside its URL.

3. Download when a stable local copy or independently verifiable provenance is needed.
   - Run `terminal(command="uv run cnmv filing download --url '<CNMV XHTML URL>' --output downloads/<issuer>-<period>.xhtml", timeout=360)`.
   - Use a new task-specific output path. If the requested output already exists, do not replace it unless the task explicitly authorizes overwrite.
   - Parse its one JSON object and retain `final_url`, `sha256`, `byte_count`, `content_type`, and `output_path` with any downstream result.

4. Compare two selected XHTML filings.
   - Run `terminal(command="uv run cnmv filing compare --older-url '<OLDER_XHTML_URL>' --newer-url '<NEWER_XHTML_URL>' --database .cnmv-filing-delta", timeout=900)`.
   - Use a task-specific `--database` directory for isolated runs. Reuse one directory only for repeated analysis of the same source documents; collections are content-SHA-256-scoped, so a changed document is not silently reused.
   - Parse stdout as one JSON object. It contains `older`, `newer`, `change_count`, and `changes`.

5. Interpret and verify results before reporting them.
   - `change_count` counts newer visible-text chunks that are not verbatim chunks in the older filing. `changes` returns at most 100 examples, each with `new_text`, `closest_old_text`, and a cosine `distance` for nearby prior context.
   - Do not infer that a distance proves equality, financial importance, a numerical calculation, or an accounting restatement. Review the cited source text for any material conclusion.
   - Confirm both `older` and `newer` include non-empty `url`, a 64-character `sha256`, and positive `byte_count` values. Confirm `change_count >= len(changes)` and `len(changes) <= 100`.
   - Report the selected filing periods, registration numbers, document role, and the two SHA-256 values with any external comparison result.

## Error Handling

- Exit code `1` with an error containing `must use HTTPS on cnmv.es` means the source URL or a redirect is outside the allowed official origin. Obtain a URL from `filing list`; do not bypass the guard.
- `unsupported content type` means the response is not HTML/XHTML. Select a `consolidated_xhtml` or `individual_xhtml` URL instead of a ZIP/PDF endpoint.
- `HTML/XHTML document has no body` or `has no visible text` means the response cannot be compared safely. Preserve the failing URL and content type; do not present a partial delta.
- `CNMV request failed` is an upstream/network failure. Retry once after a short delay, then report the failure; do not substitute another source. For `CNMV page is missing`, `multiple ... tables`, or `filing row is missing required cells`, stop and report a CNMV page-format failure; do not retry or guess fields.

## Development Verification

After changing this CLI or this skill, run:

```text
terminal(command="uv sync --all-groups && uv run pytest -q && uv run ruff check . && uv run python -m compileall -q src tests && uv lock --check && git diff --check", timeout=600)
```

For a live end-to-end check, first call `filing list`, retain the selected metadata, then compare two real `consolidated_xhtml` URLs with a disposable database directory. Remove generated database directories before staging.

## Pitfalls

- CNMV's listing page can expose the same issuer under hyphenated and compact `A`-prefixed NIF forms; `filing list` tries the alternate representation for this pattern. Do not normalize other NIF forms yourself.
- Treat output fields as current CNMV source data, not a normalized securities master.
- The comparison is a visible-text delta, not a line diff: document layout/chunk boundaries can cause a substantially rewritten section to produce many entries.
- Chroma persistence is local state, not a shared service. Do not commit database directories or present them as durable evidence without source URLs, selected-filing metadata, and hashes.
