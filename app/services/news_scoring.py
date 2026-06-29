"""EVO-04 (socle) — scoring heuristique de pertinence des actualités de veille.

Première couche **déterministe et sans dépendance externe** : un score 0–100 par
correspondance de mots-clés des domaines NEWTOWT (décarbonation, propulsion
vélique, fret maritime, réglementation, ports, écosystème/concurrents). Donne
une priorité à l'écran sans appel réseau. La **synthèse / scoring IA** (Claude)
viendra en couche optionnelle au-dessus de ce socle.
"""

import re

# (poids, mots-clés en minuscules) par thème. Un thème touché ajoute son poids
# une seule fois (présence binaire), score plafonné à 100. Les mots-clés sont
# cherchés en **début de mot** (``\b`` + préfixe) : « port » matche
# « port/ports/portuaire » mais pas « sport/transport ».
_KEYWORD_WEIGHTS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (30, ("décarbon", "decarbon", "zéro émission", "zero emission", "low carbon", "bas carbone")),
    (30, ("voile", "wind propulsion", "wind-assisted", "sail cargo", "vélique", "wingsail", "rotor sail")),
    (20, ("fret maritime", "cargo ship", "shipping", "transport maritime", "supply chain", "freight")),
    (15, ("mrv", "cii", "imo", "fueleu", "ets maritime", "réglementation", "regulation", "carbon tax")),
    (15, ("port", "escale", "terminal", "douane")),
    (10, ("towt", "neoline", "grain de sail", "windcoop", "canopée", "zéphyr", "sailcargo")),
)

# Regex compilées par thème (alternation des mots-clés, ancrés en début de mot).
_THEME_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = tuple(
    (weight, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")"))
    for weight, keywords in _KEYWORD_WEIGHTS
)


def score_news_item(title: str | None, description: str | None = None) -> int:
    """Score de pertinence 0–100 (somme des poids des thèmes touchés, plafonné)."""
    text = f"{title or ''} {description or ''}".lower()
    score = sum(weight for weight, pattern in _THEME_PATTERNS if pattern.search(text))
    return min(score, 100)


def priority_label(score: int) -> str:
    """Étiquette de priorité dérivée du score."""
    if score >= 60:
        return "haute"
    if score >= 30:
        return "moyenne"
    return "faible"
