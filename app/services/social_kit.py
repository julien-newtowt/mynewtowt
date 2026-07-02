"""Volet social du kit B2B2C (P12) — visuels prêts à poster, générés en SVG.

Trois visuels **par expédition** (booking), co-brandés NEWTOWT × marque du
client, dans la charte « Nouvelle Étoile », chacun portant le CO₂ évité en
**kilogrammes absolus**, la mention « certifié Anemos » et le QR de voyage/
vérification.

Le SVG est autonome (styles inline autorisés dans SVG), rendu **côté serveur**
donc compatible CSP-strict (aucun ``<script>``), et embarque le QR via le
data-URI existant (``mfa.qr_data_uri`` → ``data:image/svg+xml;base64,…``). Les
polices de la charte sont référencées avec des fallbacks système (le poste qui
ouvre le visuel n'a pas forcément Manrope / DM Serif Display installés).

Fonctions **pures** (sans I/O) : le routeur fournit les données du booking et
du certificat ; ici on ne fait que composer du SVG. Facilement testable.

Garde-fous ECGT (anti-greenwashing), identiques aux récits café/cacao :

* CO₂ en **kg absolus** — jamais de pourcentage, jamais « neutre / compensé » ;
* « **certifié Anemos** » nommé sur **chaque** visuel ;
* CO₂ **vérifiable** via le QR (page voyage publique) ou ``/verify`` ;
* si le certificat est absent : **phrase qualitative**, aucun chiffre inventé.
"""

from __future__ import annotations

from app.i18n import t
from app.services import cacao_stories, coffee_stories

# ── Formats (largeur, hauteur) ─────────────────────────────────────────────
# Carré LinkedIn/Instagram, portrait/story, bannière paysage (OG / LinkedIn).
FORMATS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "story": (1080, 1350),
    "landscape": (1200, 628),
}

# ── Charte « Nouvelle Étoile » (source : tokens.css) ───────────────────────
_TEAL = "#0D5966"
_VERT = "#87BD29"
_CUIVRE = "#B47148"
_SABLE = "#EFE6D6"
_SABLE_LIGHT = "#F8F2E6"
_BLANC = "#FFFFFF"

_FONT_SERIF = "'DM Serif Display', Georgia, 'Times New Roman', serif"
_FONT_SANS = "Manrope, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
_FONT_MONO = "'JetBrains Mono', 'SF Mono', Consolas, 'Courier New', monospace"


# ── Escaping XML (les valeurs peuvent venir d'une saisie client) ───────────
def _esc(value: object) -> str:
    """Échappe ``& < > " '`` pour insertion sûre dans texte/attribut SVG."""
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def resolve_origin(origin: str | None):
    """Renvoie le module de récit (café/cacao) portant cette origine, sinon None."""
    if coffee_stories.is_valid_origin(origin):
        return coffee_stories
    if cacao_stories.is_valid_origin(origin):
        return cacao_stories
    return None


def commodity_of(origin: str | None) -> str | None:
    """« coffee » / « cacao » / None selon la verticale de l'origine."""
    if coffee_stories.is_valid_origin(origin):
        return "coffee"
    if cacao_stories.is_valid_origin(origin):
        return "cacao"
    return None


def _fmt_int(n: int, lang: str) -> str:
    """Entier avec séparateur de milliers (espace fr/pt-br/es/vi, virgule en)."""
    grouped = f"{int(n):,}"
    return grouped if lang == "en" else grouped.replace(",", " ")


def _wrap(text: str, max_chars: int) -> list[str]:
    """Découpe naïf mot-à-mot (SVG ne fait pas de retour à la ligne auto)."""
    lines: list[str] = []
    current = ""
    for word in (text or "").split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text(
    x: int,
    y: int,
    content: str,
    *,
    size: int,
    color: str,
    font: str = _FONT_SANS,
    weight: str = "400",
    anchor: str = "start",
    letter_spacing: float | None = None,
    opacity: float | None = None,
) -> str:
    ls = f' letter-spacing="{letter_spacing}"' if letter_spacing is not None else ""
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}"{ls}{op}>'
        f"{_esc(content)}</text>"
    )


def _multiline(
    x: int,
    y: int,
    lines: list[str],
    *,
    size: int,
    color: str,
    font: str = _FONT_SANS,
    weight: str = "400",
    line_height: int | None = None,
    anchor: str = "start",
) -> str:
    lh = line_height or int(size * 1.3)
    spans = "".join(
        f'<tspan x="{x}" dy="{0 if i == 0 else lh}">{_esc(ln)}</tspan>'
        for i, ln in enumerate(lines)
    )
    return (
        f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{spans}</text>'
    )


