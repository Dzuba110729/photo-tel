"""Allocate frames across themes and vary each theme across visual dimensions."""

from random import Random

from pilot import make_payload, REQUEST_SIZE


DEFAULT_TONE = "dark"
PRESETS = {
    "nature": ("Природа", (
        "a glacial lake beneath a granite ridge", "wind-sculpted dunes in a salt desert",
        "an alpine waterfall spilling through a basalt ravine", "a foggy grove of giant redwoods",
        "a rugged volcanic coast with rock pools", "a high meadow beneath a distant storm",
    )),
    "city": ("Город", (
        "an old tram rounding a rain-soaked corner", "a brutalist courtyard with reflecting pools",
        "a narrow canal between weathered brick houses", "an elevated train crossing a steel bridge",
        "a quiet market street with folded awnings", "a stepped rooftop garden above a compact skyline",
    )),
    "abstract": ("Абстракция", (
        "a flowing ribbon of polished obsidian", "layered paper arches with irregular cut edges",
        "floating translucent glass slabs", "interlocking ceramic shapes with subtle imperfections",
        "a twisted ribbon of brushed metal", "an arrangement of soft mineral-pigment forms",
    )),
    "space": ("Космос", (
        "a ringed planet rising above an icy moon", "a star nursery seen through a dust cloud",
        "a crater and a thin crescent planet", "comet fragments drifting along a planet's rings",
        "a luminous nebula reflected in a frozen plain", "an eclipse above a rugged alien coastline",
    )),
    "botany": ("Ботаника", (
        "fern fronds beaded with dew", "orchid petals emerging from deep shadow",
        "a cluster of translucent seed pods", "silver eucalyptus branches",
        "tangled roots and soft moss on a forest floor", "water lily leaves with scattered raindrops",
    )),
    "ocean": ("Океан", (
        "a translucent jellyfish in deep water", "a kelp forest with delicate drifting fronds",
        "a manta ray above a reef", "a school of silver fish curling through open water",
        "wave-cut sandstone shelves below the tide", "a reef of soft coral and tiny drifting particles",
    )),
}

CUSTOM_DIRECTIONS = (
    "show its defining subject in an atmospheric setting",
    "focus on a recognizable close-up and the materials around it",
    "use two or three distinct forms to show scale and depth",
    "feature a small moment of movement related to the theme",
    "reinterpret the theme with tactile materials and a clear silhouette",
    "place a single distinctive detail in a spacious setting",
)
STYLES = (
    "cinematic editorial photography", "fine-grained analog film photography",
    "detailed matte painting", "refined gouache on subtly textured paper",
    "high-end photorealistic 3D render", "hand-printed color lithograph",
    "painterly digital illustration with crisp focal details",
)
COMPOSITIONS = (
    "a low focal point and an open upper third",
    "an asymmetrical focal cluster in the lower right",
    "a foreground silhouette at lower left with layered depth beyond",
    "a diagonal beginning near the bottom corner and ending at mid-frame",
    "a central subject below the midpoint with generous negative space",
    "a wide establishing view with a low horizon",
    "a close crop with fine detail concentrated in the lower half",
    "a gentle curve from the bottom foreground into the middle distance",
)
DARK_PALETTES = (
    "ink blue and misty gray", "forest green and antique gold", "indigo and copper",
    "aubergine and dusty rose", "slate and sea glass", "charcoal and burnt orange",
    "midnight teal and silver", "deep burgundy and warm ivory", "blue-violet and amber",
)
DARK_LIGHTING = (
    "diffuse twilight", "a soft side light from the left", "backlight near the horizon",
    "a narrow pool of warm light in the lower half", "cool bounce light",
    "muted overcast light", "a subtle rim light around the focal subject",
    "low-angle light that picks out textures", "soft reflected light from below",
    "a gentle falloff from mid-frame into shadow",
)
LIGHT_PALETTES = (
    "warm ivory and pale sage", "chalk white and soft peach", "cream and mist blue",
    "pale sand and muted terracotta", "pearl gray and sea glass", "oat and warm apricot",
    "porcelain blue and ivory", "pale lilac and dusty pink", "buttermilk and soft gold",
)
LIGHT_LIGHTING = (
    "bright diffuse morning light", "soft sunlight from the left", "bright veiled sunlight",
    "high-key bounced daylight", "silvery daylight", "bright overcast daylight",
    "a luminous edge light in a bright setting", "clear low-angle morning light",
    "soft reflected daylight", "even ambient daylight with gentle shadows",
)
TONE_STYLES = {
    "light": (
        LIGHT_PALETTES,
        LIGHT_LIGHTING,
        "Make the image as a whole bright and airy, with predominantly light values and soft shadows. "
        "Keep the upper center pale, evenly lit, and low contrast so a dark date and clock remain legible.",
    ),
    "dark": (
        DARK_PALETTES,
        DARK_LIGHTING,
        "Make the image as a whole low-key, with predominantly dark values and visible detail in shadows. "
        "Keep the upper center dark, softly shaded, and low contrast so a white date and clock remain legible.",
    ),
}


def plan_frames(order_id, presets, custom, count, *, tone=DEFAULT_TONE, quantities=None):
    themes = [(key, *PRESETS[key]) for key in presets]
    themes += [(f"custom-{i}", theme, CUSTOM_DIRECTIONS) for i, theme in enumerate(custom, 1)]
    if quantities is None:
        base, remainder = divmod(count, len(themes))
        quantities = [base + (index < remainder) for index in range(len(themes))]

    palettes, lighting, tone_prompt = TONE_STYLES[tone]
    rng = Random(order_id)
    # These deck lengths keep the full combination unique within a 100-frame order.
    decks = [
        [rng.sample(options, len(options)) for options in (subjects, STYLES, COMPOSITIONS, palettes, lighting)]
        for _, _, subjects in themes
    ]
    number = 0
    for round_index in range(max(quantities)):
        for (key, theme, _), deck, amount in zip(themes, decks, quantities):
            if round_index >= amount:
                continue
            subject, style, composition, palette, light = (
                options[round_index % len(options)] for options in deck
            )
            direction = (
                f'Theme “{theme}”. Within this theme, {subject}.'
                if key.startswith("custom-") else f"Depict {subject}."
            )
            prompt = (
                f"{tone_prompt} {direction} Use {style}, {composition}, and a palette of {palette}. "
                f"Render the scene in {light}. Keep distinct edges and visual texture outside the clock area."
            )
            number += 1
            yield {
                "number": number,
                "theme": theme,
                "filename": f"{number:03d}-{key}.jpg",
                "prompt": make_payload({"prompt": prompt}, REQUEST_SIZE)["prompt"],
            }
