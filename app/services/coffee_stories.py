"""Récits d'origine du kit B2B2C — café vert transporté à la voile.

Gabarits **pré-remplis** : l'ERP injecte région, producteur, navire et le
CO₂ évité (``[X] kg``) depuis le booking / le certificat Anemos ; la page
vitrine ``/solutions/cafe`` les rend avec des valeurs d'exemple.

Fonctions **pures** (sans I/O) — testées dans ``tests/unit``. Garde-fous :

* **aucun pourcentage** (les chiffres sont en kg, jamais en %) ;
* « **certifié Anemos** » (jamais « label ») ;
* le CO₂ évité reste **vérifiable via le QR** ``/verify``.

Le récit est renvoyé en **texte brut** (pas de HTML / markdown) : les
valeurs injectées par l'ERP peuvent provenir d'une saisie client, donc on
ne marque jamais la sortie ``|safe`` — l'auto-échappement Jinja s'applique.
"""

from __future__ import annotations

# Origines café desservies (ordre d'affichage sur /solutions/cafe).
ORIGINS: tuple[str, ...] = ("colombie", "guatemala", "mexique")

# Langues du kit (le castillan retombe sur le français — cf. _norm_lang).
KIT_LANGS: tuple[str, ...] = ("fr", "en", "pt-br")
_DEFAULT_LANG = "fr"

# Pays par origine, décliné par langue (intégré au champ « région »).
_COUNTRY: dict[str, dict[str, str]] = {
    "colombie": {"fr": "Colombie", "en": "Colombia", "pt-br": "Colômbia"},
    "guatemala": {"fr": "Guatemala", "en": "Guatemala", "pt-br": "Guatemala"},
    "mexique": {"fr": "Mexique", "en": "Mexico", "pt-br": "México"},
}

