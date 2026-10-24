#!/bin/sh
# Builds a DX Garden Stations release into stations/.
#
#   tools/build.sh 2026.10.23
#
# Downloads, once, into cache/ (git-ignored):
#   - GeoNames cities1000.zip (towns over 1,000 people, CC BY 4.0)
#   - Natural Earth 1:50m country shapes (public domain), from its GitHub repo
# Then takes one snapshot of Radio Browser (all codecs the apps can use),
# saves it as cache/radio-browser-<release>.json, and builds every list
# from that same snapshot so they all agree with each other.
#
# Needs Python 3.8+ and curl. Nothing else.
set -eu

RELEASE="${1:?usage: tools/build.sh RELEASE  (e.g. 2026.10.23)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$HERE/cache"
OUT="$HERE/stations"
RB="$HERE/tools/rb_to_stations.py"
mkdir -p "$CACHE" "$OUT"

CITIES="$CACHE/cities1000.zip"
BORDERS="$CACHE/ne_50m_admin_0_countries.geojson"
RAW="$CACHE/radio-browser-$RELEASE.json"

[ -s "$CITIES" ] || curl -fL -o "$CITIES" \
    https://download.geonames.org/export/dump/cities1000.zip
[ -s "$BORDERS" ] || curl -fL -o "$BORDERS" \
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson

# The snapshot, fetched in resumable pages (re-run to carry on if stopped).
[ -s "$RAW" ] || python3 "$HERE/tools/fetch_snapshot.py" "$RAW"

# 1. Everything the desktop and Android apps can play.
python3 "$RB" --from-raw "$RAW" --geonames "$CITIES" --borders "$BORDERS" \
    --codecs mp3,aac,aac+,ogg,opus --max 40000 --release "$RELEASE" \
    -o "$OUT/stations.txt"

# 2. MP3 only, for the PS Vita and other machines that only decode MP3.
python3 "$RB" --from-raw "$RAW" --geonames "$CITIES" --borders "$BORDERS" \
    --codecs mp3 --max 10000 --release "$RELEASE" \
    -o "$OUT/stations-mp3.txt"

# 3. JSON copies, for anything that would rather not parse the text format.
python3 "$HERE/tools/txt_to_json.py" "$OUT/stations.txt" "$OUT/stations.json"
python3 "$HERE/tools/txt_to_json.py" "$OUT/stations-mp3.txt" "$OUT/stations-mp3.json"

echo "$RELEASE" > "$HERE/VERSION"
echo "Built release $RELEASE in $OUT"
