"""The family's `beams` task (see scrape.json): Maharashtra treasury spending from BEAMS (https://beams.mahakosh.gov.in), the
state's Budget Estimation, Allocation and Monitoring System. Try one item without the hub (needs an Indian IP, e.g. `myproxy run`):
    python beams.py --local dept 2026-2027 A          # one department, every level below it
    python beams.py --local ddo 2026-2027 45          # one district (45 = WARDHA): its treasuries and their DDOs

BEAMS answers only from India, so both projects run with proxy="IN" (family.py): each pool worker gets one of the hub's Mumbai
proxies in HTTPS_PROXY, which requests picks up by itself.

The MIS reports are public, server-rendered JSP pages (no login). Two kinds of queue item (payload["report"]):
- "dept": one department of one financial year, progressive up to `month0` (0-based; March = 2 for a past year).
  Department-wise expenditure -> the department's demands -> major heads -> schemes -> detail (object) heads.
  Records dept:, demand:, mh:, scheme:, head:. Columns: budget, releases, the month's and April-to-month received / distributed /
  expenditure / slips. Units differ by level and are stored with each record (`unit`: crore or thousand).
- "ddo": one district of one financial year. District -> treasuries -> DDOs (drawing and disbursing officers), with received,
  distributed, expenditure, bills and balance for the current month and April-to-date. Records tr:, ddo:. Amounts in crore.
"""
import gzip
import json
import os
import re
import sys
import time
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://beams.mahakosh.gov.in")
BASE = SITE.rstrip("/") + "/Beams5/BudgetMVC/MISRPT"
BUDGET_S = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET_S - 120
ITEM_MINUTES = float(os.environ.get("ITEM_MINUTES", "30"))     # a long item of this project; set per project in family.py
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 (s1d-labs treasury)"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------------------------------------------------------------------
# Parsing (pure; tested on saved pages in tests/pages/mh-*.html)
# ---------------------------------------------------------------------------------------------------------------------------

class LayoutChanged(ValueError):
    """The page is not laid out the way the parser expects: fail the item instead of storing wrong numbers."""


def amount(text):
    """'1,695.490' -> 1695.49; '' / '-' -> None. Units are the page's (crore or thousand)."""
    t = (text or "").replace(",", "").replace("\xa0", "").strip()
    if t in ("", "-", "--", "NA", "N.A."):
        return None
    try:
        return float(t)
    except ValueError:
        raise LayoutChanged(f"not an amount: {text!r}")


def column_name(label, block):
    """Header label -> field name. The month block and the April-to-month block repeat Received / Distributed / Expenditure, so
    they are prefixed month_ / prog_ (block 1 / 2); the yearly budget block (0) is not. 'Released FD(In crore)' ->
    released_fd_crore, 'No. of Slips' -> slips, '% Released Dept' -> pct_released_dept."""
    t = re.sub(r"\s+", " ", label or "").strip().lower()
    crore = "crore" in t
    t = re.sub(r"\(\s*in crores?\s*\)", "", t)
    if t.startswith("available balance"):
        t = "available_balance"
    elif t.startswith("no.") or t.startswith("no "):
        t = t.split("of", 1)[-1]
    t = t.replace("%", " pct ").replace("(+)", " plus").replace("(-)", " minus")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if crore:
        t += "_crore"
    return ["", "month_", "prog_"][min(block, 2)] + t


def grid(rows):
    """Header rows (lists of <th>) -> the same rows as lists of (cell_index, text) per column position, with rowspan/colspan
    expanded."""
    out, pending = [], {}                           # pending[(row, col)] = text carried down by a rowspan
    for r, cells in enumerate(rows):
        line, col = [], 0
        cells = list(cells)
        while cells or (r, col) in pending:
            if (r, col) in pending:
                line.append(pending.pop((r, col)))
                col += 1
                continue
            c = cells.pop(0)
            text = c.get_text(" ", strip=True)
            span, down = int(c.get("colspan") or 1), int(c.get("rowspan") or 1)
            for k in range(span):
                line.append(text)
                for d in range(1, down):
                    pending[(r + d, col + k)] = text
            col += span
        out.append(line)
    return out


