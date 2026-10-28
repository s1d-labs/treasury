"""Put this family's projects in the pool and register its health checks. Run by the Setup workflow (GitHub Actions: the hub
trusts this repo, s1d-labs/<family>, through its OIDC token). Safe to run again: queue items that exist are kept.
SEED_LIMIT=n seeds only the first n items of each project (for a first pool run); unset or 0 seeds them all."""
import os

from scrape_hub_client import register_project, seed_queue, set_pool, set_probes

from family import FAMILY, PROBES, PROJECTS

limit = int(os.environ.get("SEED_LIMIT") or 0)
print("health checks:", set_probes(FAMILY, PROBES))
for p in PROJECTS:
    items = p["items"][:limit] if limit else p["items"]
    register_project(p["name"], title=p["title"], unit_label=p["unit_label"], record_label=p["record_label"],
                     expected_units=len(p["items"]), expected_records=sum(i["payload"].get("expected", 0) for i in p["items"]))
    set_pool(p["name"], FAMILY, p["task"], target=p["target"], env=p["env"], probe=p["probe"], max_workers=p.get("max_workers"))
    print(p["name"], f"queue ({len(items)} of {len(p['items'])} items):", seed_queue(p["name"], items))