def _image(href: str, x: int, y: int, w: int, h: int, *, ratio: str = "xMidYMid meet") -> str:
    """Balise ``<image>`` avec ``href`` **et** ``xlink:href`` (compat large)."""
    e = _esc(href)
    return (
        f'<image x="{x}" y="{y}" width="{w}" height="{h}" '
        f'preserveAspectRatio="{ratio}" href="{e}" xlink:href="{e}"/>'
    )


def _wordmark(x: int, y: int, size: int, *, on_dark: bool = True) -> str:
    """Wordmark NEWTOWT : préfixe « NEW » cuivre + « TOWT » (charte presse)."""
    tail = _BLANC if on_dark else _TEAL
    return (
        f'<text x="{x}" y="{y}" font-family="{_FONT_SANS}" font-size="{size}" '
        f'font-weight="800" letter-spacing="1">'
        f'<tspan fill="{_CUIVRE}">NEW</tspan><tspan fill="{tail}">TOWT</tspan></text>'
    )


def _cobrand(
    x_right: int, y: int, *, brand_name: str | None, logo_data: str | None, lang: str
) -> str:
    """Co-branding (droite) : « avec » + logo client, ou nom de marque."""
    if not brand_name and not logo_data:
        return ""
    label = _text(
        x_right,
        y,
        t("social_cobrand_with", lang),
        size=24,
        color=_SABLE,
        anchor="end",
        opacity=0.85,
    )
    if logo_data:
        # Boîte 220×60 alignée à droite, sous le libellé « avec ».
        img = _image(logo_data, x_right - 220, y + 12, 220, 60, ratio="xMaxYMid meet")
        return label + img
    return label + _text(
        x_right, y + 46, brand_name or "", size=34, color=_BLANC, weight="800", anchor="end"
    )


def _pill_certified(x: int, y: int, lang: str, *, size: int = 28) -> str:
    """Pastille « certifié Anemos » (cuivre) — présente sur chaque visuel."""
    label = t("social_certified", lang)
    w = int(len(label) * size * 0.62) + 56
    h = int(size * 1.9)
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h // 2}" fill="{_CUIVRE}"/>'
        + _text(
            x + w // 2,
            y + int(h * 0.66),
            label,
            size=size,
            color=_BLANC,
            weight="700",
            anchor="middle",
        )
    )


def _qr_caption(lang: str, *, qr_is_voyage: bool) -> str:
    return t("social_scan_voyage" if qr_is_voyage else "social_scan_verify", lang)


def _co2_summary(co2_kg: int | None, lang: str) -> str:
    """Chaîne contiguë « 300 kg de CO₂ évité — certifié Anemos » (aria/desc).

    Sans certificat : phrase qualitative, **aucun chiffre** (garde-fou ECGT).
    """
    certified = t("social_certified", lang)
    if co2_kg is None:
        return f"{t('social_co2_qualitative', lang)} — {certified}"
    n = _fmt_int(co2_kg, lang)
    return f"{n} kg {t('social_co2_avoided', lang)} — {certified}"


def _eyebrow(origin: str | None, origin_label: str, lang: str) -> str:
    """Bandeau supérieur : « Café · Colombie » / « Cacao · … » ou générique."""
    commodity = commodity_of(origin)
    if commodity and origin_label:
        return f"{t('social_commodity_' + commodity, lang)} · {origin_label}".upper()
    return t("social_eyebrow_generic", lang).upper()


# ── Blocs composables ──────────────────────────────────────────────────────
def _co2_block(x: int, y: int, co2_kg: int | None, lang: str, *, num_size: int) -> str:
    """Bloc CO₂ : grand nombre vert + « kg » + libellé, ou phrase qualitative."""
    if co2_kg is None:
        lines = _wrap(t("social_co2_qualitative", lang), 22)
        return _multiline(
            x,
            y,
            lines,
            size=int(num_size * 0.32),
            color=_BLANC,
            font=_FONT_SERIF,
            line_height=int(num_size * 0.36),
        )
    n = _fmt_int(co2_kg, lang)
    kg_size = int(num_size * 0.34)
    num = (
        f'<text x="{x}" y="{y}" font-family="{_FONT_SANS}" font-weight="800">'
        f'<tspan font-size="{num_size}" fill="{_VERT}">{_esc(n)}</tspan>'
        f'<tspan font-size="{kg_size}" fill="{_VERT}" dx="14">kg</tspan></text>'
    )
    label = _text(
        x,
        y + int(num_size * 0.30),
        t("social_co2_avoided", lang),
        size=int(num_size * 0.24),
        color=_BLANC,
        weight="600",
    )
    return num + label