def header_rows(table):
    """The rows of a table's header: its <thead> rows (some pages use <td> there), else the <th>-only rows before the data."""
    thead = table.find("thead")
    if thead is not None:
        return [tr for tr in thead.find_all("tr") if tr.find(["th", "td"])]
    rows = []
    for tr in table.find_all("tr"):
        if tr.find("th") and not tr.find("td"):
            rows.append(tr)
        elif rows:
            break
    return rows


def report_table(soup):
    """(table, header rows) of the report: the first table with a 'Received' or 'Budgeted' header (each page has a second copy
    for printing)."""
    for t in soup.find_all("table"):
        heads = header_rows(t)
        if any(c.get_text(" ", strip=True) in ("Received", "Budgeted") for tr in heads for c in tr.find_all(["th", "td"])):
            return t, heads
    raise LayoutChanged("no report table (no Received/Budgeted header)")


def parse_report(soup):
    """{'unit', 'columns', 'rows': [{'label', 'args', values...}], 'total': {values}} for any BEAMS MIS report table.
    `args` are the quoted arguments of the row's drill link (onclick), which carry the codes the next level needs."""
    table, head_trs = report_table(soup)
    trs = table.find_all("tr")
    head_trs = head_trs[-2:]                        # group row + label row (some pages have a spacer row before them)
    g = grid([tr.find_all(["th", "td"]) for tr in head_trs])
    if len(g) != 2:
        raise LayoutChanged(f"expected 2 header rows, found {len(g)}")
    labels = g[1]          # the group row's colspans are not reliable (BEAMS gets them wrong on some pages); the labels row is
    width = len(labels)
    # positions of the leading label column(s): those whose bottom header is the row label (Department, Demand No, DDO, ...)
    first = labels[0]
    nlabel = sum(1 for x in labels if x == first)
    # blocks: yearly budget, then the month (from the first "Received"), then April-to-month (from the second "Received")
    columns, block = [], 0
    for x in labels[nlabel:]:
        if x == "Received":
            block += 1
        columns.append(column_name(x, block))
    if len(set(columns)) != len(columns):
        raise LayoutChanged(f"duplicate columns {columns}")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Amount in (Thousands|Crores?|Lakhs?)", text, re.I)
    unit = {"t": "thousand", "c": "crore", "l": "lakh"}[m.group(1)[0].lower()] if m else None
    rows, total = [], None
    for tr in trs[trs.index(head_trs[-1]) + 1:]:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        expanded = []
        for c in cells:
            expanded += [c] + [None] * (int(c.get("colspan") or 1) - 1)
        if len(expanded) != width or any(c is None for c in expanded[nlabel:]):
            continue
        label_cell = expanded[0]
        label = label_cell.get_text(" ", strip=True)
        values = [c.get_text(" ", strip=True) for c in expanded[nlabel:]]
        if label in (first, "") and not any(re.search(r"\d", v) for v in values):
            continue                                # a header row
        data = dict(zip(columns, (amount(v) for v in values)))
        if re.match(r"total", label, re.I):
            total = data
            continue
        a = label_cell.find("a")
        onclick = (a.get("onclick") or a.get("onClick") or "") if a else ""
        rows.append({"label": label, "args": re.findall(r"'([^']*)'", onclick), **data})
    return {"unit": unit, "columns": columns, "rows": rows, "total": total}


def split_label(label, sep="-"):
    """'A-Gen. Admin' -> ('A', 'Gen. Admin'); '2012-PRESIDENT/...' -> ('2012', ...); '4501004479 -- NAME' -> ('4501004479', 'NAME')."""
    code, _, name = (label or "").partition(sep)
    return code.strip(), name.strip(" -") or None


def unique_ids(records):
    """Keep the first of records with the same id, numbering the rest (#2, #3): a code can repeat on a page (BEAMS lists a scheme
    once per plan category)."""
    seen, out = {}, []
    for rid, data in records:
        n = seen.get(rid, 0) + 1
        seen[rid] = n
        out.append((rid if n == 1 else f"{rid}#{n}", data))
    return out


def values(row):
    return {k: v for k, v in row.items() if k not in ("label", "args")}


# ---------------------------------------------------------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------------------------------------------------------

class SiteError(Exception):
    """The site did not answer usefully (timeout, connection error, non-200)."""


class TimeUp(Exception):
    pass


