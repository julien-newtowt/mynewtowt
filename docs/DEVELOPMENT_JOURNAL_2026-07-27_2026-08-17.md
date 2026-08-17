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

---

## 2026-07-30 — J3 : fusion Alembic, 1re PR, et la CI se prend son propre audit

### Fusion des deux têtes Alembic (RAF R1 — levé)

Branche `fix/alembic-merge-heads`, issue directement de `main`.
Migration **de fusion pure** `20260730_0113_merge_heads.py` : aucun DDL, son
seul rôle est de raccorder les deux chaînes divergentes
(`20260716_0112` MRV / `20260720_0107` rapports générés) en une tête unique.

Écrite **à la main** plutôt que via `alembic merge` : le dossier `migrations/`
n'est pas monté dans le conteneur, la sortie de la commande y serait restée.

Vérifié : `alembic heads` → **une seule tête** ; `alembic history` affiche bien
`20260716_0112, 20260720_0107 -> 20260730_0113 (head) (mergepoint)` ; fichier
importable, `upgrade()`/`downgrade()` exécutables ; ruff + black verts.

Sûreté de la fusion : les deux chaînes touchent des tables **disjointes**
(`nav_event_noon` d'un côté, `generated_reports` de l'autre) — leur ordre
d'application relatif est donc indifférent.

> ⚠️ **À faire valider par le manager** : cela touche l'historique de schéma.

**Limite préexistante découverte au passage, sans lien avec la fusion** :
`alembic upgrade head --sql` (prévisualisation SQL hors-ligne) échoue sur
`20260703_0094_planning_rules_hardening.py`, qui fait un `fetchall()` — le mode
offline n'a pas de connexion. **On ne peut donc pas prévisualiser le DDL d'un
déploiement sur ce dépôt.** À traiter si un jour on veut un déploiement en deux
temps (revue du SQL puis application).

### PR #149 — première PR du lot 1

https://github.com/julien-newtowt/mynewtowt/pull/149 (brouillon).
`gh pr create` avait été refusé par le contrôle de permissions de
l'environnement la veille ; autorisation accordée par Yasmin, PR créée sans
contournement. Corps rédigé avec Quality Gate + audit de compatibilité complets.

### La CI a tourné — et elle valide la prémisse du lot

**2015 passés · 1 ignoré** sur Ubuntu. Mes 2000 passés + 15 échecs locaux = 2015 :
les 15 échecs étaient bien un **artefact WeasyPrint/GTK sous Windows**, comme
annoncé au J1. L'hypothèse est désormais **confirmée, plus supposée**.

⚠️ **Erreur de méthode de ma part** : mon premier guetteur interrogeait
`gh pr checks 149`, qui ne renvoyait rien, et j'ai rapporté « aucun check après
20 min ». Le run existait depuis le début, rattaché à la **branche**. Correction :
interroger `/actions/runs?branch=…`, pas la PR.

### Trois gardes de la CI affichaient vert sans rien garder

