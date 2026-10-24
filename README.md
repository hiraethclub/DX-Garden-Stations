# DX Garden Stations

An open directory of internet radio stations with their places on the map, made for [DX Garden](https://github.com/hiraethclub/DX-Garden) and free for anything else that wants it.

Each station has a name, a stream URL, a country, a town, coordinates, genres and its codec and bitrate. Every list is built from one dated snapshot of [Radio Browser](https://www.radio-browser.info), a community station directory, then cleaned up: dead and HLS-only streams dropped, positions checked against country borders, swapped or sign-flipped coordinates fixed, and towns filled in.

## Files

All in `stations/`. Each release is tagged with its date (e.g. `2026.10.23`), and `VERSION` holds the current one.

| File | What's in it | For |
|---|---|---|
| `stations.txt` | MP3, AAC, AAC+, Ogg Vorbis and Opus stations | Desktop and Android players |
| `stations-mp3.txt` | MP3 stations only | PS Vita and other MP3-only players |
| `stations.json`, `stations-mp3.json` | The same lists as JSON | Anything that would rather not parse text |

The text files are the master copies. The JSON is generated from them.

## Format

One station per line, fields separated by ` | `:

```
Name | Stream URL | Country | City | Latitude | Longitude | Genre | Notes
```

- Lines starting with `#` are comments. The header says when the snapshot was taken and how the list was made.
- Genre is a comma-separated list of tags, possibly empty.
- Notes start with the codec and bitrate (`MP3 128k`, `?k` when unknown) and the Radio Browser station id (`rb:<uuid>`), followed by an explanation of anything the build changed.
- City is the nearest town within 30 km, otherwise Radio Browser's region (state or province), otherwise blank.

In JSON, each station is `{name, url, country, city, lat, lon, genres, codec, bitrate, rb_id, notes}`.

## How places are labelled

Country labels follow where a station actually is:

- Ukraine's territory, including Crimea and the occupied east and south, is labelled Ukraine.
- Palestinian territory is labelled Gaza or Occupied West Bank.
- UK stations are labelled by nation: England, Scotland, Wales or Northern Ireland. The Crown Dependencies use their own names (Isle of Man, Jersey, Guernsey).

## Stations change

A list is correct as of its snapshot date. Stations move, change address and close, and Radio Browser's entries are written by its users, so some will stop working or sit in the wrong place over time. If you spot a problem with a station, the best fix is at [Radio Browser](https://www.radio-browser.info) itself, so it reaches every app that uses it. The next release here picks it up.

## Building a release

Needs Python 3.8 or newer (standard library only) and curl:

```sh
tools/build.sh 2026.10.23
```

This downloads the GeoNames towns file and Natural Earth country borders into `cache/` (once), takes one Radio Browser snapshot, and builds every file in `stations/` from it. The snapshot is fetched page by page (`tools/fetch_snapshot.py`), so if it is interrupted, running the build again carries on where it stopped. It is kept as `cache/radio-browser-<release>.json`, so a release can be rebuilt offline.

For a custom list (one country, one genre, a bitrate floor, a quick check for dead streams), run `tools/rb_to_stations.py --help`.

`tools/test_rb_to_stations.py` runs the builder's unit tests.

## Licences

- **Station data** (`stations/`): Radio Browser's data is in the public domain. The selection, corrections and labelling added here are dedicated to the public domain under CC0 1.0 (`LICENSE-DATA`). Town names come from GeoNames under CC BY 4.0, so a copy that keeps the City field should credit GeoNames.
- **Tools** (`tools/`): GNU GPL v3 (`LICENSE`).

## Credits

- Station data: [Radio Browser](https://www.radio-browser.info) and its contributors.
- Town names: [GeoNames](https://www.geonames.org), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Country borders for position checks: [Natural Earth](https://www.naturalearthdata.com), public domain.
