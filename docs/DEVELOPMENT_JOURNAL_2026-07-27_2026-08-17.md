# Journal de développement — 2026-07-27 → 2026-08-17

> Rapport de passation destiné au manager, tenu pendant son absence.
> Une entrée par journée de travail. Voir `PROJECT_CONTEXT.md` pour l'état
> du projet et les audits, `CLAUDE.md` pour les consignes opérationnelles.

---

## 2026-07-27 → 2026-07-29 — Phase de découverte

**Objectif** : comprendre l'architecture, les processus métier et les
interactions entre modules avant tout développement.

**Livrables** :
- `PROJECT_CONTEXT.md` §1-12 — architecture, workflows, rôles/permissions,
  flux portail client ↔ ERP, APIs et intégrations, procédure réelle de
  lancement local (le README était incomplet), dette technique priorisée.
- `PROJECT_CONTEXT.md` §13 — audit de cohérence métier (1re passe).
- `PROJECT_CONTEXT.md` §14 — audit approfondi 6 domaines, regard expert
  maritime (~60 constats, organisés par motif de risque transverse).
- `docs/user-guide/roles-processus-integrations.md` — guide fonctionnel de
  découverte (4 publics, fil rouge d'une expédition, 5W+1H par service).

**Branches** : `docs/decouverte-fonctionnelle` (depuis `main`).
**Commits** : `633d73f` (fondations), `98d9e25` (§13), `3b2a54e` (§14).

**Décisions actées avec Yasmin** :
- Marad porte les certificats navire/ISM et les décisions d'embarquement ;
  Pennylane porte la comptabilité réelle. Ces « trous » de mynewtowt sont
  intentionnels, pas des oublis.
- Le DPA (Code ISM) est le rôle `manager_maritime`, supérieur hiérarchique
  d'Armement et de Technique. Commercial et Opérations sont deux filières
  autonomes sans chef commun.
- **Correction d'une hypothèse erronée** : le MRV UE s'applique bien à cette
  flotte (Règl. UE 2023/957, extension aux 400-5000 GT depuis le 01/01/2025) ;
  les émissions sont déjà vérifiées par un organisme accrédité (THETIS-MRV,
  datasets déposés chez DNV). Le module MRV v2 n'est pas de la
  sur-ingénierie réglementaire.

**Risques identifiés** : voir `PROJECT_CONTEXT.md` §14. Les plus critiques :
faux vert Schengen, absence de registre de connaissements, horodatage ATD/ATA
= heure de saisie, heures voile du carnet de bord surévaluées ×6.

---

## 2026-07-29 — Phase 2, J1 : filet de sécurité CI

**Objectif** : établir l'état réel de la suite de tests avant de modifier du
code sensible (capacité, horodatage, émission de BL). Prérequis à tout
développement.

**Contexte de la décision** : le 2026-07-27, la question « faut-il ajouter
`tests/integration` + `tests/regression` à la CI ? » avait été tranchée par
« pas d'action immédiate ». Décision **rouverte** le 2026-07-29 avec l'accord
de Yasmin : elle était raisonnable tant qu'on ne développait pas, elle ne
l'est plus dès lors qu'on touche à la capacité de cale, à l'horodatage des
faits et à l'émission de connaissements.

**Constat de départ (vérifié)** : `.github/workflows/ci.yml:65` ne lance que
`pytest tests/unit` (84 fichiers). `tests/integration/` (110 fichiers) et
`tests/regression/` (4 fichiers, dont la sentinelle des facteurs d'émission
et le filet de parité V2↔V3) **ne tournent jamais en CI**. Le « 710 passed »
des rapports de déploiement provenait d'exécutions locales.

**Travaux réalisés** :
1. ~~Environnement de test local remis en état — `docker-compose.override.yml`
   étendu pour publier Postgres sur `localhost:5432`, base `towt_test` créée.~~
   **Correction (2026-07-29) : ce travail était inutile.** La suite de tests
   n'utilise aucun Postgres (SQLite en mémoire — voir « limite majeure du
   filet » plus bas). L'étape a bien été réalisée, mais elle n'était pas un
   prérequis d'exécution comme écrit initialement. Le port publié reste utile
   pour l'inspection manuelle de la base et le `pg_dump`.