Le thème du jour (« l'outil cesse de mentir ») s'est appliqué à nos propres
outils avant de s'appliquer au métier.

| Garde | Réalité constatée |
|---|---|
| Suites `integration` + `regression` | **Jamais exécutées** (114 fichiers) — corrigé au J1 |
| Mypy « baseline 142 erreurs » | **434 en réalité** — dérive de 3× masquée par `continue-on-error` |
| Gitleaks | **Échoue à chaque run, n'a jamais scanné une seule PR** |

**1. Cette PR introduisait 1 erreur mypy** (434 → 435), dans le fichier même
qu'elle corrigeait — invisible en local, masquée en CI. Mon Quality Gate
affirmait « dette introduite : aucune » : **c'était faux**, et la cause est que
mes contrôles locaux ne lançaient pas mypy.

Et c'était un **vrai signal**, pas du bruit : `Leg.etd` est NOT NULL
(`models/leg.py:40`), donc `leg.atd or leg.etd` vaut toujours un datetime et
`start` ne peut jamais être `None` — le garde `if start is not None` que j'avais
ajouté dans `leg_window` était du **code mort**, et l'annotation de retour
`tuple[datetime, datetime, bool]` était violée.

Corrigé **à la racine** plutôt qu'au point d'appel : `ensure_utc` est
*préservante de nullité* (elle ne fabrique jamais une date, n'en supprime jamais
une), ce que sa signature n'exprimait pas. Deux `@overload` le déclarent —
purement statique, **aucun effet à l'exécution**.

Mesuré : **435 → 371 erreurs, 61 → 59 fichiers**. La mienne **+ 63
préexistantes**, aux 57 points d'appel du helper. Vérification : suite
**complète** rejouée (helper partagé) → 2000/15/1, identique à la référence.
Aucune régression. Commit `4363ea0`.

**2. Gitleaks n'a jamais scanné une PR.** `gitleaks-action@v2` exige désormais
`GITHUB_TOKEN` pour scanner une pull request ; sans lui l'étape échoue
immédiatement et `continue-on-error: true` la faisait passer pour verte.
**Vérifié sur le run du 23/07 (PR #147) : exactement la même erreur** — donc
**préexistant**, pas introduit par ce lot. La détection de fuite de secret était
décorative. Réparé (+ `pull-requests: read`, lecture seule).

**3. Cliquet anti-dérive du typage.** L'étape mypy reste **non bloquante** (on ne
bloque pas l'équipe sur 371 erreurs héritées) mais elle est désormais bornée par
une étape **bloquante** : toute erreur *nouvelle* fait échouer la CI. Plafond
posé à la valeur réelle mesurée (371), à baisser à chaque résorption. Commit
`4293a96`.

**Élargissement de périmètre assumé** : les points 2 et 3 dépassent le lot
initial. Signalé explicitement à Yasmin et dans le corps de la PR plutôt que
glissé discrètement — c'est le lot « filet de sécurité », et le filet ne tenait
pas ses promesses.

### Analyse d'impact J3 — découverte structurante : **deux registres d'embarquement parallèles**

Analyse faite **avant** tout codage (§9 du plan). Aucune ligne de code J3
écrite à ce stade. La découverte change le périmètre du lot.

Il existe **deux registres d'embarquement qui ne se parlent pas** :

| Registre | Alimenté par | Nature |
|---|---|---|
| `marad_crew_schedules` | Cron Marad (`sync_schedules`) | **Lecture seule.** C'est là que l'Armement décide réellement. |
| `crew_assignments` | **Uniquement** la saisie d'escale | Le seul que lit le calcul Schengen. |

**Un seul point de création dans toute l'application** : `escale_crew.py:52`,
appelé depuis `escale_router.py:388` (opération d'escale
`armement`/`embarquement`). Le module `/crew` **ne sait pas créer** une
affectation — seulement éditer/supprimer (la route de création a été supprimée,
la spec `SPEC-CREW-reprise-P0.md` qui la décrivait n'a jamais été appliquée).

**Décalage de permissions** : l'Armement — qui décide les embarquements — a
`crew: CMS` mais seulement `escale: C`. **Le service qui décide ne peut pas
saisir.** Ceux qui peuvent sont `operation`/`technique` (CMS),
`manager_maritime` (CM), `administrateur`.

**Ce que chaque indicateur lit réellement** :

| Indicateur | Source |
|---|---|
| Schengen 90/180 | `crew_assignments` **seul** |
| Jours embarqués de l'année | `crew_assignments` **+** Marad |
| Bordée du jour / « en activité » | Marad **seul** |
| Équipage d'un leg (certificat Anemos) | Marad **seul** |
| Armement réglementaire du navire | `crew_assignments` **seul**, et via `leg_id` uniquement |

⇒ **L'écran peut afficher une bordée complète venue de Marad pendant que le
compteur Schengen affiche « conforme / 0 jour » pour ces mêmes marins.** Le
registre où vit la décision est **invisible du calcul de conformité**.

**Défauts confirmés dans le code** :
1. `crew_compliance.py:251-257` — présence vide donne 0 jour donne `compliant`.
   Cumulé au défaut de colonne `compliant` (`models/crew.py:43`) : deux chemins
   vers un affichage rassurant sans aucune donnée.
2. `crew_compliance.py:231-233` — `if leg is None: continue` : toute affectation
   sans leg est **ignorée**. L'arrêt technique à quai en zone Schengen — le cas
   qui **consomme le plus de jours** — compte pour zéro.
3. `EscaleOperation.leg_id` est **NOT NULL**, donc le seul point de création
   impose un leg. Le modèle `CrewAssignment` autorise `leg_id = NULL` (arbitrage
   A4) mais **aucun écran ne peut en produire une**.
4. `vessel_readiness` (l.311) a le **même angle mort** (`leg_id.in_(…)`).
5. `passport_blocking_reason` (l.277-294) est complète et correcte —
   **zéro appelant**. La saisie d'escale crée l'affectation sans jamais regarder
   passeport ni compteur Schengen.

**Point d'honnêteté** : les ports enregistrés sur l'affectation sont déduits du
leg de façon rigide, mais le calcul Schengen **ne les relit pas** (il repart des
ports du leg). C'est une redondance douteuse, **pas un bug prouvé** — non compté
comme défaut faute de démonstration.

### 🟡 Décision en attente de Yasmin (bloque le démarrage du J3)

Le calcul Schengen doit-il lire **le registre des Opérations seul** (périmètre
annoncé) ou **les deux registres, Marad inclus** ? La seconde option est plus
juste métier — c'est Marad qui porte la vérité des embarquements — mais elle
sort du périmètre et demande plus de travail. **Rien n'est lancé avant arbitrage.**

Également en attente : le recâblage du garde-fou passeport **bloquera une saisie
d'escale** que les Opérations faisaient jusqu'ici sans contrainte (override
tracé possible). Recommandation : le brancher en mode override-possible — le
risque de ne rien faire est réglementaire (contrôle PAF, responsabilité
armateur), le risque de le faire est une case à cocher de plus.

---

## 2026-08-03 — Lot workflow BL : ce qui est livrable sans migration

Journée cadrée par une contrainte externe : **Julien est le seul à pouvoir valider
une fusion** et il est absent jusqu'au 2026-08-17. Stratégie retenue avec Yasmin :
**tout préparer, ne rien fusionner** (cf. `07-ordre-pr-et-merge.md` §1 bis).

### Base de la branche — un choix contraint, pas une commodité

`feat/bl-workflow` dérive de **deux** lots, et il le fallait :

- **lot 3** (`fix/alembic-merge-heads`) : `alembic revision` exige une **tête
  unique**. Brancher sur `main` produirait une migration rattachée à l'une des
  deux têtes divergentes ⇒ migration à refaire après fusion.
- **lot 1** (`chore/ci-integration-tests`) : il porte **la spec BL elle-même**
  (absente de `main`) **et** les 11 corrections de tests périmés. Sans lui, la
  référence de test compterait une vingtaine d'échecs connus — inacceptable pour
  détecter une régression sur un lot qui touche un **titre de propriété**.

Fusion à blanc testée : zéro conflit. Référence de test établie **avant** toute
modification : **2000 passés / 15 échoués / 1 ignoré** (les 15 = rendus PDF
WeasyPrint absents de l'hôte Windows).

### 🔴 Une migration parasite était posée sur la migration de fusion

`migrations/versions/4f4eeb7bfc89_.py` — créée le 2026-08-03 à 10:05:28, **non
suivie par git**, `upgrade()` et `downgrade()` vides, intitulée « empty message »,
révisant `20260730_0113`. Origine quasi certaine : le **hook du harnais qui lance
`alembic`** (RAF R8).

⚠️ **Cela requalifie R8** : ce hook ne produit pas seulement du bruit au commit,
il **peut polluer la chaîne de migrations**. Si ce fichier avait été commité,
toute migration ultérieure se serait enchaînée depuis une révision anonyme et
vide, et le contrôle de tête aurait désigné la mauvaise.

Fichier **déplacé** (non supprimé) vers le dossier de travail temporaire. Tête
rétablie et vérifiée : 121 révisions, tête unique `20260730_0113`.

### Deux défauts dans mes propres outils de vérification

1. **Mon script de contrôle des têtes Alembic** ne gérait pas les déclarations
   annotées (`down_revision: Union[str, None] = "…"`) et a annoncé **deux têtes**
   là où il n'y en avait qu'une. Attrapé en recoupant avec le résultat réel
   d'`alembic heads` obtenu la veille — sans ce recoupement, je signalais un faux
   problème. Corrigé.
2. **Deux de mes nouveaux tests passaient à vide** : sans aucune journalisation,
   « le token n'apparaît pas dans le journal » est trivialement vrai sur zéro
   ligne. Gardes anti-succès-à-vide ajoutées ⇒ **8 sur 8** échouent désormais sur
   l'ancien code.

### Volet livré 1 — journalisation des 8 mutations du portail (commit `1cb1d40`)

**Correction d'un constat que j'avais énoncé trop fortement** : j'avais dit
« aucun appel de journalisation ». Faux. Les opérations de batch alimentaient bien
`PackingListAudit` (champ par champ, via `create_batch` / `apply_batch_update`).
Ce qui manquait réellement :

- **zéro entrée dans `activity_logs`** (le journal append-only de
  `/admin/activity-logs`) — c'est pourtant la piste qu'un P&I club réclame ;
- `actor_name=None` partout : la piste disait « client » sans jamais dire lequel ;
- **deux routes tracées nulle part** : la soumission de la packing list (qui
  change son état) et l'envoi d'un message.

**Choix de conception — on ne nomme volontairement personne.** Le portail est
**anonyme par conception** : quiconque détient le lien agit, et ce peut être un
transitaire. Écrire un nom de société laisserait croire à une attribution que rien
ne vérifie. Le libellé est `portal:PL<id>` (canal + dossier), l'IP est
enregistrée, et l'expéditeur déclaré reste lisible sur chaque batch.

**Erreur de ma conception, attrapée par les tests** : j'avais écrit
`pl.shipper_name`. **`PackingList` ne porte aucun champ d'identité d'expéditeur** —
les parties (shipper / notify / consignee) vivent sur `PackingListBatch`. Ce code
aurait levé une `AttributeError` à **chaque mutation du portail** en production.
La spécification suggérait ce champ : elle se trompait aussi. Un test fige
désormais le constat (`assert not hasattr(pl, "shipper_name")`).

Le corps d'un message n'est **pas** dupliqué dans le journal (longueur seulement) :
il est déjà persisté dans `portal_messages`, et `/admin/activity-logs` a une
surface de lecture plus large.

**Durcissement, pas correction de bug** : `get_by_token` comparait
`token_expires_at` (naïf sous SQLite) à `datetime.now(UTC)` (aware). En production
la valeur vient toujours de `default_token_expiry()`, donc aware — **ce n'est pas
un bug applicatif constaté**, c'est la limite Postgres-free de la suite (RAF R5).
Mais l'expiration de token est un contrôle de sécurité : rendue **testable**
plutôt que contournée en fixture, via `planning.ensure_utc`.

Vérifié **par AST** (pas par grep) qu'aucun des 10 appels de journalisation ne
reçoit le token sous quelque forme que ce soit.

### Volet livré 2 — notify party au formulaire du portail

Décision de Yasmin : « Notify party & consignee à saisir depuis le portail
expéditeur ». État constaté : les cinq colonnes `notify_*` existaient **déjà** sur
`PackingListBatch` **et** figuraient **déjà** dans `AUDITABLE_FIELDS`. Le backend
les acceptait même déjà via `coerce_batch_form`. **Seule l'interface manquait** —
si bien que tout BL issu d'une packing list remplie par l'expéditeur sortait
**sans notify party**, sans que rien ne le signale.

Ajoutés au formulaire d'édition **et** de création, avec les 4 libellés dans les
**5 catalogues** i18n. « Notify party » reste en anglais partout, comme
« Consignee » l'est déjà : c'est un terme du connaissement.

9 tests, dont **7 échouent sur l'ancien code**. Les 2 qui passent des deux côtés
vérifient un comportement **préexistant et correct** (couverture d'audit +
acceptation backend) — ce sont des garde-fous contre une régression future, pas
des tests de ce changement. Distinction consignée pour ne pas surestimer la
couverture.

### ⛔ Volet NON livré — le retrait du rail booking, et pourquoi

La spec prévoyait de retirer le rail booking **avant** ce lot. **L'inventaire des
routes invalide ce séquencement.**

| Route | Public | Remplacement |
|---|---|---|
| `/cargo/packing-lists/{pl}/batches/{b}/bl.pdf` | staff | *(la cible)* |
| `/cargo/booking/{ref}/bl.pdf` · `.docx` | staff | ✅ rail packing list |
| `/me/bookings/{ref}/bl.pdf` · `.docx` | **client** | ❌ **AUCUN** |

🔴 **Le rail packing list n'a aucune route côté client**, et
`templates/client/booking_detail.html:199` expose un bouton visible
« 📄 Bill of Lading » pointant vers la route booking.

Retirer maintenant supprimerait **la seule façon pour un client d'obtenir son
connaissement**, sans remplacement, pendant tout le délai d'attente. Régression
fonctionnelle visible ⇒ **refusé**, en application de la méthode de développement
prudent.

Séquencement corrigé (dans le lot, pas avant) : créer les routes client du rail
packing list → rebrancher le bouton client → **alors** retirer les 4 routes
booking et leurs 3 entrées staff.

Point de conception soulevé au passage : un booking peut porter **plusieurs
batches**, donc plusieurs BL, alors que l'URL client est au niveau du booking. Le
choix (lister les BL / un document par batch) appartient au lot.

### Décisions métier obtenues de Yasmin (les 5 points de la spec sont tranchés)

1. **Date « shipped on board » = dernier jour des opérations**, lu depuis la
   timeline d'escale, modifiable par les Opérations **sous justification** avec
   journal « en cas de contrôle ».
2. **Toujours 3 originaux** — aucun paramétrage. **Mais** nouvelle exigence : un
   **suivi de réception** (horodatage du téléchargement client et/ou case de
   confirmation, **plus** un repli Opérations avec date, heure, moyen et pièce
   jointe si le BL part en papier). C'est précisément le registre de remise dont
   l'absence exclut la *misdelivery* de la couverture P&I.
3. **Signature du commandant au choix** : unitaire **ou** groupée, les deux modes.
4. **Marchandises** depuis la packing list, **parties** depuis le portail.

🔁 **Mutualisation identifiée** : le motif *valeur dérivée → override →
justification obligatoire* est demandé **deux fois** — ici pour la date *shipped
on board*, et dans le lot relèves pour les durées de contrat. À construire **une
seule fois**. Précédent proche dans le dépôt : `validation_engine.get_threshold`
(MRV v2, « zéro seuil en dur », résolution fail-closed + snapshot d'audit).

**Estimation révisée : 6,5 j → 10,25 j**, dont 0,75 livré. La hausse vient des
deux ajouts du §5 (registre de remise, date dérivée avec override justifié) — des
**ajouts de valeur**, pas des dérives de périmètre, et signalés comme tels.

### Reste à faire sur ce lot

Plus rien de livrable sans migration. La suite (machine à états, écrans, registre
de remise, routes client, retrait du rail booking) attend **la fusion du lot 3 par
Julien**.

---

## 2026-08-17 — BL : l'émission cesse d'être un effet de bord de la consultation

Branche `feat/bl-workflow`. Quatrième et dernier volet du socle BL avant les
écrans.

### Le défaut corrigé

`GET /cargo/packing-lists/{pl}/batches/{b}/bl.pdf` **écrivait en base** : il
appelait `assign_bl_number`, attribuait un numéro de connaissement et le
persistait. Deux conséquences, indépendantes l'une de l'autre :

1. **Un `GET` qui écrit s'exécute sans intention.** Un préchargement de lien par
   le navigateur, un scan de sécurité, un passage de crawler authentifié : chacun
   émet un connaissement et consomme un numéro. Personne ne l'a demandé, et rien
   ne le distingue d'une émission volontaire dans le journal.
2. **La permission était celle de la consultation.** `cargo:C` couvre
   `technique`, `data_analyst` et **`marins`** : trois rôles qui pouvaient émettre
   un titre de propriété sans avoir le droit de modifier une packing list.

Le second point est le plus grave des deux : ce n'est pas un accident possible,
c'est une autorisation permanente.

### La correction

| Route | Méthode | Permission | Écrit ? |
|---|---|---|---|
| `.../bl/draft` | **`POST`** | **`cargo:M`** | oui — attribue le numéro, passe en `draft`, trace |
| `.../bl.pdf` | `GET` | `cargo:C` | **non** — rend le document, 404 si aucun BL |

Le `POST` redirige en 303 vers la consultation : l'utilisateur voit son document,
et un rafraîchissement de page ne réémet rien.

Le gabarit suit — c'est la moitié du correctif, pas un détail cosmétique : un
`<a href>` continuerait de déclencher l'écriture par préchargement.
`packing_list_detail.html` affiche désormais **un lien quand un BL existe, un
formulaire `POST` sinon**.

### Vérification

7 tests (`test_bl_emission_post_only.py`), dont trois qui portent le poids :

- le `GET` sur un lot sans BL renvoie 404 **et laisse `bl_number` à `None`** —
  c'est l'assertion sur l'état en base qui prouve l'absence d'écriture, pas le
  code de retour ;
- la **déclaration** de route est figée (méthode `POST`, `cargo:M` dans la
  signature, `assign_bl_number` absent du corps de la lecture) : repasser la
  route en `GET` fait échouer la suite. Vérifié par sabotage réel — bascule du
  décorateur en `@router.get`, le test tombe, restauration ;
- une garde anti-sur-correction : la consultation d'un BL existant fonctionne
  toujours. Un correctif qui casserait la lecture serait pire que le défaut.

### Trois erreurs de ma part, corrigées

- **`Set-Content -Encoding utf8` en PowerShell 5.1 écrit un BOM**, et
  `Get-Content -Raw` relit en ANSI. Mon aller-retour de sabotage a donc
  double-encodé tous les accents du routeur (mojibake sur ~50 lignes) et ajouté
  un BOM. Réparé par réencodage cp1252 ; le diff est retombé de 100/52 à 51/3,
  ce qui a servi de preuve de restauration. **À retenir : pour un sabotage
  temporaire, passer par `git stash`, pas par un aller-retour de fichier.**
- **`pytest.importorskip("weasyprint")` ne protège rien ici** : il ne rattrape
  qu'`ImportError`, alors que l'absence de GTK lève une `OSError`. C'est
  l'explication des 15 échecs locaux de la suite — ils ne sont pas « connus et
  inévitables », leur garde est simplement inopérante. Le test de lecture
  monkeypatche le rendu (motif déjà utilisé par `test_carnet_conditions`) et
  passe donc partout.
- **3 erreurs mypy introduites** dans `bl_workflow.py` : un `actor_name`
  nullable passé à un paramètre non-optionnel. Corrigées à la racine (nom lu
  depuis la variable locale, pas depuis la colonne nullable) plutôt qu'en
  élargissant la signature de `_trace` — une trace d'audit sans acteur nommé ne
  vaut rien. mypy revenu à la référence de 371. Au passage, un vrai trou : si
  `company_name` **et** `email` étaient vides, la validation client était tracée
  sans acteur. Repli explicite ajouté.

### Reste à faire sur le lot BL

Le socle est complet (machine à états, gel, traçabilité, émission protégée).
Restent les **écrans** (validation client, signature commandant unitaire et
groupée), le **filigrane DRAFT**, le **registre de remise** (§5.1), la **date
*shipped on board* dérivée** (§5.0), la **séquence non recyclable + upsert de
l'import**, et enfin les **routes client puis le retrait du rail booking** — dans
cet ordre, jamais l'inverse.

### Constat de méthode — la suite complète n'est plus exécutable en local

Mesuré le 2026-08-17 : **~36 tests/minute en série**, soit plus de **cinq heures**
pour les ~2 400 tests. J'ai d'abord cru à un blocage et tué un run après 20 min —
à tort : le processus consommait bien du CPU, il était simplement lent. Le
diagnostic correct est venu de `Get-Process | Select CPU`, pas de la taille du
fichier de sortie.

`pytest-xdist` est déjà installé et la machine a 20 cœurs : `pytest -n 8` ramène
la durée à environ une heure. C'est utilisable, mais cela reste trop long pour
servir de boucle de vérification pendant le développement.

**Conséquence pratique adoptée** : vérification locale sur le **sous-ensemble
concerné** (ici cargo / portail / BL / parité des routes), et la **CI comme
véritable filet** sur la totalité — ce qu'elle est déjà, prouvé par le run de la
PR #149 (2 015 passés sur Ubuntu, cf. RAF R4).

⚠️ À ne pas confondre avec R5 (« filet Postgres-free ») : la lenteur est
**intrinsèque à la suite**, pas causée par SQLite. Passer à Postgres via
`testcontainers` la ralentirait davantage — argument à verser au dossier quand
Julien arbitrera R5.

Note annexe vérifiée : les 15 échecs locaux « WeasyPrint » ne sont pas protégés
par leur garde. `pytest.importorskip("weasyprint")` ne rattrape qu'`ImportError`,
or l'absence de GTK lève une `OSError`. Le remède est le monkeypatch du rendu, pas
l'`importorskip` — trois fichiers de tests existants gagneraient à être alignés.

---

## 2026-08-17 (2) — le PDF du BL cesse d'affirmer qu'il est signé

Branche `feat/bl-workflow`, suite immédiate du volet précédent.

### Ce qui n'allait pas — et ce n'était pas cosmétique

Le gabarit `pdf/bill_of_lading_pl.html` affirmait, **quel que soit l'état du
lot** :

    Number of Original B/L : 3 (3 OBL signés)

et affichait une zone « Cachet et signature du transporteur ». Sur un brouillon
que personne n'a signé, ces deux mentions sont **fausses sur un document
opposable**. Un tiers de bonne foi — banque en crédit documentaire, destinataire,
assureur — lit un connaissement original émis en trois exemplaires signés.

C'est exactement la tension que le §2 de la spec avait identifiée à l'ouverture du
lot : le défaut n'était pas la mutabilité du document, c'était **l'absence de
distinction draft/final** couplée à une mention affirmant le contraire.

### La correction

Le document dit désormais ce qu'il est, à trois endroits complémentaires :

- **filigrane `DRAFT`** en `position: fixed` — donc répété sur **chaque page** ;
  un filigrane limité à la première page laisserait les suivantes passer pour un
  original ;
- **mention lisible** en tête (« Projet de connaissement — sans valeur de
  titre… ») : le filigrane est visuel, il ne se **cite** pas. Un tiers doit
  pouvoir opposer une phrase, pas une impression ;
- **bloc de signature conditionnel** : plus de zone de signature ni de mention
  d'originaux tant que rien n'est signé. Une fois signé, le bloc nomme le
  signataire, l'horodate et imprime l'empreinte SHA-256 — ce qui permet de
  confronter le papier au registre.

Le **nom du fichier** suit aussi (`TUAW_…-DRAFT.pdf`) : un PDF nommé comme un
original finit par circuler comme un original.

Repli prudent assumé : si `bl_state` est absent (lot antérieur à la machine à
états), le document est traité comme **non signé**. Dans le doute, filigrane.

### Vérification — 15 tests

Les tests portent sur le **HTML rendu** en substituant *seulement* la conversion
WeasyPrint, en gardant le vrai gabarit **et le vrai contexte construit par le
service**. Fabriquer le contexte à la main aurait rendu ces tests vides de sens :
un `bl_state` oublié dans `pdf_generator` serait passé inaperçu.

Non-vacuité mesurée : sur le code d'avant, **10 échouent, 3 passent** — et les 3
qui passent sont précisément les gardes anti-sur-correction (« un BL signé déclare
toujours 3 originaux », exigence §5.1 « toujours 3 »).

Une erreur de ma part corrigée en cours de route : ma première assertion cherchait
`bl-watermark` dans le HTML, ce qui matchait la **définition CSS** de
`pdf/_base.html` et non l'élément. Resserrée sur `class="bl-watermark"`.

### ⚠️ Limite honnête de cette vérification

La CI prouve que le PDF **se construit**. Elle ne prouve pas qu'il **s'affiche
correctement** : opacité du filigrane, rotation, absence de recouvrement du texte.
**Une revue visuelle d'un PDF réel reste à faire** — à mettre sous les yeux de
Yasmin ou de Julien, ce n'est pas automatisable ici (GTK absent en local).

Reste sur ce point de la spec : les **révisions numérotées** (`TUAW_…_R2`
annulant la précédente), non abordées.

---

## 2026-08-17 (3) — écran commandant : signature des connaissements

Branche `feat/bl-workflow`. Réponse au §5.2 de la spec — « Donner le choix au
commandant de tout signer ou signer un BL en particulier ».

### Ce qui est livré

`/captain/bl` (`captain:C`), avec deux actions en `captain:M` : signature
**unitaire** et signature **groupée**. L'écran sépare trois listes, et cette
séparation est le cœur du travail :

| Liste | Signable ? | Pourquoi la montrer |
|---|---|---|
| **À signer** (`client_validated`) | oui | le travail du commandant |
| **En attente de validation client** (`draft`) | **non** | la masquer ferait croire à un oubli d'émission alors que la balle est chez le client |
| **Signés** (`master_signed`, `final`) | non | registre : signataire, horodatage, empreinte |

Chaque ligne porte un lien « Lire » vers le PDF filigrané : **lire avant de
signer** n'est pas une option quand on engage le transporteur.

### La signature groupée ne doit pas mentir

C'est le point de conception qui a demandé le plus d'attention. Un lot peut être
revenu à `draft` entre l'affichage de l'écran et l'envoi du formulaire (la règle de
régression). Trois comportements possibles, deux mauvais :

- tout refuser ⇒ le commandant qui signe douze connaissements en perd onze à cause
  du douzième ;
- signer ce qui passe et annoncer « 11 signés » ⇒ **laisse croire à une réussite
  complète** ;
- signer ce qui passe et annoncer **11 signés, 1 écarté**, l'écarté restant visible
  dans sa liste avec son état réel. C'est ce qui est implémenté.

`BulkSignResult` porte donc deux listes et non un compteur, chaque écart avec sa
**raison**. Rien n'est encodé dans l'URL : seuls les deux nombres y transitent, le
détail se lit dans les listes.

### Deux pièges du gabarit, évités et épinglés par des tests

- **`<form>` imbriqué** : j'avais d'abord mis le formulaire de signature unitaire
  *dans* le formulaire de signature groupée. HTML l'interdit et le navigateur
  aurait cassé **les deux** envois. Corrigé par `formaction` sur le bouton.
- **`onchange="this.form.submit()"`** : je m'apprêtais à copier le motif de quatre
  écrans existants. Vérification faite, la CSP du projet est
  `script-src 'self' https://unpkg.com`, **sans `unsafe-inline`** : les
  gestionnaires d'événements en attribut sont **bloqués**.

  🔴 **Constat à part, préexistant** : `staff/captain/cargo_doc_form.html`,
  `staff/dashboard_env/quality.html`, `staff/onboard/compliance.html` et
  `staff/rh/absences.html` utilisent ce motif — **leurs filtres ne font donc rien
  en production**. Non corrigé ici (hors périmètre du lot BL), mais signalé.

### Vérification — 16 tests, non-vacuité mesurée par sabotage

Deux sabotages réels, appliqués puis annulés :

- supprimer le report des écarts ⇒ **2 tests tombent** ;
- retirer le repli COM-11 de la requête (`coalesce(pl.leg_id, order.leg_id,
  booking.leg_id)`) ⇒ **le test du repli tombe**. Ce repli n'est pas décoratif :
  sans lui, les connaissements des packing lists héritées (`leg_id` NULL) seraient
  **invisibles à bord** et jamais signés.

### Trois erreurs de ma part

1. **🔴 J'ai détruit mon propre travail** avec `git checkout -- <fichier>` sur un
   fichier portant des modifications **non committées** : `batches_for_leg`,
   `sign_many` et `BulkSignResult` ont disparu d'un coup. J'avais raisonné
   « stash » et tapé « checkout ». Réécrit depuis le contexte. La méthode retenue
   pour un sabotage temporaire est désormais une **copie de sauvegarde du fichier**,
   restaurée par `cp` — vérifiable, et sans commande destructive.
2. **Shells mélangés** : un heredoc Bash dans un appel PowerShell (qui n'en
   accepte pas), puis des cmdlets PowerShell dans un appel Bash. Résultat : le
   sabotage appliqué, le test jamais exécuté, et un fichier laissé modifié. C'est
   ce qui a mené à l'erreur 1.
3. **Deux assertions de test trop grossières** : `"<form"` et `"onchange="`
   cherchés dans la source du gabarit matchaient mes **propres commentaires
   Jinja**, ceux qui documentent justement les pièges. Corrigé en retirant les
   commentaires avant analyse — ils ne sortent jamais au rendu.

### Reste sur les écrans

⛔ **La validation client n'est pas livrée** : elle passe par `/me` (espace
authentifié), or le rail packing list **n'a toujours aucune route client** (§5.4).
Elle est donc **liée au lot des routes client**, pas à celui-ci. Le repli staff
(« valider pour le compte du client ») existe en service et reste à exposer.

---

## 2026-08-17 (4) — date de mise à bord : dérivée, corrigeable, justifiée

Branche `feat/bl-workflow`. Réponse au §5.0 de la spec.

### Le mécanisme partagé, construit une seule fois

La spec insistait : le motif *valeur dérivée → override → justification
obligatoire* est demandé **deux fois** (date de mise à bord ici, durées de contrat
dans le lot relèves) et devait être écrit une seule fois. C'est
`app/services/derived_override.py` : il ne connaît **ni la base ni le métier**, il
décide et ne persiste pas — c'est ce qui le rend utilisable par deux lots qui ne
partagent aucune table. Précédent suivi : `validation_engine.get_threshold` (MRV
v2), avec la même idée de snapshot d'audit.

Trois règles y sont posées :

1. **la dérivée est la référence** — jamais recopiée dans la colonne d'override,
   sinon « corrigé volontairement à cette valeur » devient indistinguable de « pas
   corrigé », et la valeur figée devient fausse dès que la source bouge ;
2. **un override est explicite** — valeur, auteur, horodatage ; pas de drapeau
   séparé qui pourrait se désynchroniser ;
3. **sans justification, pas d'enregistrement** — refus levé *avant* toute écriture.

Une justification vide est refusée, mais aussi une justification **creuse** :
« ok », « erreur », « correction », « cf. mail »… Elles remplissent le champ sans
répondre à la question qui sera posée en contrôle des mois plus tard, par
quelqu'un qui n'était pas là. Longueur minimale de 10 caractères, paramétrable —
c'est un garde-fou, pas un dogme.

### 🔴 Le point le plus grave : ne dériver que du réel

La dérivation lit `actual_end`, à défaut `actual_start`. **Jamais** le
prévisionnel. Une opération planifiée le 20 et non réalisée produirait sinon un
connaissement portant une date de mise à bord **future** — c'est-à-dire une fraude
documentaire et une exclusion de garantie, précisément ce que le §5.0 cherche à
éviter.

Corollaire assumé : **pas d'opération réelle ⇒ pas de date**. Le PDF affiche
« — non constatée » plutôt qu'une date inventée. C'est moins confortable et c'est
juste.

### La contrainte est en base, pas seulement dans le service

`ck_bl_sob_override_needs_reason` : une date corrigée sans motif est
**instockable**. Un futur chemin d'écriture qui oublierait de passer par
`override_shipped_on_board` échouera quand même. Le journal demandé « en cas de
contrôle » n'a de valeur que si le motif existe toujours.

### Choix de conception : le PDF porte la date, pas la mention « corrigée »

La provenance remonte jusqu'à l'écran Opérations (pastille « corrigée » + date
dérivée + motif), mais **pas** sur le connaissement. Annoter « date corrigée » sur
un titre présenté à une banque en crédit documentaire jetterait un doute
injustifié sur le document lui-même. La piste d'audit est le bon endroit pour ça —
et elle porte un snapshot JSON complet (valeur, dérivée, motif, divergence).

### Vérification — 47 tests

Le socle partagé est testé **pour lui-même** (25 tests unitaires) et non seulement
à travers son appelant : il servira à un autre lot.

Sabotage réel : faire accepter `planned_end` par la dérivation ⇒ le test du
post-datage tombe. Restauration par copie de sauvegarde du fichier (méthode
adoptée après l'incident du `git checkout` de ce matin).

Un point d'entrée a été ajouté sur l'écran Opérations — sans lui la route
n'existerait pas, exactement le défaut relevé la veille sur
`passport_blocking_reason`.

### Reste du §5.1 — le registre de remise

**Non livré.** Le suivi de réception des originaux (téléchargement horodaté, case
de confirmation client, repli Opérations avec date/heure/moyen/pièce jointe) est le
prochain volet. Il dépend en partie des **routes client** (`/me`), comme la
validation client.

---

## 2026-08-17 (5) — rail client du connaissement, et validation par le client

Branche `feat/bl-workflow`. Ce volet lève la dépendance qui bloquait tout le
reste : le rail packing list n'avait **aucune** route côté client.

### Pourquoi une liste et non un document

Le rail packing list produit **un connaissement par lot** (`PackingListBatch`),
alors que l'URL client historique est au niveau du **booking**. Un booking pouvant
porter plusieurs lots, « le » BL du booking n'existe pas. D'où :

    GET  /me/bookings/{ref}/bls                  liste (état, PDF, action)
    GET  /me/bookings/{ref}/bl/{batch_id}.pdf    un document
    POST /me/bookings/{ref}/bl/{batch_id}/validate   validation client

### 🔴 Le point qui compte : la référence dans l'URL ne suffit pas

Le contrôle de propriété existant (`_get_booking(..., owner_client_id=…)`) vérifie
que **le booking** est bien au client. Ce n'est pas assez : **c'est le lot qui
porte le document**. Un client authentifié passant *sa propre* référence de booking
avec un `batch_id` appartenant à un tiers aurait lu le connaissement de ce tiers.

D'où `_owned_batch_or_404`, qui joint sur `PackingList.booking_id`. Deux tests le
couvrent, et le sabotage (retirer la clause de jointure) les fait tomber tous les
deux.

Convention respectée : **404 et non 403** sur ce qui n'est pas à soi — un 403
confirmerait l'existence de la référence.

### Ce que le client voit, et ce qu'il ne doit pas croire

Les états sont dits en clair, jamais par un code technique : « Projet — en attente
de votre validation », « Validé par vous — en attente de signature du commandant »…
Et la page dit explicitement ce qu'un projet **ne vaut pas** : ni original
négociable, ni preuve de mise à bord. Elle prévient aussi qu'une modification
postérieure annule la validation et qu'il faudra revalider — la règle de
régression, expliquée avant d'être subie.

Un test vérifie la présence de cette mention **dans les deux langues** : la
protection ne doit pas exister qu'en français.

### Transition entre les deux rails — pas de retrait prématuré

La fiche booking client affichait un bouton « 📄 Bill of Lading » pointant vers le
rail booking. Il **reste**, mais la page préfère désormais le nouveau rail
**quand celui-ci a produit quelque chose** (`pl_bl_count`). Retirer l'ancien lien
maintenant priverait de tout document les bookings sans packing list. Un test
épingle les deux branches, y compris la présence du repli — pour qu'un futur
« nettoyage » ne l'enlève pas sans le vouloir.

⛔ Le **retrait du rail booking** reste donc à faire, et c'est volontaire.

### Une erreur mypy de ma part, réelle

`db.get(PackingList, …)` renvoie `PackingList | None`. Sur une donnée incohérente,
le code aurait planté avec une erreur obscure au lieu de rendre un 404 explicite.
Gardé.

### Reste

- **registre de remise des originaux (§5.1)** — table + horodatage de
  téléchargement + case de confirmation client + repli Opérations (date, heure,
  moyen, pièce jointe). C'est le dispositif dont l'absence exclut la *misdelivery*
  de la couverture P&I ;
- **séquence de numéros non recyclable** — aujourd'hui `nombre de BL du leg + 1` :
  supprimer un lot fait réattribuer un numéro déjà consommé ;
- **révisions numérotées** (`TUAW_…_R2`) ;
- **retrait du rail booking**, en dernier.

---

## 2026-08-17 (6) — registre de remise des originaux

Branche `feat/bl-workflow`. Réponse au §5.1. C'est le volet qui a le plus de valeur
défensive du lot : sans registre, NEWTOWT ne peut établir **ni à qui, ni quand, ni
comment** les originaux ont été remis — et c'est précisément le dispositif dont
l'absence exclut la *misdelivery* de la couverture P&I.

### Trois canaux, trois valeurs probantes — et on ne les confond jamais

Toute la conception tient dans cette distinction :

| Canal | Ce que ça prouve | Force |
|---|---|---|
| `download` | le document a été **consulté** | faible — un préchargement de lien ou un antivirus de messagerie suffit |
| `client_confirmed` | le client **déclare** avoir reçu | forte — sa propre déclaration |
| `ops_confirmed` | NEWTOWT **atteste** d'une remise hors plateforme | intermédiaire — c'est un **repli**, tracé comme tel |

🔴 Conséquence directe : `has_client_acknowledgement` **ignore délibérément les
téléchargements**. C'est la fonction où l'erreur coûterait le plus cher — présenter
un téléchargement comme une réception produirait une affirmation fausse au moment
exact où elle compte, face à un assureur en réclamation. Le sabotage (compter tous
les canaux) fait tomber le test dédié.

Le vocabulaire suit le calcul, dans les deux langues : l'écran dit
« consultation(s) — accès seulement, pas une réception », jamais « reçu ».

### Un raffinement que j'ai ajouté : un projet n'est pas un original

Télécharger un **projet** n'est pas recevoir un original — avant signature, aucun
original n'existe. Consigner ces accès dans un registre de **remise** l'aurait
rempli d'événements hors sujet et gonflé un compteur que quelqu'un aurait fini par
lire comme une preuve. `is_deliverable` restreint donc les trois canaux aux états
`master_signed` / `final`.

### Consigner une écriture sur un `GET` — assumé, et encadré

Le téléchargement est par nature un `GET`. Après avoir passé la matinée à corriger
un `GET` qui écrivait, je note la différence : ici il s'agit d'un **journal
d'accès**, pas d'une création de ressource à conséquence juridique. Deux garde-fous
bornent le risque :

- un accès à un projet n'est pas consigné du tout ;
- un accès n'est **jamais** compté comme une réception.

Un préchargement de lien ne peut donc gonfler qu'un compteur de consultations,
jamais produire une preuve de remise.

### Trois contraintes en base, pas seulement dans les formulaires

- `ck_bl_receipt_channel` — liste fermée. Trois valeurs probantes, pas une quatrième
  improvisée dont personne ne saurait ce qu'elle prouve.
- `ck_bl_receipt_confirmer_client_xor_staff` — le confirmateur est le client **ou**
  le staff, jamais les deux : une attestation du staff ne doit pas pouvoir être
  relue comme une déclaration du client (même principe que
  `bl_validated_on_behalf_by_id`).
- `ck_bl_receipt_ops_needs_means` — un repli **sans moyen de remise** n'établit
  rien : il est instockable. C'est la contrainte qui donne sa valeur au registre.

Table **append-only** : on n'écrase pas un événement de remise, on en ajoute un. Un
registre qui se réécrit ne prouve rien. La date d'attestation ne bouge donc pas
quand un téléchargement postérieur arrive — un test l'épingle.

`confirmed_at` est distinct de `created_at` : la remise papier est saisie **après
coup**, la date déclarée peut précéder la saisie.

### Un défaut latent trouvé au passage

`packing_list_detail` faisait `db.get(Order, pl.order_id)` sans garde. Or `order_id`
est **NULL** pour toute packing list issue d'un **booking** — le cas normal du rail
client, imposé par le XOR `ck_packing_lists_order_xor_booking`. Chaque affichage de
ces packing lists émettait donc un `SAWarning` « fully NULL primary key ». Corrigé.
Trouvé uniquement parce qu'un test a rendu l'écran sur une packing list du rail
booking — les tests existants ne couvraient que le rail commande.

### Une erreur de test de ma part

J'ai d'abord asserté `confirmed_at.tzinfo is not None`. Faux raisonnement :
`DateTime(timezone=True)` rend du **naïf sous SQLite** (tests) et de l'aware sous
Postgres (production) — c'est la convention `planning.ensure_utc` du dépôt.
L'assertion testait le driver, pas mon code. Corrigée pour comparer l'**instant**
après normalisation.

### Reste sur le lot BL

- **séquence de numéros non recyclable** — aujourd'hui `nombre de BL du leg + 1` :
  supprimer un lot fait réattribuer un numéro déjà consommé. Sur un registre
  opposable, deux documents différents pourraient porter le même numéro ;
- **révisions numérotées** (`TUAW_…_R2`) annulant la précédente ;
- **retrait du rail booking**, en dernier.

---

## 2026-08-17 (7) — la numérotation des connaissements cesse de se recycler

Branche `feat/bl-workflow`. Réponse au §4.4, deux derniers volets.

### Le défaut était plus large que la spec ne le disait

La spec signalait le **recyclage** : le numéro valait *nombre de BL émis sur le leg
+ 1*, donc supprimer un lot faisait baisser le compteur et le numéro suivant
réattribuait un numéro **déjà consommé**. Deux documents différents pouvaient porter
le même numéro à deux moments de l'histoire du registre.

En écrivant le test, j'ai trouvé un **second défaut, pire** : si le lot supprimé
n'était pas le dernier — numéros 001, 002, 003 avec 002 supprimé — le compteur valait
2, le code retentait 003, entrait en collision avec la contrainte d'unicité, et
**échouait après 5 tentatives**. L'émission devenait purement **impossible** sur ce
voyage. Un utilisateur aurait vu une erreur 500 sans explication, définitivement.

### La correction, et son point délicat

`bl_number_sequences` : un compteur par voyage, jamais décrémenté, avec une contrainte
`last_seq >= 0` qui interdirait une décrémentation.

Le point délicat n'est pas l'incrément, c'est **l'amorçage**. Une ligne est créée à la
demande pour un voyage qui porte peut-être déjà des numéros. L'amorcer sur leur
*nombre* recyclerait dès la première émission. Il faut le **plus grand suffixe déjà
émis** — et le test qui l'épingle utilise trois numéros dont le plus grand est 007,
précisément pour distinguer les deux lectures.

Corollaire assumé : **les trous de numérotation sont normaux**. Ils tracent un numéro
consommé puis abandonné, ce qu'un registre doit conserver.

Cas résiduel documenté : une packing list sans voyage n'a pas de clé de séquence. Le
repli lit le plus grand suffixe connu (et non leur nombre) : moins fort — supprimer le
dernier libère son numéro — mais l'émission n'est plus bloquée par un trou au milieu.

### L'import Excel : `upsert` au lieu de `delete-all + recreate`

L'import détruisait tous les lots puis les recréait. Sur un registre de
connaissements, cela veut dire que **chaque import consommait des numéros** et
cassait les liens déjà transmis au client.

Passé en *upsert* par `batch_number` : la colonne `BATCH_NUMBER` de l'export, jusque-là
« ignorée à l'import », est désormais remontée par l'analyseur sous une clé
`_batch_number` — préfixée d'un souligné pour ne jamais être confondue avec un champ
du modèle. Un rapprochement réussi passe par `apply_batch_update`, qui audite champ
par champ et ne touche **ni** `bl_number` **ni** l'état du BL.

Trois décisions à l'intérieur :

- une clé **illisible** est ignorée : la ligne devient une création. Un rapprochement
  hasardeux sur un registre de connaissements serait pire ;
- un lot **absent de l'import** n'est supprimé que s'il **ne porte pas** de
  connaissement. Le détruire consommerait son numéro sans retour et casserait un lien
  déjà remis ;
- l'audit **dit** ce qui a été conservé (`N conservés car déjà numérotés (…)`) au lieu
  du « N batches importés » d'avant, qui laissait croire à une synchronisation
  complète.

**Corrigé des deux côtés** — staff *et* portail expéditeur. Un garde-fou qui n'existe
que d'un côté se contourne par l'autre porte, comme pour le gel.

Et la garde du lot précédent tient toujours : un lot **signé** bloque l'import en
bloc. L'*upsert* préserve les numéros, mais un document signé ne se modifie pas — la
correction passe par une révision.

### Vérification — 23 tests, deux sabotages

- revenir au comptage ⇒ **6 tests tombent** (recyclage, blocage, trous, amorçage,
  numéro historique non conforme, monotonie du compteur) ;
- ignorer la clé de rapprochement ⇒ **3 tests tombent** (numéro perdu, compte rendu
  d'audit, régression de la validation).

### Reste sur le lot BL

- **révisions numérotées** (`TUAW_…_R2`) annulant la précédente, les deux restant
  tracées. Les colonnes `bl_revision` / `bl_superseded_by_id` existent depuis la
  première migration mais **aucun code ne les utilise** ;
- **retrait du rail booking**, en dernier.

---

## 2026-08-17 (8) — révisions numérotées, et un écart de conception assumé

Branche `feat/bl-workflow`. Réponse au §4.1 : « à partir de `master_signed`, la
correction ne passe plus par l'édition mais par une révision numérotée
(`TUAW_…_R2`) qui annule explicitement la précédente, les deux restant tracées ».

### ⚠️ J'ai dévié du modèle de données de la spec — voici pourquoi

Le §4.2 plaçait `bl_superseded_by_id` en clé étrangère vers
`packing_list_batches` : une révision aurait donc **créé un nouveau lot**, l'ancien
pointant vers son successeur.

Vérification faite dans le code avant d'implémenter, **ce modèle aurait corrompu tous
les agrégats**. Le lot ne porte pas seulement un document, il porte **la
marchandise** :

| Endroit | Ce qui aurait cassé |
|---|---|
| `pdf_generator.py:140-141` | somme `pallet_count` et `weight_kg` sur `batches` → **poids et palettes doublés** sur la packing list PDF et l'avis d'arrivée |
| `cargo_excel.export_packing_list_xlsx` | liste tous les lots → ligne fantôme à l'export |
| `stowage.locate_for_packing_list` | un lot périmé à placer à bord |
| `PackingList.completion_ratio` | dénominateur faussé |

Corriger cela aurait exigé de filtrer « non périmé » dans **chacun** de ces endroits,
avec **double comptage silencieux au premier oubli** — et rien n'aurait signalé
l'oubli.

D'où l'inversion : **le lot reste unique, c'est le document qui est versionné.** Une
table `bl_revisions` archive le document annulé. Les agrégats existants restent justes
**sans être touchés**, ce qui est aussi la solution la moins risquée en régression.

Conséquence : `bl_superseded_by_id` n'a plus d'objet et est **retirée** (migration
`20260817_0118`). Elle avait été ajoutée trois jours plus tôt sur cette même branche,
non fusionnée, et **aucun code ne la lisait**. La laisser en place aurait été
exactement le piège que je passe mes journées à corriger : une colonne qui a l'air de
vouloir dire quelque chose et que personne ne lit. Un test épingle son absence.

Cet écart est consigné dans la spec elle-même (§4.2, ligne barrée avec le motif) pour
que Julien le voie en revue et puisse le contester.

### Ce que l'archive conserve — et pourquoi le contenu compte

`bl_revisions` garde le numéro, l'empreinte, le signataire, la date, le motif **et le
contenu exact qui avait été signé** (`signature_payload`). Sans ce dernier, la trace
dirait qu'un document a existé sans dire **ce qu'il disait** : inexploitable en
contrôle. Un test vérifie que l'instantané ne bouge plus quand le lot est modifié
ensuite.

Le nouveau document **repart à `draft`** : les marques de l'ancien (signature,
validation client) sont effacées. Les laisser suggérerait une signature qui ne
s'applique plus au contenu courant. Conséquence assumée : le client doit
**revalider** et le commandant **resigner**. Une révision est un document neuf, pas un
correctif glissé sous une signature déjà donnée.

Motif **obligatoire**, avec les mêmes règles que les overrides
(`derived_override.clean_justification`) : « correction » est refusé.

### 🔴 Un trou trouvé par un test que j'écrivais pour autre chose

Le test devait vérifier que `…_001_R2` n'est pas lu comme un numéro de séquence. Il a
échoué pour une **autre** raison : après révision, le numéro d'origine `…_001` ne vit
plus sur aucun lot — il a migré dans l'archive. L'amorçage de la séquence, qui ne
lisait que les lots, **aurait donc réémis un numéro déjà porté par un document remis à
un tiers**.

`_max_issued_suffix` lit désormais **les deux sources**, lots et archive. C'est
exactement le genre de défaut qu'aucune relecture n'aurait attrapé : il n'apparaît
qu'à la conjonction de deux mécanismes écrits à une heure d'intervalle.

### Vérification — 18 tests, deux sabotages

- ne pas archiver le contenu signé ⇒ le test de l'archive complète tombe ;
- garder l'empreinte de l'ancien document sur le nouveau ⇒ le test « ni validé ni
  signé » tombe.

### Reste sur le lot BL

Le **retrait du rail booking** (§5.4), en dernier — c'est la seule étape encore
ouverte, et elle est purement soustractive.
