from __future__ import annotations

import json
from pathlib import Path

import typer

from .download import InvalidDocumentUrl, download_document
from .filings import MalformedPageError, UpstreamError, fetch_filings

app = typer.Typer(
    help="Query and download official CNMV filings.", no_args_is_help=True
)
filing_app = typer.Typer(help="Work with annual filings.", no_args_is_help=True)
app.add_typer(filing_app, name="filing")


@filing_app.command("list")
def filing_list(
    nif: str = typer.Option(..., "--nif", help="Issuer tax identification number."),
) -> None:
    """List annual filings as JSON Lines."""
    try:
        filings = fetch_filings(nif)
    except (UpstreamError, MalformedPageError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    for filing in filings:
        typer.echo(json.dumps(filing, ensure_ascii=False, separators=(",", ":")))


@filing_app.command("download")
def filing_download(
    url: str = typer.Option(..., "--url", help="Explicit HTTPS CNMV document URL."),
    output: Path = typer.Option(..., "--output", help="Destination file path."),
) -> None:
    """Download one CNMV document and print JSON provenance."""
    try:
        provenance = download_document(url, output)
    except (InvalidDocumentUrl, UpstreamError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(json.dumps(provenance, ensure_ascii=False, separators=(",", ":")))
