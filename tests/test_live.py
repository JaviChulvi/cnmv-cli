"""Opt-in checks of the installed CLI against the official CNMV website."""

import hashlib
import json
import math
import os
import subprocess
import time
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CNMV_LIVE_TESTS") != "1",
    reason="set CNMV_LIVE_TESTS=1 to contact the real CNMV website",
)


def run_cli(*arguments: str, timeout: int = 120) -> str:
    print(f"Running cnmv {' '.join(arguments)}", flush=True)
    for attempt in range(2):
        result = subprocess.run(
            ["cnmv", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if (
            attempt == 0
            and result.returncode != 0
            and "CNMV request failed" in result.stderr
        ):
            print(f"Retrying one upstream failure: {result.stderr}", flush=True)
            time.sleep(3)
            continue
        break
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def test_live_list_download_and_compare(tmp_path) -> None:
    filings = [
        json.loads(line)
        for line in run_cli("filing", "list", "--nif", "A08001851").splitlines()
    ]
    assert filings, "CNMV returned no filings for the smoke-test issuer"
    for filing in filings:
        assert {
            "registration_number",
            "period_end",
            "publication_date",
            "auditor",
            "documents",
        } <= filing.keys()

    selected = []
    periods = set()
    for filing in sorted(
        filings,
        key=lambda item: (
            datetime.strptime(item["period_end"], "%d/%m/%Y").replace(tzinfo=UTC),
            datetime.strptime(item["publication_date"], "%d/%m/%Y").replace(tzinfo=UTC),
        ),
        reverse=True,
    ):
        if filing["period_end"] in periods:
            continue
        for document in filing["documents"]:
            if document["role"] == "consolidated_xhtml":
                selected.append(
                    {
                        "registration_number": filing["registration_number"],
                        "period_end": filing["period_end"],
                        **document,
                    }
                )
                periods.add(filing["period_end"])
                break
        if len(selected) == 2:
            break
    assert len(selected) == 2, (
        "CNMV did not expose two distinct consolidated XHTML periods"
    )
    newer, older = selected
    print(json.dumps({"older": older, "newer": newer}), flush=True)

    output = tmp_path / "downloads" / "newer.xhtml"
    downloaded = json.loads(
        run_cli(
            "filing",
            "download",
            "--url",
            newer["url"],
            "--output",
            str(output),
            timeout=360,
        )
    )
    content = output.read_bytes()
    assert downloaded["byte_count"] == len(content) > 0
    assert downloaded["sha256"] == hashlib.sha256(content).hexdigest()
    assert downloaded["output_path"] == str(output)
    assert downloaded["content_type"].split(";", 1)[0].lower() in {
        "application/xhtml+xml",
        "text/html",
    }

    database = tmp_path / "chroma"
    comparison = json.loads(
        run_cli(
            "filing",
            "compare",
            "--older-url",
            older["url"],
            "--newer-url",
            newer["url"],
            "--database",
            str(database),
            timeout=900,
        )
    )
    for label in ("older", "newer"):
        provenance = comparison[label]
        assert provenance["url"]
        assert len(provenance["sha256"]) == 64
        int(provenance["sha256"], 16)
        assert provenance["byte_count"] > 0
    assert comparison["newer"] == {
        "url": downloaded["final_url"],
        "sha256": downloaded["sha256"],
        "byte_count": downloaded["byte_count"],
    }
    assert isinstance(comparison["change_count"], int)
    assert comparison["change_count"] >= len(comparison["changes"])
    assert len(comparison["changes"]) <= 100
    for change in comparison["changes"]:
        assert change["new_text"]
        assert change["closest_old_text"]
        assert math.isfinite(change["distance"])
    assert any(database.iterdir()), "Comparison did not persist its database"
    print(
        json.dumps(
            {
                "older": comparison["older"],
                "newer": comparison["newer"],
                "change_count": comparison["change_count"],
            }
        ),
        flush=True,
    )
