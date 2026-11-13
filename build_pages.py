"""Build a static demo into a new directory."""

import argparse
import json
import shutil
from pathlib import Path

from wallpaper_app import MAX_COUNT
from wallpaper_prompts import PRESETS
from pilot import ESTIMATED_IMAGE_USD, ROOT


def build(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    options = {
        "presets": {key: item[0] for key, item in PRESETS.items()},
        "price_usd": str(ESTIMATED_IMAGE_USD),
        "max_count": MAX_COUNT,
    }
    page = (ROOT / "wallpaper_ui.html").read_text(encoding="utf-8")
    entry = '  <script src="./assets/studio.js" defer></script>'
    if page.count(entry) != 1:
        raise ValueError("Cannot find the studio script in wallpaper_ui.html")
    demo_scripts = (
        f'  <script id="demo-options" type="application/json">{json.dumps(options)}</script>\n'
        '  <script src="./demo.js" defer></script>\n'
    )
    (output / "index.html").write_text(page.replace(entry, demo_scripts + entry), encoding="utf-8")
    shutil.copytree(ROOT / "assets", output / "assets")
    shutil.copytree(ROOT / "pages" / "examples", output / "examples")
    shutil.copyfile(ROOT / "pages" / "demo.js", output / "demo.js")
    (output / ".nojekyll").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="path to a directory that does not yet exist")
    build(parser.parse_args().output)
