# ADR-014 — Reprise de l'historique TOWT : archive immuable dans l'ERP

- **Date** : 2026-09-02
- **Statut** : **proposé** — en attente de l'arbitrage de Julien Gondé (décisions 1 à 5 implémentées sur branche, décision 6 ouverte)
- **Décideur** : Julien Gondé
- **Rédaction** : suites de l'audit des sources d'historique (`docs/audit/2026-09-02-reprise-historique-towt.md`)

NEWTOWT reprend une compagnie antérieure (TOWT) dont les navires naviguent
depuis 08/2024. L'ERP a démarré à vide en 2026 (décision Q1, MRV v2). Julien
demande de reprendre l'historique — voyages, relevés GPS, noon reports — de
façon **non modifiable** et **filtrable avec la mention TOWT**. Ces six
décisions fixent comment cet historique entre dans l'ERP sans en dégrader les
invariants (chemin unique du réel, renumérotation des codes, grand livre des
émissions, purges administrées).

---

## Décision 1 — L'archive vit dans les tables de production, marquée par une origine

**Contexte.** Deux options : une table `legs_archive` séparée (isolation
totale, aucun consommateur ne la voit) ou les tables existantes avec un
marqueur d'origine (tous les consommateurs — cartes, KPI navigation, distances
réelles, filtre transverse — fonctionnent sans code spécifique).

**Décision.** Colonne `legs.origin` (`newtowt` par défaut serveur, ou
`towt_archive`), indexée ; positions GPS dans `vessel_positions` avec
`source = 'towt_archive'`. Propriété `Leg.is_archive`.

**Ce que cela implique.**
- La valeur opérationnelle est immédiate : `/tracking`, `/performance/navigation`
  et la fiche leg lisent l'archive comme un voyage ordinaire.
- Tout consommateur qui agrège des legs voit 2024-2025 apparaître ; le badge
  « TOWT » et le filtre d'origine de `/planning` rendent la provenance lisible.
  Aucun agrégat financier, commercial ou carbone n'est alimenté (les sources
  ne les contiennent pas).

*Alternative écartée* : table d'archive dédiée. Rejetée — elle imposait de
dupliquer les vues (carte, distances, historique de trajets) pour un gain
d'isolation que le marqueur + les gardes procurent déjà.

---

## Décision 2 — Le code d'archive est le TRIP CODE TOWT d'origine, hors renumérotation

**Contexte.** `leg_code` ERP = `{navire}{rang}{pays POL}{pays POD}{année}`,
recalculé à chaque insertion par `renumber_vessel_year`. Les codes TOWT
(`1YMB4`, `2LQF5-B`) suivent deux conventions successives non reconstructibles
(caractères de port en collision) et sont la clé citée par les noon reports
(« Voyage number ») et l'ancien tableau de bord (TRIP CODE). Insérer un leg
d'archive dans 2026 aurait décalé les codes des legs NEWTOWT en cours.

**Décision.** `leg_code` = TRIP CODE TOWT verbatim ; `renumber_vessel_year`
exclut `origin = 'towt_archive'` (comme les legs annulés).

**Ce que cela implique.**
- Rapprochement direct archive ↔ noon reports ↔ PBIX.
- Les codes ERP 2026 restent stables ; la lettre de rang ne compte que les
  voyages vécus dans l'ERP.
- Un homonyme NEWTOWT bloque l'import (collision explicite).

*Alternative écartée* : recoder au format ERP (`1AUSFR6`) et garder le code
TOWT dans une colonne `legacy_code`. Rejetée — deux legs « rang A » en 2026
(un archive, un vécu) auraient rendu la lettre de rang mensongère.

---

## Décision 3 — Lecture seule par garde unique, pas par absence d'écran

**Contexte.** L'ERP connaît deux façons de figer : supprimer les endpoints
(`/mrv/archive/events`) ou une garde d'exécution (`_assert_escale_unlocked`).
Les legs d'archive partagent les écrans des legs vivants.

**Décision.** Trois couches, de la plus lisible à la plus dure :
1. `services.planning.assert_leg_mutable(leg)` lève `LegArchivedError`
   (sous-classe de `PlanningError`, donc déjà rendue en 400 lisible par les
   routes) ; appelée dans `update_leg`, `delete_leg`,
   `voyage_transitions.declare_departure/declare_arrival`, `scenario.apply`,
   le décalage d'ETA et la saisie SOF du bord ; le cockpit escale considère
   l'archive verrouillée (message dédié, déverrouillage refusé). La fiche
   masque « Éditer » / « Supprimer » et affiche un bandeau.
