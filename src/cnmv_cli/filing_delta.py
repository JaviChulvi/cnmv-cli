from __future__ import annotations

import hashlib
import math
import re
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TypeVar

import chromadb
from lxml import etree, html

from .download import download_document

_DIMENSIONS = 128
_MAX_CHUNK_CHARS = 600
_CHROMA_BATCH_SIZE = 1_000
_MAX_REPORTED_CHANGES = 100
_HTML_TYPES = {"application/xhtml+xml", "text/html"}
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_Item = TypeVar("_Item")
_BLOCK_NAMES = {
    "article",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "pre",
    "section",
    "td",
    "th",
}


class UnsupportedDocumentError(ValueError):
    """A downloaded document is not usable HTML/XHTML."""


def _embedding(text: str) -> list[float]:
    vector = [0.0] * _DIMENSIONS
    for token in _TOKEN_RE.findall(text.casefold()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % _DIMENSIONS
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def _split_chunk(text: str) -> list[str]:
    pieces: list[str] = []
    while len(text) > _MAX_CHUNK_CHARS:
        boundary = text.rfind(" ", 0, _MAX_CHUNK_CHARS + 1)
        boundary = boundary if boundary > 0 else _MAX_CHUNK_CHARS
        pieces.append(text[:boundary])
        text = text[boundary:].lstrip()
    if text:
        pieces.append(text)
    return pieces


def _batches(values: Sequence[_Item], size: int) -> Iterator[Sequence[_Item]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _chroma_batch_size(client: chromadb.PersistentClient) -> int:
    return min(_CHROMA_BATCH_SIZE, client.get_max_batch_size())


def _local_name(element: etree._Element) -> str:
    return element.tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()


def _is_hidden_element(element: etree._Element) -> bool:
    if not isinstance(element.tag, str):
        return False
    local_name = _local_name(element)
    if local_name in {"script", "style"}:
        return True
    if local_name not in {"header", "hidden"}:
        return False
    tag = element.tag
    return (
        tag.startswith("{http://www.xbrl.org/") and tag.endswith("/inlineXBRL}" + local_name)
    ) or tag.startswith("ix:")


def _visible_chunks(content: bytes) -> list[str]:
    try:
        document = etree.fromstring(
            content,
            etree.XMLParser(huge_tree=True, no_network=True, resolve_entities=False),
        )
    except etree.XMLSyntaxError:
        try:
            document = html.document_fromstring(content)
        except (etree.ParserError, ValueError) as exc:
            raise UnsupportedDocumentError("document is not valid HTML/XHTML") from exc

    bodies = document.xpath("//*[local-name()='body']")
    if not bodies:
        if _local_name(document) == "xbrl":
            return _split_chunk(" ".join(" ".join(document.itertext()).split()))
        raise UnsupportedDocumentError("HTML/XHTML document has no body")
    body = bodies[0]
    for hidden in [element for element in body.iterdescendants() if _is_hidden_element(element)]:
        parent = hidden.getparent()
        if parent is not None:
            parent.remove(hidden)

    candidates = [
        element
        for element in body.iterdescendants()
        if isinstance(element.tag, str) and _local_name(element) in _BLOCK_NAMES
    ]
    candidate_set = set(candidates)
    nonleaf_candidates = set()
    for element in candidates:
        parent = element.getparent()
        while parent is not None and parent not in candidate_set:
            parent = parent.getparent()
        if parent is not None:
            nonleaf_candidates.add(parent)
    blocks = [element for element in candidates if element not in nonleaf_candidates]
    if not blocks:
        blocks = [body]
    chunks: list[str] = []
    for block in blocks:
        normalized = " ".join(" ".join(block.itertext()).split())
        chunks.extend(_split_chunk(normalized))
    return chunks


def _document_details(provenance: dict[str, object]) -> dict[str, object]:
    return {
        "url": provenance["final_url"],
        "sha256": provenance["sha256"],
        "byte_count": provenance["byte_count"],
    }


def _store_document(
    client: chromadb.PersistentClient,
    provenance: dict[str, object],
    chunks: list[str],
):
    document_hash = str(provenance["sha256"])
    metadata = {
        "document_sha256": document_hash,
        "document_url": str(provenance["final_url"]),
        "hnsw:space": "cosine",
    }
    collection = client.get_or_create_collection(
        name=f"filing-{document_hash}", metadata=metadata
    )
    if collection.metadata.get("document_sha256") != document_hash:
        raise RuntimeError("stored collection does not match the downloaded document")
    if chunks:
        ids = [f"{document_hash}:{index}" for index in range(len(chunks))]
        batch_size = _chroma_batch_size(client)
        for id_batch, chunk_batch in zip(
            _batches(ids, batch_size),
            _batches(chunks, batch_size),
            strict=True,
        ):
            collection.upsert(
                ids=list(id_batch),
                documents=list(chunk_batch),
                embeddings=[_embedding(chunk) for chunk in chunk_batch],
            )
    return collection


def compare_filings(
    older_url: str, newer_url: str, database: Path
) -> dict[str, object]:
    """Download, persist, and compare two explicitly selected HTML filings."""
    database.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        older_provenance = download_document(older_url, directory / "older.xhtml")
        newer_provenance = download_document(newer_url, directory / "newer.xhtml")
        for provenance in (older_provenance, newer_provenance):
            media_type = str(provenance.get("content_type") or "").split(";", 1)[0]
            if media_type.casefold() not in _HTML_TYPES:
                raise UnsupportedDocumentError(
                    f"unsupported content type: {provenance.get('content_type') or 'missing'}"
                )
        older_chunks = _visible_chunks((directory / "older.xhtml").read_bytes())
        newer_chunks = _visible_chunks((directory / "newer.xhtml").read_bytes())

    for label, chunks in (("older", older_chunks), ("newer", newer_chunks)):
        if not chunks:
            raise UnsupportedDocumentError(
                f"{label} HTML/XHTML document has no visible text"
            )

    client = chromadb.PersistentClient(path=str(database))
    older_collection = _store_document(client, older_provenance, older_chunks)
    _store_document(client, newer_provenance, newer_chunks)

    changes: list[dict[str, object]] = []
    change_count = 0
    older_chunk_set = set(older_chunks)
    for chunk_batch in _batches(newer_chunks, _chroma_batch_size(client)):
        nearest = older_collection.query(
            query_embeddings=[_embedding(chunk) for chunk in chunk_batch], n_results=1
        )
        for chunk, distances, documents in zip(
            chunk_batch,
            nearest["distances"],
            nearest["documents"],
            strict=True,
        ):
            if chunk not in older_chunk_set:
                change_count += 1
                if len(changes) < _MAX_REPORTED_CHANGES:
                    changes.append(
                        {
                            "new_text": chunk,
                            "closest_old_text": documents[0],
                            "distance": round(float(distances[0]), 6),
                        }
                    )

    return {
        "older": _document_details(older_provenance),
        "newer": _document_details(newer_provenance),
        "change_count": change_count,
        "changes": changes,
    }
