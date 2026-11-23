"""Resolve categorical and semantic paints against the surface where they will be used."""
import colorsys
from functools import lru_cache
from math import dist, hypot

from app.render_geometry import contrast


def blend(first, second, share):
    channels = [round(int(first[i:i+2], 16) * (1 - share) + int(second[i:i+2], 16) * share)
                for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def readable(preferred, surface, *alternatives, minimum=4.5):
    colors = (preferred, *alternatives, "#FFFFFF", "#000000")
    return next(color for color in colors if contrast(color, surface) >= minimum)


def channels(color):
    return tuple(int(color[i:i+2], 16) / 255 for i in (1, 3, 5))


@lru_cache(maxsize=512)
def oklab(color):
    r, g, b = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels(color))
    l = (.41222147*r + .53633254*g + .05144599*b) ** (1/3)
    m = (.21190350*r + .68069955*g + .10739696*b) ** (1/3)
    s = (.08830246*r + .28171884*g + .62997870*b) ** (1/3)
    return (.21045426*l + .79361779*m - .00407205*s, 1.97799850*l - 2.42859221*m + .45059371*s,
            .02590404*l + .78277177*m - .80867577*s)


def visible_tone(color, background, minimum=3):
    end = max(("#FFFFFF", "#000000"), key=lambda shade: contrast(shade, background))
    return next(tone for i in range(21) if contrast(tone := blend(color, end, i / 20), background) >= minimum)


@lru_cache(maxsize=2048)
def series_distinguishable(first, second):
    """Require a visible lightness or chroma step, including near black.

    OKLab distance alone overstates the usefulness of small dark-neutral steps
    for thin chart marks. These are conservative chart heuristics, not text
    contrast requirements.
    """
    a, b = oklab(first), oklab(second)
    return dist(a, b) >= .10 and (contrast(first, second) >= 1.5 or hypot(a[1]-b[1], a[2]-b[2]) >= .06)


def categorical_palette(theme, background, count=5, *, distinct=False):
    seeds = [theme.accent, *(theme.design.chart_colors if theme.design else [])]
    restricted = theme.constraints.palette if theme.constraints else []
    if theme.design and theme.brand_kit and "chart_colors" in theme.brand_kit.protected:
        seeds = restricted = theme.design.chart_colors
    restrained = (theme.constraints and theme.constraints.restrained or
                  theme.design and len(set(theme.design.chart_colors)) <= 2)
    return list(_series(tuple(c.upper() for c in seeds), tuple(c.upper() for c in restricted),
                        background.upper(), count, bool(restrained), distinct))


def _distinct_series(candidates, seeds, count):
    """Find up to five separated series without spending the palette on near duplicates."""
    best = candidates[:1]
    budget = 2048

    def choose(selected, remaining):
        nonlocal best, budget
        if not budget:
            return None
        budget -= 1
        if len(selected) > len(best):
            best = selected
        if len(selected) == count:
            return selected
        eligible = [c for c in remaining if all(series_distinguishable(c, other) for other in selected)]
        if len(selected) + len(eligible) <= len(best):
            return None
        distances = {c: min(dist(oklab(c), oklab(other)) for other in selected) for c in eligible}
        ranked = sorted(eligible, key=lambda c: (c in seeds and distances[c] >= .12, distances[c]), reverse=True)
        for index, shade in enumerate(ranked):
            if result := choose([*selected, shade], ranked[index+1:]):
                return result
            if not budget:
                break
        return None

    return tuple(choose(best, candidates[1:]) or best)


@lru_cache(maxsize=256)
def _series(seeds, restricted, background, count, restrained, distinct=False):
    if restricted:
        candidates = list(dict.fromkeys(c for c in (*seeds, *restricted) if c in restricted and contrast(c, background) >= 3))
    else:
        hue, _, saturation = colorsys.rgb_to_hls(*channels(seeds[0]))
        variants = ["#" + "".join(f"{round(v*255):02X}" for v in colorsys.hls_to_rgb(
            (hue + i / 12) % 1, light, max(.5, min(.8, saturation))))
            for light in (.42, .64) for i in range(12)]
        if restrained:
            shares = [i / 40 for i in range(1, 41)] if distinct else (.15, .3, .45, .6, .75, .9, 1)
            variants = [blend(seed, end, share) for seed in seeds for end in ("#000000", "#FFFFFF")
                        for share in shares]
        candidates = list(dict.fromkeys(visible_tone(c, background) for c in [*seeds, *variants]))
    if not candidates:
        return (max(restricted, key=lambda c: contrast(c, background)),)
    # Authored locked colors must remain unchanged; validation reports when
    # they cannot distinguish the requested series. Numbered pies can reuse
    # tones, so only ordinary multi-series charts request this stricter choice.
    if distinct and not restricted:
        return _distinct_series(candidates, seeds, count)
    result = [candidates.pop(0)]
    while candidates and len(result) < count:
        distances = {c: min(dist(oklab(c), oklab(other)) for other in result) for c in candidates}
        # Keep an authored series color when it has perceptual separation from earlier ones.
        next_color = next((c for c in candidates if c in seeds and distances[c] >= .12),
                          max(candidates, key=distances.get))
        if distances[next_color] < .055:
            break
        candidates.remove(next_color)
        result.append(next_color)
    return tuple(result)


def semantic_palette(theme, background=None):
    bg = background or theme.background
    series = categorical_palette(theme, bg)
    restricted = theme.constraints and theme.constraints.palette

    def mapped(color):
        return min(restricted, key=lambda c: dist(oklab(c), oklab(color))) if restricted else color

    def ink(color, surface):
        if restricted:
            eligible = [c for c in restricted if contrast(c, surface) >= 4.5]
            return min(eligible, key=lambda c: dist(oklab(c), oklab(color))) if eligible else max(
                restricted, key=lambda c: contrast(c, surface))
        return readable(color, surface, theme.ink, theme.background)

    main = ink(theme.ink, bg)
    neutral = mapped(visible_tone(blend(bg, main, .5), bg))
    if dist(oklab(neutral), oklab(series[0])) < .10:
        neutral = max((c for c in (main, *series, neutral) if contrast(c, bg) >= 3),
                      key=lambda c: dist(oklab(c), oklab(series[0])), default=neutral)
    return {"background": theme.background, "surface": theme.surface, "ink": main, "heading": main,
            "body": main, "muted": ink(theme.muted, bg), "accent": theme.accent, "highlight": series[0],
            "accentText": ink(series[0], bg), "border": mapped(blend(bg, main, .28)), "neutral": neutral,
            "positive": mapped(visible_tone(blend("#187548", theme.accent, .16), bg)),
            "negative": mapped(visible_tone(blend("#A43C40", theme.accent, .12), bg)),
            "onAccent": ink(theme.background, theme.accent), "onSurface": ink(theme.ink, theme.surface),
            "cover": theme.design.cover_background if theme.design else theme.background,
            "dense": theme.design.dense_surface if theme.design else theme.surface,
            **{f"series{i}": shade for i, shade in enumerate(series, 1)}}
