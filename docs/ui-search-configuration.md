# UI image search configuration (planned)

Status: configuration and implementation decision only. The image search adapters,
acquisition Activity, and UI CLI are not connected yet. Setting these variables
does not enable image search or make any network request.

The game UI workflow will automatically route between SerpApi Google Images and
Tavily Search using available quota, health and the task's shared budget. Brave is
no longer a prerequisite. Environment configuration contains credentials only;
provider selection belongs to a versioned routing policy.

```dotenv
SERPAPI_API_KEY=
TAVILY_API_KEY=
```

Use `SERPAPI_API_KEY`, not `SEPAPI_API_KEY`. Both keys allow automatic switching;
one configured key allows only that provider. The previous fixed-provider variable
`LETSAIGC_IMAGE_SEARCH_PROVIDER` is superseded and will not control the new router.
Keep secrets in the ignored `.env` or process environment. Process
environment takes priority over the runtime repository's `.env`; separate Git
worktrees do not automatically inherit the primary checkout's `.env`.

Implementation belongs in `assets/search.py`, `assets/search_routing.py` and provider modules
`assets/providers/serpapi.py` and `assets/providers/tavily.py`, using the existing
httpx dependency. Freeze the allowed provider set, routing policy/version, request
limits, provider parameters and pricing assumptions in the pipeline plan.
Credentials stay outside plan fingerprints and workflow history.

Before each new search attempt, atomically save its actual provider, quota snapshot
reference, routing reason and reservation in the existing operation ledger. Replaying
or retrying that operation reuses its saved decision. A new, safe attempt may switch
within the approved set without another approval. Adding a provider, increasing the
budget or enabling incremental PAYG spending requires a new plan.

Quota observations use SerpApi's Account API and Tavily's Usage API. Normalize plan
and key limits, units, freshness and known reset dates. Tavily's effective included
quota must respect both account and key limits; PAYG is excluded by default. Keep
SerpApi extra/prepaid credits distinct from free allowances. Missing fields do not
mean unlimited capacity. Account responses must be parsed with an allowlist: they
can contain credentials and personal account information.

Share quota snapshots, reservations, cooldowns and refresh limits across local tasks
through the existing ledger. Refresh at most once per 75 seconds per scope, with
a five-minute maximum snapshot age and at most two external probes per provider per
task. Tavily additionally limits usage queries to ten per ten minutes. Persist this
limiter across Worker restarts and honor Retry-After. Deduct in-flight and not-yet-
reflected consumption without charging reflected settlements twice.

Exclude missing credentials, invalid authentication, stale/unknown quota, cooldowns
and insufficient available quota or budget. Prefer SerpApi at normal water levels.
When its available ratio falls below 10% and Tavily has a higher ratio with enough
capacity, switch early. Retain that choice for subsequent safe attempts until the
preferred provider recovers above 20%, or the current provider becomes ineligible.
If no comparable limit is available, use affordable call counts and normal preference.
Cap changes at two and external search attempts at three per task across both services.

Confirmed quota/rate-limit rejection may lead to a new attempt on the other service
after the original attempt's usage is accounted for. An accepted or unknown search
must first be recovered/reconciled: timeouts and 5xx responses do not prove rejection.
Both providers unavailable produces a resumable stop, never a recharge or polling
loop. Quota probes are bounded reads and can skip an unavailable provider.

Provider observations are not remote reservations. Other applications sharing an
account may consume its balance between checks. The router coordinates this project's
consumption, but a no-new-charge requirement also needs provider-side PAYG/automatic
renewal disabled. It must not modify those billing settings itself. Quota renewal
does not reset the task's spent budget or discard unresolved operations.

SerpApi maps original image URLs, declared dimensions and source pages from Google
Images results. Tavily enables images with fixed basic depth and automatic
parameters disabled; missing dimensions and source associations remain unknown
until verified. Both feed the same bounded downloader, content validation and
deduplication. Returned image descriptions remain untrusted evidence.

The original limits remain shared: at most three queries per task, twenty retained
candidates and five download attempts per query, with one selected image by default.
Switches and rejected attempts count toward the same three-attempt ceiling. Candidate
and download limits accumulate per logical query across switches. Track request/credit usage
through the existing operation ledger; free quotas are not unlimited budgets.
Unknown provider outcomes require reconciliation before a new chargeable attempt.
Raw URLs, authentication, query strings and fragments must not enter HTTP logs,
exception chains, Temporal payloads or manifests.

Next implementation gates are offline provider/quota contracts and ledger/acquisition
integration, then bounded live quota probes and search with configured credentials.
Test low-water switching, hysteresis, depleted/unknown quota, concurrent reservations,
probe throttling, rejection versus unknown outcome, replayed route decisions and
budget/attempt limits. Do not exhaust real accounts merely to test failover.
Live OCR/VLM, segmentation, inpainting and the complete UI workflow retain their
separate acceptance gates. Local image analysis does not require search credentials.

Provider references, reviewed 2026-09-04:

- [SerpApi Google Images](https://serpapi.com/google-images-api)
- [SerpApi result fields](https://serpapi.com/images-results)
- [SerpApi Account API](https://serpapi.com/account-api)
- [SerpApi status codes](https://serpapi.com/api-status-and-error-codes)
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily Usage API](https://docs.tavily.com/documentation/api-reference/endpoint/usage)
- [Tavily usage-query rate limit](https://docs.tavily.com/documentation/rate-limits)
