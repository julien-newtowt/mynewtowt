<!-- source de vérité : le CODE. Chaque route/token/valeur ci-dessous est vérifiée
     dans les fichiers cités en commentaire HTML. Runbook opérationnel — lot 15. -->
# Runbook — Reporting environnemental événementiel (MRV v2)

> Architecture **événementielle déclarative** de la refonte MRV (migrations
> `0096`→`0105`). Ce runbook couvre l'exploitation : crons Power Automate,
> provisionnement des tokens, initialisation des référentiels navire, bascule
> pilote (double-run) et rollback, génération/dépôt des datasets DNV
> OVDLA/OVDBR, import du dataset 2025 en staging, calibrage des seuils, et le
> dépannage courant. Fonctionnement détaillé + règles de gestion :
> `docs/strategy/REGLES_GESTION_DONNEES_EMISSIONS.md`.

## 1. Vue d'ensemble (10 lignes)

Le bord **déclare des événements** (`/onboard/events` : Noon, Departure,
Arrival, Begin/End Anchoring) et des **soutages** (BDN) ; tout le reste est
**dérivé, jamais ressaisi** (distance, temps, conso par deltas de compteurs,
ROB chaîné, cargo MRV — `services/inter_event_compute.py`). Un **grand livre
unique** (`services/emission_ledger.py`) est le seul endroit où une conso est
multipliée par un facteur d'émission (multi-GES CO₂/CH₄/N₂O + WtT). Un **moteur
de règles** (R01-R26 + IR01-IR05, `services/validation_engine.py` +
`validation_rules_catalog.py`) contrôle la qualité à la finalisation, à la
validation des rapports/soutages, à la sync FLGO et lors d'un **run nocturne** ;
tous les seuils vivent en base (`validation_rule_thresholds`), zéro littéral. Les
**rapports générés** (Noon/Carbon/Stopover) suivent un workflow brouillon →
validé Master → validé siège (Carbon). Les sorties réglementaires **OVDLA/OVDBR**
(`services/mrv_dataset.py`) remplacent le CSV DNV 18 colonnes. Un **dashboard**
4 pages (`/dashboard-env`) restitue le tout. La bascule est pilotée par le
feature flag `mrv_v2_capture` (défaut ON, opt-out par navire).

## 2. Les 3 crons Power Automate

Tous les endpoints : méthode `POST`, header `X-API-Token: <token>`, comparaison
à **temps constant**, **corps vide**. `503` si le token n'est pas configuré dans
le `.env` du conteneur ; `403`/`401` si le token du flux ne correspond pas.

| Cron | Endpoint | Token (`.env`) | Périodicité recommandée | Réponse OK |
|---|---|---|---|---|
| Rappels brouillons (R19) | `POST /api/mrv/draft-reminders` | `MRV_DRAFTS_API_TOKEN` | **quotidien** (matin) | `{"scanned":…, "master":…, "siege":…}` |
| Run nocturne qualité | `POST /api/mrv/quality-run` | `MRV_QUALITY_API_TOKEN` | **nocturne** (1×/jour) | `{"legs_scanned":…, "checks":…, "fails":…}` |
| Rapprochement FLGO (Marad) | `POST /api/marad/flgo-refresh` | `MARAD_FLGO_TOKEN` | **quotidien** | `{"imported":…, "updated":…, "skipped":…, "errors":…}` |

<!-- source: onboard_router.py:2106 (draft-reminders, 503→2117, 403→2124) ;
     mrv_router.py:1625 (quality-run, 503→1639, 403→1646) ;
     marad_router.py:91 (flgo-refresh, 503→105, 401→112) -->

- **draft-reminders** — alerte R19 des brouillons d'événements dormants :
  rappel **Master** (auteur du brouillon) au 1ᵉʳ seuil (`delai_rappel_brouillon_h`,
  défaut **24 h**), puis alerte **siège** (`manager_maritime` + `administrateur`)
  au 2ᵉ seuil (`delai_alerte_siege_brouillon_h`, défaut **48 h**). Idempotent
  (pas de doublon de notification). <!-- source: draft_reminders.py:43,47,48,167 -->
