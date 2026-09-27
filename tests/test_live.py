"""Opt-in checks of the installed CLI against the official CNMV website."""

import hashlib
import json
import math
import os
import subprocess
import time
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

SMOKE_TIMEOUT = 16 * 60
RETRY_DELAY = 10


@pytest.fixture
def cli_process(monkeypatch):
    now = [0.0]
    process = Mock()
    sleep = Mock(side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", sleep)
    return process, sleep, now


@pytest.mark.parametrize("recovers", [True, False])
def test_run_cli_retries_transient_failure_once(cli_process, capsys, recovers) -> None:
    process, sleep, now = cli_process
    failure = subprocess.CompletedProcess(
        ["cnmv"],
        1,
        "",
        "Error: CNMV request failed: [retryable] ReadTimeout at https://www.cnmv.es/",
    )
    success = subprocess.CompletedProcess(["cnmv"], 0, "result", "")
    process.side_effect = [failure, success if recovers else failure]
    if recovers:
        assert run_cli("filing", "list", deadline=60) == "result"
    else:
        with pytest.raises(pytest.fail.Exception, match="attempt 2/2.*ReadTimeout"):
            run_cli("filing", "list", deadline=60)
    assert process.call_count == 2
    sleep.assert_called_once_with(10)
    assert [call.kwargs["timeout"] for call in process.call_args_list] == [60, 50]
    assert now[0] == 10
    assert "Attempt 1/2 finished in 0.0s, exit=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        "Error: CNMV request failed: HTTPStatusError at https://www.cnmv.es/: 404",
        "Error: CNMV page is missing the table",
        "Error: unsupported content type",
        "Error: CNMV request failed: redirect has no Location",
    ],
)
def test_run_cli_does_not_retry_permanent_errors(cli_process, error) -> None:
    process, sleep, _ = cli_process
    process.return_value = subprocess.CompletedProcess(["cnmv"], 1, "", error)
    with pytest.raises(pytest.fail.Exception, match="attempt 1/2 failed"):
        run_cli("filing", "list", deadline=60)
    process.assert_called_once()
    sleep.assert_not_called()


def test_run_cli_reports_subprocess_timeout_without_retry(cli_process) -> None:
    process, sleep, _ = cli_process
    process.side_effect = subprocess.TimeoutExpired(
        ["cnmv"], 30, b"partial", b"diagnostic"
    )
    with pytest.raises(
        pytest.fail.Exception, match="TimeoutExpired.*limit=30.0s.*partial.*diagnostic"
    ):
        run_cli("filing", "compare", deadline=30, timeout=900)
    process.assert_called_once()
    sleep.assert_not_called()


def test_run_cli_shares_deadline_between_commands(cli_process) -> None:
    process, _, now = cli_process

    def complete(*args, **kwargs):
        now[0] += 5
        return subprocess.CompletedProcess(args[0], 0, "result", "")

    process.side_effect = complete
    run_cli("filing", "list", deadline=10)
    run_cli("filing", "download", deadline=10, timeout=360)
    with pytest.raises(pytest.fail.Exception, match="deadline exhausted"):
        run_cli("filing", "compare", deadline=10, timeout=900)
    assert [call.kwargs["timeout"] for call in process.call_args_list] == [10, 5]


def test_run_cli_does_not_sleep_past_deadline(cli_process) -> None:
    process, sleep, _ = cli_process
    process.return_value = subprocess.CompletedProcess(
        ["cnmv"], 1, "", "Error: CNMV request failed: [retryable] ReadTimeout"
    )
    with pytest.raises(pytest.fail.Exception, match="deadline leaves no time to retry"):
        run_cli("filing", "list", deadline=10)
    process.assert_called_once()
    sleep.assert_not_called()


def run_cli(*arguments: str, deadline: float, timeout: int = 120) -> str:
    command = ["cnmv", *arguments]
    for attempt in range(1, 3):
        started = time.monotonic()
        remaining = deadline - started
        if remaining <= 0:
            pytest.fail(f"Live CNMV deadline exhausted before {' '.join(command)}")
        attempt_timeout = min(timeout, remaining)
        print(
            f"Running {' '.join(command)} (attempt {attempt}/2, "
            f"timeout={attempt_timeout:.1f}s)",
            flush=True,
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=attempt_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(
                f"{' '.join(command)} attempt {attempt}/2: TimeoutExpired after "
                f"{time.monotonic() - started:.1f}s (limit={attempt_timeout:.1f}s); "
                f"stdout={exc.stdout!r}; stderr={exc.stderr!r}"
            )
        elapsed = time.monotonic() - started
        print(
            f"Attempt {attempt}/2 finished in {elapsed:.1f}s, exit={result.returncode}",
            flush=True,
        )
        if result.returncode == 0:
            return result.stdout
        error = result.stderr or result.stdout
        if attempt == 1 and result.stderr.startswith(
            "Error: CNMV request failed: [retryable] "
        ):
            if deadline - time.monotonic() <= RETRY_DELAY:
                pytest.fail(f"Live CNMV deadline leaves no time to retry: {error}")
            print(f"Retrying in {RETRY_DELAY}s: {error}", flush=True)
            time.sleep(RETRY_DELAY)
            continue
        pytest.fail(
            f"{' '.join(command)} attempt {attempt}/2 failed after {elapsed:.1f}s: {error}"
        )


@pytest.mark.skipif(
    os.environ.get("CNMV_LIVE_TESTS") != "1",
    reason="set CNMV_LIVE_TESTS=1 to contact the real CNMV website",
)
def test_live_list_download_and_compare(tmp_path) -> None:
    deadline = time.monotonic() + SMOKE_TIMEOUT
    filings = [
        json.loads(line)
        for line in run_cli(
            "filing", "list", "--nif", "A08001851", deadline=deadline
        ).splitlines()
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
            deadline=deadline,
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
            deadline=deadline,
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
