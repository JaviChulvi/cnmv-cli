from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin

import httpx
from lxml import html
from lxml.etree import ParserError

TABLE_ID = "ctl00_ContentPrincipal_gridInformes"
IFA_URL = "https://www.cnmv.es/portal/consultas/ifa/listadoifa"


class MalformedPageError(ValueError):
    """The CNMV response does not have the expected filing table shape."""


class UpstreamError(RuntimeError):
    """CNMV could not be reached successfully."""


def fetch_filings(
    nif: str, *, transport: httpx.BaseTransport | None = None
) -> list[dict[str, object]]:
    try:
        with httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "cnmv-cli/0.1"},
        ) as client:
            response = client.get(
                IFA_URL,
                params={"id": "0", "lang": "es", "nif": nif},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamError(f"CNMV request failed: {exc}") from exc

    return parse_filings(response.content, str(response.url))


def _text(element: html.HtmlElement) -> str:
    return " ".join(element.text_content().split())


def _label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s*\(\d+\)\s*$", "", ascii_text).strip().casefold()


def parse_filings(content: bytes, page_url: str) -> list[dict[str, object]]:
    try:
        document = html.fromstring(content)
    except (ParserError, ValueError) as exc:
        raise MalformedPageError("CNMV returned invalid HTML") from exc

    tables = document.xpath(f'//table[@id="{TABLE_ID}"]')
    if len(tables) != 1:
        raise MalformedPageError(f"CNMV page is missing the {TABLE_ID} table")

    filings: list[dict[str, object]] = []
    for row in tables[0].xpath(".//tr[td]"):
        cells = {
            _label(cell.get("data-th", "")): cell
            for cell in row.xpath("./td[@data-th]")
        }
        if not cells:
            continue

        required = {
            "no registro oficial",
            "fecha estados financieros",
            "fecha de publicacion",
            "nombre del auditor",
        }
        if not required.issubset(cells):
            raise MalformedPageError("CNMV filing row is missing required cells")

        filing: dict[str, object] = {
            "registration_number": _text(cells["no registro oficial"]),
            "period_end": _text(cells["fecha estados financieros"]),
            "publication_date": _text(cells["fecha de publicacion"]),
            "auditor": _text(cells["nombre del auditor"]),
        }

        optional_fields = {
            "opinion del auditor": "audit_opinion",
            "parrafo de enfasis": "emphasis",
            "observaciones": "observations",
        }
        for cell_label, field_name in optional_fields.items():
            value = _text(cells[cell_label]) if cell_label in cells else ""
            if value and value != "-":
                filing[field_name] = value

        documents: list[dict[str, str]] = []
        type_cell = cells.get("tipo")
        if type_cell is not None:
            for link in type_cell.xpath(".//a[@href]"):
                link_text = _text(link).casefold()
                if "individual" in link_text:
                    role = "individual_xhtml"
                elif "consolidad" in link_text:
                    role = "consolidated_xhtml"
                else:
                    continue
                documents.append(
                    {"role": role, "url": urljoin(page_url, link.get("href"))}
                )

        zip_cell = cells.get("fichero zip/xbri")
        if zip_cell is not None:
            zip_links = zip_cell.xpath(".//a[@href]")
            if zip_links:
                documents.append(
                    {
                        "role": "consolidated_zip",
                        "url": urljoin(page_url, zip_links[0].get("href")),
                    }
                )
        filing["documents"] = documents
        filings.append(filing)

    return filings
