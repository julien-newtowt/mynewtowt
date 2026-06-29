"""Artefacts de référencement et de lisibilité par les moteurs d'IA.

Fonctions **pures** (sans I/O) : ``robots.txt``, ``llms.txt``, ``sitemap.xml``
et données structurées Schema.org. Les routes (``seo_router``) ne font que
les exposer ; les tests ciblent ces fonctions.

Décision (dossier Phase 4) : les robots d'IA sont explicitement autorisés ;
un ``llms.txt`` guide vers les contenus clés. Faits : section 2 du dossier.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# Langues publiques de la vitrine (le vietnamien reste interne à l'ERP).
PUBLIC_LANGS: tuple[str, ...] = ("fr", "en", "es", "pt-br")
DEFAULT_LANG = "fr"

# Correspondance code interne → valeur hreflang BCP-47.
_HREFLANG = {"fr": "fr", "en": "en", "es": "es", "pt-br": "pt-BR"}

# Robots d'IA explicitement autorisés (génératifs + récupération/citation).
AI_BOTS: tuple[str, ...] = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Applebot-Extended",
    "CCBot",
    "Amazonbot",
    "Meta-ExternalAgent",
    "cohere-ai",
    "YouBot",
    "DuckAssistBot",
)

# Pages publiques indexables : (chemin, changefreq, priorité).
PUBLIC_PAGES: tuple[tuple[str, str, str], ...] = (
    ("/", "weekly", "1.0"),
    ("/flotte", "monthly", "0.9"),
    ("/impact", "monthly", "0.9"),
    ("/preuves", "monthly", "0.8"),
    ("/navigation", "monthly", "0.7"),
    ("/carnet", "weekly", "0.6"),
    ("/actualites", "weekly", "0.5"),
    ("/recrutement", "monthly", "0.5"),
    ("/presse", "monthly", "0.4"),
    ("/routes", "daily", "0.8"),
    ("/fleet", "daily", "0.6"),
    ("/contact", "monthly", "0.8"),
    ("/about", "monthly", "0.6"),
    ("/about/anemos", "monthly", "0.7"),
    ("/about/legal", "yearly", "0.2"),
    ("/about/privacy", "yearly", "0.2"),
)

# Zones réservées à l'extranet / l'ERP — hors indexation.
DISALLOW = (
    "/admin/",
    "/me/",
    "/booking/",
    "/p/",
    "/chat",
    "/api/",
    "/staff/",
    "/planning/share",
)


def _norm(base_url: str) -> str:
    return base_url.rstrip("/")


def build_robots_txt(base_url: str) -> str:
    base = _norm(base_url)
    lines: list[str] = [
        "# NEWTOWT — robots.txt",
        "# Robots d'IA explicitement autorisés (cf. dossier vitrine, Phase 4).",
        "",
        "User-agent: *",
        "Allow: /",
    ]
    lines += [f"Disallow: {p}" for p in DISALLOW]
    lines.append("")
    for bot in AI_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines.append(f"Sitemap: {base}/sitemap.xml")
    return "\n".join(lines) + "\n"


def build_llms_txt(base_url: str) -> str:
    base = _norm(base_url)
    return (
        "# NewTowt\n\n"
        "> Compagnie maritime française de fret à la voile. NewTowt opère déjà "
        "une ligne régulière vers le Brésil et l'Amérique latine — une flotte "
        "qui navigue, pas un projet — et transporte du fret palettisé (café, "
        "cacao, fret industriel, marchandises dangereuses incluses) avec une "
        "réduction de CO₂ jusqu'à 95 %, mesurée et certifiée par le label "
        "ANEMOS. Présidée par Karl Sement.\n\n"
        "## Faits clés\n\n"
        "- Six voiliers-cargos sisterships de la classe TSC 80 : Anemos et "
        "Artemis en opération ; Atlantis, Astérias, Archimedes et Atlas en "
        "construction.\n"
        "- Charge utile > 1 200 t sur 1 050 m² exploitables en six cales, trois "
        "ponts ; 9 à 11 nœuds ; équipage 9–10 marins ; "
        "pavillon français.\n"
        "- Routes : Europe ↔ Brésil (Fécamp ↔ São Sebastião) et Amérique latine "
        "(Colombie, Mexique, Guatemala).\n"
        "- Construction par Piriou au Vietnam (chantiers Song Thu et Ba Son).\n\n"
        "## Pages clés\n\n"
        f"- [Notre flotte]({base}/flotte) : navires TSC 80, capacités, cales.\n"
        f"- [Impact]({base}/impact) : environnement maîtrisé à bord, surveillance "
        "qualité, décarbonation, certificat Anemos d'émissions évitées.\n"
        f"- [Preuves]({base}/preuves) : méthode (tank-to-wake, CO₂), vérification "
        "EU MRV / registre THETIS-MRV, et vérification publique des certificats.\n"
        f"- [Navigation]({base}/navigation) : courants, propulsion vélique, routes.\n"
        f"- [Routes & plannings]({base}/routes) : prochaines traversées.\n"
        f"- [Carnet de construction]({base}/carnet) : avancée des navires en "
        "construction (Piriou, chantiers Song Thu et Ba Son).\n"
        f"- [Contact & cotation]({base}/contact) : demande de devis.\n\n"
        "## Légal\n\n"
        f"- [Mentions légales]({base}/about/legal)\n"
        f"- [Confidentialité]({base}/about/privacy)\n"
    )


def build_sitemap_xml(base_url: str, *, lastmod: str | None = None) -> str:
    base = _norm(base_url)
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for path, changefreq, priority in PUBLIC_PAGES:
        loc = f"{base}{path}"
        out.append("  <url>")
        out.append(f"    <loc>{escape(loc)}</loc>")
        # Alternates hreflang par langue (?lang=xx) + x-default.
        for lang in PUBLIC_LANGS:
            href = f"{loc}?lang={lang}"
            out.append(
                f'    <xhtml:link rel="alternate" hreflang="{_HREFLANG[lang]}" '
                f'href="{escape(href)}"/>'
            )
        out.append(
            f'    <xhtml:link rel="alternate" hreflang="x-default" ' f'href="{escape(loc)}"/>'
        )
        if lastmod:
            out.append(f"    <lastmod>{escape(lastmod)}</lastmod>")
        out.append(f"    <changefreq>{changefreq}</changefreq>")
        out.append(f"    <priority>{priority}</priority>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def organization_jsonld(base_url: str) -> dict:
    """Fiche Organisation Schema.org — source de vérité de l'entité (§2)."""
    base = _norm(base_url)
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{base}/#organization",
        "name": "NewTowt",
        "legalName": "NewTowt",
        "alternateName": "NEWTOWT",
        "url": base + "/",
        "description": (
            "Compagnie maritime française de fret à la voile vers le Brésil et "
            "l'Amérique latine : café, cacao et fret industriel, émissions "
            "évitées documentées par le certificat Anemos (EU MRV, tank-to-wake CO₂)."
        ),
        "foundingDate": "2011",
        "founder": {"@type": "Person", "name": "Karl Sement"},
        "email": "dpo@newtowt.eu",
        "telephone": "+33 9 84 33 89 62",
        "vatID": "FR7501.994529873",
        "taxID": "994 529 873",
        "address": [
            {
                "@type": "PostalAddress",
                "name": "Siège social",
                "streetAddress": "128 boulevard Raspail",
                "postalCode": "75006",
                "addressLocality": "Paris",
                "addressCountry": "FR",
            },
            {
                "@type": "PostalAddress",
                "name": "Adresse opérationnelle",
                "streetAddress": "52 quai Frissard",
                "postalCode": "76600",
                "addressLocality": "Le Havre",
                "addressCountry": "FR",
            },
        ],
    }


def service_jsonld(base_url: str) -> dict:
    """Description du service de transport maritime décarboné."""
    base = _norm(base_url)
    return {
        "@context": "https://schema.org",
        "@type": "Service",
        "serviceType": "Transport maritime de fret à la voile (décarboné)",
        "provider": {"@id": f"{base}/#organization"},
        "areaServed": ["Europe", "Brésil", "Amérique latine"],
        "description": (
            "Transport de fret palettisé (café, cacao, fret industriel, "
            "marchandises dangereuses des classes 2, 3, 4.1, 8 et 9) sur "
            "voiliers-cargos, émissions évitées documentées par le certificat "
            "Anemos (EU MRV, tank-to-wake CO₂)."
        ),
    }


def faq_jsonld(qa_pairs: list[tuple[str, str]]) -> dict:
    """FAQPage à partir de paires (question, réponse)."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in qa_pairs
        ],
    }


def breadcrumb_jsonld(base_url: str, items: list[tuple[str, str]]) -> dict:
    """BreadcrumbList à partir de paires (nom, chemin relatif)."""
    base = _norm(base_url)
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": name,
                "item": f"{base}{path}",
            }
            for i, (name, path) in enumerate(items)
        ],
    }
