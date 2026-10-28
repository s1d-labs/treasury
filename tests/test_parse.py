"""Parsing and navigation tests on saved Koshvani pages (tests/pages, fetched 2026-10-28, FY 2026-2027 upto October)."""
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import koshvani as k  # noqa: E402

PAGES = Path(__file__).parent / "pages"
SITE = "https://koshvani.up.nic.in"


def html(name):
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


def page(name):
    return BeautifulSoup(html(name), "lxml")


def test_amounts_and_codes():
    assert k.amount("1,86,342.00") == 186342.0
    assert k.amount(".00") == 0.0
    assert k.amount("-") is None and k.amount("Zero") is None and k.amount("") is None
    with pytest.raises(k.LayoutChanged):
        k.amount("abc")
    assert k.split_code("01-> वेतन") == ("01", "वेतन")
    assert k.split_code("001- EXCISE") == ("001", "EXCISE")
    assert k.split_code("PAC--") == ("PAC", None)


def test_page_info():
    assert k.page_info(page("ExpGrant")) == {"fy": "2026-2027", "upto_month": 10, "month_name": "October"}
    assert k.page_info(page("ExpHead-001"))["fy"] == "2026-2027"
    with pytest.raises(k.LayoutChanged):
        k.page_info(page("ClearSession"))


def test_grants_sum_to_site_total():
    grants = k.parse_grants(page("ExpGrant"))
    assert len(grants) == 93 and sum(1 for g in grants if g["link"]) == 91
    assert grants[0] == {"grant": "001", "department": "आबकारी विभाग", "budget": 6836044000.0, "sanction": 6454315840.0,
                         "allotment": 4624226156.0, "exp_upto_prev": 2325210641.0, "exp_month": 24447872.0,
                         "exp_total": 2349658513.0, "link": "001"}
    assert sum(g["budget"] or 0 for g in grants) == 9990564756000.0        # the page's own "Sum:" row


def test_grant_heads_and_treasuries_agree():
    heads = k.parse_grant_heads(page("ExpHead-001"))
    assert len(heads) == 66 and len({h["scheme"] for h in heads}) == 12
    assert not any(k._is_total(h["scheme"]) for h in heads)
    first = heads[0]
    assert (first["scheme"], first["scheme_name"], first["object"], first["object_name"]) == ("2039000010300", "अधीक्षण", "01", "वेतन")
    rows = k.parse_scheme_treasuries(page("ExpTreas-001-2039000010300"))
    assert len(rows) == 311 and len({r["treasury"] for r in rows}) == 19
    assert sum(r["allotment"] for r in rows) == 514379533.0                 # the page's own "Total:" row
    assert sum(h["allotment"] for h in heads if h["scheme"] == "2039000010300") == 514379533.0


def test_ddo_pages():
    divs = k.parse_divisions(page("DivExp"))
    assert len(divs) == 83 and divs[0]["treasury"] == "08" and divs[0]["division"] == "0800" and divs[0]["link"] == "08-AGRA"
    ddos = k.parse_treasury_ddos(page("DivExpDDO-08"))
    assert len(ddos) == 121 and ddos[0]["ddo"] == "2003" and ddos[-1]["ddo"] == "--"
    grants = k.parse_ddo_grants(page("DivExpGr-08-2003"))
    assert [g["grant"] for g in grants] == ["001", "062", "080", "PAC", "REC"]
    heads = k.parse_ddo_heads(page("DivExpHd-08-2003-001"))
    assert len(heads) == 32 and heads[0]["object"] == "01"
    assert sum(h["allotment"] for h in heads) == grants[0]["allotment"]


def test_layout_change_is_detected():
    with pytest.raises(k.LayoutChanged):
        k.parse_grants(page("ExpHead-001"))                                 # wrong page: 12 columns, not 10


def test_record_ids_unique_and_stable():
    info = k.page_info(page("ExpGrant"))
    recs = k.grant_records(info, "001", k.parse_grants(page("ExpGrant")), k.parse_grant_heads(page("ExpHead-001")),
                           {"2039000010300": k.parse_scheme_treasuries(page("ExpTreas-001-2039000010300"))})
    ids = [r for r, _ in recs]
    assert len(ids) == len(set(ids)) == 1 + 66 + 311
    assert ids[0] == "g:2026-2027@10/001"
    assert "so:2026-2027@10/001/2039000010300/01/N/V" in ids
    assert "to:2026-2027@10/001/2039000010300/AGRA/01/N/V" in ids
    assert all("link" not in d for _, d in recs)
    assert k.unique_ids([("a", 1), ("a", 2), ("b", 3)]) == [("a", 1), ("a#2", 2), ("b", 3)]


# --- navigation, against a fake site that serves the saved pages ---------------------------------------------------------

class Resp:
    def __init__(self, url, text):
        self.url, self.text, self.status_code, self.encoding = url, text, 200, "utf-8"
        self.content = text.encode()