# Gabarits de récit : origine → langue → format. Placeholders nommés
# {region} {producer} {vessel} {co2} remplis par render_story().
_LONG: dict[str, dict[str, str]] = {
    "colombie": {
        "fr": (
            "Ce café a poussé en altitude dans {region}, sur les pentes andines, "
            "lavé et séché par {producer}. Plutôt que de traverser l'Atlantique "
            "dans un conteneur surchauffé, il a voyagé à la voile à bord de "
            "{vessel}, sous la ligne de flottaison, à la température de la mer — "
            "à l'abri des à-coups thermiques qui éteignent l'acidité fine d'un "
            "grain de haute altitude. La traversée a évité {co2}, mesuré et "
            "certifié Anemos : scannez le code pour le vérifier. Un café qui "
            "arrive comme il est parti — et qui n'a pas réchauffé la planète pour "
            "venir jusqu'à vous."
        ),
        "en": (
            "This coffee grew at altitude in {region}, on the Andean slopes, "
            "washed and dried by {producer}. Instead of crossing the Atlantic in "
            "an overheating container, it travelled under sail aboard {vessel}, "
            "below the waterline, at sea temperature — shielded from the thermal "
            "swings that dull the bright acidity of a high-grown bean. The "
            "crossing avoided {co2}, measured and certified by Anemos: scan the "
            "code to check it. Coffee that arrives as it left — without warming "
            "the planet to reach you."
        ),
        "pt-br": (
            "Este café cresceu em altitude em {region}, nas encostas andinas, "
            "lavado e seco por {producer}. Em vez de cruzar o Atlântico num "
            "contêiner superaquecido, viajou à vela a bordo {vessel}, abaixo da "
            "linha de flutuação, à temperatura do mar — protegido das variações "
            "térmicas que apagam a acidez fina de um grão de altitude. A "
            "travessia evitou {co2}, medido e certificado pela Anemos: escaneie o "
            "código para conferir. Um café que chega como partiu — sem esquentar "
            "o planeta para chegar até você."
        ),
    },
    "guatemala": {
        "fr": (
            "Ce café a mûri sur des sols volcaniques d'altitude, à {region}, "
            "cultivé par {producer}. Plutôt que de subir un conteneur qui "
            "surchauffe à l'approche de l'équateur, il a voyagé à la voile à bord "
            "de {vessel}, sous la ligne de flottaison, à la température de la mer, "
            "à l'abri de la condensation qui ternit les arômes. La traversée a "
            "évité {co2}, mesuré et certifié Anemos : scannez le code pour le "
            "vérifier. La richesse du volcan, préservée jusqu'à votre tasse — "
            "sans réchauffer la planète."
        ),
        "en": (
            "This coffee ripened on high-altitude volcanic soils in {region}, "
            "grown by {producer}. Instead of enduring a container that overheats "
            "near the equator, it travelled under sail aboard {vessel}, below the "
            "waterline, at sea temperature, shielded from the condensation that "
            "dulls aroma. The crossing avoided {co2}, measured and certified by "
            "Anemos: scan the code to check it. The richness of the volcano, "
            "preserved to your cup — without warming the planet."
        ),
        "pt-br": (
            "Este café amadureceu em solos vulcânicos de altitude, em {region}, "
            "cultivado por {producer}. Em vez de enfrentar um contêiner que "
            "superaquece perto do equador, viajou à vela a bordo {vessel}, abaixo "
            "da linha de flutuação, à temperatura do mar, protegido da "
            "condensação que apaga os aromas. A travessia evitou {co2}, medido e "
            "certificado pela Anemos: escaneie o código para conferir. A riqueza "
            "do vulcão, preservada até a sua xícara — sem esquentar o planeta."
        ),
    },
    "mexique": {
        "fr": (
            "Ce café a poussé à l'ombre, en altitude, à {region}, cultivé par "
            "{producer}. Plutôt que de traverser l'Atlantique dans un conteneur "
            "surchauffé, il a voyagé à la voile à bord de {vessel}, sous la ligne "
            "de flottaison, à la température de la mer — à l'abri des à-coups "
            "thermiques qui fatiguent un grain délicat. La traversée a évité "
            "{co2}, mesuré et certifié Anemos : scannez le code pour le vérifier. "
            "La douceur d'un café d'ombre, intacte — et la planète épargnée."
        ),
        "en": (
            "This coffee grew in the shade, at altitude, in {region}, cultivated "
            "by {producer}. Instead of crossing the Atlantic in an overheating "
            "container, it travelled under sail aboard {vessel}, below the "
            "waterline, at sea temperature — shielded from the thermal swings "
            "that tire a delicate bean. The crossing avoided {co2}, measured and "
            "certified by Anemos: scan the code to check it. The softness of a "
            "shade-grown coffee, intact — and the planet spared."
        ),
        "pt-br": (
            "Este café cresceu à sombra, em altitude, em {region}, cultivado por "
            "{producer}. Em vez de cruzar o Atlântico num contêiner "
            "superaquecido, viajou à vela a bordo {vessel}, abaixo da linha de "
            "flutuação, à temperatura do mar — protegido das variações térmicas "
            "que cansam um grão delicado. A travessia evitou {co2}, medido e "
            "certificado pela Anemos: escaneie o código para conferir. A "
            "suavidade de um café de sombra, intacta — e o planeta poupado."
        ),
    },
}

_SHORT: dict[str, dict[str, str]] = {
    "colombie": {
        "fr": "Café des Andes, traversé à la voile, pas en conteneur. {co2}",
        "en": "Andean coffee, sailed across, not boxed. {co2}",
        "pt-br": "Café dos Andes, atravessou à vela, não num contêiner. {co2}",
    },
    "guatemala": {
        "fr": "Café volcanique du Guatemala, traversé à la voile. {co2}",
        "en": "Guatemalan volcanic coffee, sailed across. {co2}",
        "pt-br": "Café vulcânico da Guatemala, atravessou à vela. {co2}",
    },
    "mexique": {
        "fr": "Café d'ombre du Mexique, traversé à la voile. {co2}",
        "en": "Mexican shade-grown coffee, sailed across. {co2}",
        "pt-br": "Café de sombra do México, atravessou à vela. {co2}",
    },
}

