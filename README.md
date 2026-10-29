# treasury

State treasury spending at department and DDO (drawing and disbursing officer) level, scraped for the s1d-labs pool.

**Phase 1: Uttar Pradesh, [Koshvani](https://koshvani.up.nic.in).** This is the only state treasury portal that answers
GitHub-hosted runners, so it runs on the pool. The others wait for an Indian vantage point (see [Other states](#other-states)).

## What is scraped

Koshvani's public reports (no login), for the five financial years the site offers (now 2022-23 to 2026-27). Amounts are rupees,
exactly as the site shows them. All figures are progressive "up to month" snapshots: past years are final, and the current
year is re-scraped every month.

| Project | Queue item | Drill path on the site | Records |
|---|---|---|---|
| `treasury-up-grants` | one grant (demand) × year, 91 × 5 | Grant-wise expenditure → grant → scheme | `g:` grant totals; `so:` scheme × standard object, statewide (budget, sanction, allotment, spent); `to:` scheme × treasury × object |
| `treasury-up-ddo` | one treasury × year, 83 × 5 | Division-wise expenditure → treasury → DDO → grant | `t:` treasury; `td:` treasury × DDO; `tdg:` DDO × grant; `tdo:` DDO × grant × scheme × object |

Record ids are `<kind>:<fy>@<month>/<keys>`, e.g. `so:2026-2027@10/001/2039000010300/01/N/V` (grant 001, scheme
2039000010300, object 01, non-plan, voted). Because the month is part of the id, each month's snapshot of the current year is kept.
Every record carries `kind`, `fy` and `upto_month`. Amount fields: `budget`, `sanction`, `allotment`, `exp_upto_prev` (spent before
this month), `exp_month` (spent this month), `exp_total` (progressive). A field is `null` where the site shows `-`.

The raw pages of each item go to the family bucket as `raw/<queue key>.jsonl.gz` (one JSON line per page: label, url, html).

Notes:
- A grant's scheme rows (`so:`) include 8xxx deposit heads that the site lists under the grant but leaves out of the grant total
  (`g:`). For grant 001, the 8009 head is exactly the difference.
- Grant lists include `PAC` and `REC` rows, which the site shows without a drill-down.
- A DDO's total (`td:`) is the sum of its grant rows (`tdg:`) except grant 062 (pensions), `PAC` and `REC`. This held exactly for
  every DDO checked. A treasury's own total (`t:`) is larger than the sum of its DDO totals (by about 10% at AGRA, 2026-27), so
  take each level as the site reports it rather than adding levels up.
- The DDO row `--` (spending without a DDO code) is stored at treasury level but can't be opened on the site.
- A drill the site keeps refusing (it does so for a few `PAC--` rows and schemes whose names contain quotes) gets one replay in a
  new session, then it is skipped, listed under the run's failures (`drill`, `<item> > <path>`) and counted as `drill_skipped`;
  the rest of the item is stored.
- A worker stops claiming items when less time is left in its budget than a long item takes (`ITEM_MINUTES` in the project's
  env, or 1.2 × the longest item it has done; at most a quarter of the budget): items cut off by the time limit lose their work.
- Voucher-level pages (voucher no., date, DDO, gross / deduction / net; no payee names) exist under both paths. They are not scraped
  yet: probably hundreds of thousands of pages.

## How the site works (and why the code looks like this)

- Every session has to open `KoshvaniStatic.aspx` first. The financial year is a postback on `ddlFinYear`.
- Drill links are encrypted, session-bound `Extra_plnDept.aspx?…` URLs that only work from the page that showed them, so the
  queue holds `(report, fy, grant|treasury)` and the task clicks through by link text.
- When the site drops a session, it redirects to `ClearSession.aspx`. The task then starts a new session and replays the
  clicks (metric `session_bounce`).
- The server needs legacy TLS renegotiation, which OpenSSL 3 refuses by default (`LegacyTLS` adapter; certificates are still
  checked).
- Koshvani drops connections from the team server's network, so there is **no hub health check** (it would always fail and
  hold all work). Instead, a worker that gets no answer for two items in a row exits with code 3.

## Working here

```
pip install -r requirements.txt pytest
python -m pytest tests -q                                   # parsers + navigation on saved pages (tests/pages)
python koshvani.py --local grants 2026-2027 001             # one item, no hub (needs a network Koshvani answers)
python koshvani.py --local ddo 2025-2026 08                 # one treasury: ~500 requests, ~10 minutes
scrape-check --project treasury-up-grants --items 2         # setup + task against a local stand-in hub
```

- `family.py` defines the projects, years, grants and treasuries. After changing it, run **Actions → setup**: its `limit` input
  seeds only the first N items of each project. Setup also runs itself on the 5th of each month, which seeds that month's round
  for the current year.
- The **check** workflow runs the tests and `scrape-check` on 2 live grant items on every push.

## Other states

Tested on 2026-10-28 from GitHub runners (US), from check-host.net nodes (US, DE, SG) and from the team server (FR):

| State | Portal | Host | Result |
|---|---|---|---|
| Uttar Pradesh | Koshvani | koshvani.up.nic.in | answers GitHub runners (not the team server) |
| Maharashtra | BEAMS | beams.mahakosh.gov.in | TCP timeout everywhere |
| Rajasthan | IFMS | ifms.rajasthan.gov.in | unreachable |
| Bihar | CTMIS | ctmis.bihar.gov.in | TCP timeout everywhere |
| Karnataka | Khajane-II | karnatakatreasury.gov.in (khajane2.* do not resolve) | TCP timeout everywhere |

The next step is to check these from an Indian server. If they only answer Indian addresses, they can't run on GitHub-hosted
runners. They would need a worker on an Indian machine (same `HubClient` contract) or a self-hosted runner there. Each state will
get its own task script and `treasury-<state>-*` projects here.