class Beams:
    def __init__(self, hub, site=SITE, keep=None):
        self.hub, self.base = hub, site.rstrip("/") + "/Beams5/BudgetMVC/MISRPT"
        self.keep = keep                # list to append {label, url, html} to, for the raw-page archive
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.last = 0.0
        self.skipped, self.failed_in_row = [], 0

    def _wait(self):
        delay = max(0.3, (self.hub.settings.get("delay_ms", 0) or 0) / 1000)
        time.sleep(max(0.0, self.last + delay - time.time()))

    def fetch(self, page, params=None, data=None, referer="MIST1.jsp", label="", tries=3):
        """GET (or POST with data) a report page -> BeautifulSoup. BEAMS is slow on old years (20-40 s a page), so the read timeout
        is long; connection errors and non-200 answers are retried with a growing wait."""
        url = f"{self.base}/{page}" + (("?" + urlencode(params, quote_via=quote)) if params else "")
        err = ""
        for attempt in range(tries):
            if time.time() > DEADLINE:
                raise TimeUp()
            self._wait()
            t = time.time()
            try:
                r = self.session.request("POST" if data is not None else "GET", url, data=data,
                                         headers={"Referer": f"{self.base}/{referer}"}, timeout=(20, 180))
            except requests.RequestException as e:
                self.last = time.time()
                self.hub.metric("http_error")
                self.hub.error()
                err = type(e).__name__
                time.sleep(5 * (attempt + 1))
                continue
            self.last = time.time()
            self.hub.metric("n_http")
            self.hub.metric("t_http_s", time.time() - t)
            if r.status_code != 200:
                self.hub.metric(f"http_{r.status_code}")
                if r.status_code in (403, 429, 503):
                    self.hub.block()
                err = f"HTTP {r.status_code}"
                time.sleep(10 * (attempt + 1))
                continue
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            if "Exception" in r.text[:2000] and len(r.text) < 2000:
                err = f"server error: {r.text.strip()[:100]}"
                time.sleep(5 * (attempt + 1))
                continue
            if self.keep is not None:
                self.keep.append({"label": label or page, "url": url, "html": r.text})
            return BeautifulSoup(r.text, "lxml")
        raise SiteError(f"{label or page}: {err}")

    def report(self, page, params=None, data=None, label="", leaf=False):
        """fetch + parse_report. A leaf (a page with nothing below it that we need) that keeps failing is noted in self.skipped and
        the item goes on; three failures in a row mean the site is down (SiteError)."""
        try:
            parsed = parse_report(self.fetch(page, params, data, label=label, tries=2 if leaf else 3))
        except SiteError as e:
            if not leaf:
                raise
            self.failed_in_row += 1
            self.skipped.append((label, str(e)))
            self.hub.metric("drill_skipped")
            if self.failed_in_row >= 3:
                raise
            return None
        self.failed_in_row = 0
        return parsed


# ---------------------------------------------------------------------------------------------------------------------------
# Department item: department -> demand -> major head -> scheme -> detail head
# ---------------------------------------------------------------------------------------------------------------------------

def fy_start(fy):
    return int(fy.split("-")[0])


