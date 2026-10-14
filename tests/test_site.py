import struct
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
CANONICAL = "https://absterjr.github.io/UNSeQueL/"
DOC_PAGES = [
    "docs/index.html",
    "docs/getting-started.html",
    "docs/language.html",
    "docs/expressions.html",
    "docs/cli.html",
    "docs/semantics.html",
    "docs/examples.html",
    "docs/vscode.html",
    "docs/prototype.html",
    "docs/comparison.html",
    "docs/roadmap.html",
]


class SiteMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.in_title = False
        self.canonical = None
        self.description = None
        self.stylesheets = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href")
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))
        if tag == "meta" and values.get("name") == "description":
            self.description = values.get("content")
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def parse_page(path: Path) -> SiteMetadataParser:
    parser = SiteMetadataParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


class SiteTests(unittest.TestCase):
    def test_landing_page_has_search_metadata_and_canonical_url(self):
        parser = parse_page(SITE / "index.html")
        self.assertIn("UNSeQueL", parser.title)
        self.assertIn("execution order", parser.title.lower())
        self.assertEqual(parser.canonical, CANONICAL)
        self.assertIn("FROM-first", parser.description)

    def test_every_page_has_title_canonical_description_and_stylesheet(self):
        pages = sorted(SITE.rglob("*.html"))
        self.assertEqual(len(pages), 1 + len(DOC_PAGES))
        for page in pages:
            with self.subTest(page=page.relative_to(SITE).as_posix()):
                parser = parse_page(page)
                self.assertTrue(parser.title.strip(), "missing <title>")
                self.assertIsNotNone(parser.canonical, "missing canonical link")
                self.assertTrue(parser.canonical.startswith(CANONICAL),
                                f"unexpected canonical {parser.canonical}")
                self.assertTrue(parser.description, "missing meta description")
                self.assertTrue(parser.stylesheets, "missing stylesheet link")

    def test_local_links_resolve_to_files(self):
        for page in sorted(SITE.rglob("*.html")):
            parser = parse_page(page)
            for href in parser.links:
                if href.startswith(("http://", "https://", "data:", "mailto:", "#")):
                    continue
                target = href.split("#", 1)[0]
                resolved = (page.parent / target).resolve()
                with self.subTest(page=page.name, href=href):
                    self.assertTrue(resolved.exists(), f"{href} does not resolve")

    def test_docs_pages_are_listed_and_indexed(self):
        index = parse_page(SITE / "docs" / "index.html")
        for page in DOC_PAGES:
            name = Path(page).name
            with self.subTest(page=name):
                self.assertTrue(
                    {name, f"./{name}"}.intersection(index.links),
                    f"{name} is not linked from docs/index.html")

    def test_robots_and_sitemap_point_to_the_landing_page(self):
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {CANONICAL}sitemap.xml", robots)
        tree = ET.parse(SITE / "sitemap.xml")
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text for node in tree.findall(".//s:loc", namespace)]
        self.assertEqual(locations[0], CANONICAL)
        self.assertEqual(len(locations), 1 + len(DOC_PAGES))
        for page in DOC_PAGES:
            url_path = "docs/" if page == "docs/index.html" else page
            self.assertIn(f"{CANONICAL}{url_path}", locations)

    def test_social_preview_is_a_github_sized_png(self):
        png = (SITE / "social-preview.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (1280, 640))


if __name__ == "__main__":
    unittest.main()
