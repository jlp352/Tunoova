"""Icones SVG (Lucide, licence ISC) inlinees dans le HTML pour etre
recolorables via CSS (`stroke="currentColor"` dans chaque fichier source,
cf. app/static/icons/). Un seul fichier sert donc en noir (contexte clair)
comme en blanc (barre de navigation violette) selon le CSS ambiant."""

import os

from markupsafe import Markup

_ICONS_DIR = os.path.join(os.path.dirname(__file__), "static", "icons")
_cache: dict[str, str] = {}


def icon(name: str, css_class: str = "") -> Markup:
    svg = _cache.get(name)
    if svg is None:
        path = os.path.join(_ICONS_DIR, f"{name}.svg")
        with open(path, encoding="utf-8") as f:
            svg = f.read()
        _cache[name] = svg

    if css_class:
        svg = svg.replace('class="lucide', f'class="{css_class} lucide', 1)
    return Markup(svg)
