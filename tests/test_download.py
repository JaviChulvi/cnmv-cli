import hashlib
import urllib.error
from collections.abc import Iterable
from typing import Self

import pytest

from cnmv_cli import download
from cnmv_cli.download import InvalidDocumentUrl, download_document

DOCUMENT_URL = "https://www.cnmv.es/webservices/verdocumento/ver?e=abc"


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: Iterable[bytes] = (),
    ) -> None:
        self._url = url
        self.status = status
        self.headers = headers or {}
        self._chunks = iter(chunks)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        del size
        return next(self._chunks, b"")


class FakeOpener:
    def __init__(self, responses: Iterable[FakeResponse]) -> None:
        self._responses = iter(responses)
        self.requested_urls: list[str] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        assert timeout == 30.0
        self.requested_urls.append(request.full_url)  # type: ignore[attr-defined]
        return next(self._responses)


def install_opener(monkeypatch, responses: Iterable[FakeResponse]) -> FakeOpener:
    opener = FakeOpener(responses)
    monkeypatch.setattr(download.urllib.request, "build_opener", lambda *args: opener)
    return opener


def test_download_document_creates_parents_and_returns_provenance(
    monkeypatch, tmp_path
) -> None:
    body = b"<html>official filing</html>"
    opener = install_opener(
        monkeypatch,
        [
            FakeResponse(
                DOCUMENT_URL,
                headers={"Content-Type": "application/xhtml+xml; charset=utf-8"},
                chunks=[body[:7], body[7:]],
            )
        ],
    )

    output = tmp_path / "nested" / "filing.xhtml"
    provenance = download_document(DOCUMENT_URL, output)

    assert opener.requested_urls == [DOCUMENT_URL]
    assert output.read_bytes() == body
    assert provenance == {
        "final_url": DOCUMENT_URL,
        "sha256": hashlib.sha256(body).hexdigest(),
        "byte_count": len(body),
        "content_type": "application/xhtml+xml; charset=utf-8",
        "output_path": str(output),
    }
    assert list(output.parent.glob(".*.part")) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://www.cnmv.es/document",
        "https://example.com/document",
        "https://cnmv.es.evil.example/document",
        "not a url",
    ],
)
def test_download_document_rejects_non_https_or_non_cnmv_urls(
    monkeypatch, tmp_path, url: str
) -> None:
    def fail_if_called(*args: object) -> None:
        pytest.fail("invalid URLs must not create an opener")

    monkeypatch.setattr(download.urllib.request, "build_opener", fail_if_called)
    output = tmp_path / "not-created" / "document"
    with pytest.raises(InvalidDocumentUrl):
        download_document(url, output)

    assert not output.parent.exists()


def test_download_document_rejects_redirect_away_from_cnmv(
    monkeypatch, tmp_path
) -> None:
    opener = install_opener(
        monkeypatch,
        [
            FakeResponse(
                DOCUMENT_URL,
                status=302,
                headers={"Location": "https://example.com/file"},
            ),
            FakeResponse("https://example.com/file", chunks=[b"untrusted"]),
        ],
    )

    output = tmp_path / "document"
    with pytest.raises(InvalidDocumentUrl, match="final URL"):
        download_document(DOCUMENT_URL, output)

    assert opener.requested_urls == [DOCUMENT_URL]
    assert not output.exists()


def test_download_document_follows_relative_cnmv_redirect(
    monkeypatch, tmp_path
) -> None:
    redirected_url = "https://www.cnmv.es/files/document.zip"
    opener = install_opener(
        monkeypatch,
        [
            FakeResponse(
                DOCUMENT_URL,
                status=302,
                headers={"Location": "/files/document.zip"},
            ),
            FakeResponse(redirected_url, chunks=[b"zip"]),
        ],
    )

    provenance = download_document(DOCUMENT_URL, tmp_path / "document")

    assert opener.requested_urls == [DOCUMENT_URL, redirected_url]
    assert provenance["final_url"] == redirected_url


def test_download_document_rejects_valid_oversized_content_length(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(download, "MAX_DOWNLOAD_BYTES", 3)
    install_opener(
        monkeypatch,
        [
            FakeResponse(
                DOCUMENT_URL,
                headers={"Content-Length": "4"},
                chunks=[b"four"],
            )
        ],
    )

    output = tmp_path / "document"
    with pytest.raises(download.UpstreamError, match="maximum size"):
        download_document(DOCUMENT_URL, output)

    assert not output.exists()
    assert list(tmp_path.glob(".*.part")) == []


def test_download_document_ignores_invalid_content_length_and_streams(
    monkeypatch, tmp_path
) -> None:
    install_opener(
        monkeypatch,
        [
            FakeResponse(
                DOCUMENT_URL,
                headers={"Content-Length": "unknown"},
                chunks=[b"ok"],
            )
        ],
    )
    output = tmp_path / "document"

    provenance = download_document(DOCUMENT_URL, output)

    assert output.read_bytes() == b"ok"
    assert provenance["byte_count"] == 2


def test_download_document_stream_cap_preserves_existing_output_and_cleans_temp(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(download, "MAX_DOWNLOAD_BYTES", 3)
    install_opener(monkeypatch, [FakeResponse(DOCUMENT_URL, chunks=[b"abc", b"d"])])
    output = tmp_path / "document"
    output.write_bytes(b"existing")

    with pytest.raises(download.UpstreamError, match="maximum size"):
        download_document(DOCUMENT_URL, output)

    assert output.read_bytes() == b"existing"
    assert list(tmp_path.glob(".*.part")) == []


def test_download_document_wraps_url_errors(monkeypatch, tmp_path) -> None:
    class FailingOpener:
        def open(self, request: object, *, timeout: float) -> None:
            raise urllib.error.URLError("offline")

    monkeypatch.setattr(
        download.urllib.request, "build_opener", lambda *args: FailingOpener()
    )

    with pytest.raises(download.UpstreamError, match="CNMV request failed:.*offline"):
        download_document(DOCUMENT_URL, tmp_path / "document")