- **quality-run** — rejoue le moteur de règles (event + voyage + inter-rapports)
  sur les **legs actifs** (non clôturés, non annulés) de chaque navire, route les
  alertes (R10/R24 → Administrateur ; R14 critique → Manager maritime +
  Administrateur), dédup 24 h + acquittement. <!-- source: validation_rules_catalog.py:1349 (run_nightly_quality), 1204 (_alert_roles) -->
- **flgo-refresh** — rapatrie les relevés FLGO (jaugeage/réception) depuis Marad,
  **lecture seule** (upsert idempotent, fenêtre glissante `MARAD_FLGO_LOOKBACK_DAYS`,
  défaut 400 j). ⚠ Ce cron renvoie **401** (et non 403) sur token invalide, et
  **502** si l'API Marad amont est indisponible — une panne amont ne bloque
  jamais la saisie de bord. <!-- source: marad_router.py:112 (401), 116-123 (502) -->

> **Attention** : `MARAD_FLGO_TOKEN` est **distinct** de `MARAD_SYNC_TOKEN` (cron
> crew). Le socle d'API Marad (`MARAD_BASE_URL` / `MARAD_API_TOKEN` /
> `MARAD_API_KEY_HEADER`) est réutilisé tel quel — aucun nouveau secret d'API.
> Repli manuel si l'API FLGO n'est pas contractualisée : import xlsx via
> `POST /mrv/flgo/import` (écran `/mrv/flgo`, permission `mrv:M`).
> <!-- source: config.py:116-126 ; mrv_router.py:1112 -->

### Exemple de configuration Power Automate (flux planifié)

1. **HTTP** — `POST https://my.towt.eu/api/mrv/draft-reminders`, en-tête
   `X-API-Token: <MRV_DRAFTS_API_TOKEN>`, corps vide (récurrence : 1×/jour).
2. **HTTP** — `POST https://my.towt.eu/api/mrv/quality-run`, en-tête
   `X-API-Token: <MRV_QUALITY_API_TOKEN>` (récurrence : nuit).
3. **HTTP** — `POST https://my.towt.eu/api/marad/flgo-refresh`, en-tête
   `X-API-Token: <MARAD_FLGO_TOKEN>` (récurrence : 1×/jour).

Test manuel :
```bash
TOK=$(grep '^MRV_QUALITY_API_TOKEN=' .env | cut -d= -f2-)
curl -X POST "https://my.towt.eu/api/mrv/quality-run" -H "X-API-Token: $TOK"
```

## 3. Provisionnement des tokens (`.env`)

Trois tokens à générer (aléatoires, ≥ 32 car.) et à recopier dans les flux
Power Automate. Patron des tokens existants (cf. `.env.example`) :

```dotenv
MRV_DRAFTS_API_TOKEN=<aléatoire>     # POST /api/mrv/draft-reminders
MRV_QUALITY_API_TOKEN=<aléatoire>    # POST /api/mrv/quality-run
MARAD_FLGO_TOKEN=<aléatoire>         # POST /api/marad/flgo-refresh (≠ MARAD_SYNC_TOKEN)
```

Après édition du `.env` : `docker compose up -d --force-recreate app` pour
recharger l'environnement du conteneur (sinon l'endpoint continue de répondre
`503`). Relire une valeur : `grep '^MRV_QUALITY_API_TOKEN=' .env`.

## 4. Initialisation des référentiels

### 4.1 Référentiels navire (cuves + moteurs) — écran admin

Écran **`/admin/flotte-env`** (permission `admin:C`). Pour chaque navire, le
bouton d'init poste `POST /admin/flotte-env/{vessel_id}/init` (`admin:M`) →
`services.referential_env.ensure_vessel_env_defaults` : crée **5 cuves**
(`14`/`15`/`16`/`17`/`other`) et **6 moteurs** (`PME`, `SME`, `FWD_GEN`,
`AFT_GEN`, `PORT_SHAFT_GEN`, `STBD_SHAFT_GEN` ; groupe ME/AE dérivé).
**Idempotent** : un appel répété ne crée jamais de doublon. Les capacités de
cuve et les hydrostatiques restent vides (données officielles à fournir, Q11).
<!-- source: admin_router.py:1619,1646,1661 ; referential_env.py:89 -->

