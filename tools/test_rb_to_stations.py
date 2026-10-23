#!/usr/bin/env python3
"""Offline tests for rb_to_stations.py (no network).

Run: python3 -I tools/test_rb_to_stations.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rb_to_stations as rb  # noqa: E402


class Args:
    def __init__(self, **kw):
        self.codecs = kw.get("codecs", ["mp3"])
        self.min_bitrate = kw.get("min_bitrate", 0)
        self.max = kw.get("max", 100)
        self.city_km = kw.get("city_km", 30.0)
        self.countries = kw.get("countries")
        self.tag = kw.get("tag")


def station(**kw):
    s = {
        "stationuuid": "u-%s" % kw.get("name", "x"),
        "name": "Sample FM",
        "url_resolved": "http://example.com/stream",
        "country": "Germany",
        "countrycode": "DE",
        "geo_lat": 52.52,
        "geo_long": 13.40,
        "codec": "MP3",
        "bitrate": 128,
        "tags": "jazz,soul,funk",
        "hls": 0,
        "lastcheckok": 1,
    }
    s.update(kw)
    return s


class Cleaning(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(rb.clean("a|b\nc\td\x00e\x85f g"), "a b c d e f g")
        self.assertEqual(rb.clean("  lots   of   space "), "lots of space")
        self.assertEqual(rb.clean(None), "")
        self.assertEqual(len(rb.clean("x" * 500, 100)), 100)
        self.assertEqual(rb.clean("Zürich ​"), "Zürich")  # Cf removed

    def test_name(self):
        self.assertEqual(rb.clean_name("#1 Hits | Radio"), "1 Hits Radio")
        self.assertEqual(rb.clean_name("##"), "")
        long = rb.clean_name("word " * 40)
        self.assertLessEqual(len(long), 100)
        self.assertTrue(long.endswith("word\u2026"))

    def test_unwrap(self):
        pp = ("https://urldefense.proofpoint.com/v2/url?u=https-3A__listen.warpradio.com_"
              "ALOHAJOE-3Ftype-3Dhttp-26nocache-3D219622&d=DwMFaQ&c=x&r=y&m=z&s=w&e=")
        self.assertEqual(rb.clean_url(pp),
                         "https://listen.warpradio.com/ALOHAJOE?type=http&nocache=219622")
        sl = ("https://na01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fstream.example"
              ".org%2Flive.mp3&data=abc&reserved=0")
        self.assertEqual(rb.clean_url(sl), "https://stream.example.org/live.mp3")
        self.assertEqual(rb.clean_url("https://a.b/x-1F_y"), "https://a.b/x-1F_y")

    def test_url(self):
        self.assertEqual(rb.clean_url(" https://a.b/c d "), "https://a.b/c%20d")
        self.assertIsNone(rb.clean_url("ftp://a.b/c"))
        self.assertIsNone(rb.clean_url("http://a.b/c|d"))
        self.assertIsNone(rb.clean_url("http://a.b/\nc"))
        self.assertIsNone(rb.clean_url(""))
        self.assertIsNone(rb.clean_url(None))

    def test_country_and_genre(self):
        self.assertEqual(rb.country_name("The United States Of America"), "United States")
        self.assertEqual(rb.country_name("The Republic Of Moldova"), "Moldova")
        self.assertEqual(rb.country_name("Islamic Republic Of Iran"), "Iran")
        self.assertEqual(rb.country_name("The Gambia"), "Gambia")
        self.assertEqual(rb.country_name("Germany"), "Germany")
        self.assertEqual(rb.genre_from_tags("jazz,Jazz, soul ,funk"), "jazz, soul")
        self.assertEqual(rb.genre_from_tags("#,#charts,pop"), "charts, pop")
        self.assertEqual(rb.genre_from_tags(""), "")
        self.assertEqual(rb.genre_from_tags(None), "")

    def test_coord(self):
        self.assertEqual(rb.coord("51.5", -90, 90), 51.5)
        self.assertIsNone(rb.coord("x", -90, 90))
        self.assertIsNone(rb.coord(None, -90, 90))
        self.assertIsNone(rb.coord(95, -90, 90))
        self.assertIsNone(rb.coord(float("nan"), -90, 90))


class CityLookup(unittest.TestCase):
    def setUp(self):
        rows = [
            # name, lat, lon, feature code, country, population
            ("Berlin", "52.52437", "13.41053", "PPLC", "DE", "3426354"),
            ("Friedenau", "52.4709", "13.3328", "PPLX", "DE", "28000"),
            ("Potsdam", "52.39886", "13.06566", "PPLA", "DE", "159456"),
            ("Teltow", "52.40310", "13.26014", "PPL", "DE", "25000"),
            ("Strasbourg", "48.58392", "7.74553", "PPLA", "FR", "274845"),
            ("Kehl", "48.57275", "7.81510", "PPL", "DE", "34000"),
            ("Suva", "-18.14161", "178.44149", "PPLC", "FJ", "77366"),
            ("Taveuni", "-16.8", "-179.97", "PPL", "FJ", "16000"),
        ]
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for i, (name, lat, lon, code, cc, pop) in enumerate(rows):
                cols = [str(i), name, name, "", lat, lon, "P", code, cc, "", "", "", "", "",
                        pop, "", "", "", ""]
                f.write("\t".join(cols) + "\n")
        self.c = rb.Cities(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_nearest(self):
        self.assertEqual(self.c.count, 7)  # Friedenau (a district) is left out
        self.assertEqual(self.c.nearest(52.50, 13.40, 30)[0], "Berlin")
        # In Friedenau: Berlin, not the district.
        self.assertEqual(self.c.nearest(52.471, 13.333, 30)[0], "Berlin")
        # Teltow is nearest, but Berlin is bigger and within 20 km.
        self.assertEqual(self.c.nearest(52.40, 13.26, 30)[0], "Berlin")
        # In Potsdam, Berlin is over 20 km away: the nearest wins.
        self.assertEqual(self.c.nearest(52.40, 13.065, 30)[0], "Potsdam")
        # Nothing within the cap: blank.
        self.assertEqual(self.c.nearest(55.0, 13.0, 30), (None, None))

    def test_same_country_preferred(self):
        # Between Strasbourg (FR) and Kehl (DE): the station's country wins.
        self.assertEqual(self.c.nearest(48.578, 7.79, 30, "FR")[0], "Strasbourg")
        self.assertEqual(self.c.nearest(48.578, 7.76, 30, "DE")[0], "Kehl")

    def test_date_line(self):
        self.assertEqual(self.c.nearest(-16.81, 179.99, 30)[0], "Taveuni")

    def test_positions(self):
        check = self.c.check_position
        self.assertEqual(check(52.50, 13.40, "DE", ""), (52.50, 13.40, "ok", "DE"))
        # Near a German town just over the border counts as in the country.
        self.assertEqual(check(48.58, 7.75, "DE", "")[2], "ok")
        # Common typing mistakes are put right.
        self.assertEqual(check(13.40, 52.50, "DE", ""), (52.50, 13.40, "swapped", "DE"))
        self.assertEqual(check(52.50, -13.40, "DE", ""), (52.50, 13.40, "flipped", "DE"))
        # In the Atlantic: the region names a town, else the capital.
        self.assertEqual(check(0.0, -30.0, "DE", "potsdam")[2], "region")
        self.assertEqual(check(0.0, -30.0, "DE", "Potsdam")[:2], (52.39886, 13.06566))
        self.assertEqual(check(0.0, -30.0, "DE", "Bavaria")[:3], (52.52437, 13.41053, "capital"))
        # A big town in the name wins over the region: in its country first,
        # else anywhere (and the country follows the town).
        self.assertEqual(check(0.0, -30.0, "DE", "Bavaria", "Radio Potsdam 1")[2:],
                         ("named", "DE"))
        self.assertEqual(check(0.0, -30.0, "FJ", "", "Strasbourg Jazz")[2:], ("named", "FR"))
        self.assertIsNone(self.c.named_town("Sample FM", "DE"))
        # On a big city in another country: the position stands and the
        # country is corrected, unless the name insists on its country.
        self.c.country_names = {"FJ": "Fiji", "DE": "Germany"}
        self.assertEqual(check(52.52, 13.41, "FJ", ""), (52.52, 13.41, "trusted", "DE"))
        self.assertEqual(check(52.52, 13.41, "FJ", "", "Radio Fiji Berlin")[2], "named")
        self.assertEqual(check(52.52, 13.41, "FJ", "", "Fiji One")[2], "capital")
        # Fiji across the date line is fine.
        self.assertEqual(check(-16.81, 179.99, "FJ", "")[2], "ok")
        # Unknown country: kept near any town, dropped at sea.
        self.assertEqual(check(52.50, 13.40, "XX", "")[2], "ok")
        self.assertIsNone(check(0.0, -30.0, "XX", ""))
        self.assertIsNone(check(0.0, -30.0, "", ""))

    def test_convert_moves_and_notes(self):
        out, skipped = rb.convert([station(name="Lost", geo_lat=0.0, geo_long=-30.0),
                                   station(name="Atlantis", geo_lat=0.0, geo_long=-30.0,
                                           countrycode="", stationuuid="u-a",
                                           url_resolved="http://example.com/a")],
                                  Args(), self.c)
        self.assertEqual(len(out), 1)
        fields = out[0][0].split(" | ")
        self.assertEqual(fields[3], "Berlin")
        self.assertEqual((fields[4], fields[5]), ("52.5244", "13.4105"))
        self.assertIn("the capital", fields[7])
        self.assertEqual(out[0][3], "capital")
        self.assertEqual(skipped, {"coordinates in the open sea, no country to fall back on": 1})


class BorderCheck(unittest.TestCase):
    """With country shapes: inside the country counts even far from towns."""

    def setUp(self):
        import json
        # A square "Germany" around Berlin/Potsdam with a hole, and a
        # square "US" far away with no towns in the cities file.
        feats = [
            {"properties": {"ISO_A2": "-99", "ISO_A2_EH": "DE"},
             "geometry": {"type": "Polygon", "coordinates": [
                 [[12, 51], [15, 51], [15, 54], [12, 54], [12, 51]],
                 [[14.5, 53.5], [14.9, 53.5], [14.9, 53.9], [14.5, 53.9], [14.5, 53.5]]]}},
            {"properties": {"ISO_A2": "US"},
             "geometry": {"type": "MultiPolygon", "coordinates": [
                 [[[-160, 60], [-150, 60], [-150, 65], [-160, 65], [-160, 60]]]]}},
        ]
        fd, self.geo = tempfile.mkstemp(suffix=".geojson")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"features": feats}, f)
        rows = [("Berlin", "52.52437", "13.41053", "PPLC", "DE", "3426354"),
                ("Washington", "38.89511", "-77.03637", "PPLC", "US", "689545")]
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for i, (name, lat, lon, code, cc, pop) in enumerate(rows):
                f.write("\t".join([str(i), name, name, "", lat, lon, "P", code, cc, "", "", "",
                                   "", "", pop, "", "", "", ""]) + "\n")
        self.c = rb.Cities(self.path)
        self.c.borders = rb.Borders(self.geo)

    def tearDown(self):
        os.unlink(self.geo)
        os.unlink(self.path)

    def test_borders(self):
        b = self.c.borders
        self.assertEqual(b.at(52.5, 13.4), "DE")
        self.assertIsNone(b.at(53.7, 14.7))  # in the hole
        self.assertEqual(b.at(62.95, -155.6), "US")
        self.assertIsNone(b.at(0, -30))

    def test_remote_but_inside(self):
        # McGrath, Alaska: no town for hundreds of km, but in the US.
        self.assertEqual(self.c.check_position(62.95, -155.6, "US", "")[2], "ok")
        # Off the coast, far from any US town: moved.
        self.assertEqual(self.c.check_position(28.2, -75.47, "US", "")[2], "capital")
        # At sea off its own coast: brought to the nearest town.
        self.assertEqual(self.c.check_position(54.2, 13.4, "DE", ""),
                         (52.52437, 13.41053, "ashore", "DE"))
        # Just offshore of a harbour town (within 30 km): left alone.
        self.assertEqual(self.c.check_position(52.70, 13.41, "DE", "")[2], "ok")
        # Just across a border, in another country's shape: left as given.
        self.c.borders.shapes.append(("PL", (15, 51, 18, 54), [[[15, 51], [18, 51], [18, 54],
                                                              [15, 54], [15, 51]]]))
        self.assertEqual(self.c.check_position(52.5, 16.5, "DE", ""), (52.5, 16.5, "ok", "DE"))


class Territory(unittest.TestCase):
    def setUp(self):
        rows = [("Simferopol", "44.95", "34.10", "PPLA", "UA", "336460"),
                ("Taganrog", "47.24", "38.90", "PPL", "RU", "253585"),
                ("Gaza", "31.50", "34.47", "PPL", "PS", "410000"),
                ("Ramallah", "31.90", "35.20", "PPLA", "PS", "38998"),
                ("Wadi al-Joz", "31.79", "35.24", "PPLX0", "PS", "8000"),
                ("Jerusalem", "31.769", "35.216", "PPLC", "IL", "801000"),
                ("Cardiff", "51.48", "-3.18", "PPLA", "GB", "447287", "WLS"),
                ("Inverness", "57.48", "-4.22", "PPL", "GB", "47000", "SCT"),
                ("Chester", "53.19", "-2.89", "PPL", "GB", "80000", "ENG"),
                ("Belfast", "54.60", "-5.93", "PPLA", "GB", "345000", "NIR"),
                ("Douglas", "54.15", "-4.48", "PPLC", "IM", "26218")]
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for i, row in enumerate(rows):
                name, lat, lon, code, cc, pop = row[:6]
                admin1 = row[6] if len(row) > 6 else ""
                f.write("\t".join([str(i), name, name, "", lat, lon, "P", code, cc, "", admin1,
                                   "", "", "", pop, "", "", "", ""]) + "\n")
        self.c = rb.Cities(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_labels(self):
        t = self.c.territory
        self.assertEqual(t(44.96, 34.11), ("UA", "Ukraine"))
        self.assertEqual(t(45.5, 33.5), ("UA", "Ukraine"))  # Crimea, no town near
        self.assertIsNone(t(47.24, 38.90))  # Taganrog, Russia
        self.assertEqual(t(31.51, 34.46), ("PS", "Gaza"))
        self.assertEqual(t(31.90, 35.21), ("PS", "Occupied West Bank"))
        self.assertEqual(t(31.792, 35.242), ("PS", "Occupied West Bank"))
        self.assertIsNone(t(31.770, 35.214))  # by GeoNames' Jerusalem point: Israel
        # UK stations by nation.
        self.assertEqual(t(51.49, -3.17), ("GB", "Wales"))
        self.assertEqual(t(57.48, -4.22), ("GB", "Scotland"))
        self.assertEqual(t(57.2, -4.9), ("GB", "Scotland"))  # Highlands, 40 km out
        self.assertEqual(t(53.19, -2.89), ("GB", "England"))
        self.assertEqual(t(54.60, -5.93), ("GB", "Northern Ireland"))
        self.assertEqual(t(54.15, -4.48), ("IM", "Isle of Man"))

    def test_convert_relabels(self):
        out, _ = rb.convert([station(name="Krym FM", country="Russia", countrycode="RU",
                                     geo_lat=44.95, geo_long=34.10)], Args(), self.c)
        f = out[0][0].split(" | ")
        self.assertEqual((f[2], f[3]), ("Ukraine", "Simferopol"))
        self.assertIn("(country: Ukraine, where the position is)", f[7])
        out, _ = rb.convert([station(name="Gaza FM", country="Palestine", countrycode="PS",
                                     geo_lat=31.50, geo_long=34.47)], Args(), self.c)
        self.assertEqual(out[0][0].split(" | ")[2], "Gaza")


class Convert(unittest.TestCase):
    def test_filters_and_format(self):
        rows = [
            station(name="Good"),
            station(name="Good", stationuuid="u-Good"),  # same uuid: ignored
            station(name="Hls", hls=1),
            station(name="Dead", lastcheckok=0),
            station(name="NoGeo", geo_lat=None),
            station(name="Null island", geo_lat=0, geo_long=0),
            station(name="Aac", codec="AAC"),
            station(name="Dup url"),
            station(name="Ftp", url_resolved="ftp://x"),
            station(name="Low", stationuuid="u-low", url_resolved="http://e/low", bitrate=32),
            station(name="RG", stationuuid="u-rg",
                    url_resolved="http://e/s?listening-from-radio-garden=1"),
        ]
        out, skipped = rb.convert(rows, Args(min_bitrate=64), None)
        self.assertEqual(len(out), 1)
        line = out[0][0]
        self.assertEqual(
            line,
            "Good | http://example.com/stream | Germany |  | 52.5200 | 13.4000 | "
            "jazz, soul | MP3 128k rb:u-Good")
        self.assertEqual(skipped["HLS stream"], 1)
        self.assertEqual(skipped["last check failed"], 1)
        self.assertEqual(skipped["no usable coordinates"], 2)
        self.assertEqual(skipped["codec AAC not selected"], 1)
        self.assertEqual(skipped["duplicate stream URL"], 1)
        self.assertEqual(skipped["no usable stream URL"], 1)
        self.assertEqual(skipped["bitrate below 64k or unknown"], 1)
        self.assertEqual(skipped["URL refers to Radio Garden"], 1)

    def test_codecs_and_max(self):
        rows = [station(name="s%d" % i, url_resolved="http://e/%d" % i,
                        codec=["MP3", "AAC", "AAC+", "OGG", "OPUS"][i % 5]) for i in range(20)]
        out, _ = rb.convert(rows, Args(codecs=["aac", "aac+", "ogg", "opus"], max=8), None)
        self.assertEqual(len(out), 8)
        self.assertTrue(all(c != "MP3" for _, c, _, _ in out))

    def test_region_fallback(self):
        # No town nearby: the region names the place, unless it is just the
        # country again or looks like a code.
        out, _ = rb.convert([station(state="Saskatchewan")], Args(), None)
        self.assertEqual(out[0][0].split(" | ")[3], "Saskatchewan")
        self.assertEqual(out[0][2], "region")
        out, _ = rb.convert([station(state="Germany")], Args(), None)
        self.assertEqual(out[0][0].split(" | ")[3], "")
        out, _ = rb.convert([station(state="R-04")], Args(), None)
        self.assertEqual(out[0][0].split(" | ")[3], "")

    def test_unknown_bitrate_note(self):
        out, _ = rb.convert([station(bitrate=0)], Args(), None)
        self.assertIn("MP3 ?k rb:", out[0][0])

    def test_test_broadcasts_skipped(self):
        self.assertTrue(rb.is_test("Radyo Dede Test Yayını"))
        self.assertTrue(rb.is_test("TEST stream"))
        self.assertFalse(rb.is_test("Testament Radio"))
        self.assertFalse(rb.is_test("Contest FM"))
        out, skipped = rb.convert([station(name="My Test")], Args(), None)
        self.assertEqual(out, [])
        self.assertEqual(skipped, {"test broadcast": 1})

    def test_fields_never_break_the_format(self):
        nasty = station(name="A|B\nC", country="X|Y", tags="a|b,c\nd")
        out, _ = rb.convert([nasty], Args(), None)
        fields = out[0][0].split(" | ")
        self.assertEqual(len(fields), 8)
        self.assertNotIn("\n", out[0][0])


if __name__ == "__main__":
    unittest.main()
