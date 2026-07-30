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

### J3 après-midi — recadrage métier par l'Armement, et repriorisation du lot

Yasmin a recueilli le **processus réel des relèves d'équipage** auprès de
l'Armement. Il invalide une partie de ce que j'avais prévu le matin.

**Processus réel (référence métier)** :
1. **Simulation dans Excel** (Armement) : jours en mer, périodes embarquées / à
   terre, anticipation des relèves, planning cohérent avec les contraintes
   opérationnelles. Cette étape existe **parce que Marad ne donne pas assez de
   visibilité pour planifier** — ce n'est pas un contournement d'outil, c'est un
   manque fonctionnel de Marad.
2. **Décision dans Excel** : une seconde feuille définit et valide les dates
   d'embarquement/débarquement. **C'est la décision d'Armement.**
3. **Transmission à l'agent d'escale** : nom/prénom, nationalité, n° de passeport
   ou titre de séjour, ETD, n° de vol ou de train, heure de départ → alimente la
   **note d'escale**. L'agent d'escale organise les RDV PAF, **il ne décide
   rien**.
4. **Conformité documentaire déjà couverte** : Marad notifie l'Armement
   suffisamment en amont des expirations (passeports, titres de séjour,
   Schengen), et l'équipe en tient compte à la planification.

**Ordre de priorité imposé par Yasmin** : comprendre les processus réels → les
reproduire fidèlement → valider que les équipes travaillent efficacement →
*ensuite* ajouter contrôles, conformité et qualité de données. « La valeur métier
doit toujours passer avant les contrôles de conformité ou les fonctionnalités
*nice to have*. » Elle demande explicitement à être challengée si un risque
opérationnel ou réglementaire majeur justifie l'inverse.

#### Ce que j'abandonne, et pourquoi ma recommandation du matin était mauvaise

**Recâblage du garde-fou passeport — ABANDONNÉ.** J'avais recommandé le matin de
brancher `passport_blocking_reason` sur la saisie d'escale, en mode override
tracé. **Cette recommandation était mauvaise** : l'agent d'escale ne décide pas
les embarquements, il **transcrit** une décision déjà prise par l'Armement après
vérification des documents via les alertes Marad. Le contrôle aurait contraint le
mauvais acteur, pour une décision prise ailleurs, avec moins d'information que
celui qui l'a prise.

**Approfondissement du calcul Schengen — DÉPRIORITISÉ.** Marad alerte déjà en
amont. Ce n'est pas une fonctionnalité manquante, c'est un **doublon**.

#### Ce que j'ai maintenu, en le justifiant par la valeur métier

Distinction proposée à Yasmin et retenue : **un indicateur qui affirme quelque
chose de faux n'est pas un contrôle manquant, c'est un défaut.** Le corriger peut
consister à le faire **taire** plutôt qu'à le rendre intelligent.

**1. Double comptage des jours en mer — CORRIGÉ (commit à suivre).**

`embarked_days_by_member` **additionnait** les jours de deux registres qui
décrivent parfois la même période : `MaradCrewSchedule` (les relèves décidées par
l'Armement) et `CrewAssignment` (créé par la saisie d'escale). Sa docstring
supposait explicitement `CrewAssignment` vide (« les marins proviennent
exclusivement de Marad, aucune saisie manuelle ») — ce qui est faux, la saisie
d'escale en crée. Dès qu'une escale était saisie pour un embarquement déjà connu
de Marad, **les jours en mer du marin doublaient**. Affiché sur `/crew`.

Reconstruit sur une **union d'ensembles de jours calendaires** (même approche que
`refresh_schengen_for_members`) : un jour couvert par les deux registres compte
une fois. `_marad_days_in_year` devenait du code mort → retiré.

**Pourquoi ça reste prioritaire dans la logique de Yasmin** : si mynewtowt doit
reproduire la planification des relèves, le comptage des jours en mer n'est pas un
contrôle de phase 4 — **c'est la fonctionnalité elle-même**. Toute la raison
d'être de la simulation Excel est de compter les jours en mer et les périodes
embarquées / à terre. Un planificateur qui double-compte est **pire qu'Excel**.
Ce point de qualité de données ne se reporte donc pas : il est **absorbé** par la
valeur métier.

Preuve : sur l'ancien code, les deux nouveaux tests donnent `20` là où la vérité
est `10`, et `21` là où l'union vaut `15` (recouvrement partiel : somme = 21,
maximum = 11, union = 15 — trois résultats différents).

**2. La pastille Schengen ne mentira plus — CORRIGÉ.**

Nouveau statut `indetermine`, affiché **« Non calculé — voir Marad »** en pastille
neutre avec infobulle explicative. Déclenché quand des embarquements existent hors
de portée du calcul : plannings Marad (cas dominant) ou affectation sans voyage
(`leg_id` nul, arbitrage A4).

⚠️ **Piège évité** : les trois templates avaient un `{% else %}` affichant
« Non-compliant ». Un nouveau statut y serait apparu comme une **alerte** — on
aurait remplacé une fausse réassurance par une fausse alarme. Calcul et affichage
ont donc bougé ensemble (`crew/index.html`, `crew/detail.html`,
`crew/compliance.html`).