2. **Garde ORM** (`before_flush` sur `Leg`, `app/models/leg.py`) : tout
   UPDATE/DELETE d'un leg d'archive est refusé quel que soit l'écrivain —
   y compris ceux qui n'appellent pas la garde de service (la revue critique a
   trouvé deux tels chemins dès le premier jour : `scenario.apply_to_active_planning`
   et le décalage d'ETA du bord). Échappement explicite réservé aux scripts :
   `session.info["allow_towt_archive_write"] = True`.
3. **Trigger PostgreSQL** (`trg_legs_towt_archive_readonly`, migration 0138) :
   `BEFORE UPDATE OR DELETE` refuse la ligne si `origin = 'towt_archive'`,
   sauf `SET LOCAL newtowt.allow_towt_archive_write = 'on'`. L'immutabilité est
   une propriété du schéma, pas une promesse applicative.

**Ce que cela implique.**
- Le réel d'archive (ATD/ATA) est posé **directement** par le script d'import,
  pas par `voyage_transitions` : il n'y a ni SOF, ni ETA à re-ancrer, ni leg
  suivant à activer. C'est l'exception assumée à la règle « ne jamais écrire
  `leg.atd/ata` ailleurs », déjà pratiquée par `import_mrv_2025`.
- Aucun prévisionnel n'est inventé : `etd_ref = etd = atd`, `eta_ref = eta = ata`
  (minuit UTC), `status = completed`, `voyage_completed_at = ata`, `closure_*`
  laissés vides sauf la note de provenance.

*Alternative écartée* : `legs` d'archive en `status = 'cancelled'` pour
profiter de l'exclusion existante. Rejetée — un voyage réalisé n'est pas annulé ;
la phase affichée serait fausse.

---

## Décision 4 — Les positions d'archive sont protégées de toute purge, au niveau ligne

**Contexte.** `vessel_positions` est purgeable (`ALLOWED_PURGE_TABLES`, aussi
par rétention) — légitime pour le live, inacceptable pour un historique repris.
Sortir la table des purges priverait l'exploitation d'un outil de rétention.

**Décision.** `admin_data.PURGE_PROTECTED_ROWS = {"vessel_positions": ("source",
"towt_archive")}` : chaque DELETE (intégral ou par rétention) porte
`source != 'towt_archive'`. Colonne et valeur viennent de la whitelist, la
valeur est un paramètre lié.

**Ce que cela implique.** La rétention du live continue ; l'archive est
inaltérable par l'écran d'administration. Un DELETE SQL direct reste possible,
comme pour toute table — le garde-fou est applicatif, documenté ici.

*Alternative écartée* : table `vessel_positions_archive`. Rejetée — les
consommateurs (fenêtre temporelle par leg, distance réelle) auraient dû
interroger deux tables.

---

## Décision 5 — L'import est un script serveur, alimenté par une consolidation locale

**Contexte.** Les relevés GPS sont ~32 000 fichiers d'1,4 Ko sur SharePoint ;
`POST /api/tracking/upload` déduplique ligne par ligne (un SELECT par point) et
plafonne à 20 Mo. Les noon reports sont ~1 300 classeurs Excel.

**Décision.** Deux étages : (a) sur le poste synchronisé, un script en
bibliothèque standard consolide en un CSV par navire et par an + manifeste
SHA-256 ; (b) sur le serveur, `import_towt_positions` insère en lot après
préchargement des clés existantes — idempotent, dry-run par défaut,
`import_batch` = fichier consolidé. Même patron pour les legs
(`import_towt_legs` + CSV versionné dans `scripts/data/`). L'affichage de
`/tracking` est décimé (4 000 points) ; les distances restent calculées sur la
trace complète.

**Ce que cela implique.** Le manifeste est la preuve de complétude (fichiers
lus, points, trous > 6 h, empreintes). Le jeu de données consolidé est
rejouable à l'identique.

*Alternative écartée* : rejouer les 32 000 fichiers via l'endpoint HTTP.
Rejetée — coût réseau et base sans valeur ajoutée, pas de manifeste.

---

