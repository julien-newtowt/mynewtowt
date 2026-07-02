"""Seed des 6 premiers billets du carnet éditorial (P8).

Contenu adossé aux faits du dossier : livraisons Atlantis/Atlas/Archimedes/
Astérias, ligne Brésil, arrivées café. Garde-fous : jalons **positifs**
uniquement (aucune critique du constructeur, aucun défaut) ; allégations
environnementales **factuelles** (kg absolus, « certifié Anemos », vérifiable —
jamais de pourcentage ni de superlatif). Idempotent : ne réécrit pas un billet
déjà présent (par slug).

Run :
  docker compose exec app python -m scripts.seed_carnet
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models.blog_post import BlogPost
from app.services.blog import slugify

_UTC = UTC

# (category, topic, title, lead, cover_image, author, published_at, body_html)
ARTICLES: list[tuple] = [
    (
        "carnet",
        "chantier",
        "Atlantis entre en phase d'essais",
        "Le troisième voilier-cargo de la série TSC 80 a quitté la cale de "
        "construction pour ses premiers essais — livraison attendue en juillet 2026.",
        "img/Artemis_devant.jpg",
        "L'équipe NewTowt",
        datetime(2026, 6, 12, 9, 0, tzinfo=_UTC),
        "<p>Atlantis, troisième sistership de la classe <strong>TSC 80</strong>, "
        "a franchi une étape décisive : la coque est achevée et le gréement "
        "installé. Le navire entre désormais en phase d'essais, dernière ligne "
        "droite avant une livraison attendue en <strong>juillet 2026</strong>.</p>"
        "<p>Comme ses aînés Anemos et Artemis, Atlantis embarque 978 emplacements "
        "de palettes en six cales séparées, dont une apte aux marchandises "
        "dangereuses. Il rejoindra la ligne régulière vers le Brésil et "
        "l'Amérique latine.</p>",
    ),
    (
        "carnet",
        "chantier",
        "Atlas prend forme en cale de finition",
        "Quatrième de la série, Atlas avance en cale de finition — livraison "
        "prévue en septembre 2026.",
        "img/sortie-fecamp.jpg",
        "L'équipe NewTowt",
        datetime(2026, 5, 28, 9, 0, tzinfo=_UTC),
        "<p>Atlas, quatrième voilier-cargo de la série, poursuit son assemblage "
        "en cale de finition sur les chantiers Piriou. Ponts, cloisons de cale "
        "et systèmes de ventilation — clés pour transporter le café à la "
        "température de la mer — sont en cours d'installation.</p>"
        "<p>Sa livraison est prévue pour <strong>septembre 2026</strong>. Deux "
        "navires supplémentaires, Archimedes et Astérias, suivront en 2027.</p>",
    ),
    (
        "carnet",
        "chantier",
        "Archimedes et Astérias : la série se poursuit en 2027",
        "Les deux derniers voiliers-cargos de la série de six sont engagés — "
        "livraisons attendues en 2027.",
        "img/Anemos-Artemis_mer.jpg",
        "L'équipe NewTowt",
        datetime(2026, 4, 15, 9, 0, tzinfo=_UTC),
        "<p>La série de six sisterships se complète : <strong>Archimedes</strong> "
        "et <strong>Astérias</strong> sont engagés en construction, pour des "
        "livraisons attendues en <strong>2027</strong>. À terme, six navires "
        "identiques assureront une ligne dense et régulière.</p>"
        "<p>Des coques identiques, c'est pour un chargeur la promesse d'un "
        "service homogène quelle que soit la rotation.</p>",
    ),
    (
        "actualite",
        "clients",
        "La ligne Europe ↔ Brésil passe au rythme régulier",
        "Anemos et Artemis assurent désormais des départs réguliers entre "
        "l'Europe et São Sebastião.",
        "img/Anemos_depart.jpg",
        "L'équipe NewTowt",
        datetime(2026, 3, 20, 9, 0, tzinfo=_UTC),
        "<p>Avec deux voiliers-cargos en service, la ligne <strong>Europe ↔ "
        "Brésil</strong> gagne en régularité. Les départs depuis Fécamp et "
        "Le Havre vers São Sebastião s'enchaînent, ouvrant des créneaux de "
        "réservation d'espace en cale pour le café, le cacao et le fret "
        "industriel.</p>"
        "<p>Chaque traversée fait l'objet d'un certificat Anemos : le CO₂ évité "
        "par lot y est mesuré, en kilogrammes, et vérifiable en scannant le "
        "code du certificat.</p>",
    ),
    (
        "actualite",
        "arrivees",
        "Le premier café de la saison arrive à la voile",
        "Un lot de café vert d'Amérique latine a rejoint l'Europe sous voile, "
        "à la température de la mer.",
        "img/Anemos-Artemis_mer.jpg",
        "L'équipe NewTowt",
        datetime(2026, 2, 10, 9, 0, tzinfo=_UTC),
        "<p>Le premier café de la saison est arrivé : un lot de café vert "
        "d'Amérique latine transporté sous la ligne de flottaison, à la "
        "température de la mer, à l'abri des à-coups thermiques qui fatiguent "
        "un grain de haute altitude.</p>"
        "<p>Pour ce lot, le certificat Anemos documente le CO₂ évité en "
        "kilogrammes — un chiffre par expédition, vérifiable, jamais un "
        "pourcentage. Les torréfacteurs partenaires reçoivent un kit pour le "
        "raconter à leurs clients.</p>",
    ),
    (
        "actualite",
        "equipage",
        "Nos bordées s'étoffent : NewTowt recrute",
        "Pour armer une flotte qui grandit, NewTowt recrute marins et fonctions " "support.",
        "img/equipe-newtowt.jpg",
        "L'équipe NewTowt",
        datetime(2026, 1, 22, 9, 0, tzinfo=_UTC),
        "<p>Six navires demandent des équipages. NewTowt étoffe ses bordées et "
        "ses fonctions à terre : marins, officiers, et métiers support autour "
        "de l'exploitation d'une ligne au long cours.</p>"
        "<p>Rejoindre NewTowt, c'est armer une filière du transport de "
        "marchandises à la voile qui navigue déjà. Les postes ouverts sont "
        "publiés sur la page recrutement.</p>",
    ),
]


async def seed() -> None:
    await init_db()
    async with SessionLocal() as db:
        created = 0
        for category, topic, title, lead, cover, author, published_at, body in ARTICLES:
            slug = slugify(title)
            exists = (
                await db.execute(select(BlogPost.id).where(BlogPost.slug == slug))
            ).scalar_one_or_none()
            if exists:
                continue
            db.add(
                BlogPost(
                    slug=slug,
                    category=category,
                    topic=topic,
                    lang="fr",
                    title=title,
                    lead=lead,
                    body=body,
                    cover_image=cover,
                    author=author,
                    is_published=True,
                    published_at=published_at,
                )
            )
            created += 1
        await db.commit()
        print(f"Carnet seed completed. {created} nouveau(x) billet(s) sur {len(ARTICLES)}.")


if __name__ == "__main__":
    asyncio.run(seed())