Trois décisions de conception :
- **Un dépassement certain prime sur l'incertitude** : 100 jours établis restent
  `non_compliant`, même si d'autres embarquements échappent au calcul.
- **`indetermine` ne remonte pas dans les alertes** — c'est une absence
  d'information, pas un avertissement, et Marad alerte déjà. L'y mettre
  produirait du bruit sur presque tous les marins (leurs embarquements viennent
  de Marad, que ce calcul n'exploite pas). Le filtre d'alerte de
  `crew_router.py` liste explicitement `warning`/`non_compliant` : correct par
  construction, aucune modification nécessaire.
- **Un marin sans embarquement nulle part reste `compliant`** : là, zéro jour est
  la vérité.

6 tests dédiés (`test_crew_schengen_indetermine.py`), dont 2 échouent sur
l'ancien code avec le faux `compliant` et 4 protègent contre une sur-correction
(congé Marad ≠ embarquement, ressortissant Schengen, absence réelle
d'embarquement, dépassement établi).

#### Le cliquet de typage m'a bloqué — deuxième fois dans la journée

Après ces corrections : **372 erreurs mypy pour un plafond à 371**. Mon nouveau
code en ajoutait une — `MaradCrewSchedule.crew_member_id` est une FK **nullable**
et je l'ajoutais à un `set[int]` sans vérifier, en *supposant* que le filtre SQL
`in_` l'excluait. Garde explicite ajouté. Condition redondante simplifiée au
passage (`bool(ensemble and id in ensemble)`).

Après correction : **371, exactement au plafond**. Le cliquet posé le matin a donc
attrapé, le jour même, une erreur qui serait partie en CI comme celle du matin.
Il a déjà payé son coût d'installation.

#### Gitleaks — troisième itération, et un défaut dans mon propre garde-fou

Le run précédent a montré que le `GITHUB_TOKEN` était **nécessaire mais pas
suffisant** : l'action ignore l'`args` fourni et construit sa propre commande sur
une **plage de commits**, irrésoluble avec le `fetch-depth: 1` par défaut.
Gitleaks sortait en code 1 (erreur, pas détection) avec un rapport SARIF **vide**.
« 0 détection » signifiait donc « rien n'a été scanné ».

Corrigé par `fetch-depth: 0`, et `args` retiré (il donnait l'illusion d'une
configuration active).

⚠️ **Et mon propre garde-fou était insuffisant** : il exigeait « au moins une
exécution dans le rapport ». Or le rapport du run cassé en contenait déjà une,
avec zéro résultat — **mon contrôle aurait validé le scan cassé**. Remplacé par le
vrai discriminant : `steps.gitleaks.outcome`, qui conserve le résultat réel avant
que `continue-on-error` ne l'efface. Détection d'un secret rendue **bloquante**,
avec `.gitleaksignore` comme échappatoire documentée.

Résultat vérifié : **22 commits scanned · no leaks found**, erreur de code de
sortie disparue. **Le dépôt est propre** — aucun secret dans l'historique.

C'est la quatrième fois dans la journée que le même piège se referme, dont deux
sur mes propres contrôles. La règle « un contrôle qu'on ne fait pas échouer
volontairement au moins une fois n'est pas un contrôle » est confirmée par
l'expérience, pas par principe.

#### En attente

**Les fichiers Excel des relèves ne sont pas encore partagés** (vérifié : seuls le
template de note d'escale `Port Call Preparation-ARTEMIS-Voyage 2BGPFR6` et les
fichiers MRV sont disponibles). **Aucune implémentation proposée** sur les relèves.

Ce qui sera examiné en priorité à leur réception, parce que c'est là qu'une
réimplémentation dévie silencieusement d'Excel :
- **conventions de comptage** — le jour d'embarquement compte-t-il plein ? les
  jours de transit (vol, train) ? les jours à terre entre deux legs ?
- **ce qui rend une relève valide** — repos minimum, durée maximale embarquée,
  postes obligatoires à bord ;
- **la forme exacte de la transmission** à l'agent d'escale, pour la brancher sur
  le template de note d'escale.

#### Piste identifiée pour le lot relèves (non engagée)

La transmission PAF existe **déjà à moitié** : la route
`/crew/border-police/{vessel_id}` produit une liste d'équipage bilingue FR/EN
pour la PAF. Mais :
- elle ne lit que les affectations **rattachées à un leg** (`leg_id.in_(...)`,
  `crew_router.py:1166`) — le registre des Opérations, pas les décisions de
  l'Armement. En pratique elle est donc probablement vide ou incomplète ;
- il lui manque exactement les deux champs que l'Armement transmet : **n° de
  vol/train** et **heure de départ** — or ils existent déjà dans le modèle
  `CrewTicket` (`mode`, `reference`, `carrier`, `departure_at`).

Les pièces sont là, branchées sur la mauvaise source et non assemblées.