## Décision 6 — Noon reports : table d'archive dédiée, en attente d'arbitrage

**Contexte.** Trois cibles possibles : `noon_reports` legacy (gelée en écriture
depuis le LOT 14 — y écrire rouvre une table décommissionnée), `nav_events`
MRV v2 (périmètre réglementaire, décision Q1 « démarrage à vide », statut
`valide` fabriqué), ou une **nouvelle table d'archive** (`towt_noon_reports` :
identité, horodatage UTC, position, voyage TOWT, charge utile JSON du formulaire
complet, SHA-256 du fichier source) avec un viewer lecture seule et une
inscription dans `NEVER_PURGE_TABLES`.

**Décision (proposée).** Nouvelle table d'archive, hors périmètre MRV, aucune
émission calculée (règle d'or `emission_ledger`), alimentée par la sortie NDJSON
de `scripts/towt_noon_extract.py`. À arbitrer par Julien après un premier passage
du prototype sur les classeurs 2024-2025.

**Ce que cela implique.** Lot 2 distinct (migration, modèle, viewer, tests).
Les indicateurs « profil de propulsion » et « cales » de l'ancien tableau de
bord pourront être reconstruits en lecture seule depuis cette archive, sans
toucher au grand livre.

*Alternative écartée* : `nav_events` en statut `valide`. Rejetée — cela
introduirait dans le périmètre MRV des données que le Master n'a pas
finalisées dans l'outil, en contradiction avec Q1 et Q6.

---

## Décision 7 — L'archive est hors séquence vivante et hors indicateurs publiés

**Contexte.** Les legs d'archive partagent `vessel_id` avec les legs NEWTOWT
et s'étendent jusqu'au 2026-01-31. La revue critique a montré que, sans
exclusion, ils entraient dans : la validation de chevauchement et de
continuité (`validate_leg_schedule`), la cascade aval (`_lane_after`), les
voisins de séquence (`_previous_legs`, `_next_leg`, `repair_vessel_sequence`),
l'audit de séquence et les conflits de port de `/planning`, le taux de
ponctualité **publié** (`service_reliability` — un « prévu » égal au réel donne
100 % de départs tenus par construction), le compteur public de traversées
(`social_proof`), le contrôle qualité MRV nocturne (`_leg_is_active`), et le
filtre transverse `leg_filter` dont dix modules dépendent (dont `/kpi`, qui
**écrit** des `LegKPI` par leg).

**Décision.** Un leg d'archive n'appartient pas à la séquence vivante du
navire ni aux indicateurs de la nouvelle compagnie : exclu de toutes ces
requêtes. `build_leg_filter(include_archive=False)` par défaut ; seules les
pages de lecture (`/tracking`, `/performance/navigation`) l'activent. Les
traces GPS sont lues en lignes allégées (pas d'instances ORM) et décimées à
l'affichage.

**Ce que cela implique.**
- Le premier leg NEWTOWT d'un navire n'a **pas** l'archive pour prédécesseur :
  pas de rupture de continuité artificielle, pas de cascade bloquée.
- Le taux de service et le compteur de traversées de la vitrine ne comptent
  que NEWTOWT. Si la Direction souhaite y inclure la période TOWT, c'est une
  décision de communication distincte, avec méthodologie publiée.
- Le contrôle qualité MRV ne signale jamais une archive (elle n'a ni événements
  ni clôture à attendre).

*Alternative écartée* : une seule séquence par navire, archives comprises.
Rejetée — les ruptures connues de l'archive (arrêts techniques non tracés)
auraient rendu les legs NEWTOWT voisins non éditables et généré des alertes
`cascade_blocked` que personne ne peut résoudre.

---

## Conséquences transverses et suites

- Décision Q1 (`REGLES_GESTION_DONNEES_EMISSIONS.md` §8) **partiellement
  rouverte** : des données historiques entrent en production, mais hors MRV.
- `leg_filter` étend sa fenêtre d'années jusqu'à la première ETD en base.
- Points à surveiller : qualité des ports créés (`source=user`) à raffiner
  dans Admin → Ports ; couverture GPS avant le 2024-10-21 à confirmer ;
  ~40 sites `select(Leg)` dans l'application — une sentinelle listant les
  modules autorisés à voir l'archive (à la manière de
  `tests/unit/test_delete_leg_models.py`) reste à écrire (lot 2).
