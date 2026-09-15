from __future__ import annotations

import hashlib
import http.client
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO

from .filings import UpstreamError

MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
_MAX_REDIRECTS = 10
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class InvalidDocumentUrl(ValueError):
    """A document URL is outside the allowed CNMV HTTPS origin."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return redirects to the caller so their destinations can be validated."""

    def http_error_302(
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: object,
    ) -> BinaryIO:
        return fp

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def _validated_url(value: str, *, final: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
    except ValueError as exc:
        raise InvalidDocumentUrl(
            f"invalid {'final ' if final else ''}URL: {value}"
        ) from exc

    if parsed.scheme != "https" or not (host == "cnmv.es" or host.endswith(".cnmv.es")):
        raise InvalidDocumentUrl(
            f"{'final URL' if final else 'URL'} must use HTTPS on cnmv.es: {value}"
        )
    return value


def download_document(url: str, output: Path) -> dict[str, object]:
    current_url = _validated_url(url)
    opener = urllib.request.build_opener(_NoRedirectHandler())

    try:
        for _ in range(_MAX_REDIRECTS):
            request = urllib.request.Request(
                current_url,
                headers={"User-Agent": "cnmv-cli/0.1"},
            )
            with opener.open(request, timeout=30.0) as response:
                status = response.status
                if status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise UpstreamError(
                            "CNMV request failed: redirect has no Location"
                        )
                    current_url = _validated_url(
                        urllib.parse.urljoin(current_url, location), final=True
                    )
                    continue

                final_url = _validated_url(response.geturl(), final=True)
                return _write_response(response, output, final_url)

        raise UpstreamError("CNMV request failed: too many redirects")
    except (InvalidDocumentUrl, UpstreamError):
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise UpstreamError(f"CNMV request failed: {exc}") from exc


def _write_response(
    response: BinaryIO, output: Path, final_url: str
) -> dict[str, object]:
    content_length = response.headers.get("Content-Length")  # type: ignore[attr-defined]
    if content_length is not None:
        normalized_length = content_length.strip()
        if (
            normalized_length.isascii()
            and normalized_length.isdecimal()
            and int(normalized_length) > MAX_DOWNLOAD_BYTES
        ):
            raise UpstreamError("CNMV document exceeds the maximum size")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".part",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(_CHUNK_SIZE):
                byte_count += len(chunk)
                if byte_count > MAX_DOWNLOAD_BYTES:
                    raise UpstreamError("CNMV document exceeds the maximum size")
                digest.update(chunk)
                temporary.write(chunk)
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "final_url": final_url,
        "sha256": digest.hexdigest(),
        "byte_count": byte_count,
        "content_type": response.headers.get("Content-Type"),  # type: ignore[attr-defined]
        "output_path": str(output),
    }
