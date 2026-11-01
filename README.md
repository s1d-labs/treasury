# treasury

State treasury spending at department and DDO (drawing and disbursing officer) level, scraped for the s1d-labs pool.

**Uttar Pradesh, [Koshvani](https://koshvani.up.nic.in)** runs on plain GitHub runners. **Maharashtra,
[BEAMS](https://beams.mahakosh.gov.in)** answers only from India, so its projects run with `proxy="IN"`: each pool worker borrows
one of the hub's Mumbai proxies. See [Maharashtra](#maharashtra-beams) and [Other states](#other-states).

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

## Maharashtra (BEAMS)

`beams.py` (task `beams`) reads BEAMS's public MIS reports (server-rendered JSP pages, no login).

| Project | Queue item | Drill path on the site | Records |
|---|---|---|---|
| `treasury-mh-dept` | one department × year, 33 × 5 | Department-wise expenditure → demand → major head → scheme → detail head | `dept:`, `demand:`, `mh:`, `scheme:`, `head:` |
| `treasury-mh-ddo` | one district × year, 36 × 5 | District-wise (DDO wise) → treasury → DDO | `dist:`, `tr:`, `ddo:` |

- Department reports run up to a month: March for past years, the last complete month for the current one (`month0` in the
  payload, 0-based). Columns: budget (and supplementary S1-S3, re-appropriations at the lower levels), releases, the month's and
  April-to-month (`prog_`) received / distributed / expenditure / slips, % released and % of budget.
- **Units differ by level**, as the site shows them: department and demand in rupees crore, major head and below in thousands
  (`released_fd_crore` on major heads is in crore). Every record carries `unit`.
- DDO reports show the current calendar month and April-to-date as of the day they are read; the current year's record ids carry the
  scrape month (`ddo:2026-2027@2026-11/4501004479`). Amounts in crore. A treasury's `prog_balance` does not match the sum of its
  DDOs (the site repeats the month's balance there); the other columns add up.
- The columns are read from each table's header row (the group row's colspans are wrong on some pages). Parsed rows match the
  page's own total row.
- From a team box: `myproxy run python beams.py --local dept 2026-2027 A` (about 170 requests, 2 minutes) or
  `--local ddo 2026-2027 45` (one district, about 12 requests). `myproxy run scrape-check --project treasury-mh-ddo --items 2`.
  The **check** workflow can't reach BEAMS (no proxy there), so it checks Koshvani only.
- Not used: the "DDO wise Expenditure" report (`AGExpMain.jsp`) returns zeros for every DDO tried.

## Other states

Checked on 2026-11-01 through the hub's Mumbai proxies (earlier, 2026-10-28/29, from GitHub runners, the team server and a Delhi VPS):

| State | Portal | Host | Result |
|---|---|---|---|
| Uttar Pradesh | Koshvani | koshvani.up.nic.in | answers GitHub runners (not the team server): scraped |
| Maharashtra | BEAMS | beams.mahakosh.gov.in | answers from India only: scraped through the proxies |
| Rajasthan | IFMS | ifms.rajasthan.gov.in | answers from India only: next |
| Bihar | CTMIS | ctmis.bihar.gov.in | TCP timeout, from India too |
| Karnataka | Khajane-II | karnatakatreasury.gov.in | no answer from India either; k2.karnataka.gov.in is the challan portal, not spending |
