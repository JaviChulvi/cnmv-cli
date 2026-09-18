import json
from pathlib import Path

from typer.testing import CliRunner

from cnmv_cli import cli
from cnmv_cli.filings import MalformedPageError, UpstreamError

runner = CliRunner()


def test_help_lists_filing_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "filing" in result.stdout


def test_filing_list_emits_json_lines_by_default(monkeypatch) -> None:
    filings = [
        {
            "registration_number": "2",
            "period_end": "31/12/2025",
            "publication_date": "01/03/2026",
            "auditor": "Auditor dos",
            "documents": [],
        },
        {
            "registration_number": "1",
            "period_end": "31/12/2024",
            "publication_date": "01/03/2025",
            "auditor": "Auditor uno",
            "observations": "Sustitutiva",
            "documents": [],
        },
    ]
    seen: list[str] = []

    def fake_fetch(nif: str):
        seen.append(nif)
        return filings

    monkeypatch.setattr(cli, "fetch_filings", fake_fetch)
    result = runner.invoke(cli.app, ["filing", "list", "--nif", "A12345678"])

    assert result.exit_code == 0
    assert seen == ["A12345678"]
    assert [json.loads(line) for line in result.stdout.splitlines()] == filings


def test_filing_list_returns_nonzero_for_clear_service_errors(monkeypatch) -> None:
    for error in (
        UpstreamError("CNMV request failed: 503"),
        MalformedPageError("missing table"),
    ):
        monkeypatch.setattr(
            cli, "fetch_filings", lambda nif, error=error: (_ for _ in ()).throw(error)
        )

        result = runner.invoke(cli.app, ["filing", "list", "--nif", "A12345678"])

        assert result.exit_code == 1
        assert str(error) in result.stderr


def test_filing_download_prints_json_provenance(monkeypatch, tmp_path) -> None:
    output = tmp_path / "report.zip"
    expected = {
        "final_url": "https://www.cnmv.es/final.zip",
        "sha256": "abc",
        "byte_count": 3,
        "content_type": "application/zip",
        "output_path": str(output),
    }
    seen: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path):
        seen.append((url, destination))
        return expected

    monkeypatch.setattr(cli, "download_document", fake_download)
    result = runner.invoke(
        cli.app,
        [
            "filing",
            "download",
            "--url",
            "https://www.cnmv.es/report.zip",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert seen == [("https://www.cnmv.es/report.zip", output)]
    assert json.loads(result.stdout) == expected


def test_filing_download_reports_invalid_url_without_network(tmp_path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "filing",
            "download",
            "--url",
            "http://example.com/report.zip",
            "--output",
            str(tmp_path / "report.zip"),
        ],
    )

    assert result.exit_code == 1
    assert "must use HTTPS on cnmv.es" in result.stderr


def test_filing_compare_prints_json(monkeypatch, tmp_path) -> None:
    expected = {
        "older": {"url": "https://www.cnmv.es/old.xhtml", "sha256": "a", "byte_count": 1},
        "newer": {"url": "https://www.cnmv.es/new.xhtml", "sha256": "b", "byte_count": 2},
        "changes": [],
    }
    seen = []

    def fake_compare(older_url: str, newer_url: str, database: Path):
        seen.append((older_url, newer_url, database))
        return expected

    monkeypatch.setattr(cli, "compare_filings", fake_compare)
    result = runner.invoke(
        cli.app,
        [
            "filing",
            "compare",
            "--older-url",
            "https://www.cnmv.es/old.xhtml",
            "--newer-url",
            "https://www.cnmv.es/new.xhtml",
            "--database",
            str(tmp_path / "db"),
        ],
    )

    assert result.exit_code == 0
    assert seen == [
        (
            "https://www.cnmv.es/old.xhtml",
            "https://www.cnmv.es/new.xhtml",
            tmp_path / "db",
        )
    ]
    assert json.loads(result.stdout) == expected
