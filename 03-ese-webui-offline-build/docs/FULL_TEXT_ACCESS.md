# Lawful full-text access routing

This increment is an offline planner. It accepts a citation, preserves the
original citation, normalizes DOI/PMID identity fields, and emits a stable
ordered list of possible routes. It does not search, authenticate, read
browser cookies, bypass a paywall, download a paper, or mark full text as
verified.

The WebUI routes are available at:

- `GET /api/full-text/connectors`
- `POST /api/full-text/plan` with `{ "citation": {...} }` and an optional
  `enabled_connectors` list and reference-only `access_config`
- `GET /api/full-text/access-config`
- `POST /api/full-text/access-config/validate`
- `POST /api/full-text/probe-plan` (builds a Scopus/Embase metadata plan only)
- `POST /api/full-text/manual-record` (records user-observed access and identity)

The current connector catalog is:

| Connector | What the plan means | What it does not prove |
| --- | --- | --- |
| `pubmed_central` | Open-access-subset candidate | That the cited article is in the reusable OA subset |
| `crossref` | DOI metadata and publisher-link identity route | Full text or entitlement |
| `google_scholar` | Manual discovery of PDF/HTML/library links | A stable automated API or lawful access |
| `scopus` | Metadata/abstract or entitlement-link route | Full text from Scopus itself |
| `embase` | Metadata/abstract or entitlement-link route | Full text from Embase itself |
| `ezproxy` | User-owned institutional proxy route | That the user has access or that login succeeded |
| `publisher` | DOI landing-page access check | A subscription entitlement |
| `interlibrary_loan` | Human library/document-delivery request | Fulfilment or reuse rights |
| `manual_browser` | User-owned browser session | An automated session, login, or 2FA capability |

Any future network connector must be a separate work package with explicit
operator authorization, bounded request/response handling, entitlement and
license checks, and identity verification against DOI/PMID/title before an
article is admitted to extraction. Institutional credentials, cookies, and
2FA remain outside this repository's automated path.

The probe-plan endpoint is still offline. It emits the official Elsevier host
and bounded route shape, an identity-bound DOI/PMID query, and the reference
name required by a future caller-owned runtime. It does not resolve the name,
send a request, retrieve an abstract, retrieve full text, or change
`verified_full_text: false`.

The manual-record endpoint is the only route that can set
`verified_full_text: true`, and only when the operator supplies a matching
DOI/PMID/title identity and an HTTPS source URL for a `full_text_observed`
record. This means “the user observed the cited article,” not “the article is
licensed for reuse”; license status remains `not_assessed`.

## Source constraints used for the catalog

- [Google Scholar Search Help](https://scholar.google.com/intl/us/scholar/help.html)
  describes manual PDF/HTML, library-link, and “All versions” discovery and
  asks users not to automate queries in violation of `robots.txt`.
- [Elsevier Scopus API documentation](https://dev.elsevier.com/sc_apis.html)
  describes curated abstract/citation data, links, API keys, and subscription
  or policy conditions; it is not treated as a full-text entitlement.
- [Elsevier Embase API guide](https://supportcontent.elsevier.com/Support%20Hub/DaaS/Embase%20API%20Guide.pdf)
  is recorded as a future metadata connector requiring separate authorization.
- The [Embase API specification](https://dev.elsevier.com/documentation/EmbaseAPI.wadl)
  documents DOI and PubMed-ID retrieval routes; those routes are represented
  as identity-bound probe descriptors only.
- [PMC developer documentation](https://pmc.ncbi.nlm.nih.gov/tools/developers/)
  distinguishes the Open Access subset and services whose license/usage rights
  allow reuse.
- [Crossref REST API](https://api.crossref.org/) is used conceptually as a
  metadata/DOI identity route, not as a full-text provider.
- EZproxy is treated as a user-owned institutional route; its access groups
  and authentication behavior are controlled by the institution's EZproxy
  administrator, as described in [OCLC EZproxy groups documentation](https://help.oclc.org/Library_Management/EZproxy/Configure_resources/Groups).