# Valeurs génériques (récit sans booking) — utilisées quand un champ n'est
# pas injecté. Aucun chiffre inventé : le CO₂ reste qualitatif sans valeur.
_DEFAULT_PRODUCER = {
    "fr": "nos producteurs partenaires",
    "en": "our partner growers",
    "pt-br": "nossos produtores parceiros",
}
_DEFAULT_REGION = {
    "colombie": {
        "fr": "les hautes terres de Colombie",
        "en": "Colombia's highlands",
        "pt-br": "as terras altas da Colômbia",
    },
    "guatemala": {
        "fr": "les hautes terres du Guatemala",
        "en": "Guatemala's highlands",
        "pt-br": "as terras altas da Guatemala",
    },
    "mexique": {
        "fr": "les hautes terres du Mexique",
        "en": "Mexico's highlands",
        "pt-br": "as terras altas do México",
    },
}

# Exemples d'illustration pour la vitrine (clairement présentés comme tels ;
# les valeurs réelles proviennent du certificat, vérifiables via le QR).
_MARKETING_EXAMPLE: dict[str, dict[str, object]] = {
    "colombie": {
        "vessel": "Anemos",
        "co2_kg": 250,
        "region": {"fr": "Huila", "en": "Huila", "pt-br": "Huila"},
        "producer": {
            "fr": "une coopérative du Huila",
            "en": "a Huila cooperative",
            "pt-br": "uma cooperativa de Huila",
        },
        "title": {
            "fr": "Colombie — café des Andes",
            "en": "Colombia — Andean coffee",
            "pt-br": "Colômbia — café dos Andes",
        },
    },
    "guatemala": {
        "vessel": "Artemis",
        "co2_kg": 300,
        "region": {"fr": "Huehuetenango", "en": "Huehuetenango", "pt-br": "Huehuetenango"},
        "producer": {
            "fr": "une coopérative de Huehuetenango",
            "en": "a Huehuetenango cooperative",
            "pt-br": "uma cooperativa de Huehuetenango",
        },
        "title": {
            "fr": "Guatemala — café volcanique",
            "en": "Guatemala — volcanic coffee",
            "pt-br": "Guatemala — café vulcânico",
        },
    },
    "mexique": {
        "vessel": "Anemos",
        "co2_kg": 280,
        "region": {"fr": "Chiapas", "en": "Chiapas", "pt-br": "Chiapas"},
        "producer": {
            "fr": "une coopérative du Chiapas",
            "en": "a Chiapas cooperative",
            "pt-br": "uma cooperativa do Chiapas",
        },
        "title": {
            "fr": "Mexique — café d'ombre",
            "en": "Mexico — shade-grown coffee",
            "pt-br": "México — café de sombra",
        },
    },
}


def _norm_lang(lang: str | None) -> str:
    """Langue du kit (es → fr ; inconnue → fr)."""
    code = (lang or "").lower()
    return code if code in KIT_LANGS else _DEFAULT_LANG


def _fmt_int(n: int, lang: str) -> str:
    """Entier avec séparateur de milliers (espace en fr/pt-br, virgule en en)."""
    grouped = f"{n:,}"  # 1,200
    if lang == "en":
        return grouped
    return grouped.replace(",", " ")  # espace insécable : 1 200


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
    """Région + pays (ex. « Huila, Colombie ») ou phrase générique."""
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
    """Rend un récit d'origine en **texte brut**.

    ``origin`` ∈ :data:`ORIGINS` ; ``fmt`` ∈ ``{"long", "short"}``. Les champs
    laissés à ``None`` retombent sur une formulation générique (aucun chiffre
    de CO₂ inventé). Lève ``KeyError`` si l'origine est inconnue.
    """
    if origin not in ORIGINS:
        raise KeyError(f"origine café inconnue : {origin!r}")
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
    """Valeurs d'illustration pour ``/solutions/cafe`` (titre + champs + co2_kg)."""
    if origin not in ORIGINS:
        raise KeyError(f"origine café inconnue : {origin!r}")
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
