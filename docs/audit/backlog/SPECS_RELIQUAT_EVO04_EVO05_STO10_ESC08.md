# Specs d'implémentation — reliquat backlog (EVO‑04 IA · EVO‑05 PWA · STO‑10 SVG · ESC‑08 lanes)

> **Objet :** specs détaillées et actionnables des 4 derniers items du backlog,
> qui requièrent un **environnement riche** (clé Anthropic, navigateur/IndexedDB,
> revue visuelle) non disponible en exécution headless. Chaque spec est conçue
> pour être implémentable d'un trait, avec critères d'acceptation et tests.
> **Date :** 2026‑06‑29. **Pré‑requis communs :** charte Kairos, CSP‑strict
> (JS externe), `require_permission`, `flush+303`, `activity.record`, i18n×5 si
> texte public, test de non‑régression, migration additive si schéma.

---

## 1. EVO‑04 — Couche IA de la veille (synthèse + scoring)

**Socle déjà livré (lot 70) :** `services/news_scoring.py` (scoring heuristique
0–100 + `priority_label`). La couche IA se branche **au‑dessus**, en *enrichissement*.

### Objectif
Pour le flux de veille : (a) un **score de pertinence affiné par IA** par item,
(b) une **synthèse quotidienne** (digest) des actualités saillantes, exploitables
par le staff.

### Approche (réutilise le pattern chatbot)
- `app/services/chatbot.py` expose déjà le pattern : `MODEL = "claude-sonnet-4-6"`,
  `_call_anthropic(...)`, garde `if not settings.anthropic_api_key: <fallback>`,
  `from anthropic import AsyncAnthropic`. **Mirroir** ce pattern dans un nouveau
  service `app/services/news_ai.py`.
- **Dégradation gracieuse obligatoire** : sans `ANTHROPIC_API_KEY`, l'IA est un
  no‑op → on retombe sur le **scoring heuristique** (lot 70). Aucun chemin ne
  doit dépendre d'un appel réseau pour fonctionner.

### Fichiers
- **Créer** `app/services/news_ai.py` :
  - `async def ai_relevance(db, items: list[NewsItem]) -> dict[int, int]` —
    score IA 0–100 par item. Batch (1 appel pour ≤ N items, prompt structuré
    « note la pertinence pour un armateur de cargo à voile décarboné »).
    Retourne `{}` si pas de clé → l'appelant garde l'heuristique.
  - `async def daily_digest(db, items, *, lang="fr") -> str | None` — synthèse
    markdown des items du jour (None si pas de clé). Cache le résultat (voir
    persistance ci‑dessous) pour éviter de rappeler l'API à chaque page.
- **Modèle / migration** (`0084`, additive) — option A (recommandée) :
  - `news_items.ai_score: int | None` (persiste le score IA, recalculé au cron) ;
  - une petite table `news_digests(id, day Date unique, lang, body Text,
    generated_at)` pour le digest quotidien.
- **Cron** : étendre `POST /api/veille/refresh` (token `VEILLE_API_TOKEN`) pour,
  après ingestion, appeler `ai_relevance` (remplit `ai_score`) et `daily_digest`
  (upsert `news_digests` du jour). **Idempotent**.
- **Vue** `veille_router.veille_index` : si `ai_score` présent, l'utiliser pour le
  badge priorité (sinon heuristique) ; afficher un encart « Synthèse du jour »
  (digest) en tête si disponible.

### Sécurité / coûts
- Anti‑injection : réutiliser `chatbot.detect_injection` sur les titres/descriptions
  avant de les passer au prompt (contenu externe non fiable).
- Budget : 1 appel batch scoring + 1 appel digest par run de cron (pas par page).
- CSP : aucun rendu HTML d'IA non échappé (markdown → HTML via un rendu sûr,
  pas de `|safe` sur du contenu modèle).

### Tests
- `news_ai.ai_relevance` / `daily_digest` **sans clé** → `{}` / `None` (exécutable).
- Mock du client Anthropic (monkeypatch `_call_anthropic`) → mapping IA appliqué,
  fallback heuristique quand l'IA renvoie vide.
- Migration `0084` rendue en SQL offline.

### Dépendances / risque
- **Clé `ANTHROPIC_API_KEY`** requise pour la valeur réelle (sinon socle heuristique).
- **Effort :** M. **Acceptation :** veille priorisée par IA quand la clé est là,
  inchangée (heuristique) sinon ; digest du jour affiché ; cron idempotent.

---

## 2. EVO‑05 — PWA offline réel (Carnet de bord / noon report)