Les **facteurs d'émission** multi-GES se gèrent sur **`/admin/emission-factors`**
(`admin:C` / création `admin:M`, append-only versionné par fenêtre de validité).
<!-- source: admin_router.py:1724,1762 -->

### 4.2 Référentiel de validation (règles + seuils) — écran MRV

En dev, le seed s'exécute au boot (`create_all`). En prod (Alembic), poser les
31 règles + seuils + paramètres dashboard via le bouton d'init de
**`/mrv/parametres`** → `POST /mrv/parametres/init` (permission **`mrv:S`**) →
`validation_engine.seed_reference_data` (idempotent : n'insère que le manquant).
<!-- source: mrv_router.py:151,219,226 ; validation_engine.py:838 -->

## 5. Bascule pilote (double-run) & rollback

Le flag `mrv_v2_capture` gouverne la capture. Sémantique du helper
`feature_flags.capture_v2_enabled` : <!-- source: feature_flags.py:91-127 -->

- **flag absent** → capture v2 **ON** (défaut global) ;
- `enabled=false` → capture v2 **OFF** (tous les navires repassent au formulaire
  noon legacy) ;
- `enabled=true` + navire dans `audience.vessels_off` → **OFF pour ce navire
  seul** (double-run inversé : le pilote garde le legacy) ; les autres en v2.
- **Fail-open vers ON** : une panne DB ne rouvre jamais le legacy en douce.
- **Cache 20 s** : tout changement prend effet en **≤ 20 s** (pas de redéploiement).

### 5.1 Double-run — mettre UN navire (ex. ANEMOS, code `ANE`) en legacy

```sql
-- v2 imposé partout SAUF ANEMOS (qui garde l'ancien formulaire noon)
INSERT INTO feature_flags (key, enabled, rollout_pct, audience, description)
VALUES ('mrv_v2_capture', true, 0, '{"vessels_off": ["ANE"]}',
        'Bascule capture événementielle MRV v2 (double-run pilote)')
ON CONFLICT (key) DO UPDATE
   SET enabled = true, audience = '{"vessels_off": ["ANE"]}';
```

L'entrée de `vessels_off` est comparée au **code navire** (insensible à la casse)
ET à l'**id** (en chaîne) : `["ANE"]` ou `["2"]` fonctionnent tous deux.
<!-- source: feature_flags.py:116-121 -->

### 5.2 Rollback — tout repasser en legacy immédiatement

```sql
UPDATE feature_flags SET enabled = false WHERE key = 'mrv_v2_capture';
```

Effet en **≤ 20 s** (TTL du cache). Le formulaire noon legacy redevient actif
sur tous les navires ; les données v2 déjà capturées restent intactes.

## 6. Datasets réglementaires OVDLA / OVDBR

Écran **`/mrv/datasets`** (`mrv:C`). La génération poste
`POST /mrv/datasets/generate` (`mrv:M`) ; téléchargements :
`/mrv/datasets/ovdla.{xlsx,csv}` et `/mrv/datasets/ovdbr.{xlsx,csv}`.
<!-- source: mrv_router.py:1471,1523,1585-1620 -->

- **OVDLA** (*Log Abstract*) : **1 ligne par événement validé** Departure /
  Arrival / Begin|End Anchoring — **jamais les Noon** (qui agrègent les deltas
  entre deux lignes OVDLA). Valeurs en **deltas** depuis l'événement OVDLA
  précédent (`Time_Since_Previous_Report`, `Distance`, `ME/AE_Consumption_MDO`) ;
  `MDO_ROB` et `Cargo_Mt` sont absolus ; positions en DMS. `Source_System =
  "MyTOWT"` (décision Q10). <!-- source: mrv_dataset.py:344,410-444,524 ; models/mrv_dataset.py:52 -->
- **OVDBR** (*Bunker Report*) : **1 ligne par soutage validé Master**.
  <!-- source: mrv_dataset.py:533,568 -->

### Portes qualité (ce qui entre — ou non — dans le dataset)

| Dataset | Porte de génération | Exclusion |
|---|---|---|
| OVDLA | événement `nav_events.status == "valide"` | événement non validé ; rapport lié `under_conformity` |
| OVDBR | soutage `bunker_operations.status == "valide_master"` | soutage non validé Master ; `under_conformity` |

Une ligne dont le `verification_status` (taxonomie `conform` / `corrected` /
`clarified` / `under_conformity`) est **`under_conformity`** est **exclue** de la
consolidation et **déclenche une alerte** Administrateur. Un `payload`
déjà gelé n'est jamais réécrit à la régénération (reproductibilité d'audit) — seuls
`Source_System`/`Last_Updated` sont rafraîchis. Export : `.xlsx` (openpyxl) et
`.csv` (virgule) — **seules les lignes incluses**.
<!-- source: mrv_dataset.py:417-437 (OVDLA gates), 568-595 (OVDBR gates), 663-683 (freeze), 746-790 (export) -->

