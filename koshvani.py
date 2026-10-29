"""The family's `koshvani` task (see scrape.json): Uttar Pradesh treasury spending from Koshvani (https://koshvani.up.nic.in).
Try one item without the hub: python koshvani.py --local grants 2026-2027 001   |   python koshvani.py --local ddo 2026-2027 08-AGRA

Koshvani's public reports are ASP.NET drill-downs. A session must open the main page first (it sets the financial year), and
each drill link is an encrypted, session-bound URL that only works from the page that showed it (or a page near it). So the
task never stores URLs: it walks the same clicks a person makes, by link text, and when the site drops the session (it
redirects to ClearSession.aspx) it starts a new one and replays the clicks.

Two kinds of queue item (payload["report"]):
- "grants": one grant (demand) of one financial year. Grant-wise expenditure -> the grant's schemes x standard objects
  (statewide) -> each scheme's treasury x object table. Records g: (grant), so: (scheme/object), to: (treasury/object).
- "ddo": one treasury of one financial year. Division-wise expenditure -> the treasury's DDOs -> each DDO's grants -> each
  grant's schemes x objects. Records t: (treasury), td: (treasury/DDO), tdg: (DDO/grant), tdo: (DDO/grant/scheme/object).
Amounts are rupees, as the site shows them; figures are progressive up to `upto_month` of `fy`, so record ids carry both.
"""
import gzip
import json
import os
import re
import ssl
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://koshvani.up.nic.in")
BUDGET_S = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET_S - 120
ITEM_MINUTES = float(os.environ.get("ITEM_MINUTES", "25"))     # a long item of this project; set per project in family.py
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 (s1d-labs treasury)"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                      "October", "November", "December"], 1)}


# ---------------------------------------------------------------------------------------------------------------------------
# Parsing (pure; tested on saved pages in tests/pages)
# ---------------------------------------------------------------------------------------------------------------------------

class LayoutChanged(ValueError):
    """The page is not laid out the way the parser expects: fail the item instead of storing wrong numbers."""


def amount(text):
    """'1,86,342.00' -> 186342.0; '.00' -> 0.0; '-', 'Zero', '' -> None."""
    t = (text or "").replace(",", "").replace("\xa0", "").strip()
    if t in ("", "-", "Zero"):
        return None
    try:
        return float(t)
    except ValueError:
        raise LayoutChanged(f"not an amount: {text!r}")


def split_code(text, sep="-"):
    """'01-> वेतन' -> ('01', 'वेतन'); '2003-सहायक आबकारी आयुक्त' -> ('2003', ...); '001- EXCISE' -> ('001', 'EXCISE')."""
    code, _, name = (text or "").partition(sep)
    return code.strip(), name.lstrip(">").strip(" -") or None


def page_info(soup):
    """{'fy': '2026-2027', 'upto_month': 10, 'month_name': 'October'} from the report's lblInfo line."""
    lbl = soup.find(id="lblInfo")
    text = lbl.get_text(" ", strip=True) if lbl else ""
    fy = re.search(r"Fin Year:\s*(\d{4}-\d{4})", text)
    month = re.search(r"Upto Month:\s*([A-Za-z]+)", text)
    if not fy or not month or month.group(1) not in MONTHS:
        raise LayoutChanged(f"no 'Fin Year / Upto Month' line: {text[:120]!r}")
    return {"fy": fy.group(1), "upto_month": MONTHS[month.group(1)], "month_name": month.group(1)}


def table(soup, ncols):
    """Rows of the report table (#myTable) as [(text, link_text_or_None), ...], checking the header (#Table1) has ncols."""
    head = soup.select_one("#Table1 tr")
    if head is None or len(head.find_all("th")) != ncols:
        got = len(head.find_all("th")) if head else 0
        raise LayoutChanged(f"expected {ncols} columns, found {got}")
    body = soup.find("table", id="myTable")
    if body is None:
        raise LayoutChanged("no #myTable")
    out = []
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) != ncols:
            continue
        row = []
        for td in cells:
            a = td.find("a")
            row.append((td.get_text(" ", strip=True), a.get_text(" ", strip=True) if a else None))
        if any(t for t, _ in row):
            out.append(row)
    return out


def _is_total(text):
    return bool(re.match(r"(Sum|Total|SCHEMEWISE TOTAL|MINOR HEAD TOTAL|MAJOR HEAD TOTAL|SUB HEAD TOTAL)\b", text or "", re.I))


