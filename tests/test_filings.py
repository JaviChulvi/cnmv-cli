import httpx
import pytest

from cnmv_cli.filings import (
    MalformedPageError,
    UpstreamError,
    fetch_filings,
    parse_filings,
)

PAGE_URL = (
    "https://www.cnmv.es/portal/consultas/ifa/listadoifa?id=0&lang=es&nif=A-12345678"
)

HTML = """
<html><head><meta charset="UTF-8"></head><body>
  <table id="ctl00_ContentPrincipal_gridInformes">
    <thead><tr><th>headings are deliberately ignored</th></tr></thead>
    <tbody>
      <tr>
        <td data-th="Nº Registro Oficial"> 20915 </td>
        <td data-th="Fecha Estados Financieros"> 31/12/2025 </td>
        <td data-th="Fecha de publicación (1)"> 26/02/2026 </td>
        <td data-th="Nombre del auditor"> KPMG AUDITORES, S.L. </td>
        <td data-th="Tipo">
          <a href="/docs/individual.xhtml">Individual</a> /<br/>
          <a href="https://www.cnmv.es/docs/consolidated.xhtml">Consolidada</a>
        </td>
        <td data-th="Fichero ZIP/Xbri (2)"><a href="/docs/report.zip"><img/></a></td>
        <td data-th="Opinión del auditor (5)">Favorable /<br/> Favorable</td>
        <td data-th="Párrafo de énfasis (5)"> - /<br/> Incertidumbre material </td>
        <td data-th="Observaciones"> Presentación sustitutiva </td>
      </tr>
      <tr class="FilasAlternas">
        <td data-th="Nº Registro Oficial"> 20496 </td>
        <td data-th="Fecha Estados Financieros"> 31/12/2024 </td>
        <td data-th="Fecha de publicación (1)"> 28/02/2025 </td>
        <td data-th="Nombre del auditor"> Otro auditor </td>
        <td data-th="Tipo"><a href="/docs/only.xhtml">Individual</a></td>
        <td data-th="Fichero ZIP/Xbri (2)"></td>
        <td data-th="Opinión del auditor (5)"> - </td>
        <td data-th="Párrafo de énfasis (5)"></td>
        <td data-th="Observaciones"></td>
      </tr>
    </tbody>
  </table>
</body></html>
""".encode()


def test_parse_filings_extracts_metadata_and_document_roles() -> None:
    filings = parse_filings(HTML, PAGE_URL)

    assert filings == [
        {
            "registration_number": "20915",
            "period_end": "31/12/2025",
            "publication_date": "26/02/2026",
            "auditor": "KPMG AUDITORES, S.L.",
            "audit_opinion": "Favorable / Favorable",
            "emphasis": "- / Incertidumbre material",
            "observations": "Presentación sustitutiva",
            "documents": [
                {
                    "role": "individual_xhtml",
                    "url": "https://www.cnmv.es/docs/individual.xhtml",
                },
                {
                    "role": "consolidated_xhtml",
                    "url": "https://www.cnmv.es/docs/consolidated.xhtml",
                },
                {
                    "role": "consolidated_zip",
                    "url": "https://www.cnmv.es/docs/report.zip",
                },
            ],
        },
        {
            "registration_number": "20496",
            "period_end": "31/12/2024",
            "publication_date": "28/02/2025",
            "auditor": "Otro auditor",
            "documents": [
                {
                    "role": "individual_xhtml",
                    "url": "https://www.cnmv.es/docs/only.xhtml",
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    "html",
    [
        b"<html><body><p>maintenance</p></body></html>",
        (
            b'<table id="ctl00_ContentPrincipal_gridInformes"><tr>'
            b'<td data-th="N\xc2\xba Registro Oficial">1</td></tr></table>'
        ),
    ],
)
def test_parse_filings_rejects_missing_table_or_required_cells(html: bytes) -> None:
    with pytest.raises(MalformedPageError):
        parse_filings(html, PAGE_URL)


def test_parse_filings_ignores_non_filing_table_rows() -> None:
    html_with_pagination = HTML.replace(
        b"</table>",
        b'<tr><td colspan="11"><span class="pagination">2</span></td></tr></table>',
    )

    filings = parse_filings(html_with_pagination, PAGE_URL)

    assert len(filings) == 2


@pytest.mark.parametrize("nif", ["A12345678", "A-12345678", "a12345678", "a-12345678"])
def test_fetch_filings_requests_official_ifa_page(nif: str) -> None:
    requested_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(request.url)
        return httpx.Response(200, content=HTML)

    filings = fetch_filings(nif, transport=httpx.MockTransport(handler))

    assert len(filings) == 2
    assert requested_urls == [httpx.URL(PAGE_URL)]


def test_fetch_filings_leaves_non_a_prefixes_unchanged() -> None:
    requested_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(request.url)
        return httpx.Response(200, content=HTML)

    fetch_filings("B12345678", transport=httpx.MockTransport(handler))

    assert requested_urls == [
        httpx.URL(
            "https://www.cnmv.es/portal/consultas/ifa/listadoifa?id=0&lang=es&nif=B12345678"
        )
    ]


@pytest.mark.parametrize("failure", ["status", "connection"])
def test_fetch_filings_reports_upstream_failures(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connection":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503, request=request)

    with pytest.raises(UpstreamError, match="CNMV request failed"):
        fetch_filings("A12345678", transport=httpx.MockTransport(handler))
