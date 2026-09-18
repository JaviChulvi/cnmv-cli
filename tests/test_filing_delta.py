import hashlib
from pathlib import Path

import pytest

from cnmv_cli import filing_delta


def test_compare_filings_persists_documents_and_reports_changed_chunks(
    monkeypatch, tmp_path
) -> None:
    older = b"<html><body><p>Revenue was 4 million euros.</p><p>Staff: 20.</p></body></html>"
    newer = b"<html><body><p>Revenue was 20 million euros.</p><p>Staff: 20.</p></body></html>"
    documents = {
        "https://www.cnmv.es/older.xhtml": older,
        "https://www.cnmv.es/newer.xhtml": newer,
    }

    def fake_download(url: str, output: Path) -> dict[str, object]:
        content = documents[url]
        output.write_bytes(content)
        return {
            "final_url": url,
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_count": len(content),
            "content_type": "application/xhtml+xml",
            "output_path": str(output),
        }

    monkeypatch.setattr(filing_delta, "download_document", fake_download)

    result = filing_delta.compare_filings(
        "https://www.cnmv.es/older.xhtml",
        "https://www.cnmv.es/newer.xhtml",
        tmp_path / "chroma",
    )

    assert result["older"] == {
        "url": "https://www.cnmv.es/older.xhtml",
        "sha256": hashlib.sha256(older).hexdigest(),
        "byte_count": len(older),
    }
    assert result["newer"]["url"] == "https://www.cnmv.es/newer.xhtml"
    assert result["newer"]["sha256"] == hashlib.sha256(newer).hexdigest()
    assert result["newer"]["byte_count"] == len(newer)
    assert result["change_count"] == 1
    assert result["changes"] == [
        {
            "new_text": "Revenue was 20 million euros.",
            "closest_old_text": "Revenue was 4 million euros.",
            "distance": result["changes"][0]["distance"],
        }
    ]
    assert 0 <= result["changes"][0]["distance"] <= 1
    assert any((tmp_path / "chroma").iterdir())


def test_compare_filings_rejects_non_html_content(monkeypatch, tmp_path) -> None:
    def fake_download(url: str, output: Path) -> dict[str, object]:
        output.write_bytes(b"%PDF-1.7")
        return {
            "final_url": url,
            "sha256": hashlib.sha256(b"%PDF-1.7").hexdigest(),
            "byte_count": 8,
            "content_type": "application/pdf",
            "output_path": str(output),
        }

    monkeypatch.setattr(filing_delta, "download_document", fake_download)

    with pytest.raises(
        filing_delta.UnsupportedDocumentError, match="unsupported content type"
    ):
        filing_delta.compare_filings(
            "https://www.cnmv.es/older.pdf",
            "https://www.cnmv.es/newer.pdf",
            tmp_path / "chroma",
        )


def test_visible_chunks_rejects_html_without_body() -> None:
    with pytest.raises(filing_delta.UnsupportedDocumentError, match="has no body"):
        filing_delta._visible_chunks(b"<html><head><title>Filing</title></head></html>")


def test_visible_chunks_extracts_text_from_raw_xbrl() -> None:
    content = (
        b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">'
        b"<xbrli:context>2025</xbrli:context><Revenue>140</Revenue></xbrli:xbrl>"
    )

    assert filing_delta._visible_chunks(content) == ["2025 140"]


def test_visible_chunks_uses_xml_parser_for_xhtml(monkeypatch) -> None:
    content = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Revenue 140</p>'
        b"</body></html>"
    )

    monkeypatch.setattr(
        filing_delta.html,
        "document_fromstring",
        lambda _: (_ for _ in ()).throw(AssertionError("HTML parser should not run")),
    )

    assert filing_delta._visible_chunks(content) == ["Revenue 140"]


def test_visible_chunks_extracts_leaf_div_text() -> None:
    content = b"<html><body><div><span>Inline XBRL value: 42</span></div></body></html>"

    assert filing_delta._visible_chunks(content) == ["Inline XBRL value: 42"]


def test_visible_chunks_accepts_inline_xbrl_prefixed_tags() -> None:
    content = (
        b"<html><body><ix:header><div>metadata</div></ix:header>"
        b"<div><p>Revenue was 140 million euros.</p></div></body></html>"
    )

    assert filing_delta._visible_chunks(content) == ["Revenue was 140 million euros."]


def test_visible_chunks_excludes_inline_xbrl_metadata_with_any_prefix() -> None:
    content = (
        b'<html xmlns="http://www.w3.org/1999/xhtml" '
        b'xmlns:i="http://www.xbrl.org/2013/inlineXBRL"><body>'
        b"<i:header><p>metadata</p></i:header><i:hidden><p>secret</p></i:hidden>"
        b"<p>Revenue 140</p></body></html>"
    )

    assert filing_delta._visible_chunks(content) == ["Revenue 140"]


def test_visible_chunks_ignores_html_comments() -> None:
    assert filing_delta._visible_chunks(b"<html><body><!-- metadata --><p>Revenue 140</p></body></html>") == [
        "Revenue 140"
    ]


def test_batches_keep_chroma_writes_under_the_limit() -> None:
    assert list(filing_delta._batches(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]


def test_chroma_batch_size_respects_the_active_client_limit() -> None:
    class Client:
        def get_max_batch_size(self) -> int:
            return 2

    assert filing_delta._chroma_batch_size(Client()) == 2
