#!/usr/bin/env python3
"""Builds a DX Garden stations.txt from a Radio Browser snapshot.

Radio Browser (radio-browser.info) is a community station directory. This
desktop script downloads a filtered list of stations once and writes a
normal stations.txt that you copy to the Vita. The Vita app never contacts
Radio Browser. A snapshot made with --bundle is shipped in the VPK as the
default station list (decided by the project owner, 2026-10-21).

Coordinates in Radio Browser are sometimes wrong (a station in the middle
of the ocean, or in another country), and so are country codes (Germany's
DE picked as DK, DZ or NE). With a GeoNames file, each station must lie
within GEO_CHECK_KM of a town in its own country, or with --borders (Natural
Earth country shapes, public domain), inside its country or within
NEAR_OWN_KM of one of its towns. If not, in order:
swapped or sign-flipped coordinates; a town of NAMED_TOWN_POP or more named
in the station's name; the coordinates themselves, if they are on a big
city in another country (the country label is corrected); Radio Browser's
region as a town name; the country's capital. Stations in the open sea that
name no country are dropped.

Radio Browser has no city field, so the city is the nearest place in a
GeoNames cities file on this computer (CC BY 4.0, geonames.org), within a
distance cap, falling back to Radio Browser's region (state or province).
Download a cities file first. cities1000 (towns over 1,000 people) leaves
far fewer stations without a city than cities15000:
    https://download.geonames.org/export/dump/cities1000.zip

Examples:
    python3 tools/rb_to_stations.py --geonames ~/Downloads/cities1000.zip \\
        -o stations.txt
    python3 tools/rb_to_stations.py --geonames cities1000.zip \\
        --countries GB,IE --codecs mp3 --min-bitrate 96 --max 500 -o uk.txt
    python3 tools/rb_to_stations.py ... --check 40

Standard library only. Python 3.8 or newer.
"""
import argparse
import concurrent.futures
import http.client
import io
import json
import math
import os
import random
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DNS_NAME = "all.api.radio-browser.info"
PAGE_SIZE = 500
PAGE_DELAY_S = 1.0
TIMEOUT_S = 30
RETRIES = 4  # per server, waiting 2, 4 and 8 s between tries
DEFAULT_MAX = 2000
DEFAULT_CITY_KM = 30.0
MAX_FILE_BYTES = 8 * 1024 * 1024  # the app's stations.txt limit

# Codec names as Radio Browser reports them, keyed by our option names.
# "ogg" covers Ogg Vorbis (and some Ogg Opus) streams.
CODECS = {"mp3": "MP3", "aac": "AAC", "aac+": "AAC+", "ogg": "OGG", "opus": "OPUS"}

# Radio Browser's long official country names, shortened for the screen.
# Keys are lower case, without a leading "The ".
COUNTRY_NAMES = {
    "united states of america": "United States",
    "united kingdom of great britain and northern ireland": "United Kingdom",
    "russian federation": "Russia",
    "republic of korea": "South Korea",
    "democratic people's republic of korea": "North Korea",
    "iran (islamic republic of)": "Iran",
    "islamic republic of iran": "Iran",
    "bolivia (plurinational state of)": "Bolivia",
    "plurinational state of bolivia": "Bolivia",
    "venezuela (bolivarian republic of)": "Venezuela",
    "bolivarian republic of venezuela": "Venezuela",
    "taiwan, republic of china": "Taiwan",
    "taiwan, province of china": "Taiwan",
    "viet nam": "Vietnam",
    "republic of moldova": "Moldova",
    "united republic of tanzania": "Tanzania",
    "lao people's democratic republic": "Laos",
    "syrian arab republic": "Syria",
    "czech republic": "Czechia",
    "democratic republic of the congo": "DR Congo",
    "state of palestine": "Palestine",
    "republic of north macedonia": "North Macedonia",
    "holy see": "Vatican City",
}

# Stream URLs that point back at Radio Garden (for example a
# "listening-from-radio-garden" parameter) suggest the entry was copied
# from it. Radio Garden's data is off-limits, so these are skipped.
RADIO_GARDEN_MARKS = ("radio-garden", "radiogarden", "radio_garden", "radio.garden")