> ⚠ **Écart code/spéc à connaître** : contrairement à ce que suggère la
> spécification, `services/mrv_dataset.py` n'impose **aucune** porte « Carbon
> validé siège » pour générer l'OVDLA. Les seules portes réelles sont le statut
> `valide` de l'événement, le statut `valide_master` du soutage, et l'exclusion
> `under_conformity`. À faire évoluer si DNV exige le verrou Carbon-siège.

### Dépôt DNV

Générer, contrôler la page qualité (`/mrv/qualite`) pour lever les
`under_conformity` bloquants, régénérer, puis télécharger les `.xlsx` et déposer
chez DNV. Le premier dépôt réel doit être fait **en parallèle** du canal actuel
(validation croisée). Comparaison à l'identique aux échantillons 2025 :
`scripts/golden_ovd_2025.py` (rapport d'écart, ne tolère que `Source_System` et
`Last_Updated`).

## 7. Import du dataset 2025 (staging / tests uniquement)

> ⚠️ **Q1 — DÉMARRAGE À VIDE EN PRODUCTION.** `scripts/import_mrv_2025.py` sert
> **exclusivement** aux tests et au staging : aucun module de `app/` ne
> l'importe, aucun écran/cron ne le déclenche. **Ne JAMAIS le lancer contre la
> base de production.** <!-- source: scripts/import_mrv_2025.py:4-11 -->

Procédure (docstring du script), base **non-prod** choisie par `--database-url` :

```bash
createdb towt_staging
alembic upgrade head
# 1) simulation complète (fait tout le travail puis ROLLBACK) :
python -m scripts.import_mrv_2025 \
    --database-url postgresql+asyncpg://towt:…@localhost:5432/towt_staging \
    --xlsx "Sample_Dataset_Architecture_Evenementielle_2025.xlsx" --dry-run
# 2) import réel (COMMIT) :
python -m scripts.import_mrv_2025 --database-url … --xlsx "…2025.xlsx"
# 3) réconciliation totaux annuels ANEMOS vs PDF DNV (tolérance ±1,5 %) :
python -m scripts.import_mrv_2025 --database-url … --xlsx "…2025.xlsx" --reconcile
```

Peuple : référentiels (ANEMOS/ARTEMIS + cuves/moteurs/facteurs/ports), **28
voyages** créés **clôturés** (`status=completed`, jamais de modification d'un leg
existant), **672 événements** (dont **148 hors périmètre** — codes voyage
2024/2026 — comptés et ignorés), relevés, **21 soutages**, lectures FLGO,
contrôles croisés conso. Un leg existant sert seulement de cible d'attachement.
Le xlsx source vit dans le dossier client (non versionné) ; seules les fixtures
compactes le sont. <!-- source: scripts/import_mrv_2025.py:13-41,745-800,1748-1783 -->