def parse_grants(soup):
    """Grant-wise expenditure (ExpGrant.aspx): [{grant, department, budget, sanction, allotment, exp_upto_prev, exp_month, exp_total, link}]."""
    out = []
    for row in table(soup, 10):
        (grant, link), (dept, _) = row[0], row[1]
        if _is_total(grant):
            continue
        b, s, a, p, m, t = (amount(c[0]) for c in row[2:8])
        out.append({"grant": grant, "department": dept if dept != "-" else None, "budget": b, "sanction": s, "allotment": a,
                    "exp_upto_prev": p, "exp_month": m, "exp_total": t, "link": link})
    return out


def parse_grant_heads(soup):
    """A grant's schemes x standard objects (ExpHead.aspx). The scheme cell is filled on its first row only."""
    out, scheme, name, link = [], None, None, None
    for row in table(soup, 12):
        first = row[0][0]
        if _is_total(first):
            continue
        if first:
            scheme, name = (first.split("=", 1) + [None])[:2]
            link = row[0][1]
        if scheme is None:
            raise LayoutChanged("object row before any scheme")
        obj, obj_name = split_code(row[1][0])
        b, s, a, p, m, t = (amount(c[0]) for c in row[4:10])
        out.append({"scheme": scheme.strip(), "scheme_name": (name or "").strip(" -") or None, "object": obj, "object_name": obj_name,
                    "plan": row[2][0], "voted": row[3][0], "budget": b, "sanction": s, "allotment": a, "exp_upto_prev": p,
                    "exp_month": m, "exp_total": t, "link": link})
    return out


def parse_scheme_treasuries(soup):
    """One scheme's treasury x object table (ExpTreas.aspx). The treasury cell is filled on its first row only."""
    out, treasury, link = [], None, None
    for row in table(soup, 9):
        first = row[0][0]
        if _is_total(first):
            continue
        if first:
            treasury, link = first, row[0][1]
        if treasury is None:
            raise LayoutChanged("object row before any treasury")
        obj, obj_name = split_code(row[1][0])
        a, p, m, t = (amount(c[0]) for c in row[4:8])
        out.append({"treasury": treasury, "object": obj, "object_name": obj_name, "plan": row[2][0], "voted": row[3][0],
                    "allotment": a, "exp_upto_prev": p, "exp_month": m, "exp_total": t, "link": link})
    return out


def parse_divisions(soup):
    """Division-wise expenditure (DivExp.aspx): one row per treasury, under its division."""
    out, division = [], None
    for row in table(soup, 4):
        if _is_total(row[0][0]) or _is_total(row[1][0]):
            continue
        if row[0][0]:
            division = row[0][0]
        code, name = split_code(row[1][0])
        dcode, dname = split_code(division or "")
        out.append({"division": dcode or None, "division_name": dname, "treasury": code, "treasury_name": name,
                    "allotment": amount(row[2][0]), "exp_total": amount(row[3][0]), "link": row[1][1]})
    return out


def parse_treasury_ddos(soup):
    """One treasury's DDOs (DivExpDDO.aspx)."""
    out = []
    for row in table(soup, 5):
        if _is_total(row[0][0]):
            continue
        code, name = split_code(row[0][0])
        out.append({"ddo": code or row[0][0], "ddo_name": name, "allotment": amount(row[1][0]), "exp_month": amount(row[2][0]),
                    "exp_total": amount(row[3][0]), "link": row[0][1]})
    return out


def parse_ddo_grants(soup):
    """One DDO's grants at one treasury (DivExpGr.aspx)."""
    out = []
    for row in table(soup, 5):
        if _is_total(row[0][0]):
            continue
        code, name = split_code(row[0][0])
        out.append({"grant": code, "grant_name": name, "allotment": amount(row[1][0]), "exp_month": amount(row[2][0]),
                    "exp_total": amount(row[3][0]), "link": row[0][1]})
    return out


def parse_ddo_heads(soup):
    """One DDO's schemes x objects under one grant at one treasury (DivExpHd.aspx)."""
    out = []
    for row in table(soup, 9):
        if _is_total(row[0][0]):
            continue
        out.append({"scheme": row[0][0], "scheme_name": row[1][0] or None, "object": row[2][0], "plan": row[3][0], "voted": row[4][0],
                    "allotment": amount(row[5][0]), "exp_month": amount(row[6][0]), "exp_total": amount(row[7][0])})
    return out


def unique_ids(pairs):
    """[(id, data)] with repeated ids made unique (#2, #3 ...): the site sometimes lists the same key on two rows."""
    seen, out = {}, []
    for rid, data in pairs:
        n = seen[rid] = seen.get(rid, 0) + 1
        out.append((rid if n == 1 else f"{rid}#{n}", data))
    return out