# GeoNames feature codes that are parts of a city or no longer exist.
SKIP_FEATURES = {"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"}
BIG_CITY_KM = 20.0  # within this, the most populous city wins
GEO_CHECK_KM = 200.0  # a station must be this close to a town in its country
GEO_FIX_KM = 30.0  # a swapped/flipped position must land this close to one
# A misplaced station's coordinates are trusted (and its country label
# corrected) when they sit this close to a city at least this big: someone
# put the pin on a real city, and picked the wrong country from the list.
TRUST_CITY_KM = 15.0
TRUST_CITY_POP = 40000
NAMED_TOWN_POP = 50000  # smallest town in a station name, in its own country
NAMED_ABROAD_POP = 200000  # in another country ("Forest", "Roman" are towns too)
NEAR_OWN_KM = 30.0  # with --borders: this close to an own-country town counts as in it
# Within this of its own country, a station placed across a border is left
# exactly as Radio Browser has it (border towns, disputed areas): the
# script only corrects what is clearly a slip.
BORDER_AREA_KM = 300.0

# Country labels by where a station is, whatever Radio Browser says (the
# project owner's decision, 2026-10-21): Ukraine's territory, including
# Crimea and the occupied east and south, is labelled Ukraine; Palestinian
# territory is labelled Gaza or Occupied West Bank. Decided by the nearest
# GeoNames town (which has Crimea as UA and tells East Jerusalem from West),
# then by the Natural Earth shape, which puts Crimea in Russia, hence the
# explicit Crimea box.
# Likewise stations in the UK are labelled by nation: England, Scotland,
# Wales or Northern Ireland (GeoNames admin1 codes).
UK_NATIONS = {"ENG": "England", "SCT": "Scotland", "WLS": "Wales", "NIR": "Northern Ireland"}
# The Crown Dependencies are not in the UK: by their own names.
CROWN_DEPENDENCIES = {"IM": "Isle of Man", "JE": "Jersey", "GG": "Guernsey"}
UK_TOWN_KM = 60.0  # far from a town (Highlands, islands): nearest UK town this close
TERRITORY_TOWN_KM = 15.0
CRIMEA_BOX = (44.3, 32.4, 46.2, 36.65)  # lat0, lon0, lat1, lon1
GAZA_MAX_LON = 34.6  # Gaza lies west of this, the West Bank east of it

# Words that mark a test broadcast rather than a station.
TEST_WORDS = ("test",)

def tool_version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


USER_AGENT = ("dx-garden-stations/" + tool_version()
              + " (+https://github.com/hiraethclub/dx-garden-stations)")


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- cleaning


def clean(s, limit=200):
    """One stations.txt field: no '|', no line breaks or control characters,
    single spaces, trimmed, at most `limit` characters."""
    if s is None:
        return ""
    s = str(s)
    out = []
    for ch in s:
        if ch == "|":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            out.append(" ")
        else:
            out.append(ch)
    s = " ".join("".join(out).split())
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


