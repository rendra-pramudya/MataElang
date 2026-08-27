from datetime import UTC, datetime

import pytest

from mataelang.fetchers import rss
from mataelang.fetchers.rss import Gazetteer, RssFetcher

RSS_DOC = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Wire - World</title>
  <item>
    <title>Shelling reported near Kyiv, officials say</title>
    <link>https://example.org/a</link>
    <pubDate>Wed, 27 Aug 2026 04:00:00 GMT</pubDate>
    <description>&lt;p&gt;Casualty figures are &lt;b&gt;not&lt;/b&gt;
      confirmed.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Aid convoys stalled across Sudan as evacuation routes close</title>
    <link>https://example.org/b</link>
    <pubDate>Wed, 27 Aug 2026 03:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Quarterly earnings beat expectations</title>
    <link>https://example.org/c</link>
    <pubDate>Wed, 27 Aug 2026 02:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ATOM_DOC = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Wire</title>
  <entry>
    <title>Explosion reported in Beirut</title>
    <link rel="alternate" href="https://example.org/atom-1"/>
    <updated>2026-08-27T05:30:00Z</updated>
    <summary>Emergency services are responding.</summary>
  </entry>
</feed>
"""


@pytest.fixture(scope="module")
def gaz() -> Gazetteer:
    return Gazetteer.load()


def test_gazetteer_prefers_city_over_country(gaz):
    # "Kyiv" (city) beats "Ukraine" (country) — the city is the more specific claim.
    p = gaz.locate("Fighting near Kyiv in eastern Ukraine")
    assert p.name == "Kyiv" and p.kind == "city" and p.confidence == "high"


def test_gazetteer_country_is_low_confidence(gaz):
    p = gaz.locate("Talks continue in Sudan")
    assert p.name == "Sudan" and p.kind == "country" and p.confidence == "low"


def test_gazetteer_longest_match_wins(gaz):
    # "South Sudan" must not be swallowed by "Sudan".
    assert gaz.locate("Fuel shortages across South Sudan").name == "South Sudan"


def test_gazetteer_word_boundaries(gaz):
    # Acceptance: "Chad" must not fire on "Chadwick" (phase-1 §8).
    assert gaz.locate("Chadwick scored twice on Saturday") is None
    assert gaz.locate("Aid reaches Chad this week").name == "Chad"


def test_gazetteer_short_aliases_are_case_sensitive(gaz):
    # "US" is a country; "us" is a pronoun.
    assert gaz.locate("The US announced sanctions").name == "United States"
    assert gaz.locate("A spokesperson told us nothing") is None


def test_gazetteer_alias_resolves_to_canonical(gaz):
    assert gaz.locate("A statement from the Kremlin").name == "Moscow"
    assert gaz.locate("Reaction in Britain was muted").name == "United Kingdom"


def test_gazetteer_unmatched_returns_none(gaz):
    assert gaz.locate("Quarterly earnings beat expectations") is None


@pytest.mark.parametrize(
    "text,sev",
    [
        ("Council approves new bike lanes", 1),
        ("Protest march closes the ring road", 2),
        ("Evacuation ordered as waters rise", 2),
        ("Shelling reported overnight", 3),
        ("Dozens killed in explosion", 3),
        ("Major offensive launched at dawn", 4),
        ("Government declares state of emergency", 4),
        ("Reports of genocide investigated", 5),
        ("Nuclear test confirmed", 5),
    ],
)
def test_severity_tiers(text, sev):
    assert rss.severity_for(text) == sev


def test_severity_takes_the_highest_tier():
    assert rss.severity_for("Protest turns to massacre") == 4


def test_strip_html():
    assert rss.strip_html("<p>Hello &amp;   <b>world</b></p>") == "Hello & world"
    assert rss.strip_html(None) == ""


def test_parse_feed_rss(gaz):
    events, stats = rss.parse_feed(
        RSS_DOC, "https://example.org/feed.xml", gaz=gaz, ttl=43200, max_items=50
    )
    assert stats["items"] == 3
    assert stats["kept"] == 2
    assert stats["no_location"] == 1  # the earnings item is dropped, never parked at 0,0

    first = events[0]
    assert first.type == "news" and first.source == "Test Wire - World"
    assert first.severity == 3 and first.lat == 50.45
    assert first.ts == datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    assert first.payload["geocode"] == {"match": "Kyiv", "kind": "city", "confidence": "high"}
    assert first.payload["ts_source"] == "feed"
    assert first.payload["summary"] == "Casualty figures are not confirmed."
    assert events[1].payload["geocode"]["confidence"] == "low"


def test_parse_feed_atom(gaz):
    events, stats = rss.parse_feed(
        ATOM_DOC, "https://example.org/atom", gaz=gaz, ttl=43200, max_items=50
    )
    assert stats["kept"] == 1
    e = events[0]
    assert e.url == "https://example.org/atom-1"  # Atom puts the link in an attribute
    assert e.source == "Atom Wire" and e.severity == 3
    assert e.ts == datetime(2026, 8, 27, 5, 30, tzinfo=UTC)


def test_parse_feed_ids_are_stable_and_prefixed(gaz):
    a, _ = rss.parse_feed(RSS_DOC, "u", gaz=gaz, ttl=1, max_items=50)
    b, _ = rss.parse_feed(RSS_DOC, "u", gaz=gaz, ttl=1, max_items=50)
    assert [e.id for e in a] == [e.id for e in b]
    assert all(e.id.startswith("rss:") for e in a)


def test_parse_feed_respects_max_items(gaz):
    events, _ = rss.parse_feed(RSS_DOC, "u", gaz=gaz, ttl=1, max_items=1)
    assert len(events) == 1


def test_parse_feed_missing_date_falls_back_and_says_so(gaz):
    doc = RSS_DOC.replace("<pubDate>Wed, 27 Aug 2026 04:00:00 GMT</pubDate>", "")
    events, _ = rss.parse_feed(doc, "u", gaz=gaz, ttl=1, max_items=50)
    assert events[0].payload["ts_source"] == "fetch"


def test_parse_feed_rejects_malformed_xml(gaz):
    from mataelang.fetchers.base import FetchError

    with pytest.raises(FetchError):
        rss.parse_feed("<rss><channel>", "https://example.org/x", gaz=gaz, ttl=1, max_items=5)


def test_fetcher_metadata():
    f = RssFetcher()
    assert f.name == "rss" and f.ttl_default == 12 * 3600 and f.min_interval_s >= 60