def run_dept(site, fy, dept, month0):
    """All records of one department for one financial year, up to month0 (0-based)."""
    y = fy_start(fy)
    stamp = {"state": "MH", "source": "beams", "fy": fy, "upto_month": month0 + 1, "month_name": MONTHS[month0]}
    rid = lambda kind, *keys: f"{kind}:{fy}@{month0 + 1:02d}/" + "/".join(keys)
    common = {"month": month0, "type": 0}
    out = []

    top = site.report("DepartmentExp111.jsp", {"month": month0, "year": y, "type": 0}, label="departments")
    row = next((r for r in top["rows"] if split_label(r["label"])[0] == dept), None)
    if row is None:
        return out                                      # department not in this year's list
    _, dname = split_label(row["label"])
    flag = row["args"][2] if len(row["args"]) >= 3 else "C"
    out.append((rid("dept", dept), {**stamp, "kind": "dept", "unit": top["unit"], "department": dept, "department_name": dname, **values(row)}))
    base = {**stamp, "department": dept, "department_name": dname}

    demands = site.report("DemandNumExp11.jsp", {**common, "year1": y, "flag1": flag, "dept1": dept, "deptnm1": dname},
                          label=f"{dept}")
    for d in demands["rows"]:
        demand = d["label"].strip()
        out.append((rid("demand", demand), {**base, "kind": "demand", "unit": demands["unit"], "demand": demand, **values(d)}))
        dflag = d["args"][4] if len(d["args"]) >= 5 else flag
        heads = site.report("MajorHeadExp111.jsp", {"month": month0, "year1": y, "flag1": dflag, "dept1": dept, "deptnm1": dname,
                                                    "demand_no1": demand, "type": 0}, label=f"{dept} > {demand}")
        for h in heads["rows"]:
            mh, mh_name = (h["args"][3], h["args"][4]) if len(h["args"]) >= 7 else split_label(h["label"])
            hflag = h["args"][6] if len(h["args"]) >= 7 else "T"
            hb = {**base, "demand": demand, "major_head": mh, "major_head_name": mh_name}
            out.append((rid("mh", demand, mh), {**hb, "kind": "mh", "unit": heads["unit"], **values(h)}))
            if not any(h.get(f) for f in ("budgeted", "prog_expenditure", "released_dept")):
                continue                                # nothing below a head with no budget or spending
            schemes = site.report("SchemeWiseDeptExpAjx.jsp", {"month": month0, "year1": y, "flag1": hflag, "dept1": dept,
                                  "deptnm1": dname, "mh": mh, "mh_nm": mh_name, "demand_no1": demand, "type": 0},
                                  label=f"{dept} > {demand} > {mh}")
            for s in schemes["rows"]:
                if len(s["args"]) >= 4:
                    scheme, scheme_name = s["args"][2], s["args"][3]
                else:
                    scheme, scheme_name = split_label(s["label"])
                sb = {**hb, "scheme": scheme, "scheme_name": scheme_name, "scheme_label": s["label"]}
                out.append((rid("scheme", demand, mh, scheme), {**sb, "kind": "scheme", "unit": schemes["unit"], **values(s)}))
                if len(s["args"]) < 4 or not any(s.get(f) for f in ("budgeted_total", "budgeted", "prog_expenditure")):
                    continue
                details = site.report("DetailHeadExp1.jsp", {"month": month0, "flag1": s["args"][6] if len(s["args"]) >= 7 else hflag,
                                      "dept1": dept, "deptnm1": dname, "demand_no1": demand, "mh": mh, "mh_nm": mh_name,
                                      "scheme": scheme, "scheme_nm": scheme_name, "year1": y, "type": 0},
                                      label=f"{dept} > {demand} > {mh} > {scheme}", leaf=True)
                for x in (details or {}).get("rows", []):
                    head, head_name = (x["args"][-2], x["args"][-1]) if len(x["args"]) >= 13 else split_label(x["label"])
                    out.append((rid("head", demand, mh, scheme, head),
                                {**sb, "kind": "head", "unit": details["unit"], "detail_head": head, "detail_head_name": head_name, **values(x)}))
    return unique_ids(out)


# ---------------------------------------------------------------------------------------------------------------------------
# DDO item: district -> treasury -> DDO
# ---------------------------------------------------------------------------------------------------------------------------

def run_district(site, fy, district, asof):
    """All records of one district (BEAMS district code, e.g. '45') for one financial year. The figures are the current month and
    April-to-date as of the day of the scrape (`asof`, 'YYYY-MM'), final for past years."""
    y = fy_start(fy)
    stamp = {"state": "MH", "source": "beams", "fy": fy}
    tag = f"{fy}@{asof}" if asof else fy
    # this page lists the districts and sets the year in the server session; the treasury page below needs it
    dists = site.report("Department_Dist_CurMonth.jsp", {"year1": y, "year2": y + 1, "pnType": 0, "rptType": "ddoWise"},
                        label="districts")
    d = next((r for r in dists["rows"] if len(r["args"]) >= 4 and r["args"][2] == district), None)
    if d is None:
        return []
    dname = d["args"][3]
    out = [(f"dist:{tag}/{district}", {**stamp, "kind": "district", "unit": dists["unit"], "district": district,
                                       "district_name": dname, **values(d)})]
    form = {"year11": y, "year22": y + 1, "DISTRICTCODE1": district, "DISTRICTNAME1": dname, "flag1": "C", "pnType": 0,
            "rptType": "ddoWise", "link": ""}
    trs = site.report("Department_Treasury_CurMonth.jsp", data=form, label=f"{district}")
    for t in trs["rows"]:
        if len(t["args"]) < 8:
            continue
        _, _, reg, _, _, treasury, tname, flag = t["args"][:8]
        tb = {**stamp, "district": district, "district_name": dname, "region": reg, "treasury": treasury, "treasury_name": tname}
        out.append((f"tr:{tag}/{treasury}", {**tb, "kind": "treasury", "unit": trs["unit"], **values(t)}))
        ddos = site.report(f"Department_TreasuryDDO_CurMonth.jsp", {"year11": y, "year22": y + 1, "reg_cd1": reg,
                           "district_Cd1": district, "district_Nm": dname, "treasury1": treasury, "treasury_nm1": tname,
                           "flag1": flag}, data={}, label=f"{district} > {treasury}", leaf=True)
        for x in (ddos or {}).get("rows", []):
            code, name = split_label(x["label"], "--")
            out.append((f"ddo:{tag}/{code}", {**tb, "kind": "ddo", "unit": ddos["unit"], "ddo": code, "ddo_name": name, **values(x)}))
    return unique_ids(out)


