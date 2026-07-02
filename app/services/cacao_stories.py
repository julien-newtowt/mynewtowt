"""Récits d'origine du kit B2B2C — cacao/fèves transporté à la voile.

Verticale sœur de ``coffee_stories`` (même contrat, mêmes garde-fous). Le
cacao fin est **sensible à la chaleur et à l'humidité** : au-delà de ~25 °C le
beurre de cacao migre (« fat bloom », blanchiment gras) et les précurseurs
aromatiques se dégradent. Transporté sous la ligne de flottaison, à la
température de la mer et en cale ventilée, il arrive intact — argument réel,
pas une allégation d'ambiance.

Gabarits **pré-remplis** : l'ERP injecte région, producteur, navire et le
CO₂ évité (``[X] kg``) depuis le booking / le certificat Anemos ; la page
vitrine ``/solutions/cacao`` les rend avec des valeurs d'exemple.

Fonctions **pures** (sans I/O). Garde-fous identiques à la verticale café :

* **aucun pourcentage** (les chiffres sont en kg, jamais en %) ;
* « **certifié Anemos** » (jamais « label ») ;
* le CO₂ évité reste **vérifiable via le QR** ``/verify`` ;
* sortie en **texte brut** (jamais ``|safe`` — auto-échappement Jinja).
"""

from __future__ import annotations

# Origines cacao desservies (ordre d'affichage sur /solutions/cacao). Bassin
# latino-américain du cacao fin/aromatique, cohérent avec nos routes.
ORIGINS: tuple[str, ...] = ("equateur", "perou", "republique_dominicaine")

# Langues du kit (le castillan retombe sur le français — cf. _norm_lang).
KIT_LANGS: tuple[str, ...] = ("fr", "en", "pt-br")
_DEFAULT_LANG = "fr"

# Pays par origine, décliné par langue (intégré au champ « région »).
_COUNTRY: dict[str, dict[str, str]] = {
    "equateur": {"fr": "Équateur", "en": "Ecuador", "pt-br": "Equador"},
    "perou": {"fr": "Pérou", "en": "Peru", "pt-br": "Peru"},
    "republique_dominicaine": {
        "fr": "République dominicaine",
        "en": "Dominican Republic",
        "pt-br": "República Dominicana",
    },
}