## 8. Calibrage des seuils (post-pilote)

**21 des 27 seuils** sont marqués `provisional=True` (propositions Q8, à confirmer
métier après le voyage pilote). Écran **`/mrv/parametres`** (`mrv:C` en lecture ;
édition `mrv:S`) :

- `POST /mrv/parametres/thresholds/{id}/update` — modifier une valeur globale ;
- `POST /mrv/parametres/thresholds/override` — poser un **override par navire** ;
- `POST /mrv/parametres/dashboard/{id}/update` — paramètres dashboard.

<!-- source: mrv_router.py:279,318,391 -->

Chaque changement est tracé (`activity.record`) et pris en compte **sans
redéploiement** (cache 60 s, résolution `(rule,vessel)` → `(rule,NULL)` → défaut
codé). Le marqueur `provisional` reste affiché tant que la valeur n'est pas
validée. Exemple : passer `seuil_conso_ref_l_j` de 750 à 800 change le verdict de
R08/R11/R15 à la volée. Liste des 21 seuils provisoires :
`docs/strategy/REGLES_GESTION_DONNEES_EMISSIONS.md` §5.

## 9. Dépannage

### Finalisation d'un événement refusée (`EventFinalizationError`)
Une règle **bloquante** (scope `event`) a échoué à la finalisation, OU une
position manuelle est sans justification (R05). Le message liste les motifs
(`R01 : …`, `R05 : …`) ; l'événement reste **brouillon**. Corriger le brouillon
(l'auteur seul peut le reprendre) et re-finaliser. Les anomalies sont journalisées
dans `quality_check_results` (visibles sur `/mrv/qualite`).
<!-- source: event_capture.py:225-268 -->

### Régression de compteur moteur (R10 / IR04)
Un compteur carburant qui régresse sans reset **confirmé** casse la chaîne de
conso aval. Sur `/mrv/qualite`, l'Administrateur **confirme le reset** via
`POST /mrv/qualite/engine-readings/{reading_id}/confirm-reset` (`mrv:M`) — la conso
repart de la valeur aval. Non confirmé au-delà de `delai_confirmation_reset_j`
(défaut 3 j), R10 **escalade en bloquant**. IR04 accepte un reset simplement
**documenté** (`is_counter_reset`), R10 exige la **confirmation**.
<!-- source: mrv_router.py:1357 ; inter_event_compute.py:212-240 ; validation_rules_catalog.py:446 -->

### Soutage non rapproché avec FLGO (R24)
Un BDN sans lecture FLGO « Received » sous `delai_flgo_bunkering_j` (défaut 5 j)
lève R24 (warning routé Administrateur). Vérifier que le cron `flgo-refresh`
tourne et que la lecture Marad existe ; sinon importer le xlsx via
`/mrv/flgo/import`. R24 ne bloque pas — elle signale une complétude Marad à
vérifier. <!-- source: validation_rules_catalog.py:1019 -->

### Endpoint cron → `503`
Le token n'est pas configuré dans le `.env` **du conteneur**. Poser la variable
puis `docker compose up -d --force-recreate app`.

### Endpoint cron → `403` (draft-reminders, quality-run) ou `401` (flgo-refresh)
Le header `X-API-Token` du flux ne correspond pas au token du `.env`. Vérifier la
valeur **brute** (sans espace ni guillemets).

### `flgo-refresh` → `502`
L'API Marad amont est indisponible. Sans conséquence sur la saisie de bord ;
réessayer, ou basculer sur l'import xlsx manuel.

## 10. Sécurité

- Endpoints crons protégés par `X-API-Token` dédié (comparaison à temps
  constant) ; `/api/mrv/*` et `/api/marad/*` exemptés de CSRF (auth par token).
- FLGO/Marad : **lecture seule** (aucune écriture vers Marad).
- Toutes les écritures MRV passent par `services.activity.record()` (audit).
- Datasets figés (`payload` gelé) — reproductibilité d'audit du dépôt DNV.
