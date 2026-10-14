import struct
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
CANONICAL = "https://absterjr.github.io/UNSeQueL/"


class SiteMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.in_title = False
        self.canonical = None
        self.description = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href")
        if tag == "meta" and values.get("name") == "description":
            self.description = values.get("content")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class SiteTests(unittest.TestCase):
    def test_landing_page_has_search_metadata_and_canonical_url(self):
        parser = SiteMetadataParser()
        parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
        self.assertIn("UNSeQueL", parser.title)
        self.assertIn("execution order", parser.title.lower())
        self.assertEqual(parser.canonical, CANONICAL)
        self.assertIn("FROM-first", parser.description)

    def test_robots_and_sitemap_point_to_the_landing_page(self):
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {CANONICAL}sitemap.xml", robots)
        tree = ET.parse(SITE / "sitemap.xml")
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text for node in tree.findall(".//s:loc", namespace)]
        self.assertEqual(locations, [CANONICAL])

    def test_social_preview_is_a_github_sized_png(self):
        png = (SITE / "social-preview.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (1280, 640))


if __name__ == "__main__":
    unittest.main()