# Gabarits de récit long : origine → langue → texte. Placeholders nommés
# {region} {producer} {vessel} {co2} remplis par render_story().
_LONG: dict[str, dict[str, str]] = {
    "equateur": {
        "fr": (
            "Ce cacao Nacional a mûri dans {region}, cabosses récoltées et "
            "fermentées par {producer}. Plutôt que de traverser l'Atlantique "
            "dans un conteneur qui surchauffe et fait « fleurir » le beurre de "
            "cacao, il a voyagé à la voile à bord de {vessel}, sous la ligne de "
            "flottaison, à la température de la mer — à l'abri des à-coups "
            "thermiques qui ternissent les arômes floraux de l'Arriba. La "
            "traversée a évité {co2}, mesuré et certifié Anemos : scannez le "
            "code pour le vérifier. Un cacao qui arrive comme il est parti — "
            "sans avoir réchauffé la planète pour venir jusqu'à vous."
        ),
        "en": (
            "This Nacional cacao ripened in {region}, its pods harvested and "
            "fermented by {producer}. Instead of crossing the Atlantic in a "
            "container that overheats and blooms the cocoa butter, it travelled "
            "under sail aboard {vessel}, below the waterline, at sea "
            "temperature — shielded from the thermal swings that dull the "
            "floral notes of Arriba. The crossing avoided {co2}, measured and "
            "certified by Anemos: scan the code to check it. Cacao that arrives "
            "as it left — without warming the planet to reach you."
        ),
        "pt-br": (
            "Este cacau Nacional amadureceu em {region}, com favas colhidas e "
            "fermentadas por {producer}. Em vez de cruzar o Atlântico num "
            "contêiner que superaquece e faz aflorar a manteiga de cacau, "
            "viajou à vela a bordo {vessel}, abaixo da linha de flutuação, à "
            "temperatura do mar — protegido das variações térmicas que apagam "
            "as notas florais do Arriba. A travessia evitou {co2}, medido e "
            "certificado pela Anemos: escaneie o código para conferir. Um cacau "
            "que chega como partiu — sem esquentar o planeta para chegar a você."
        ),
    },
    "perou": {
        "fr": (
            "Ce cacao fin a poussé en lisière d'Amazonie, à {region}, cultivé "
            "par {producer}. Plutôt que de subir un conteneur qui condense et "
            "moisit sous les tropiques, il a voyagé à la voile à bord de "
            "{vessel}, sous la ligne de flottaison, à la température de la mer, "
            "en cale ventilée — à l'abri de l'humidité qui gâte la fève. La "
            "traversée a évité {co2}, mesuré et certifié Anemos : scannez le "
            "code pour le vérifier. La finesse amazonienne, préservée jusqu'à "
            "votre atelier — sans réchauffer la planète."
        ),
        "en": (
            "This fine cacao grew at the edge of the Amazon, in {region}, grown "
            "by {producer}. Instead of enduring a container that condenses and "
            "moulds in the tropics, it travelled under sail aboard {vessel}, "
            "below the waterline, at sea temperature, in a ventilated hold — "
            "shielded from the damp that spoils the bean. The crossing avoided "
            "{co2}, measured and certified by Anemos: scan the code to check "
            "it. Amazonian finesse, preserved to your workshop — without "
            "warming the planet."
        ),
        "pt-br": (
            "Este cacau fino cresceu na borda da Amazônia, em {region}, "
            "cultivado por {producer}. Em vez de enfrentar um contêiner que "
            "condensa e mofa nos trópicos, viajou à vela a bordo {vessel}, "
            "abaixo da linha de flutuação, à temperatura do mar, em porão "
            "ventilado — protegido da umidade que estraga a fava. A travessia "
            "evitou {co2}, medido e certificado pela Anemos: escaneie o código "
            "para conferir. A finura amazônica, preservada até a sua oficina — "
            "sem esquentar o planeta."
        ),
    },
    "republique_dominicaine": {
        "fr": (
            "Ce cacao biologique a été récolté à {region}, fermenté et séché "
            "au soleil par {producer}. Plutôt que de traverser l'Atlantique "
            "dans un conteneur surchauffé, il a voyagé à la voile à bord de "
            "{vessel}, sous la ligne de flottaison, à la température de la mer — "
            "à l'abri des à-coups thermiques qui font blanchir et fatiguent la "
            "fève. La traversée a évité {co2}, mesuré et certifié Anemos : "
            "scannez le code pour le vérifier. Un cacao bio intact, de la "
            "cabosse à la tablette — et la planète épargnée."
        ),
        "en": (
            "This organic cacao was harvested in {region}, fermented and "
            "sun-dried by {producer}. Instead of crossing the Atlantic in an "
            "overheating container, it travelled under sail aboard {vessel}, "
            "below the waterline, at sea temperature — shielded from the "
            "thermal swings that bloom and tire the bean. The crossing avoided "
            "{co2}, measured and certified by Anemos: scan the code to check "
            "it. Organic cacao intact, from pod to bar — and the planet spared."
        ),
        "pt-br": (
            "Este cacau orgânico foi colhido em {region}, fermentado e seco ao "
            "sol por {producer}. Em vez de cruzar o Atlântico num contêiner "
            "superaquecido, viajou à vela a bordo {vessel}, abaixo da linha de "
            "flutuação, à temperatura do mar — protegido das variações térmicas "
            "que embranquecem e cansam a fava. A travessia evitou {co2}, medido "
            "e certificado pela Anemos: escaneie o código para conferir. Um "
            "cacau orgânico intacto, da fava à barra — e o planeta poupado."
        ),
    },
}

_SHORT: dict[str, dict[str, str]] = {
    "equateur": {
        "fr": "Cacao Nacional d'Équateur, traversé à la voile, pas en conteneur. {co2}",
        "en": "Ecuadorian Nacional cacao, sailed across, not boxed. {co2}",
        "pt-br": "Cacau Nacional do Equador, atravessou à vela, não num contêiner. {co2}",
    },
    "perou": {
        "fr": "Cacao fin d'Amazonie péruvienne, traversé à la voile. {co2}",
        "en": "Fine Peruvian Amazon cacao, sailed across. {co2}",
        "pt-br": "Cacau fino da Amazônia peruana, atravessou à vela. {co2}",
    },
    "republique_dominicaine": {
        "fr": "Cacao bio de République dominicaine, traversé à la voile. {co2}",
        "en": "Organic Dominican cacao, sailed across. {co2}",
        "pt-br": "Cacau orgânico da República Dominicana, atravessou à vela. {co2}",
    },
}

