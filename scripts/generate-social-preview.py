"""Render site/social-preview.svg's design as the PNG used by GitHub cards.

Requires Pillow (`python -m pip install pillow`). This is a maintainer-only
asset generator; Pillow is not a project/runtime dependency.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "site" / "social-preview.png"
WIDTH, HEIGHT = 1280, 640


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf")
        if bold else ("C:/Windows/Fonts/arial.ttf",)
    ) + (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if bold else ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",)
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#101827")
    draw = ImageDraw.Draw(image)
    draw.ellipse((820, -270, 1420, 330), fill="#142c3a")
    draw.text((76, 52), "FROM-FIRST · STAGE-BY-STAGE · DUCKDB",
              font=font(20, True), fill="#bef264")
    draw.text((76, 105), "UNSeQueL", font=font(68, True), fill="#edf4ff")
    draw.rounded_rectangle((76, 187, 316, 194), radius=3, fill="#5eead4")
    draw.text((76, 220), "Write queries in the order they run.",
              font=font(37, True), fill="#edf4ff")
    draw.text((76, 270), "A FROM-first language for SQL-like data transformations.",
              font=font(22), fill="#a9b8cc")

    draw.rounded_rectangle((76, 340, 596, 565), radius=18,
                           fill="#172235", outline="#2b3a50", width=2)
    draw.text((104, 365), "A QUERY, TOP TO BOTTOM", font=font(16, True), fill="#a9b8cc")
    for y, text, color in (
        (405, "from orders", "#5eead4"),
        (442, "filter quantity > 0", "#edf4ff"),
        (479, "group by country", "#edf4ff"),
        (516, "aggregate revenue = SUM(...) ", "#bef264"),
    ):
        draw.text((104, y), text, font=font(21), fill=color)

    draw.rounded_rectangle((626, 340, 1204, 565), radius=18,
                           fill="#172235", outline="#2b3a50", width=2)
    draw.text((662, 380), "DuckDB compiler", font=font(25, True), fill="#5eead4")
    draw.text((662, 425), "CLI · interactive REPL · line selections",
              font=font(19), fill="#a9b8cc")
    draw.text((662, 464), "VS Code syntax + run commands",
              font=font(19), fill="#a9b8cc")
    draw.text((662, 510), "Open source · MIT · feedback welcome",
              font=font(17, True), fill="#bef264")

    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