def _clean(d):
    return {k: v for k, v in d.items() if k != "link"}


def grant_records(info, grant, grants, heads, treasuries):
    """Records for one grant item. grants: parse_grants; heads: parse_grant_heads; treasuries: {scheme: parse_scheme_treasuries}."""
    stamp = {"fy": info["fy"], "upto_month": info["upto_month"]}
    at = f"{info['fy']}@{info['upto_month']:02d}"
    out = [(f"g:{at}/{g['grant']}", {"kind": "grant", **stamp, **_clean(g)}) for g in grants if g["grant"] == grant]
    out += [(f"so:{at}/{grant}/{h['scheme']}/{h['object']}/{h['plan']}/{h['voted']}", {"kind": "scheme_object", **stamp, "grant": grant, **_clean(h)})
            for h in heads]
    for scheme, rows in treasuries.items():
        out += [(f"to:{at}/{grant}/{scheme}/{r['treasury']}/{r['object']}/{r['plan']}/{r['voted']}",
                 {"kind": "treasury_object", **stamp, "grant": grant, "scheme": scheme, **_clean(r)}) for r in rows]
    return unique_ids(out)


def ddo_records(info, treasury, divisions, ddos, ddo_grants, ddo_heads):
    """Records for one treasury item. ddo_grants: {ddo: parse_ddo_grants}; ddo_heads: {(ddo, grant): parse_ddo_heads}."""
    stamp = {"fy": info["fy"], "upto_month": info["upto_month"], "treasury": treasury}
    at = f"{info['fy']}@{info['upto_month']:02d}"
    out = [(f"t:{at}/{treasury}", {"kind": "treasury", **stamp, **_clean(d)}) for d in divisions if d["treasury"] == treasury]
    out += [(f"td:{at}/{treasury}/{d['ddo']}", {"kind": "treasury_ddo", **stamp, **_clean(d)}) for d in ddos]
    for ddo, rows in ddo_grants.items():
        out += [(f"tdg:{at}/{treasury}/{ddo}/{g['grant']}", {"kind": "ddo_grant", **stamp, "ddo": ddo, **_clean(g)}) for g in rows]
    for (ddo, grant), rows in ddo_heads.items():
        out += [(f"tdo:{at}/{treasury}/{ddo}/{grant}/{h['scheme']}/{h['object']}/{h['plan']}/{h['voted']}",
                 {"kind": "ddo_object", **stamp, "ddo": ddo, "grant": grant, **h}) for h in rows]
    return unique_ids(out)


# ---------------------------------------------------------------------------------------------------------------------------
# Fetching: one Koshvani session, walked by link text
# ---------------------------------------------------------------------------------------------------------------------------

class Bounce(Exception):
    """The site dropped the session (ClearSession.aspx)."""


class EmptyDrill(Bounce):
    """A drill link gave an empty page: the site does this for rows with nothing below them, and sometimes for a stale session."""


class SiteError(Exception):
    """The site did not answer usefully (timeout, connection error, non-200)."""


class TimeUp(Exception):
    pass