# Valeurs génériques (récit sans booking) — utilisées quand un champ n'est
# pas injecté. Aucun chiffre inventé : le CO₂ reste qualitatif sans valeur.
_DEFAULT_PRODUCER = {
    "fr": "nos coopératives partenaires",
    "en": "our partner cooperatives",
    "pt-br": "nossas cooperativas parceiras",
}
_DEFAULT_REGION = {
    "equateur": {
        "fr": "les terres à cacao d'Équateur",
        "en": "Ecuador's cacao lands",
        "pt-br": "as terras de cacau do Equador",
    },
    "perou": {
        "fr": "l'Amazonie péruvienne",
        "en": "the Peruvian Amazon",
        "pt-br": "a Amazônia peruana",
    },
    "republique_dominicaine": {
        "fr": "les terres à cacao de République dominicaine",
        "en": "the Dominican Republic's cacao lands",
        "pt-br": "as terras de cacau da República Dominicana",
    },
}

# Exemples d'illustration pour la vitrine (clairement présentés comme tels ;
# les valeurs réelles proviennent du certificat, vérifiables via le QR).
_MARKETING_EXAMPLE: dict[str, dict[str, object]] = {
    "equateur": {
        "vessel": "Anemos",
        "co2_kg": 260,
        "region": {"fr": "Los Ríos", "en": "Los Ríos", "pt-br": "Los Ríos"},
        "producer": {
            "fr": "une coopérative de cacao Nacional",
            "en": "a Nacional cacao cooperative",
            "pt-br": "uma cooperativa de cacau Nacional",
        },
        "title": {
            "fr": "Équateur — cacao Nacional (Arriba)",
            "en": "Ecuador — Nacional cacao (Arriba)",
            "pt-br": "Equador — cacau Nacional (Arriba)",
        },
    },
    "perou": {
        "vessel": "Artemis",
        "co2_kg": 290,
        "region": {"fr": "San Martín", "en": "San Martín", "pt-br": "San Martín"},
        "producer": {
            "fr": "une coopérative amazonienne",
            "en": "an Amazonian cooperative",
            "pt-br": "uma cooperativa amazônica",
        },
        "title": {
            "fr": "Pérou — cacao fin d'Amazonie",
            "en": "Peru — fine Amazon cacao",
            "pt-br": "Peru — cacau fino da Amazônia",
        },
    },
    "republique_dominicaine": {
        "vessel": "Anemos",
        "co2_kg": 240,
        "region": {"fr": "Duarte", "en": "Duarte", "pt-br": "Duarte"},
        "producer": {
            "fr": "une coopérative biologique",
            "en": "an organic cooperative",
            "pt-br": "uma cooperativa orgânica",
        },
        "title": {
            "fr": "République dominicaine — cacao bio",
            "en": "Dominican Republic — organic cacao",
            "pt-br": "República Dominicana — cacau orgânico",
        },
    },
}


def is_valid_origin(origin: str | None) -> bool:
    """Vrai si ``origin`` est une origine cacao connue (validation formulaire)."""
    return bool(origin) and origin in ORIGINS


def origin_label(origin: str | None, lang: str = _DEFAULT_LANG) -> str:
    """Nom de pays d'une origine (ex. « Équateur »), vide si inconnue."""
    if origin not in ORIGINS:
        return ""
    return _COUNTRY[origin][_norm_lang(lang)]


def _norm_lang(lang: str | None) -> str:
    """Langue du kit (es → fr ; inconnue → fr)."""
    code = (lang or "").lower()
    return code if code in KIT_LANGS else _DEFAULT_LANG


def _fmt_int(n: int, lang: str) -> str:
    """Entier avec séparateur de milliers (espace en fr/pt-br, virgule en en)."""
    grouped = f"{n:,}"  # 1,200
    if lang == "en":
        return grouped
    return grouped.replace(",", " ")  # espace insécable : 1 200