def run_item(hub, payload, keep=None):
    """(records, skipped leaves) for one queue item."""
    site = Beams(hub, SITE, keep)
    if payload["report"] == "dept":
        rows = run_dept(site, payload["fy"], payload["dept"], int(payload["month0"]))
    elif payload["report"] == "ddo":
        rows = run_district(site, payload["fy"], payload["district"], payload.get("asof"))
    else:
        raise ValueError(f"unknown report {payload['report']!r}")
    return rows, site.skipped


# ---------------------------------------------------------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------------------------------------------------------

class LocalHub:
    """Stand-in for HubClient so `--local` runs without the hub: counters go to stderr, records to stdout."""
    settings = {"threads": 1, "delay_ms": 0, "paused": False}

    def __init__(self):
        self.counts = {}

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)


def local(report, fy, what):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from family import mh_month0, mh_asof
    hub = LocalHub()
    payload = {"report": report, "fy": fy, "dept": what, "month0": mh_month0(fy)} if report == "dept" else \
              {"report": report, "fy": fy, "district": what, "asof": mh_asof(fy)}
    started = time.time()
    rows, skipped = run_item(hub, payload)
    for rid, data in rows:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    for path, reason in skipped:
        print(f"skipped {path}: {reason}", file=sys.stderr)
    print(f"{payload} {len(rows)} records in {time.time() - started:.0f} s, counters {hub.counts}", file=sys.stderr)


def archive_path(key):
    return "raw/" + re.sub(r"[^A-Za-z0-9._/-]", "_", key).strip("/") + ".jsonl.gz"


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    down = 0                                            # items in a row the site did not answer for
    longest = 0.0                                       # seconds, the longest item this run has done
    try:
        # claim only while a long item still fits (work cut off by the time budget is lost); at most a quarter of the budget
        while time.time() < DEADLINE - min(max(ITEM_MINUTES * 60, 1.2 * longest), BUDGET_S / 4) and down < 2:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            pages, started = [], time.time()
            try:
                rows, skipped = run_item(hub, item["payload"], keep=pages)
            except TimeUp:
                hub.complete([item["key"]], "failed", error="time budget ran out mid-item")
                break
            except (SiteError, LayoutChanged) as e:
                down = down + 1 if isinstance(e, SiteError) else 0
                hub.failure("item", item["key"], f"{type(e).__name__}: {e}"[:300])
                hub.complete([item["key"]], "failed", error=f"{type(e).__name__}: {e}"[:200])
                continue
            down = 0
            longest = max(longest, time.time() - started)
            if pages:
                body = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pages).encode()
                hub.upload_many([(archive_path(item["key"]), gzip.compress(body), "application/gzip")])
            for rid, data in rows:
                hub.record(rid, data)
            for path, reason in skipped:
                hub.failure("drill", f"{item['key']} > {path}", reason[:300])
            hub.clear_failure("item", item["key"])
            hub.complete([item["key"]], "done", fetched=len(rows))
        if down:
            print("BEAMS is not answering from this worker; stopping so it waits and moves on", file=sys.stderr)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise
    if down:
        sys.exit(3)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(*sys.argv[2:5])
    else:
        main()
