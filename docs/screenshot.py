"""
screenshot.py — Renders docs/index.html and saves each card as a PNG for the README.
Run from the project root:
    python docs/screenshot.py
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

HTML_FILE = Path(__file__).parent / "index.html"
ASSETS    = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)

SECTIONS = [
    ("header",    ".header",    "banner.png"),
    ("card1",     ".card:nth-child(2)", "how_it_works.png"),
    ("card2",     ".card:nth-child(3)", "old_vs_new.png"),
    ("card3",     ".card:nth-child(4)", "providers.png"),
    ("card4",     ".card:nth-child(5)", "setup.png"),
    ("banner",    ".free-banner",       "free_banner.png"),
]

def screenshot_sections() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 960, "height": 800})
        page.goto(HTML_FILE.as_uri())

        # Wait for fonts + animations to settle
        page.wait_for_timeout(1500)

        # Full-page single image for README hero
        page.set_viewport_size({"width": 960, "height": 800})
        full_path = ASSETS / "full_preview.png"
        page.screenshot(path=str(full_path), full_page=True)
        print(f"Saved full preview -> {full_path}")

        # Individual sections
        for _name, selector, filename in SECTIONS:
            try:
                el = page.locator(selector).first
                out = ASSETS / filename
                el.screenshot(path=str(out))
                print(f"Saved {filename}")
            except Exception as exc:
                print(f"  Skipped {filename}: {exc}")

        browser.close()
        print("Done. Images saved to docs/assets/")

if __name__ == "__main__":
    screenshot_sections()