def _co2_phrase(lang: str, co2_kg: int | None) -> str:
    """Quantité de CO₂ évité pour le récit long (sans % ; sans chiffre si None)."""
    if co2_kg is None:
        return {
            "fr": "le CO₂ d'un transport conventionnel équivalent",
            "en": "the CO₂ of an equivalent conventional shipment",
            "pt-br": "o CO₂ de um transporte convencional equivalente",
        }[lang]
    n = _fmt_int(int(co2_kg), lang)
    return {
        "fr": f"{n} kg de CO₂",
        "en": f"{n} kg of CO₂",
        "pt-br": f"{n} kg de CO₂",
    }[lang]


def _co2_phrase_short(lang: str, co2_kg: int | None) -> str:
    """Mention CO₂ pour le format court (étiquette / réseaux)."""
    if co2_kg is None:
        return {
            "fr": "CO₂ évité certifié Anemos, vérifiable.",
            "en": "CO₂ avoided certified by Anemos, verifiable.",
            "pt-br": "CO₂ evitado certificado pela Anemos, verificável.",
        }[lang]
    n = _fmt_int(int(co2_kg), lang)
    return {
        "fr": f"{n} kg de CO₂ évités, vérifiables.",
        "en": f"{n} kg of CO₂ avoided, verifiable.",
        "pt-br": f"{n} kg de CO₂ evitados, verificáveis.",
    }[lang]


def _vessel_clause(lang: str, vessel: str | None) -> str:
    """Groupe nominal du navire avec l'article qui va bien selon la langue."""
    if not vessel:
        return {
            "fr": "l'un de nos voiliers-cargos",
            "en": "one of our sailing cargo ships",
            "pt-br": "de um de nossos veleiros de carga",
        }[lang]
    # Tous nos navires commencent par « A » → élision systématique en fr.
    return {"fr": f"l'{vessel}", "en": f"the {vessel}", "pt-br": f"do {vessel}"}[lang]


def _region_clause(lang: str, origin: str, region: str | None) -> str:
    """Région + pays (ex. « Los Ríos, Équateur ») ou phrase générique."""
    if not region:
        return _DEFAULT_REGION[origin][lang]
    return f"{region}, {_COUNTRY[origin][lang]}"


def render_story(
    origin: str,
    lang: str = _DEFAULT_LANG,
    fmt: str = "long",
    *,
    region: str | None = None,
    producer: str | None = None,
    vessel: str | None = None,
    co2_kg: int | None = None,
) -> str:
    """Rend un récit d'origine cacao en **texte brut**.

    ``origin`` ∈ :data:`ORIGINS` ; ``fmt`` ∈ ``{"long", "short"}``. Les champs
    laissés à ``None`` retombent sur une formulation générique (aucun chiffre
    de CO₂ inventé). Lève ``KeyError`` si l'origine est inconnue.
    """
    if origin not in ORIGINS:
        raise KeyError(f"origine cacao inconnue : {origin!r}")
    lng = _norm_lang(lang)
    table = _SHORT if fmt == "short" else _LONG
    if fmt == "short":
        return table[origin][lng].format(co2=_co2_phrase_short(lng, co2_kg))
    return table[origin][lng].format(
        region=_region_clause(lng, origin, region),
        producer=producer or _DEFAULT_PRODUCER[lng],
        vessel=_vessel_clause(lng, vessel),
        co2=_co2_phrase(lng, co2_kg),
    )


def marketing_example(origin: str, lang: str = _DEFAULT_LANG) -> dict[str, object]:
    """Valeurs d'illustration pour ``/solutions/cacao`` (titre + champs + co2_kg)."""
    if origin not in ORIGINS:
        raise KeyError(f"origine cacao inconnue : {origin!r}")
    lng = _norm_lang(lang)
    ex = _MARKETING_EXAMPLE[origin]
    return {
        "origin": origin,
        "title": ex["title"][lng],  # type: ignore[index]
        "region": ex["region"][lng],  # type: ignore[index]
        "producer": ex["producer"][lng],  # type: ignore[index]
        "vessel": ex["vessel"],
        "co2_kg": ex["co2_kg"],
    }
