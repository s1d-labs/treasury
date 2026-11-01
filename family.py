"""What this family scrapes. Uttar Pradesh, Koshvani (https://koshvani.up.nic.in), answers GitHub-hosted runners directly.
Maharashtra, BEAMS (https://beams.mahakosh.gov.in), answers only from India: its projects run with proxy="IN" (one of the hub's
Mumbai proxies per worker). Rajasthan IFMS is next; Bihar CTMIS and Karnataka's treasury host don't answer even from India (README).

FAMILY must equal the repo name (s1d-labs/<FAMILY>). Each project is one queue of work on one site; `target` is the host it hits.
"""
import datetime
import json
from pathlib import Path

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]
KOSHVANI = "koshvani.up.nic.in"
BEAMS = "beams.mahakosh.gov.in"


def financial_years(today=None, n=5):
    """Koshvani offers the current April-March year and the four before it, newest first: ['2026-2027', ..., '2022-2023']."""
    today = today or datetime.date.today()
    start = today.year if today.month >= 4 else today.year - 1
    return [f"{y}-{y + 1}" for y in range(start, start - n, -1)]


FYS = financial_years()
CURRENT_FY = FYS[0]
ROUND = datetime.date.today().strftime("%Y-%m")     # the current year is re-scraped each month: its keys carry the month


def key(fy, what):
    """Past years are final: one key each. The current year's figures grow every month: one key per month it is seeded."""
    return f"{fy}/{ROUND}/{what}" if fy == CURRENT_FY else f"{fy}/{what}"


# Grant (demand) numbers with a drill-down on Grant-wise expenditure, FY 2026-2027 (056, 064, 088, 090 are not used).
# A grant missing in some year just produces no records.
GRANTS = [f"{n:03d}" for n in range(1, 96) if n not in (56, 64, 88, 90)]

# Treasuries on Division-wise expenditure, FY 2026-2027 (code-name as the site shows it).
TREASURIES = ["08-AGRA", "06-ALIGARH", "10-ETAH", "68-FIROZABAD", "78-HATHRAS", "88-KASGANJ", "09-MAINPURI", "07-MATHURA",
              "74-AMBEDKARNAGAR", "49-AYODHYA", "54-BARABANKI", "52-SULTANPUR", "34-AZAMGARH", "31-BALLIA", "66-MAU", "26-BANDA",
              "87-CHITRAKOOT", "25-HAMIRPUR", "71-MAHOBA", "13-BADAUN", "11-BAREILLY", "16-PILIBHIT", "15-SHAHJAHANPUR",
              "33-BASTI", "80-SANT KABIR NAGR", "67-SIDDHARTH NAGAR", "01-CYBER TREASURY", "51-BAHRAICH", "79-BALRAMPUR",
              "50-GONDA", "85-SRAWASTI", "35-DEORIA", "32-GORAKHPUR", "73-KUSHI NAGAR", "70-MAHARAJGANJ", "23-JHANSI-MAIN",
              "58-LALITPUR", "24-ORAI", "81-AURAIYA", "19-ETAWAH", "18-FARRUKHABAD", "84-KANNAUJ", "62-KANPUR DEHAT",
              "20-KANPUR NAGAR", "89-AMETHI", "47-HARDOI", "97-IRLA CHEQUE", "48-KHERI", "43-LUCKNOW COLL.", "60-LUCKNOW-JB",
              "45-RAEBARELI", "98-RAJBHAVAN", "46-SITAPUR", "44-UNNAO TREASURY", "95-VIDHAN PRISHAD", "96-VS-SECRETARIAT",
              "83-BAGHPAT SADAR", "05-BULANDSHAHR", "76-G.B.NAGAR", "59-GHAZIABAD", "90-HAPUR", "04-MEERUT SADAR", "72-BHADOHI",
              "28-MIRZAPUR", "69-TRY.SONBHADRA", "86-AMROHA", "12-BIJNOR", "14-MORADABAD", "17-RAMPUR", "92-SAMBHAL",
              "63-PAO,NEW DELHI", "21-FATEHPUR", "82-KAUSHAMBI", "53-PRATAPGARH", "64-PRAYAGRAJ-CIV", "22-PRAYAGRAJ-COLL",
              "03-MUZAFFARNAGAR", "02-SAHARANPUR", "91-SHAMLI", "77-CHANDAULI", "30-GHAZIPUR", "29-JAUNPUR", "27-VARANASI"]

def mh_month0(fy, today=None):
    """BEAMS department reports run up to a chosen month (0-based). A past year: March (2), the full year. The current year: the
    last complete month (October = 9 when seeded in November); in April, April itself."""
    today = today or datetime.date.today()
    if fy != financial_years(today)[0]:
        return 2
    return 3 if today.month == 4 else (today.month - 2) % 12


def mh_asof(fy, today=None):
    """BEAMS DDO reports show the current month and April-to-date as of the day they are read: None for a past (final) year, the
    scrape month 'YYYY-MM' for the current one (record ids carry it, so each month's snapshot is kept)."""
    today = today or datetime.date.today()
    return today.strftime("%Y-%m") if fy == financial_years(today)[0] else None