def _qr_card(
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    qr_data_uri: str | None,
    verify_url: str | None,
    qr_is_voyage: bool,
    lang: str,
) -> str:
    """Carte sable au QR + légende (fond clair pour lisibilité du QR)."""
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="24" fill="{_SABLE_LIGHT}"/>']
    qr_size = min(h - 48, 200)
    qx, qy = x + 24, y + (h - qr_size) // 2
    if qr_data_uri:
        parts.append(
            f'<rect x="{qx}" y="{qy}" width="{qr_size}" height="{qr_size}" rx="12" fill="{_BLANC}"/>'
        )
        parts.append(_image(qr_data_uri, qx, qy, qr_size, qr_size))
    tx = qx + qr_size + 28
    tw = x + w - tx - 20
    parts.append(
        _text(
            tx,
            y + int(h * 0.40),
            _qr_caption(lang, qr_is_voyage=qr_is_voyage),
            size=26,
            color=_TEAL,
            weight="700",
        )
    )
    if verify_url:
        url_lines = _wrap(
            verify_url.replace("https://", "").replace("http://", ""), max(14, tw // 12)
        )
        parts.append(
            _multiline(
                tx,
                y + int(h * 0.40) + 40,
                url_lines[:2],
                size=20,
                color=_TEAL,
                font=_FONT_MONO,
                line_height=26,
            )
        )
    return "".join(parts)


def _svg_root(w: int, h: int, summary: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(summary)}">'
        f"<title>{_esc(summary)}</title><desc>{_esc(summary)}</desc>"
        f'<rect width="{w}" height="{h}" fill="{_TEAL}"/>'
        # Texture discrète : cercle vert très transparent (charte minimaliste).
        f'<circle cx="{w}" cy="0" r="{int(w * 0.42)}" fill="{_VERT}" opacity="0.06"/>'
        f"{body}</svg>"
    )


def _render_portrait(
    w: int,
    h: int,
    *,
    lang: str,
    origin: str | None,
    origin_label: str,
    story_short: str | None,
    co2_kg: int | None,
    cert_ref: str | None,
    qr_data_uri: str | None,
    qr_is_voyage: bool,
    verify_url: str | None,
    client_brand_name: str | None,
    client_logo_data: str | None,
) -> str:
    tall = h >= 1300  # story vs square
    p = 80
    band_h = 210 if not tall else 240
    num_size = 150 if not tall else 176
    parts: list[str] = []
    parts.append(_wordmark(p, 108, 44))
    parts.append(f'<rect x="{p}" y="128" width="128" height="6" rx="3" fill="{_VERT}"/>')
    parts.append(
        _cobrand(w - p, 84, brand_name=client_brand_name, logo_data=client_logo_data, lang=lang)
    )

    parts.append(
        _text(
            p,
            232,
            _eyebrow(origin, origin_label, lang),
            size=28,
            color=_VERT,
            font=_FONT_MONO,
            weight="700",
            letter_spacing=1.5,
        )
    )

    hero_size = 68 if not tall else 78
    hero_lines = _wrap(t("social_hero", lang), 20)
    parts.append(
        _multiline(
            p,
            316,
            hero_lines,
            size=hero_size,
            color=_BLANC,
            font=_FONT_SERIF,
            line_height=int(hero_size * 1.05),
        )
    )

    co2_y = 520 if not tall else 620
    parts.append(_co2_block(p, co2_y, co2_kg, lang, num_size=num_size))
    parts.append(_pill_certified(p, co2_y + 56, lang))

    if story_short:
        story_y = co2_y + 180
        parts.append(
            _multiline(
                p, story_y, _wrap(story_short, 42)[:3], size=30, color=_SABLE, line_height=42
            )
        )

    # Bandeau bas : QR + légende + référence.
    by = h - band_h
    parts.append(
        _qr_card(
            0,
            by,
            w,
            band_h,
            qr_data_uri=qr_data_uri,
            verify_url=verify_url,
            qr_is_voyage=qr_is_voyage,
            lang=lang,
        )
    )
    if cert_ref:
        parts.append(
            _text(
                w - 32,
                by + band_h - 24,
                cert_ref,
                size=20,
                color=_TEAL,
                font=_FONT_MONO,
                anchor="end",
                opacity=0.75,
            )
        )
    return _svg_root(w, h, _co2_summary(co2_kg, lang), "".join(parts))


