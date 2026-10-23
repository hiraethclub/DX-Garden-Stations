#!/usr/bin/env python3
"""Converts a DX Garden stations.txt into JSON.

    python3 tools/txt_to_json.py stations/stations.txt stations/stations.json

The text file stays the master copy; this only makes a second form of it for
programs that would rather read JSON. Comment lines at the top are kept as
"header". Each station becomes:

    {"name", "url", "country", "city", "lat", "lon", "genres": [...],
     "codec", "bitrate", "rb_id", "notes"}

codec, bitrate and rb_id are read from the Notes field when present
("MP3 128k rb:<uuid>"); bitrate is null when unknown ("?k"). Standard library
only, Python 3.8 or newer.
"""
import json
import re
import sys

NOTES_RE = re.compile(r"^(?P<codec>[A-Za-z0-9+]+) (?P<br>\d+|\?)k\b")
RB_RE = re.compile(r"\brb:([0-9a-fA-F-]{36})\b")


def parse(path):
    header, stations, bad = [], [], 0
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                if not stations:
                    header.append(line[1:].strip())
                continue
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) < 6:
                bad += 1
                print("line %d: too few fields, skipped" % n, file=sys.stderr)
                continue
            parts += [""] * (8 - len(parts))
            name, url, country, city, lat, lon, genre, notes = parts[:8]
            try:
                lat_f, lon_f = float(lat), float(lon)
            except ValueError:
                lat_f = lon_f = None
            m = NOTES_RE.match(notes)
            rb = RB_RE.search(notes)
            stations.append({
                "name": name,
                "url": url,
                "country": country,
                "city": city,
                "lat": lat_f,
                "lon": lon_f,
                "genres": [g.strip() for g in genre.split(",") if g.strip()],
                "codec": m.group("codec").lower() if m else None,
                "bitrate": int(m.group("br")) if m and m.group("br").isdigit() else None,
                "rb_id": rb.group(1) if rb else None,
                "notes": notes,
            })
    return header, stations, bad


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: txt_to_json.py STATIONS.TXT OUTPUT.JSON")
    header, stations, bad = parse(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"header": header, "count": len(stations), "stations": stations},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("%d stations written to %s%s" % (
        len(stations), sys.argv[2], ", %d lines skipped" % bad if bad else ""),
        file=sys.stderr)


if __name__ == "__main__":
    main()
