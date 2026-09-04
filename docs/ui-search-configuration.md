# UI image search configuration (planned)

Status: configuration and implementation decision only. The image search adapters,
acquisition Activity, and UI CLI are not connected yet. Setting these variables
does not enable image search or make any network request.

The game UI workflow will support SerpApi Google Images and Tavily Search. Brave
is no longer a prerequisite. The default is SerpApi; select Tavily explicitly when
needed. Missing credentials or provider errors must not trigger automatic fallback.

```dotenv
LETSAIGC_IMAGE_SEARCH_PROVIDER=serpapi
SERPAPI_API_KEY=
TAVILY_API_KEY=
```

Use `SERPAPI_API_KEY`, not `SEPAPI_API_KEY`. Only the selected provider's key is
required. Keep secrets in the ignored `.env` or process environment. Process
environment takes priority over the runtime repository's `.env`; separate Git
worktrees do not automatically inherit the primary checkout's `.env`.

Implementation belongs in `assets/search.py` and provider modules
`assets/providers/serpapi.py` and `assets/providers/tavily.py`, using the existing
httpx dependency. Provider selection, request limits and pricing assumptions must
be frozen in the pipeline plan. Credentials stay outside plan fingerprints and
workflow history. Existing tasks must not change providers on Worker restart.

SerpApi maps original image URLs, declared dimensions and source pages from Google
Images results. Tavily enables images with fixed basic depth and automatic
parameters disabled; missing dimensions and source associations remain unknown
until verified. Both feed the same bounded downloader, content validation and
deduplication. Returned image descriptions remain untrusted evidence.

The original limits remain shared: at most three queries per task, twenty retained
candidates and five download attempts per query, with one selected image by default.
Adding another provider does not increase these limits. Track request/credit usage
through the existing operation ledger; free quotas are not unlimited budgets.
Unknown provider outcomes require reconciliation before a new chargeable attempt.
Raw URLs, authentication, query strings and fragments must not enter HTTP logs,
exception chains, Temporal payloads or manifests.

Next implementation gates are offline provider contracts, ledger/acquisition
integration, then an explicitly bounded live search with configured credentials.
Live OCR/VLM, segmentation, inpainting and the complete UI workflow retain their
separate acceptance gates. Local image analysis does not require search credentials.

Provider references, reviewed 2026-09-04:

- [SerpApi Google Images](https://serpapi.com/google-images-api)
- [SerpApi result fields](https://serpapi.com/images-results)
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