# BEAMS department codes (Department-wise expenditure, FY 2026-2027). A code missing in some year just produces no records.
MH_DEPTS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X",
            "Y", "ZA", "ZC", "ZD", "ZE", "ZF", "ZG", "ZH", "ZI"]

# BEAMS districts (code, name) of the district-wise DDO report; P.A.O. is the Mumbai Pay and Accounts Office.
MH_DISTRICTS = [("54", "AHILYANAGAR"), ("62", "AKOLA"), ("61", "AMRAVATI"), ("33", "BEED"), ("47", "BHANDARA"), ("63", "BULDHANA"),
                ("48", "CHANDRAPUR"), ("31", "CHHATRAPATI SAMBHAJINAGAR"), ("81", "Cyber Treasury"), ("35", "DHARASHIV"),
                ("52", "DHULE"), ("49", "GADCHIROLI"), ("44", "GONDIA"), ("38", "HINGOLI"), ("53", "JALGAON"), ("36", "JALNA"),
                ("26", "KOLHAPUR"), ("37", "LATUR"), ("46", "NAGPUR"), ("34", "NANDED"), ("55", "NANDURBAR"), ("51", "NASHIK"),
                ("71", "P.A.O."), ("16", "PALGHAR"), ("32", "PARBHANI"), ("22", "PUNE"), ("13", "RAIGAD"), ("14", "RATNAGIRI"),
                ("24", "SANGLI"), ("23", "SATARA"), ("15", "SINDHUDURG"), ("25", "SOLAPUR"), ("12", "THANE"), ("45", "WARDHA"),
                ("65", "WASHIM"), ("64", "YAVATMAL")]

PROJECTS = [
    {
        "name": f"{FAMILY}-up-grants",
        "title": "UP Koshvani: grant -> scheme -> treasury spending",
        "task": "koshvani",
        "target": KOSHVANI,
        "env": {"SITE": f"https://{KOSHVANI}", "ITEM_MINUTES": "25"},   # a long item (avg 3 min, longest seen 21)
        "probe": None,
        "max_workers": 2,                        # one session per worker on a slow, stateful site
        "unit_label": "grant-year", "record_label": "row",
        "items": [{"key": key(fy, f"grant-{g}"), "payload": {"report": "grants", "fy": fy, "grant": g}}
                  for fy in FYS for g in GRANTS],
    },
    {
        "name": f"{FAMILY}-up-ddo",
        "title": "UP Koshvani: treasury -> DDO -> grant -> scheme spending",
        "task": "koshvani",
        "target": KOSHVANI,
        "env": {"SITE": f"https://{KOSHVANI}", "ITEM_MINUTES": "50"},   # a long item (avg 11 min, longest seen 45)
        "probe": None,
        "max_workers": 2,
        "unit_label": "treasury-year", "record_label": "row",
        "items": [{"key": key(fy, f"treasury-{t.split('-')[0]}"), "payload": {"report": "ddo", "fy": fy, "treasury": t.split("-")[0], "name": t.split("-", 1)[1]}}
                  for fy in FYS for t in TREASURIES],
    },
    {
        "name": f"{FAMILY}-mh-dept",
        "title": "Maharashtra BEAMS: department -> demand -> major head -> scheme -> detail head",
        "task": "beams",
        "target": BEAMS,
        "env": {"SITE": f"https://{BEAMS}", "ITEM_MINUTES": "40"},
        "probe": None,
        "proxy": "IN",                           # BEAMS answers only from India
        "max_workers": 2,                        # the family borrows at most 4 proxies; 2 per MH project
        "unit_label": "department-year", "record_label": "row",
        "items": [{"key": key(fy, f"dept-{d}"), "payload": {"report": "dept", "fy": fy, "dept": d, "month0": mh_month0(fy)}}
                  for fy in FYS for d in MH_DEPTS],
    },
    {
        "name": f"{FAMILY}-mh-ddo",
        "title": "Maharashtra BEAMS: district -> treasury -> DDO received and spent",
        "task": "beams",
        "target": BEAMS,
        "env": {"SITE": f"https://{BEAMS}", "ITEM_MINUTES": "10"},
        "probe": None,
        "proxy": "IN",
        "max_workers": 2,
        "unit_label": "district-year", "record_label": "row",
        "items": [{"key": key(fy, f"district-{c}"), "payload": {"report": "ddo", "fy": fy, "district": c, "name": n, "asof": mh_asof(fy)}}
                  for fy in FYS for c, n in MH_DISTRICTS],
    },
]

# Health checks the hub runs every minute (work holds while one fails). None here on purpose: the hub checks from the team
# server, whose address Koshvani drops and which BEAMS (India only) never answers, so a probe would always fail and hold all work. The task itself fails items
# (and they are retried) when the site does not answer.
PROBES = []