2. Passe de collecte : **811 tests collectés, zéro erreur d'import** — la
   suite est structurellement saine.
3. Exécution complète de `tests/integration` + `tests/regression`.

**Résultat** : **781 passés · 29 échecs · 1 skip** (96 % de vert).
Trois catégories d'échecs, distinction essentielle :

| Cat. | Nb | Nature | Traitement |
|---|---|---|---|
| ① | ~15 | **Environnement** — rendu PDF, `WeasyPrint could not import some external libraries` (GTK/Pango absents sur l'hôte Windows) | À valider sur le runner Ubuntu de la CI, pas à corriger |
| ② | ~13 | **Tests périmés** — route déplacée sous préfixe (`/offers/…` → `/commercial/offers/…`) ; paramètres de route ajoutés (ex. `incident_location`, ONB-08) et tests appelant la fonction en direct recevant l'objet `Form` (`AttributeError: 'Form' object has no attribute 'strip'`). **Pas des bugs de production** | Corrections d'une ligne |
| ③ | 1 | **À investiguer** — `test_eta_extension_cascades_downstream` : attendu `BASE + 25 j`, obtenu `BASE + 26 j` sur la cascade de dates | Investigation dédiée, à ne pas « corriger » en ajustant l'attente |

**Risque identifié (important)** : l'échec ③ tombe exactement dans une zone
signalée par l'audit (§14.4 — le résolveur de cascade n'ajoute pas la durée
d'escale entre deux legs aval). Un jour d'écart. Soit l'attente du test est
périmée après un changement délibéré non documenté, soit c'est un off-by-one
réel. **Quelqu'un a modifié la cascade et personne ne l'a su, parce que ce
test ne tourne jamais en CI.** C'est la justification empirique du J1.

**Décision technique prise** : ne pas ajuster l'attente du test ③ à la valeur
observée pour obtenir du vert — ce serait entériner un comportement sans
l'avoir compris.

**En attente d'arbitrage Yasmin** : option de mise en place du filet —
(A) corriger les tests périmés puis activer en CI (~2-3 h, recommandé),
(B) activer avec 29 `xfail` documentés (~30 min, risque de noyer ③),
(C) activer tel quel (écarté : CI rouge permanente = filet ignoré).

**Prochaines étapes recommandées** :
1. Trancher l'option A/B/C, puis brancher integration+regression en CI.
2. Investiguer ③ (cascade) — à rapprocher du lot horodatage.
3. Enchaîner sur le J2 (quick wins) : alerte ETA en mer, nom client +
   `leg_code` sur la liste bookings, heures voile ×6, redirection du bouton
   BL vers le rail packing list, 2 micro-gardes BL.

### J1 — suite : option A retenue, travaux réalisés

**Branche** : `chore/ci-integration-tests` (depuis `docs/decouverte-fonctionnelle`).
**Tag de référence** : `pre-upgrade-2026-08`.

**Commits** :

| Commit | Nature |
|---|---|
| `d702678` | `fix(tracking)` — normalisation naïf/aware dans `leg_window` |
| `93d1fda` | `fix(tracking)` — extension de la normalisation à `compute_metrics` |
| `d309497` | `test` — correction de 4 tests périmés |
| `45bfc55` | `docs` — plan d'upgrade phase 2 + création du journal |
| `2eac739` | `ci` — exécution `integration`+`regression`, 7 tests périmés restants |
| `b3c0835` | `docs(journal)` — clôture J1 (titre corrigé depuis : « filet configuré », pas « activé ») |
| `34dc977` | `docs(journal)` — procédure backup/rollback testée (item DoD oublié) |
| `8d972d2` | `docs` — fausse alerte PL corrigée, A4 requalifié en manque fonctionnel |
| `26e4802` | `docs(CLAUDE)` — invariants `PackingList` / `CrewAssignment` |

**Un vrai bug applicatif trouvé** (pas un test périmé) :
`voyage_track.leg_window` comparait `end < start` **sans normaliser** les
bornes, alors que la convention du projet est explicite — la docstring de
`planning.ensure_utc` dit : *« les saisies `datetime-local` sont naïves.
Toute arithmétique de planification passe par ce helper »*. Le cas n'est donc
**pas limité à SQLite** : il peut survenir en production sur une saisie
naïve. `leg_window` alimente le tracking, la navigation et l'association des
positions GPS à un leg.

**Leçon méthodologique — le filet a immédiatement démontré sa valeur** : le
premier correctif (`d702678`) rendait `leg_window` aware mais laissait
`compute_metrics` soustraire ces bornes à des valeurs naïves (`leg.ata`,
`recorded_at`). Le TypeError était **déplacé, pas résolu**, et **2 tests de
KPI navigation qui passaient ont régressé**. Détecté au re-run complet,
corrigé en `93d1fda` (normalisation de bout en bout, `ensure_utc` remonté en
import module — pas de cycle, `planning` n'importe pas `voyage_track`).
Sans ce filet, cette régression partait en production.

**Tests périmés corrigés (aucun n'était un bug de production)** :
- `test_docx_generator` — l'assertion omettait le préfixe du router
  (`APIRouter(prefix="/commercial")`) ; la route existe bien.
- `test_packing_list_delete` — fixture antérieure à la contrainte
  `ck_packing_lists_order_xor_booking`, FK réellement appliquée : ajout de la
  chaîne minimale navire → ports → leg → booking.
- `test_attachments_autofill_reprise` — 2 tests CREW-04 exerçaient
  `crew_router.crew_assign`, **supprimée délibérément** par `9b752bd`
  (« retirer créations manuelles », doctrine Marad = source de vérité).

**Manque fonctionnel réel (requalifié le 2026-07-29 après confirmation
métier)** : l'arbitrage **A4 « embarquement hors leg autorisé »** figure
toujours dans `CLAUDE.md` et `CrewAssignment.leg_id` est toujours nullable,
mais le **seul** producteur de `CrewAssignment` est désormais
`services/escale_crew.py:52`, **appelé avec un leg**. Aucun chemin applicatif
ne crée donc plus d'affectation hors leg.

**Confirmation de Yasmin (2026-07-29)** : *« on peut embarquer des gens
(changement d'équipage) pendant un arrêt technique sans forcément l'associer à
un voyage »*. Le cas d'usage est donc **réel et courant** ⇒ ce n'est pas une
divergence de documentation à trancher, c'est un **manque fonctionnel** :
**il faut restaurer un chemin de saisie, pas retirer A4.**

⚠️ **Conséquence non anticipée, qui aggrave le faux vert Schengen (§14.1)** :
`refresh_schengen_for_members` **saute** les affectations sans leg
(`crew_compliance.py:231-233`, `if leg is None: continue`). Donc même une fois
la saisie restaurée, **les jours d'un embarquement d'arrêt technique ne
seraient pas comptés dans le 90/180**. Les deux correctifs sont liés et
doivent être traités ensemble : restaurer le chemin de saisie **et** faire
lire ces affectations par le calcul Schengen (via `CrewAssignment.vessel_id`
en repli, l'écart était déjà signalé dans `SPEC-CREW-reprise-P0.md:18`).

**État de la suite** : **784 passés · 24 échecs · 1 skip** (contre 781/29/1 au
départ). Ventilation des 24 restants :

| Cat. | Nb | Nature | Reste à faire |
|---|---|---|---|
| ① | ~13 | Rendu PDF / WeasyPrint (GTK/Pango absents sur l'hôte Windows) | À valider sur runner Ubuntu — probablement verts en CI |
| ② | ~6 | Fuite d'objet `Form` : tests appelant les fonctions de route **en direct** sans passer tous les paramètres (`admin_activity_logs`, `admin_data_reprise`, `claim_incident_fields`, `claims_onb06` ×3) | Passer les paramètres explicitement |
| ③ | ~2 | Fixtures antérieures à `ck_packing_lists_order_xor_booking` (`escale_leg_overview`, `portal_messages_read`) | Même patron que `test_packing_list_delete` |
| ④ | 1 | `trombinoscope_notification` | **Diagnostiqué le 2026-07-29 : échec WeasyPrint** (`libgobject-2.0-0.dll`), donc catégorie ① et non un cas à part — la docstring du test l'annonçait déjà (« nécessite WeasyPrint, non disponible en dev local sans GTK3 ») |
| ⑤ | 1 | **`test_eta_extension_cascades_downstream`** — attendu `BASE + 25 j`, obtenu `BASE + 26 j` | **Investigation dédiée** (cf. `PROJECT_CONTEXT.md` §14.4) |

**Décision : la CI n'est PAS encore activée sur integration+regression.** Elle
serait rouge, ce qui transformerait le filet en bruit ignoré (le motif même
que l'option C voulait éviter). Séquence retenue : **corriger d'abord (② ③ ④),
activer ensuite, valider ① sur le runner Ubuntu.**

**Risques identifiés ce jour** :
- Le correctif partiel `d702678` montre qu'une normalisation de fuseau
  appliquée à un seul point d'un module **déplace** le problème. Toute
  correction de ce type doit être vérifiée par un re-run **complet**, pas
  ciblé.
- L'écart cascade (⑤) tombe dans une zone déjà signalée par l'audit. **Ne pas
  ajuster l'attente du test à la valeur observée** pour obtenir du vert.

### J1 — clôture : filet **configuré** (non encore exécuté), suite verte en local

**Commit** : `2eac739` — `ci: executer integration+regression, corriger les 13
tests perimes restants`.

**Résultat final : 2000 passés · 15 échecs · 1 skip.** Les 15 échecs restants
sont **tous** des tests de rendu PDF échouant faute de GTK/Pango sur l'hôte
Windows — **aucun échec de code, aucun test périmé**.

**CI configurée — pas encore exécutée une seule fois** :
`.github/workflows/ci.yml` lance désormais
`pytest tests/unit tests/integration tests/regression`, avec ajout des libs
système Pango/Cairo au job `test` (WeasyPrint en a besoin pour les ~15 tests de
rendu réel).

⚠️ **À ne pas surinterpréter** : le workflow ne se déclenche que sur
`pull_request` ou `push` sur `main`. La branche n'est **pas poussée** et aucune
PR n'existe ⇒ **le pipeline modifié n'a jamais tourné**. Restent donc
hypothétiques : la validation des 15 tests PDF sur Ubuntu, et le fait que
l'installation `apt` couvre bien `libglib2.0-0`/`libharfbuzz0b` (attendus par
transitivité via `libpango`, non explicites) et les polices. Le filet est
**configuré**, son activation effective aura lieu à la première PR.

**Les tests périmés restants corrigés (11), par cause** :
- **Fuite d'objet `Form` (6 tests)** — `claims_onb06` ×3, `claim_incident_fields`,
  `admin_data_reprise` : appelés en direct (hors HTTP), les paramètres non
  passés conservent leur défaut `Form(None)`, qui finit lié en paramètre SQL ou
  comparé à un `int`. Cas typique : `incident_location`/`incident_context`
  ajoutés par ONB-08 après l'écriture des tests.
- **`admin_activity_logs`** — la route plafonne la taille de page à **10
  minimum** (`limit = max(10, min(limit, 500))`, durcissement ADM-08) ; le test
  paginait à 2 sur 3 lignes. Réécrit avec 15 lignes et `limit=10`, intention
  préservée.
- **`escale_leg_overview` / `portal_messages_read`** — fixtures antérieures à
  `ck_packing_lists_order_xor_booking`, FK réellement appliquée.
- **`planning_hardening` (la cascade, ⑤)** — **investigation conclusive**.

**Investigation ⑤ (cascade) — conclusion** : l'attente du test (`leg2.etd =
J+25`, soit **une escale nulle**) précédait l'introduction de la sémantique
`ready_at`. Le code applique `new_ready_at = new_eta + (port_stay_planned_hours
or DEFAULT_PORT_STAY_HOURS)` (`planning.py:702`, défaut **24 h**), et les legs
du test ont `port_stay_planned_hours` à NULL → repli sur 24 h → J+25 devient
J+26. Le commentaire du modèle confirme l'intention : *« le leg suivant du même
navire commence après ETA + port_stay_planned_hours »* (`models/leg.py:60-61`).
**Le code a raison, l'attente était périmée** — vérifié avant modification,
pas ajusté à la valeur observée.

**Affinement d'un constat d'audit** : ceci **précise** §14.4 de
`PROJECT_CONTEXT.md`. L'audit signalait que le résolveur de cascade n'ajoute
pas la durée d'escale entre deux legs aval (`prev_eta = peta`). C'est exact,
**mais** le port stay **est** bien appliqué au **leg source** (via
`source_ready_at`). L'asymétrie est donc précisément localisée : leg source →
premier leg aval reçoit les 24 h ; leg aval N → leg aval N+1 ne les reçoit pas.
Le constat reste valide, sa portée est plus étroite qu'écrit.

**Fausse alerte, corrigée le 2026-07-29** — j'avais signalé une seconde
divergence doc/code sur les packing lists (« PL épinglée au leg impossible en
base »). **C'était une erreur d'analyse de ma part.** Après vérification :
`PackingList` porte `order_id`, `booking_id` **et** `leg_id` ; la contrainte
n'impose l'exclusivité qu'entre les deux premiers. `leg_id` est un champ
**additionnel** désignant le voyage concerné quand une commande est ventilée
sur plusieurs legs (cf. commentaire `models/packing_list.py:83-86`), et
`commercial_overview` cherche les PL par `leg_id` direct **ou** via les
commandes du leg (`services/leg_overview.py:123-125`). « Épinglée au leg »
qualifie donc **le chemin de recherche**, pas une PL sans propriétaire.
La fixture du test créait une PL avec le seul `leg_id` — un état réellement
invalide, correctement refusé. Le correctif du test était bon ; sa
justification était fausse. **Aucun arbitrage requis sur ce point.**

**Quality Gate (partiel, lot CI)** : `ruff` ✅ · `black` ✅ · `bandit` ✅ (aucune
remontée) · YAML `ci.yml` valide ✅ · suite complète 2000/15 (15 = environnement)
✅ · documentation mise à jour ✅ · aucune migration ✅ · aucun secret ✅ ·
`pip-audit` / `gitleaks` **non exécutés en local** (couverts par le job CI
`security`, qui n'a pas tourné — cf. ci-dessus).

### ⚠️ J1 — limite majeure du filet : la suite est **Postgres-free**

Constat établi lors d'un contrôle de complétude indépendant (2026-07-29) et
**vérifié directement** :

- `tests/integration/conftest.py:35-38` crée le moteur avec
  `create_async_engine("sqlite+aiosqlite://")` — **SQLite en mémoire**.
- Preuve : la suite passe intégralement avec une URL Postgres inexistante
  (`DATABASE_URL=postgresql+asyncpg://nobody:nopass@127.0.0.1:59999/nonexistent`
  → 105 passés sur un échantillon integration+regression).
- Corollaire : le service Postgres provisionné par le job CI
  (`ci.yml:39-56`) **n'est utilisé par aucun test** — config morte.
- Corollaire 2 : la publication de Postgres sur `localhost:5432` et la création
  de `towt_test` faites en début de J1 étaient **inutiles** (décrites à tort
  plus haut comme une remise en état nécessaire ; corrigé).

**Ce que le filet ne couvre donc PAS** : tout comportement spécifique à
Postgres — `TIMESTAMP WITH TIME ZONE`, types `Numeric`, sémantique asyncpg,
et **les migrations Alembic** (la suite construit le schéma via
`Base.metadata.create_all`, jamais via Alembic).

**Ironie utile à noter** : le bug `voyage_track` corrigé ce jour a été révélé
*grâce à* SQLite (qui renvoie des datetimes naïfs là où Postgres renvoie de
l'aware). L'inverse est vrai aussi — **un bug Postgres-only resterait
invisible**.

**Impact direct sur le plan** :
- **J9** — la contrainte `atd < ata` passera par une migration Alembic sur
  Postgres : **non testable par cette suite**. Prévoir une validation manuelle
  sur la base Docker (`upgrade` + `downgrade` réellement exécutés), et
  éventuellement `testcontainers[postgres]` (déjà dans
  `requirements-dev.txt:12`, non utilisé).
- **J3** — Schengen manipule des dates ; les écarts naïf/aware se comportent
  différemment selon le moteur. Vérifier le comportement sur Postgres, pas
  seulement en test.

### J1 — sauvegarde / rollback : procédure testée (et non supposée)

Item de la DoD du J1 initialement **oublié**, exécuté et validé le 2026-07-29.

**Sauvegarde** (le dump sort du dépôt — il contient des données) :
```bash
docker compose exec -T db pg_dump -U towt -d towt --format=custom > <hors-repo>/towt_pre-upgrade.dump
```
→ 525 Ko, 135 tables dans la base source.

**Restauration testée dans une base jetable** (jamais sur `towt`) :
```bash
docker compose exec -T db psql -U towt -d postgres \
  -c "DROP DATABASE IF EXISTS towt_restore_test;" \
  -c "CREATE DATABASE towt_restore_test OWNER towt;"
docker compose exec -T -i db pg_restore -U towt -d towt_restore_test --no-owner < <dump>
```

**Vérification** — schéma **et** données, pas seulement le schéma :

| Contrôle | Source | Restaurée | |
|---|---|---|---|
| Tables (`information_schema`) | 135 | 135 | ✅ |
| `vessels` | 6 | 6 | ✅ |
| `ports` | 6 | 6 | ✅ |
| `legs` | 6 | 6 | ✅ |
| `users` | 2 | 2 | ✅ |

Base de test supprimée après contrôle. **La procédure de rollback est donc
prouvée, pas présumée.** Règle retenue : ne jamais restaurer directement sur
`towt` — toujours valider dans une base jetable d'abord.

⚠️ **Le dump ne doit jamais être committé** (données réelles). Stocké hors
dépôt ; à refaire avant chaque migration, conformément au plan §9.

### J1 — validation en application réelle (item §9 initialement non fait)

Écart 🔴 relevé par le contrôle indépendant : le seul fichier applicatif du lot
(`voyage_track.py`) n'avait **jamais été exercé dans l'app** — l'image Docker
précédait les commits de 28 h et le code n'est pas monté en volume. Corrigé.

**Rebuild + smoke test** : `docker compose up -d --build app`, authentification
staff réelle, puis appel des 15 écrans principaux.

**Résultat final : 15/15 en HTTP 200**, dont les trois écrans alimentés par
`leg_window` / `compute_metrics` — `/tracking`,
`/performance/navigation`, `/performance/navigation/kpis`. **Le correctif est
donc validé en conditions réelles, pas seulement par les tests.**

#### Découverte 1 — dérive de schéma de la base de développement

Au premier appel, `/tracking` renvoyait **500** :
`asyncpg.UndefinedColumnError: column vessels.deadweight_t does not exist`.
Diagnostic systématique (comparaison `Base.metadata` ↔ `information_schema`) :

| Table | Colonnes manquantes en base |
|---|---|
| `vessels` | `deadweight_t` |
| `crew_members` | `first_name`, `last_name`, `agency` |
| `env_reports` | `period_seq` |
| `nav_event_noon` | `rob_uree_t`, `rob_eau_douce_t` |
| `ports` | `mrv_scope` |
| `voyage_emission_summaries` | `co2eq_t` |

Plus **7 tables présentes en base et absentes du modèle** (130 vs 137) —
`create_all` ne supprime jamais.

**Cause** : la procédure de mise en route locale documentée en
`PROJECT_CONTEXT.md` §7 recommande `alembic stamp head` (parce que
`alembic upgrade head` échoue sur base fraîche en `APP_ENV=development`, où
`init_db()` a déjà exécuté `create_all`). Or **`stamp` marque l'historique
comme appliqué sans exécuter le DDL** : toute migration ultérieure qui `ALTER`
une table ne s'exécute jamais. La base était par ailleurs estampillée sur
`20260722_0106` (`qhse_foundation`), révision qui **n'existe que sur la branche
`feature/qhse-foundation`** — résidu d'un changement de branche.

**Correctif local appliqué** : 8 colonnes ajoutées par `ALTER TABLE … ADD COLUMN
IF NOT EXISTS`, DDL **généré depuis le modèle** (`CreateColumn` compilé sur le
dialecte PostgreSQL) plutôt que saisi à la main, en retirant les `NOT NULL`
(une colonne ajoutée a posteriori doit être nullable). Non destructif, base de
démo conservée.

⚠️ **§7 de `PROJECT_CONTEXT.md` était erroné** : la procédure telle qu'écrite
produit une base subtilement fausse. Documentée le 2026-07-28, l'erreur était
de mon fait. **Corrigée** — mais le correctif est porté par le **lot découverte**
(commit `e48847d` sur `docs/decouverte-fonctionnelle`), pas par le lot J1 :
`PROJECT_CONTEXT.md` est un document du lot découverte, et l'y amender depuis le
lot J1 créait une dépendance croisée entre les deux lots (cf. RAF R2, résolu).

#### Découverte 2 — 🔴 `alembic upgrade head` est cassé (blocage de déploiement)

Constat le plus sérieux de la journée, **hors périmètre de mon poste** :

```
$ alembic upgrade head
FAILED: Multiple head revisions are present for given argument 'head'
```

Deux chaînes de migration **divergentes** coexistent :
- `20260716_0112_noon_rob_annexes.py` (chaîne MRV)
- `20260720_0107_generated_reports.py` (chaîne rapports générés / trombinoscope)

Deux branches de fonctionnalité ont chacune ajouté des migrations sans se
rebaser l'une sur l'autre. **Aucun commit du J1 ne touche `migrations/`** ⇒
`main` est dans le même état.

**Pourquoi c'est grave** : `CLAUDE.md` indique que la production utilise
**Alembic exclusivement**. Un déploiement par `alembic upgrade head` échoue donc
en l'état. Il faut une **migration de fusion** (`alembic merge -m "…" <head1>
<head2>`), à faire valider — c'est une décision qui touche l'historique de
schéma, donc à signaler au manager plutôt qu'à improviser.

**À vérifier avant tout déploiement** : quelle révision la base de production
porte-t-elle réellement, et l'écart avec le modèle y est-il le même qu'en local ?

**RAF ouvert** : les 8 éléments identifiés et non traités sont suivis dans
`docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md` **§12 (RAF)**, avec pour chacun
sa portée réelle. Synthèse : **aucun ne bloque le J2** ; **R1 (fusion Alembic)
doit être traité avant le J9**, **R6 (embarquement hors leg + Schengen) avec le
J3**. R2 (rebase) et R4 (1re PR) sont des décisions de Yasmin, R3 (protection
de branche) une escalade externe.

**Prochaines étapes** :
1. 🔴 **Escalader le problème des deux `head` Alembic** — blocage de
   déploiement, décision de fusion à valider (RAF R1).
2. ~~Corriger `PROJECT_CONTEXT.md` §7~~ ✅ fait (commit `e48847d`, lot découverte).
3. **J2 — quick wins** : alerte ETA en mer, nom client + `leg_code` sur la
   liste bookings, heures voile ×6, redirection BL vers le rail packing list,
   2 micro-gardes BL.
4. Faire tourner la CI sur une PR pour valider les 15 tests PDF sur Ubuntu.
5. Rebaser `chore/ci-integration-tests` sur `origin/main` pour rendre le lot
   révocable indépendamment (§9) — **en attente d'arbitrage** : cela séparerait
   le lot CI du lot découverte, qui partiraient alors en deux PR distinctes.
6. Restaurer un chemin de saisie d'embarquement hors leg (A4) **et** faire lire
   ces affectations par le calcul Schengen — les deux vont ensemble.

**Récapitulatif exhaustif des fichiers modifiés au J1** :

| Fichier | Nature |
|---|---|
| `app/services/voyage_track.py` | **Seul fichier applicatif** — 2 correctifs de normalisation de fuseau |
| `.github/workflows/ci.yml` | Exécution de `integration` + `regression` + libs système WeasyPrint |
| `CLAUDE.md` | Invariants de rattachement `PackingList` / `CrewAssignment` (commit `26e4802`) |
| 10 fichiers `tests/` | Correction des 11 tests périmés |
| `docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md` | Création |
| `docs/DEVELOPMENT_JOURNAL_…md` | Création + mises à jour |
| `docker-compose.override.yml` | Local, **non versionné** (ajouté à `.gitignore`) |

Aucune migration Alembic. Aucun secret. Aucun fichier temporaire/debug.
