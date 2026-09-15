# cnmv-cli

A small Python CLI for listing CNMV annual filings and downloading an explicitly
selected CNMV document. It reads the official CNMV website directly and does not
create or use a local cache or database.

## Setup

Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/) are required.

```console
uv sync
uv run cnmv --help
```

## List annual filings

```console
uv run cnmv filing list --nif A08001851
```

The default and only output format is JSON Lines: one filing object per line.
Each object includes its registration number, period end, publication date,
auditor, available audit details, and available source document URLs classified
as `individual_xhtml`, `consolidated_xhtml`, or `consolidated_zip`.

## Download a document

Pass an explicit HTTPS URL found in the list output:

```console
uv run cnmv filing download \
  --url 'https://www.cnmv.es/webservices/verdocumento/ver?e=…' \
  --output downloads/filing.xhtml
```

The command creates missing parent directories, streams up to 256 MiB into a
temporary file, and atomically replaces the output after a successful download.
It prints one JSON provenance object containing the final URL, SHA-256 digest,
byte count, content type, and output path. Only HTTPS URLs on `cnmv.es` or its
subdomains are accepted, including at every redirect.

## Development

```console
uv sync
uv run pytest
uv run python -m compileall -q src tests
```

Tests use local fixtures and mocked HTTP responses; they do not access the
network.

This MVP intentionally does not implement XBRL extraction, financial metrics,
caches, SQLite, or later roadmap phases.