def _render_landscape(
    w: int,
    h: int,
    *,
    lang: str,
    origin: str | None,
    origin_label: str,
    story_short: str | None,
    co2_kg: int | None,
    cert_ref: str | None,
    qr_data_uri: str | None,
    qr_is_voyage: bool,
    verify_url: str | None,
    client_brand_name: str | None,
    client_logo_data: str | None,
) -> str:
    p = 64
    right_x = 812
    parts: list[str] = []
    parts.append(_wordmark(p, 84, 38))
    parts.append(f'<rect x="{p}" y="102" width="104" height="5" rx="2" fill="{_VERT}"/>')
    parts.append(
        _cobrand(w - p, 64, brand_name=client_brand_name, logo_data=client_logo_data, lang=lang)
    )

    parts.append(
        _text(
            p,
            184,
            _eyebrow(origin, origin_label, lang),
            size=24,
            color=_VERT,
            font=_FONT_MONO,
            weight="700",
            letter_spacing=1.2,
        )
    )
    parts.append(
        _multiline(
            p,
            244,
            _wrap(t("social_hero", lang), 24),
            size=52,
            color=_BLANC,
            font=_FONT_SERIF,
            line_height=56,
        )
    )
    parts.append(_co2_block(p, 400, co2_kg, lang, num_size=116))
    parts.append(_pill_certified(p, 430, lang))
    if story_short:
        parts.append(
            _multiline(p, 528, _wrap(story_short, 58)[:2], size=24, color=_SABLE, line_height=32)
        )

    # Carte QR à droite (pleine hauteur utile).
    card_x, card_y, card_w, card_h = right_x, 96, w - right_x - 48, h - 192
    parts.append(
        f'<rect x="{card_x}" y="{card_y}" width="{card_w}" height="{card_h}" rx="24" fill="{_SABLE_LIGHT}"/>'
    )
    qr_size = min(card_w - 48, 220)
    qx = card_x + (card_w - qr_size) // 2
    qy = card_y + 40
    if qr_data_uri:
        parts.append(
            f'<rect x="{qx}" y="{qy}" width="{qr_size}" height="{qr_size}" rx="12" fill="{_BLANC}"/>'
        )
        parts.append(_image(qr_data_uri, qx, qy, qr_size, qr_size))
    parts.append(
        _multiline(
            card_x + card_w // 2,
            qy + qr_size + 46,
            _wrap(_qr_caption(lang, qr_is_voyage=qr_is_voyage), 22),
            size=24,
            color=_TEAL,
            weight="700",
            anchor="middle",
            line_height=30,
        )
    )
    if cert_ref:
        parts.append(
            _text(
                card_x + card_w // 2,
                card_y + card_h - 24,
                cert_ref,
                size=18,
                color=_TEAL,
                font=_FONT_MONO,
                anchor="middle",
                opacity=0.75,
            )
        )
    return _svg_root(w, h, _co2_summary(co2_kg, lang), "".join(parts))


def render_svg(
    fmt: str,
    *,
    lang: str = "fr",
    origin: str | None = None,
    origin_label: str = "",
    story_short: str | None = None,
    co2_kg: int | None = None,
    cert_ref: str | None = None,
    qr_data_uri: str | None = None,
    qr_is_voyage: bool = False,
    verify_url: str | None = None,
    client_brand_name: str | None = None,
    client_logo_data: str | None = None,
) -> str:
    """Rend un visuel social autonome (SVG) pour ``fmt`` ∈ :data:`FORMATS`.

    ``co2_kg`` absolu (kg) ou ``None`` (phrase qualitative). ``qr_data_uri`` =
    QR ``data:image/svg+xml;base64,…`` (mfa.qr_data_uri) pointant sur la page
    voyage publique (``qr_is_voyage=True``) ou ``/verify``. Lève ``KeyError``
    si le format est inconnu.
    """
    if fmt not in FORMATS:
        raise KeyError(f"format social inconnu : {fmt!r}")
    w, h = FORMATS[fmt]
    kwargs = {
        "lang": lang,
        "origin": origin,
        "origin_label": origin_label,
        "story_short": story_short,
        "co2_kg": co2_kg,
        "cert_ref": cert_ref,
        "qr_data_uri": qr_data_uri,
        "qr_is_voyage": qr_is_voyage,
        "verify_url": verify_url,
        "client_brand_name": client_brand_name,
        "client_logo_data": client_logo_data,
    }
    if fmt == "landscape":
        return _render_landscape(w, h, **kwargs)
    return _render_portrait(w, h, **kwargs)