**Existant :** `pwa_router` sert `/sw.js` + `/manifest.json` (statics) avec
`Service-Worker-Allowed: /`, scope `/onboard*`. JS : `onboard-offline.js`
(file d'attente `localStorage` « towt_offline_queue » + toast), `pwa-onboard.js`.

### Objectif
Offline **réel** à bord (connexion satcom intermittente) : (a) shell `/onboard`
consultable hors‑ligne, (b) **saisies (noon report, SOF) mises en file et
rejouées** automatiquement au retour du réseau, via **IndexedDB** + **Background
Sync** (au lieu de la file `localStorage` actuelle, fragile).

### Approche
- **Service Worker** (`app/static/sw.js`) :
  - `install` : pré‑cache du shell (`/onboard`, CSS Kairos, JS, logo, offline
    fallback page). Versionner le cache (`CACHE = "towt-onboard-v{n}"`),
    purge des anciens au `activate`.
  - `fetch` : *network‑first* pour les pages `/onboard*` (fallback cache), 
    *cache‑first* pour les assets statiques.
  - `sync` (Background Sync, tag `towt-onboard-flush`) : vide la file IndexedDB
    vers les endpoints POST quand la connectivité revient.
- **IndexedDB** (`app/static/js/onboard-offline.js`, réécriture) :
  - store `pending` (clé auto, `{url, method, body, headers, created_at}`).
  - à la soumission d'un form `data-offline-queue` : si `navigator.onLine` false
    (ou POST échoue), enqueue + toast « enregistré hors‑ligne » + `registration.sync.register('towt-onboard-flush')`.
  - au retour online (`window 'online'` + SW sync) : POST chaque entrée, retire
    de la file sur 2xx, conserve et réessaie sinon.
- **Idempotence côté serveur** : les endpoints noon/SOF doivent tolérer un rejeu
  (clé d'idempotence `client_uuid` envoyée par le form, ignorée si déjà vue).
  → migration additive `noon_reports.client_uuid` (+ unique partiel) si non présent.

### Fichiers
- `app/static/sw.js` (réécriture cache + sync), `app/static/js/onboard-offline.js`
  (IndexedDB), `app/templates/staff/captain/*` (attribut `data-offline-queue` +
  champ caché `client_uuid` généré JS), endpoints noon/SOF (garde idempotence),
  migration `0085` (client_uuid) si retenue.

### Tests
- Unit JS difficile en headless → **tests d'intégration serveur** sur
  l'idempotence (POST deux fois le même `client_uuid` → une seule création) ;
  test que `/sw.js` et `/manifest.json` sont servis avec les bons headers.
- Recette manuelle (DevTools → Offline) : saisir un noon hors‑ligne, repasser
  online, vérifier le flush.

### Dépendances / risque
- **Navigateur** (SW/IndexedDB/Background Sync) requis pour la valeur réelle ;
  non vérifiable en headless. **Effort :** L. **Acceptation :** `/onboard`
  consultable offline ; saisie offline rejouée sans doublon au retour réseau.

---

## 3. STO‑10 — Vue SVG top‑down du plan d'arrimage

**Existant :** 18 zones `{DECK}_{HOLD}_{BLOCK}` (DECKS = INF/MIL/SUP,
HOLDS = AR/AV, BLOCKS = AR/MIL/AV), `DANGEROUS_ZONES`, occupation via
`occupation_by_hold` / `zones_for_leg`, capacités via
`stowage_specs.build_reference_specs`. **API JSON déjà livrée** (lot 63 :
`/stowage/legs/{id}/occupation.json`).

### Objectif
Une **vue SVG top‑down par pont** (backlog CLAUDE.md #3) : pour chaque pont
(INF/MIL/SUP), une grille **2 cales × 3 blocs** colorée par taux d'occupation,
avec badges DG, tooltip (palettes / capacité / poids).

### Approche (géométrie régulière → dérivable, pas d'arbitraire)
- **Pas de nouveau backend** : réutiliser `zones_for_leg` (ou la route JSON lot 63)
  pour l'occupation par zone, et `build_reference_specs` pour la capacité.
- **Génération SVG côté serveur (Jinja) ou JS** — recommandé **serveur** (CSP‑strict,
  pas de canvas) : un template `staff/stowage/_deck_svg.html` qui prend
  `deck`, la liste des 6 zones de ce pont (2 holds × 3 blocs) et rend un `<svg>`
  avec 6 `<rect>` positionnés sur une grille fixe (x = bloc AR/MIL/AV, y = cale
  AR/AV), `fill` interpolé du vert (vide) au cuivre/rouge (plein) selon
  `pallet_count / capacity_epal`, `<title>` pour le tooltip, contour rouge si
  zone DG.
- **Route** : enrichir `stowage_plan_view` (`/stowage/legs/{id}`) pour passer
  `decks_layout = {deck: [zone_dicts...]}` (3 ponts × 6 zones) + capacités, et
  inclure les 3 SVG (toggle « table / SVG »).
- **Couleurs** : palette Kairos (`--vert` → `--cuivre` → `--danger`) via classes
  CSS ou `fill` calculé ; pas d'inline `<script>`.

### Fichiers
- `app/templates/staff/stowage/_deck_svg.html` (partial SVG par pont),
  `app/templates/staff/stowage/plan.html` (intégration + toggle),
  `app/routers/stowage_router.py` (contexte `decks_layout`),
  `app/services/stowage.py` (helper `deck_layout(db, leg_id) -> dict[deck, list]`
  réutilisant occupation + specs).

### Tests
- `deck_layout` : structure 3 ponts × 6 zones, fusion occupation+capacité,
  marquage DG (zones de `DANGEROUS_ZONES`). Exécutable (pure/DB).
- Template `_deck_svg` parse + contient `<svg`, 6 `<rect>` par pont.
- Recette visuelle (revue navigateur).

### Dépendances / risque
- **Revue visuelle** souhaitable (proportions/couleurs) mais géométrie déterministe.
  **Effort :** M. **Acceptation :** 3 SVG top‑down lisibles, occupation/DG
  correctes, toggle table/SVG, zéro script inline.

---

## 4. ESC‑08 — Vue en lanes d'activités parallèles (cockpit d'escale)

**Existant :** opérations d'escale typées (`operation_type` ∈ technique,
armement, relations_externes, documentaire, commercial — cf. `ACTIONS_BY_TYPE`,
lot 57), table d'opérations dans `staff/escale/index.html`. Cockpit déjà doté de
la timeline (lot 64), des métriques nav (lot 65), de la synthèse commerciale
(lot 60).

### Objectif
Afficher les opérations en **swim‑lanes parallèles par catégorie** (une colonne
par `operation_type`), pour visualiser les activités menées en parallèle pendant
l'escale (complément de la table linéaire existante, via un toggle).

### Approche (pure + additif, pas de schéma)
- **Service** `app/services/leg_overview.py` (où vit déjà le cockpit) :
  `def operations_by_lane(operations) -> list[dict]` → une lane par
  `OPERATION_TYPES` non vide : `{type, label, ops: [...]}`, ops triées par
  `planned_start`/`actual_start`. Pure fonction (testable sans DB).
- **Route** `escale_index` : passer `lanes = operations_by_lane(operations)` au
  contexte (les `operations` sont déjà chargées).
- **Template** `staff/escale/index.html` : sous la table existante, un bloc
  « Activités parallèles » (grid de colonnes), chaque lane = carte avec ses
  opérations (badge action, intervenant, statut, bornes). Toggle d'affichage
  (table ↔ lanes) via une classe CSS (pas de JS lourd ; un détail `<details>`
  ou des boutons HTMX de bascule de vue).
- **Labels** : libellés FR des catégories (réutiliser un mapping court).

### Fichiers
- `app/services/leg_overview.py` (+ `operations_by_lane` + mapping libellés),
  `app/routers/escale_router.py` (contexte `lanes`),
  `app/templates/staff/escale/index.html` (bloc lanes + toggle).

### Tests
- `operations_by_lane` : regroupement correct par type, lanes vides exclues,
  tri intra‑lane. Exécutable (pure fonction, comme `port_call_steps`).
- Template : présence du bloc lanes + des colonnes par catégorie.

### Dépendances / risque
- **Revue visuelle** souhaitable (mise en page colonnes) mais logique pure.
  **Effort :** S–M. **Acceptation :** opérations groupées par catégorie en
  colonnes, cohérentes avec la table, zéro script inline.

---

## Séquencement conseillé

1. **ESC‑08 lanes** (S–M, pure + additif) — complète le cockpit, le plus sûr.
2. **STO‑10 SVG** (M, géométrie déterministe) — revue visuelle légère.
3. **EVO‑04 IA** (M) — quand `ANTHROPIC_API_KEY` est provisionnée ; dégradation
   gracieuse garantie par le socle heuristique (lot 70).
4. **EVO‑05 PWA** (L) — dernier ; nécessite recette navigateur (DevTools offline).

> Chaque item est **autoportant** et **n'introduit pas de régression** (fallback
> heuristique pour EVO‑04 ; toggles additifs pour STO‑10/ESC‑08 ; idempotence
> serveur pour EVO‑05). Migrations additives uniquement (`0084`/`0085` si retenues).
