#!/usr/bin/env python3
"""Downloads a Radio Browser snapshot in resumable chunks.

    python3 tools/fetch_snapshot.py cache/radio-browser-2026.10.24.json

Every station with coordinates and a passing last check is fetched, most
clicked first, 500 per page. Each page is saved under <output>.pages/ as it
arrives, so if the run is stopped (or --seconds runs out) the next run
carries on from the last saved page. When the last page has arrived the pages
are joined into <output>, the file rb_to_stations.py --from-raw reads.

Exit status: 0 when the snapshot is complete, 3 when there is more to fetch.
Standard library only, Python 3.8 or newer.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rb_to_stations as rb  # noqa: E402

PAGE_SIZE = rb.PAGE_SIZE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("output", help="snapshot file to write, e.g. cache/radio-browser-2026.10.24.json")
    ap.add_argument("--seconds", type=float, default=0,
                    help="stop after about this long and carry on next run (0: no limit)")
    args = ap.parse_args()

    pages = args.output + ".pages"
    os.makedirs(pages, exist_ok=True)
    done_marker = os.path.join(pages, "complete")
    start = time.time()
    api = None

    while not os.path.exists(done_marker):
        have = sorted(f for f in os.listdir(pages) if f.endswith(".json"))
        n = len(have)
        if args.seconds and time.time() - start > args.seconds:
            rb.log("stopped after %d pages; run again to carry on" % n)
            return 3
        if api is None:
            api = rb.Api()
        params = {
            "has_geo_info": "true",
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
            "limit": PAGE_SIZE,
            "offset": n * PAGE_SIZE,
        }
        page = api.get("/json/stations/search", params)
        if not isinstance(page, list):
            raise SystemExit("unexpected reply from Radio Browser")
        tmp = os.path.join(pages, "%05d.part" % n)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(page, f)
        os.replace(tmp, os.path.join(pages, "%05d.json" % n))
        rb.log("  page %d: %d stations" % (n, len(page)))
        if len(page) < PAGE_SIZE:
            open(done_marker, "w").close()
            break
        time.sleep(rb.PAGE_DELAY_S)

    raw, seen = [], set()
    for name in sorted(f for f in os.listdir(pages) if f.endswith(".json")):
        with open(os.path.join(pages, name), encoding="utf-8") as f:
            for st in json.load(f):
                uuid = st.get("stationuuid")
                if uuid in seen:
                    continue  # paging overlap while the list changed
                seen.add(uuid)
                raw.append(st)
    tmp = args.output + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    os.replace(tmp, args.output)
    rb.log("snapshot complete: %d stations in %s" % (len(raw), args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