def shorten(s, limit):
    """Cut at a word boundary with an ellipsis if longer than `limit`."""
    if len(s) <= limit:
        return s
    cut = s[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,-") + "\u2026"


def clean_name(s):
    # A line starting with '#' is a comment in stations.txt.
    return shorten(clean(s, 300).lstrip("#").strip(), 100)


def unwrap_url(u):
    """The real address inside an email link-scanner wrapper (Proofpoint
    urldefense v2, Outlook safelinks), which would route every connection
    through the scanner. Other URLs come back unchanged."""
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    host = parts.netloc.lower()
    query = urllib.parse.parse_qs(parts.query)
    if host.endswith("urldefense.proofpoint.com") and parts.path.startswith("/v2/url") \
            and query.get("u"):
        # v2 encodes "/" as "_" and other characters as "-XX" (hex).
        inner = query["u"][0].replace("_", "/")
        inner = "".join(
            chr(int(seg[:2], 16)) + seg[2:] if i and len(seg) >= 2 else ("-" + seg if i else seg)
            for i, seg in enumerate(inner.split("-")))
        return inner
    if host.endswith("safelinks.protection.outlook.com") and query.get("url"):
        return query["url"][0]
    return u


def clean_url(u):
    """The stream URL, or None if it can't be written safely."""
    u = unwrap_url((u or "").strip())
    if not (u.lower().startswith("http://") or u.lower().startswith("https://")):
        return None
    if any(unicodedata.category(c) == "Cc" for c in u) or "|" in u:
        return None
    u = u.replace(" ", "%20")
    if len(u) > 500:
        return None
    return u


def country_name(s):
    s = clean(s, 80)
    if s.lower().startswith("the "):
        s = s[4:]
    return COUNTRY_NAMES.get(s.lower(), s)


def genre_from_tags(tags):
    out = []
    for t in (tags or "").split(","):
        t = clean(t, 30).lstrip("#").strip()
        if t and t.lower() not in (x.lower() for x in out):
            out.append(t)
        if len(out) == 2:
            break
    return ", ".join(out)


def coord(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f < lo or f > hi:
        return None
    return f


# ---------------------------------------------------------------- borders


class Borders:
    """Which country a point is in, from Natural Earth's admin-0 country
    shapes (ne_50m_admin_0_countries.geojson). None means the sea, or a
    place too small for the 1:50m shapes."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.shapes = []  # (cc, (lon0, lat0, lon1, lat1), [outer, hole, ...])
        self.sovereign = {}  # country code -> sovereign state (SOV_A3): IM, GB -> GBR
        for feat in data.get("features", []):
            props = feat.get("properties") or {}
            # ISO_A2 is -99 for France and Norway; ISO_A2_EH has them.
            cc = props.get("ISO_A2_EH") or props.get("ISO_A2") or ""
            geom = feat.get("geometry") or {}
            if len(cc) != 2 or not cc.isalpha():
                continue
            if props.get("SOV_A3"):
                self.sovereign[cc.upper()] = props["SOV_A3"]
            polys = geom.get("coordinates") or []
            if geom.get("type") == "Polygon":
                polys = [polys]
            elif geom.get("type") != "MultiPolygon":
                continue
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                xs = [p[0] for p in poly[0]]
                ys = [p[1] for p in poly[0]]
                self.shapes.append((cc.upper(), (min(xs), min(ys), max(xs), max(ys)), poly))

    @staticmethod
    def _in_ring(ring, x, y):
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def same_state(self, a, b):
        """True if two country codes are one sovereign state (GB and IM)."""
        return a == b or (a in self.sovereign and self.sovereign.get(a) == self.sovereign.get(b))

    def at(self, lat, lon):
        for cc, (x0, y0, x1, y1), poly in self.shapes:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and self._in_ring(poly[0], lon, lat) \
                    and not any(self._in_ring(h, lon, lat) for h in poly[1:]):
                return cc
        return None


# ------------------------------------------------------------------ cities


class Cities:
    """Nearest-city lookup over a GeoNames cities file, bucketed by whole
    degrees so each lookup only scans nearby cells."""

    def __init__(self, path):
        self.cells = {}
        self.count = 0
        self.countries = set()
        self.capitals = {}  # country code -> (lat, lon, name, pop)
        self.by_name = {}   # (country code, folded name) -> (lat, lon, name, pop)
        self.big_by_name = {}  # folded name -> (lat, lon, name, pop, cc), pop >= NAMED_TOWN_POP
        self.country_names = {}  # country code -> name, filled in from the stations
        self.borders = None  # a Borders, if given
        for row in self._rows(path):
            parts = row.split("\t")
            if len(parts) < 15:
                continue
            try:
                lat, lon = float(parts[4]), float(parts[5])
            except ValueError:
                continue
            name = clean(parts[1], 60)
            # "Halle (Saale)" is "Halle" in a station's name.
            short = name.split(" (")[0].strip()
            if not name or parts[7] in SKIP_FEATURES:
                continue
            try:
                pop = int(parts[14] or 0)
            except ValueError:
                pop = 0
            key = (math.floor(lat), math.floor(lon))
            cc = parts[8]
            # (lat, lon, name, country code, population, admin1 code)
            self.cells.setdefault(key, []).append((lat, lon, name, cc, pop, parts[10]))
            self.count += 1
            self.countries.add(cc)
            entry = (lat, lon, name, pop)
            if parts[7] == "PPLC" and pop >= self.capitals.get(cc, (0, 0, "", -1))[3]:
                self.capitals[cc] = entry
            for n in {name, clean(parts[2], 60), short}:
                k = (cc, fold(n))
                if n and pop > self.by_name.get(k, (0, 0, "", -1))[3]:
                    self.by_name[k] = entry
                if n and pop >= NAMED_TOWN_POP and pop > self.big_by_name.get(
                        fold(n), (0, 0, "", -1, ""))[3]:
                    self.big_by_name[fold(n)] = entry + (cc,)

    @staticmethod
    def _rows(path):
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                if not names:
                    raise ValueError("no .txt file inside " + path)
                with z.open(names[0]) as f:
                    for line in io.TextIOWrapper(f, encoding="utf-8"):
                        yield line.rstrip("\n")
        else:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    yield line.rstrip("\n")

    def _within(self, lat, lon, max_km):
        span = int(max_km / 111.0) + 1  # degrees of latitude to scan
        coslat = math.cos(math.radians(min(89.0, abs(lat) + span)))
        lon_span = min(180, int(span / max(coslat, 0.01)) + 1)
        for dy in range(-span, span + 1):
            for dx in range(-lon_span, lon_span + 1):
                cx = (math.floor(lon) + dx + 180) % 360 - 180
                for c in self.cells.get((math.floor(lat) + dy, cx), ()):
                    d = km(lat, lon, c[0], c[1])
                    if d <= max_km:
                        yield d, c

    def near_town(self, lat, lon, max_km, country_code=None):
        """True if a town (in country_code, if given) is within max_km."""
        return any(country_code is None or c[3] == country_code
                   for _, c in self._within(lat, lon, max_km))

    def territory(self, lat, lon):
        """(country code, label) where the TERRITORY rule applies, else None."""
        near = list(self._within(lat, lon, TERRITORY_TOWN_KM))
        town = min(near, key=lambda f: f[0])[1] if near else None
        if town:
            cc = town[3]
        else:
            cc = self.borders.at(lat, lon) if self.borders else None
            la0, lo0, la1, lo1 = CRIMEA_BOX
            if cc in ("RU", None) and la0 <= lat <= la1 and lo0 <= lon <= lo1:
                cc = "UA"
        if cc == "UA":
            return "UA", "Ukraine"
        if cc == "PS":
            return "PS", "Gaza" if lon < GAZA_MAX_LON else "Occupied West Bank"
        if cc in CROWN_DEPENDENCIES:
            return cc, CROWN_DEPENDENCIES[cc]
        if cc in ("GB", None):
            if not town:
                uk = [f for f in self._within(lat, lon, UK_TOWN_KM) if f[1][3] == "GB"]
                town = min(uk, key=lambda f: f[0])[1] if uk else None
            if town and town[5] in UK_NATIONS:
                return "GB", UK_NATIONS[town[5]]
        return None

    def named_town(self, name, cc):
        """A town of NAMED_TOWN_POP or more named in a station name, as
        (lat, lon, name, pop, cc): one in country cc if there is one, else
        the biggest anywhere. Matches whole words, up to three of them."""
        words = fold("".join(ch if ch.isalnum() else " " for ch in name)).split()
        hits = []
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                key = " ".join(words[i:i + n])
                if len(key) < 4:
                    continue
                local = self.by_name.get((cc, key))
                if local and local[3] >= NAMED_TOWN_POP:
                    return local + (cc,)
                big = self.big_by_name.get(key)
                if big and big[3] >= NAMED_ABROAD_POP:
                    hits.append(big)
        return max(hits, key=lambda h: h[3]) if hits else None

    def check_position(self, lat, lon, cc, region, name=""):
        """(lat, lon, how, country code) for a station, or None to drop it.
        how is "ok", "swapped", "flipped", "named", "trusted", "region" or
        "capital"; the country code changes for "named" and "trusted"."""
        b = self.borders
        at = b.at(lat, lon) if b else None
        if cc not in self.countries:
            # No country to check against: only rule out the open sea.
            if at or self.near_town(lat, lon, NEAR_OWN_KM if b else GEO_CHECK_KM):
                return lat, lon, "ok", cc
            return None
        if b:
            # In the country (or a dependency of the same state), or near one
            # of its towns (the 1:50m coastline is coarse, so harbour towns
            # can fall just offshore).
            if (at and b.same_state(at, cc)) or self.near_town(lat, lon, NEAR_OWN_KM, cc):
                return lat, lon, "ok", cc
            # Out at sea off its own coast: to the nearest town ashore.
            if at is None:
                ashore = [f for f in self._within(lat, lon, GEO_CHECK_KM) if f[1][3] == cc]
                if ashore:
                    town = min(ashore, key=lambda f: f[0])[1]
                    return town[0], town[1], "ashore", cc
        elif self.near_town(lat, lon, GEO_CHECK_KM, cc):
            return lat, lon, "ok", cc
        for la, lo, how in ((lon, lat, "swapped"), (lat, -lon, "flipped"),
                            (-lat, lon, "flipped"), (-lat, -lon, "flipped"),
                            (lon, -lat, "swapped"), (-lon, lat, "swapped")):
            if -90 <= la <= 90 and -180 <= lo <= 180 and self.near_town(la, lo, GEO_FIX_KM, cc):
                return la, lo, how, cc
        if b and at and self.near_town(lat, lon, BORDER_AREA_KM, cc):
            return lat, lon, "ok", cc
        town = self.named_town(name, cc) if name else None
        if town:
            return town[0], town[1], "named", town[4]
        stated = fold(self.country_names.get(cc, ""))
        if not (stated and stated in fold(name)):
            big = [c for _, c in self._within(lat, lon, TRUST_CITY_KM) if c[4] >= TRUST_CITY_POP]
            if big:
                where = at or max(big, key=lambda c: c[4])[3]
                return lat, lon, "trusted", where
        if region:
            hit = self.by_name.get((cc, fold(region)))
            if hit:
                return hit[0], hit[1], "region", cc
        if cc in self.capitals:
            c = self.capitals[cc]
            return c[0], c[1], "capital", cc
        return None

    def nearest(self, lat, lon, max_km, country_code=""):
        """(name, km) of the city for a station, or (None, None) if none is
        within max_km. Within BIG_CITY_KM the most populous city wins (a
        station in a suburb belongs to the city); beyond that, the nearest.
        Cities in the station's own country are preferred over ones across
        a border."""
        span = int(max_km / 111.0) + 1  # degrees of latitude to scan
        coslat = math.cos(math.radians(min(89.0, abs(lat) + span)))
        lon_span = min(180, int(span / max(coslat, 0.01)) + 1)
        found = []
        for dy in range(-span, span + 1):
            for dx in range(-lon_span, lon_span + 1):
                cx = (math.floor(lon) + dx + 180) % 360 - 180
                for c in self.cells.get((math.floor(lat) + dy, cx), ()):
                    d = km(lat, lon, c[0], c[1])
                    if d <= max_km:
                        found.append((d, c))
        if country_code:
            same = [f for f in found if f[1][3] == country_code]
            if same:
                found = same
        if not found:
            return (None, None)
        near = [f for f in found if f[0] <= BIG_CITY_KM]
        if near:
            d, c = max(near, key=lambda f: (f[1][4], -f[0]))
        else:
            d, c = min(found, key=lambda f: f[0])
        return (c[2], d)


def fold(s):
    """Lower case without accents, for matching town names."""
    s = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(ch for ch in s if not unicodedata.combining(ch)).lower().split())


def is_test(name):
    words = "".join(ch if ch.isalnum() else " " for ch in name.lower()).split()
    return any(w in TEST_WORDS for w in words)


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


# --------------------------------------------------------------- the API


def servers_from_api():
    """Fallback when reverse DNS gives no names (common behind proxies):
    ask any Radio Browser server for the server list over HTTPS."""
    url = "https://%s/json/servers" % DNS_NAME
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        log("server list from %s failed: %s" % (url, e))
        return []
    return sorted({str(d.get("name", "")) for d in data
                   if str(d.get("name", "")).endswith(".radio-browser.info")})


def servers():
    """Radio Browser servers in random order, found the documented way: a
    DNS lookup of all.api.radio-browser.info, then each address's name."""
    try:
        infos = socket.getaddrinfo(DNS_NAME, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SystemExit("DNS lookup of %s failed: %s" % (DNS_NAME, e))
    names = set()
    for info in infos:
        ip = info[4][0]
        try:
            names.add(socket.gethostbyaddr(ip)[0])
        except OSError:
            continue
    names = [n for n in names if n.endswith(".radio-browser.info")]
    if not names:
        names = servers_from_api()
    if not names:
        raise SystemExit("no Radio Browser servers found via " + DNS_NAME)
    random.shuffle(names)
    return names


class Api:
    def __init__(self):
        self.hosts = servers()
        log("Radio Browser servers:", ", ".join(self.hosts))

    def get(self, path, params):
        query = urllib.parse.urlencode(params)
        last = None
        while self.hosts:
            host = self.hosts[0]
            url = "https://%s%s?%s" % (host, path, query)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            # A dropped connection is often a blip: retry the same server
            # a few times before giving up on it.
            for attempt in range(RETRIES):
                try:
                    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                        return json.loads(r.read().decode("utf-8"))
                except (urllib.error.URLError, OSError, ValueError) as e:
                    last = e
                    if attempt + 1 < RETRIES:
                        log("  %s failed (%s), retrying in %d s" % (host, e, 2 << attempt))
                        time.sleep(2 << attempt)
            log("  %s failed %d times, trying the next server" % (host, RETRIES))
            self.hosts.pop(0)
        raise SystemExit("all Radio Browser servers failed; last error: %s" % last)


def fetch(api, args):
    """Yields raw station objects, most-clicked first, page by page."""
    base = {
        "has_geo_info": "true",
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
        "limit": PAGE_SIZE,
    }
    if len(args.codecs) == 1:
        base["codec"] = CODECS[args.codecs[0]]
        base["codecExact"] = "true"
    if args.min_bitrate:
        base["bitrateMin"] = args.min_bitrate
    if args.tag:
        base["tag"] = args.tag
    # Enough raw results to fill the cap after filtering, but bounded.
    wanted = max(args.max * 4, 2000)
    for cc in args.countries or [None]:
        params = dict(base)
        if cc:
            params["countrycode"] = cc
        offset = 0
        got = 0
        while got < wanted:
            params["offset"] = offset
            page = api.get("/json/stations/search", params)
            if not isinstance(page, list):
                raise SystemExit("unexpected reply from Radio Browser")
            log("  %s offset %d: %d stations" % (cc or "all countries", offset, len(page)))
            for st in page:
                yield st
            got += len(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(PAGE_DELAY_S)


# ------------------------------------------------------------- conversion


def convert(stations, args, cities):
    allowed = {CODECS[c] for c in args.codecs}
    seen_uuid, seen_url = set(), set()
    skipped = {}
    out = []

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for st in stations:
        uuid = clean(st.get("stationuuid"), 40)
        if uuid in seen_uuid:
            continue  # the same station on two pages
        seen_uuid.add(uuid)
        if st.get("hls") in (1, True, "1"):
            skip("HLS stream")
            continue
        if st.get("lastcheckok") not in (1, True, "1"):
            skip("last check failed")
            continue
        lat = coord(st.get("geo_lat"), -90, 90)
        lon = coord(st.get("geo_long"), -180, 180)
        if lat is None or lon is None or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            skip("no usable coordinates")
            continue
        codec = clean(st.get("codec"), 20).upper()
        if codec not in allowed:
            skip("codec %s not selected" % (codec or "unknown"))
            continue
        try:
            bitrate = int(st.get("bitrate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        if args.min_bitrate and bitrate < args.min_bitrate:
            skip("bitrate below %dk or unknown" % args.min_bitrate)
            continue
        url = clean_url(st.get("url_resolved"))
        if not url:
            skip("no usable stream URL")
            continue
        if any(m in url.lower() for m in RADIO_GARDEN_MARKS):
            skip("URL refers to Radio Garden")
            continue
        if url in seen_url:
            skip("duplicate stream URL")
            continue
        name = clean_name(st.get("name"))
        if not name:
            skip("no name")
            continue
        if is_test(name):
            skip("test broadcast")
            continue
        country = country_name(st.get("country"))
        cc = clean(st.get("countrycode"), 2).upper()
        geo = "ok"
        if cities:
            pos = cities.check_position(lat, lon, cc, clean(st.get("state"), 60), name)
            if pos is None:
                skip("coordinates in the open sea, no country to fall back on")
                continue
            lat, lon, geo, new_cc = pos
            if new_cc != cc and new_cc in cities.country_names:
                country, cc = cities.country_names[new_cc], new_cc
            terr = cities.territory(lat, lon)
            if terr and terr[1] != country:
                cc, country = terr
                relabelled = True
            else:
                relabelled = False
        else:
            relabelled = False
        city, city_from = "", "none"
        if cities:
            found, _ = cities.nearest(lat, lon, args.city_km, cc)
            if found:
                city, city_from = found, "city"
        if not city:
            # No town nearby: Radio Browser's region (state/province), so
            # the station joins a named place rather than one named by its
            # coordinates.
            region = clean(st.get("state"), 60)
            if region and region.lower() != country.lower() and not any(
                    ch.isdigit() for ch in region):
                city, city_from = region, "region"
        notes = "%s %s rb:%s" % (codec, ("%dk" % bitrate) if bitrate else "?k", uuid)
        if geo != "ok":
            notes += " " + GEO_NOTES[geo]
        if relabelled:
            notes += " (country: %s, where the position is)" % country
        line = " | ".join([
            name, url, country, city,
            "%.4f" % lat, "%.4f" % lon,
            genre_from_tags(st.get("tags")), notes,
        ])
        seen_url.add(url)
        out.append((line, codec, city_from, geo))
        if len(out) >= args.max:
            break
    return out, skipped


GEO_NOTES = {
    "swapped": "(position fixed: latitude and longitude were swapped)",
    "flipped": "(position fixed: a coordinate had the wrong sign)",
    "named": "(position: the town in the station's name; Radio Browser's was elsewhere)",
    "ashore": "(position: the nearest town; Radio Browser's was out at sea)",
    "trusted": "(country corrected to match the position)",
    "region": "(position: Radio Browser's region; its coordinates were outside the country)",
    "capital": "(position: the capital; Radio Browser's coordinates were outside the country)",
}


def country_names(stations):
    """Country code -> the name Radio Browser uses most for it."""
    counts = {}
    for st in stations:
        cc = clean(st.get("countrycode"), 2).upper()
        name = country_name(st.get("country"))
        if cc and name:
            counts.setdefault(cc, {}).setdefault(name, 0)
            counts[cc][name] += 1
    return {cc: max(n, key=n.get) for cc, n in counts.items()}


def header(args, cities, count):
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    opts = "codecs %s" % ",".join(args.codecs)
    if args.countries:
        opts += ", countries %s" % ",".join(args.countries)
    if args.min_bitrate:
        opts += ", min bitrate %dk" % args.min_bitrate
    if args.tag:
        opts += ", tag '%s'" % args.tag
    lines = [
        "# DX Garden stations.txt generated by tools/rb_to_stations.py on %s." % when,
        "# Source: a snapshot of Radio Browser (radio-browser.info) taken %s, %d stations, %s."
        % (time.strftime("%Y-%m-%d", time.gmtime(args.snapshot)), count, opts),
        "# Cities: nearest place within %g km from GeoNames (geonames.org, CC BY 4.0)%s;" % (
            args.city_km, "" if cities else " - not used"),
        "# otherwise Radio Browser's region, otherwise blank.",
        "# Fields: Name | Stream URL | Country | City | Latitude | Longitude | Genre | Notes",
        "# Notes: codec, bitrate and the Radio Browser station id (rb:<uuid>).",
    ]
    if cities:
        lines.insert(4, "# Positions outside the station's country were checked and fixed "
                        "(see Notes).")
    if args.release:
        lines = [
            "# DX Garden Stations %s: an open radio station directory." % args.release,
            "# https://github.com/hiraethclub/dx-garden-stations",
            "# Correct as of %s. Stations come and go, so some will stop working."
            % time.strftime("%Y-%m-%d", time.gmtime(args.snapshot)),
            "#",
        ] + lines
    elif args.bundle:
        lines = [
            "# DX Garden default station list, shipped with DX Garden %s." % args.bundle,
            "# Correct as of %s. Stations come and go: any that are dead or wrong are"
            % time.strftime("%Y-%m-%d", time.gmtime(args.snapshot)),
            "# yours to edit, delete or replace. This file is yours; the app never",
            "# overwrites it. Delete it to get the default list back on the next start.",
            "#",
        ] + lines
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ check


def probe(url):
    """'ok', or a short reason the stream looks dead."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read(4096)
            if not data:
                return "no data"
            if "text/html" in ctype:
                return "HTML page, not audio"
            return "ok"
    except urllib.error.HTTPError as e:
        return "HTTP %d" % e.code
    except http.client.BadStatusLine as e:
        # Shoutcast v1 answers "ICY 200 OK", which http.client rejects.
        return "ok" if "ICY 200" in str(e) else "bad reply: %s" % e
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        return "no connection (%s)" % reason


def check(lines, n):
    sample = random.sample(lines, min(n, len(lines)))
    log("checking %d stream URLs..." % len(sample))
    dead = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for line, result in zip(sample, pool.map(lambda l: probe(l.split(" | ")[1]), sample)):
            if result != "ok":
                dead.append((line.split(" | ")[0], line.split(" | ")[1], result))
    print("Check: %d of %d sampled streams did not answer as audio." % (len(dead), len(sample)))
    for name, url, why in dead:
        print("  %s: %s (%s)" % (name, url, why))


# ------------------------------------------------------------------- main


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Build a DX Garden stations.txt from a Radio Browser snapshot.")
    ap.add_argument("-o", "--output", default="stations.txt", help="file to write")
    ap.add_argument("--geonames", help="GeoNames cities file (.zip or .txt), e.g. cities1000.zip")
    ap.add_argument("--no-city", action="store_true", help="leave every city blank")
    ap.add_argument("--borders", metavar="GEOJSON",
                    help="Natural Earth ne_50m_admin_0_countries.geojson, for checking that "
                         "each station is in its country")
    ap.add_argument("--city-km", type=float, default=DEFAULT_CITY_KM,
                    help="farthest a city can be from the station (default %(default)s km)")
    ap.add_argument("--countries", help="comma-separated ISO codes, e.g. GB,IE,FR")
    ap.add_argument("--codecs", default="mp3",
                    help="comma-separated: %s (default mp3)" % ", ".join(CODECS))
    ap.add_argument("--min-bitrate", type=int, default=0, help="in kbps; drops unknown bitrates")
    ap.add_argument("--tag", help="only stations with this tag, e.g. jazz")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX,
                    help="most stations to write, most popular first (default %(default)s)")
    ap.add_argument("--save-raw", metavar="FILE",
                    help="also save the downloaded stations (JSON), to rebuild offline")
    ap.add_argument("--from-raw", metavar="FILE",
                    help="rebuild from a file saved with --save-raw instead of downloading")
    ap.add_argument("--release", metavar="NAME",
                    help="add the header for a DX Garden Stations release, e.g. 2026.10.23")
    ap.add_argument("--bundle", metavar="VERSION",
                    help="add the header for the list shipped with this app version")
    ap.add_argument("--check", type=int, metavar="N", default=0,
                    help="after writing, test N random stream URLs and report dead ones")
    args = ap.parse_args(argv)

    args.codecs = [c.strip().lower() for c in args.codecs.split(",") if c.strip()]
    for c in args.codecs:
        if c not in CODECS:
            ap.error("unknown codec '%s'; use %s" % (c, ", ".join(CODECS)))
    if args.countries:
        args.countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        for c in args.countries:
            if len(c) != 2 or not c.isalpha():
                ap.error("country codes are two letters, like GB or US: '%s'" % c)
    if args.max < 1:
        ap.error("--max must be at least 1")
    if not args.geonames and not args.no_city:
        ap.error("give --geonames PATH (e.g. cities1000.zip from "
                 "download.geonames.org/export/dump/) or --no-city")
    return args


def main(argv=None):
    args = parse_args(argv)
    cities = None
    if args.geonames and not args.no_city:
        cities = Cities(args.geonames)
        log("GeoNames: %d cities loaded from %s" % (cities.count, args.geonames))
        if args.borders:
            cities.borders = Borders(args.borders)
            log("Borders: %d shapes loaded from %s" % (len(cities.borders.shapes), args.borders))

    if args.from_raw:
        with open(args.from_raw, encoding="utf-8") as f:
            raw = json.load(f)
        log("%d stations read from %s" % (len(raw), args.from_raw))
        # When the snapshot was taken: the saved file's time, not today.
        args.snapshot = os.path.getmtime(args.from_raw)
    else:
        args.snapshot = time.time()
        raw = list(fetch(Api(), args))
        if args.save_raw:
            with open(args.save_raw, "w", encoding="utf-8") as f:
                json.dump(raw, f)
    if cities:
        cities.country_names = country_names(raw)
    lines, skipped = convert(raw, args, cities)
    text = header(args, cities, len(lines)) + "".join(l[0] + "\n" for l in lines)
    data = text.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise SystemExit("output would be %d bytes, over the app's 8 MB limit; lower --max"
                         % len(data))
    tmp = args.output + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, args.output)

    per_codec = {}
    for _, codec, _, _ in lines:
        per_codec[codec] = per_codec.get(codec, 0) + 1
    print("Wrote %d stations to %s (%d KB)." % (len(lines), args.output, len(data) // 1024))
    with_city = sum(1 for _, _, c, _ in lines if c == "city")
    with_region = sum(1 for _, _, c, _ in lines if c == "region")
    geo = {}
    for _, _, _, g in lines:
        geo[g] = geo.get(g, 0) + 1
    print("  Place: nearest city within %g km %d, Radio Browser region %d, none "
          "(named by coordinates on the Vita) %d."
          % (args.city_km, with_city, with_region, len(lines) - with_city - with_region))
    print("  Per codec: " + ", ".join("%s %d" % kv for kv in sorted(per_codec.items())))
    if cities:
        print("  Positions: ok %d, swapped %d, sign flipped %d, brought ashore %d, "
              "town in name %d, country corrected %d, moved to region %d, moved to "
              "capital %d." % (geo.get("ok", 0), geo.get("swapped", 0), geo.get("flipped", 0),
                               geo.get("ashore", 0), geo.get("named", 0),
                               geo.get("trusted", 0), geo.get("region", 0),
                               geo.get("capital", 0)))
    if skipped:
        print("Skipped:")
        for why, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print("  %6d  %s" % (n, why))
    if args.check:
        check([l[0] for l in lines], args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