class LegacyTLS(HTTPAdapter):
    """koshvani.up.nic.in does not support secure renegotiation, which OpenSSL 3 refuses by default. This allows that one legacy
    behaviour (SSL_OP_LEGACY_SERVER_CONNECT); certificate checks stay on."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


class Koshvani:
    def __init__(self, hub, fy, site=SITE, keep=None):
        self.hub, self.fy, self.site = hub, fy, site.rstrip("/")
        self.keep = keep            # list to append (label, url, html) to, for the raw-page archive
        self.session, self.trail, self.last = None, [], 0.0
        self.skipped, self.failed_in_row = [], 0  # leaf drills that kept failing: (path, reason)

    def _wait(self):
        delay = max(0.5, (self.hub.settings.get("delay_ms", 0) or 0) / 1000)
        time.sleep(max(0.0, self.last + delay - time.time()))

    def fetch(self, url, referer, data=None, label=""):
        if time.time() > DEADLINE:
            raise TimeUp()
        for attempt in range(3):
            self._wait()
            t = time.time()
            try:
                r = self.session.request("POST" if data else "GET", url, data=data, headers={"Referer": referer}, timeout=(20, 120))
            except requests.RequestException as e:
                self.last = time.time()
                self.hub.metric("http_error")
                self.hub.error()
                err = f"{type(e).__name__}"
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
            r.encoding = "utf-8"                    # the pages declare <meta charset="utf-8">; the header does not
            if "ClearSession" in r.url or (len(r.content) < 3000 and "ClearSession" in r.text):
                raise Bounce(label)
            if "NoRecordFound" in r.url:
                return r.url, None                  # the site's "no data for this selection" page
            soup = BeautifulSoup(r.text, "lxml")
            if "Extra_plnDept" in r.url and soup.find(id="myTable") is None:
                raise EmptyDrill(label)
            if self.keep is not None:
                self.keep.append({"label": label, "url": r.url.split("?")[0], "html": r.text})
            return r.url, soup
        raise SiteError(f"{label}: {err}")

    def start(self):
        """New session: open the main page, switch it to self.fy if that is not the default year."""
        self.session = requests.Session()
        self.session.mount("https://", LegacyTLS())
        self.session.headers["User-Agent"] = UA
        self.trail = []
        main = self.site + "/KoshvaniStatic.aspx"
        url, soup = self.fetch(main, self.site + "/", label="main")
        sel = soup.find("select", id="ddlFinYear")
        years = [o.get("value") for o in sel.find_all("option")] if sel else []
        if self.fy not in years:
            raise LayoutChanged(f"financial year {self.fy} is not offered (site offers {years})")
        chosen = sel.find("option", selected=True) or sel.find("option")
        if chosen.get("value") != self.fy:
            form = {i["name"]: i.get("value", "") for i in soup.find_all("input", attrs={"type": "hidden"}) if i.get("name")}
            form.update({"__EVENTTARGET": "ddlFinYear", "__EVENTARGUMENT": "", "ddlFinYear": self.fy})
            url, soup = self.fetch(urljoin(url, soup.find("form").get("action") or "KoshvaniStatic.aspx"), url, data=form, label="main fy")
        self.main_url, self.main = url, soup

    def open(self, steps, attempts=4, pause=10):
        """steps = [report page, link text, link text, ...]. Returns the soup of the last page. Follows links from the deepest
        page already open that shares the path (the site allows that), and replays the whole path in a new session on a bounce
        (waiting pause, 2 x pause, ... up to 60 s). None when the site says there is no data for that selection, or gives an
        empty page for it twice (fresh session too)."""
        empty = 0
        for attempt in range(attempts):
            try:
                if self.session is None:
                    self.start()
                k = 0
                while k < min(len(self.trail), len(steps)) and self.trail[k][0] == steps[k]:
                    k += 1
                if k == len(steps):
                    return self.trail[k - 1][2]
                del self.trail[k:]
                if k == 0:
                    url, soup = self.fetch(f"{self.site}/KoshReports/{steps[0]}", self.main_url, label=steps[0])
                    self.trail.append((steps[0], url, soup))
                    k = 1
                for step in steps[k:]:
                    _, purl, psoup = self.trail[-1]
                    if psoup is None:
                        return None
                    href = link_href(psoup, step)
                    if href is None:
                        raise LayoutChanged(f"no link {step!r} on {purl.split('?')[0].rsplit('/', 1)[-1]}")
                    url, soup = self.fetch(urljoin(purl, href), purl, label=" > ".join(steps[:len(self.trail) + 1]))
                    self.trail.append((step, url, soup))
                if self.trail[-1][2] is None:
                    return None
                info = page_info(self.trail[-1][2])
                if info["fy"] != self.fy:
                    raise Bounce(f"page shows {info['fy']}, not {self.fy}")
                return self.trail[-1][2]
            except EmptyDrill:
                empty += 1
                if empty >= 2:
                    self.hub.metric("empty_drill")
                    del self.trail[len(steps) - 1:]     # keep the parent page open for the next sibling
                    return None
                self.session = None
                time.sleep(5)
            except Bounce:
                self.hub.metric("session_bounce")
                self.session = None
                time.sleep(min(60, pause * 2 ** attempt))
        raise SiteError(f"session kept dropping at {' > '.join(steps)}")


    def drill(self, steps):
        """open() for a leaf of an item: one replay in a new session, then the leaf is noted in self.skipped and the item goes on
        (the site refuses some rows every time; re-trying a whole item for one bad row would never end). Three failures in a
        row mean the site is down: SiteError."""
        try:
            soup = self.open(steps, attempts=2, pause=5)
        except SiteError as e:
            self.failed_in_row += 1
            self.skipped.append((" > ".join(steps[1:]), str(e)))
            self.hub.metric("drill_skipped")
            if self.failed_in_row >= 3:
                raise
            return None
        self.failed_in_row = 0
        return soup


def link_href(soup, text):
    body = soup.find("table", id="myTable") or soup
    for a in body.find_all("a", href=True):
        if a.get_text(" ", strip=True) == text:
            return a["href"]
    return None


def rows_of(parse, soup):
    return parse(soup) if soup is not None else []


def has_money(rows):
    """False when every allotment / expenditure figure of these rows is zero or blank: the site has nothing to drill into."""
    return any(r.get(f) for r in rows for f in ("allotment", "exp_upto_prev", "exp_month", "exp_total"))


def run_grant(site, fy, grant):
    """All records for one grant: returns (info, records)."""
    soup = site.open(["ExpGrant.aspx"])
    info, grants = page_info(soup), parse_grants(soup)
    g = next((g for g in grants if g["grant"] == grant), None)
    if g is None:
        return info, []                                         # grant not in this year's list
    if not g["link"]:
        return info, grant_records(info, grant, grants, [], {})
    heads = rows_of(parse_grant_heads, site.open(["ExpGrant.aspx", g["link"]]))
    treasuries = {}
    for h in heads:
        if h["link"] and h["scheme"] not in treasuries and has_money([x for x in heads if x["scheme"] == h["scheme"]]):
            treasuries[h["scheme"]] = rows_of(parse_scheme_treasuries, site.drill(["ExpGrant.aspx", g["link"], h["link"]]))
    return info, grant_records(info, grant, grants, heads, treasuries)


def run_treasury(site, fy, treasury):
    """All records for one treasury ('08' or '08-AGRA'): returns (info, records)."""
    code = treasury.split("-")[0]
    soup = site.open(["DivExp.aspx"])
    info, divisions = page_info(soup), parse_divisions(soup)
    t = next((d for d in divisions if d["treasury"] == code), None)
    if t is None or not t["link"]:
        return info, ddo_records(info, code, divisions, [], {}, {})
    ddos = rows_of(parse_treasury_ddos, site.open(["DivExp.aspx", t["link"]]))
    ddo_grants, ddo_heads = {}, {}
    for d in ddos:
        if not d["link"] or d["ddo"] in ddo_grants or d["ddo"] == "--" or not has_money([d]):
            continue                                    # "--": spending without a DDO code; the site can't open it
        ddo_grants[d["ddo"]] = rows = rows_of(parse_ddo_grants, site.drill(["DivExp.aspx", t["link"], d["link"]]))
        for g in rows:
            if g["link"] and has_money([g]):
                ddo_heads[(d["ddo"], g["grant"])] = rows_of(parse_ddo_heads, site.drill(["DivExp.aspx", t["link"], d["link"], g["link"]]))
    return info, ddo_records(info, code, divisions, ddos, ddo_grants, ddo_heads)


def run_item(hub, payload, keep=None):
    """(info, records, skipped leaves) for one queue item."""
    site = Koshvani(hub, payload["fy"], SITE, keep)
    if payload["report"] == "grants":
        info, rows = run_grant(site, payload["fy"], payload["grant"])
    elif payload["report"] == "ddo":
        info, rows = run_treasury(site, payload["fy"], payload["treasury"])
    else:
        raise ValueError(f"unknown report {payload['report']!r}")
    return info, rows, site.skipped


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
    hub = LocalHub()
    payload = {"report": report, "fy": fy, "grant" if report == "grants" else "treasury": what}
    info, rows, skipped = run_item(hub, payload)
    for rid, data in rows:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    for path, reason in skipped:
        print(f"skipped {path}: {reason}", file=sys.stderr)
    print(f"{info} {len(rows)} records, counters {hub.counts}", file=sys.stderr)


def archive_path(key):
    return "raw/" + re.sub(r"[^A-Za-z0-9._/-]", "_", key).strip("/") + ".jsonl.gz"


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    down = 0                                            # items in a row the site did not answer for
    longest = 0.0                                       # seconds, the longest item this run has done
    try:
        # claim only while a long item still fits: work cut off by the time budget is lost (capped at a quarter of the budget,
        # so short runs, like scrape-check's, still do some)
        while time.time() < DEADLINE - min(max(ITEM_MINUTES * 60, 1.2 * longest), BUDGET_S / 4) and down < 2:
            if hub.settings.get("paused"):
                time.sleep(30)                          # the hub holds us (the family owner paused it, or back-off)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break                                   # nothing left for this project: exit, the worker asks the hub for more
            item = items[0]
            pages, started = [], time.time()
            try:
                info, rows, skipped = run_item(hub, item["payload"], keep=pages)
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
            print("Koshvani is not answering from this runner; stopping so the worker waits and moves on", file=sys.stderr)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise
    if down:
        sys.exit(3)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(*(sys.argv[2:5] + ["grants", "2026-2027", "001"][len(sys.argv[2:5]):]))
    else:
        main()