class FakeSite:
    """Serves the saved pages: main, ExpGrant, grant 001's heads, and scheme 2039000010300's treasuries. Other drill links get
    the empty Extra_plnDept page the real site gives for rows with nothing below them. The first request for that treasury
    page bounces to ClearSession, as the real site does when a session drops."""

    def __init__(self):
        self.log, self.bounced, self.sessions = [], False, 0
        heads_url = urljoin(f"{SITE}/KoshReports/ExpGrant.aspx", k.link_href(page("ExpGrant"), "001"))
        treas_url = urljoin(heads_url, k.link_href(page("ExpHead-001"), "2039000010300=अधीक्षण"))
        self.routes = {f"{SITE}/KoshvaniStatic.aspx": "main", f"{SITE}/KoshReports/ExpGrant.aspx": "ExpGrant",
                       heads_url: "ExpHead-001", treas_url: "ExpTreas-001-2039000010300"}
        self.treas_url = treas_url

    def session(self):
        site = self
        site.sessions += 1

        class Session:
            headers = {}

            def mount(self, *a):
                pass

            def request(self, method, url, data=None, headers=None, timeout=None):
                site.log.append((method, url, data))
                if url == site.treas_url and not site.bounced:
                    site.bounced = True
                    return Resp(f"{SITE}/ClearSession.aspx?aspxerrorpath=/KoshReports/ExpTreas.aspx", html("ClearSession"))
                if url in site.routes:
                    return Resp(url, html(site.routes[url]))
                if "Extra_plnDept" in url:
                    return Resp(url, '<html><body><form action="./Extra_plnDept.aspx?gno=001"></form></body></html>')
                return Resp(f"{SITE}/KoshReports/NoRecordFound.aspx", "<html>no record</html>")
        return Session()


@pytest.fixture
def fake(monkeypatch):
    site = FakeSite()
    monkeypatch.setattr(k.requests, "Session", site.session)
    monkeypatch.setattr(k.time, "sleep", lambda s: None)
    return site


def test_grant_item_walks_and_replays_after_bounce(fake):
    hub = k.LocalHub()
    info, recs, skipped = k.run_item(hub, {"report": "grants", "fy": "2026-2027", "grant": "001"})
    assert info["fy"] == "2026-2027" and len(recs) == 1 + 66 + 311 and skipped == []
    assert fake.bounced and hub.counts["session_bounce"] == 1
    # 12 schemes: 2 with no allotment or spending are not opened; 9 more give empty pages (twice each, the second in a new session)
    opened = {u for _, u, _ in fake.log if "Extra_plnDept" in u}
    assert len(opened) == 1 + 10 and hub.counts["empty_drill"] == 9
    # after the bounce: a new session opens the main page again and re-walks ExpGrant -> 001 -> scheme
    first_bounce = [u for _, u, _ in fake.log].index(fake.treas_url)
    after = [urlsplit(u).path.rsplit("/", 1)[-1] for _, u, _ in fake.log[first_bounce + 1:]]
    assert after[:4] == ["KoshvaniStatic.aspx", "ExpGrant.aspx", "Extra_plnDept.aspx", "Extra_plnDept.aspx"]
    assert hub.counts["n_http"] == len(fake.log)


def test_other_year_posts_the_year_and_refuses_wrong_year_pages(fake):
    hub = k.LocalHub()
    with pytest.raises(k.SiteError):        # the fake always shows 2026-2027: never store it as 2025-2026
        k.run_item(hub, {"report": "grants", "fy": "2025-2026", "grant": "001"})
    posts = [d for m, _, d in fake.log if m == "POST"]
    assert posts and posts[0]["ddlFinYear"] == "2025-2026" and posts[0]["__EVENTTARGET"] == "ddlFinYear"
    assert "__VIEWSTATE" in posts[0]


def test_unknown_year_is_a_layout_error(fake):
    with pytest.raises(k.LayoutChanged):
        k.run_item(k.LocalHub(), {"report": "grants", "fy": "2019-2020", "grant": "001"})


def test_a_leaf_that_keeps_failing_is_skipped_not_fatal(fake, monkeypatch):
    """One scheme the site never opens: its treasuries are missing and reported, the rest of the item is stored."""
    monkeypatch.setattr(fake, "bounced", True)                                  # no planned bounce in this test
    real = fake.session

    def session():
        sess = real()
        inner = sess.request

        def request(method, url, data=None, headers=None, timeout=None):
            if url == fake.treas_url:
                fake.log.append((method, url, data))
                return Resp(f"{SITE}/ClearSession.aspx", html("ClearSession"))
            return inner(method, url, data, headers, timeout)
        sess.request = request
        return sess
    monkeypatch.setattr(k.requests, "Session", session)
    hub = k.LocalHub()
    info, recs, skipped = k.run_item(hub, {"report": "grants", "fy": "2026-2027", "grant": "001"})
    assert len(recs) == 1 + 66 and [p for p, _ in skipped] == ["001 > 2039000010300=अधीक्षण"]
    assert hub.counts["drill_skipped"] == 1
