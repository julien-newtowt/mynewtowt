# Plan — Grilles tarifaires multi-routes (rattrapage Module 6)

> **But** : aligner la grille tarifaire V3 sur le modèle documenté de l'ancien
> ERP (`mytowt`), Module 6 — Pricing (cf.
> `docs/strategy/NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md`, §707-765).
> **Contexte** : pas de reprise de données → **rebuild propre** du schéma de
> grille (pas de migration de compatibilité ascendante).

## Pourquoi
La V3 actuelle a perdu la structure **multi-routes** de l'ancien ERP :
- aujourd'hui : **1 grille = 1 route** (`pol_locode`/`pod_locode` sur l'en-tête),
- cible Module 6 : **1 grille = 1 client + 1 période + N routes** (chaque route
  est une *ligne* avec sa distance/OPEX/base_rate), les **brackets de volume**
  remontant au niveau de la grille.

Déjà livré (rattrapage partiel, sur `main`) :
- cycle de vie (1 active/périmètre, verrou, « repasser en brouillon ») + recalcul OPEX ;
- `volume_commitment` (engagement min palettes/commande).

## Schéma cible (rebuild propre — migration `0054` drop/recreate)

### `rate_grids` (en-tête)
| Champ | Note |
|---|---|
| `id`, `reference` (RG-YYYY-NNNN) | inchangé |
| `client_id` (nullable) | grille client ou défaut |
| `vessel_id` (nullable) **NOUVEAU** | pour le lookup OPEX par navire |
| `valid_from`, `valid_to` | période |
| `adjustment_index` | inchangé |
| `bl_fee`, `booking_fee` (Numeric, nullable) **NOUVEAU** | forfaits (sucre au-dessus des options) |
| `volume_commitment` (Int, nullable) | déjà présent |
| `hazardous_surcharge_pct`, `min_charge_eur` | déjà présents |
| `brackets_json` (Text/JSON) **NOUVEAU** | coefficients de volume au niveau grille (remplace les lignes-brackets) |
| `is_default`, `status`, `currency`, `notes`, `created_at` | inchangé |
| ~~`pol_locode`, `pod_locode`~~ | **RETIRÉS de l'en-tête** (passent sur les lignes) |

### `rate_grid_lines` (REDÉFINI = routes)
| Champ | Note |
|---|---|
| `id`, `grid_id` (FK CASCADE) | |
| `pol_locode`, `pod_locode` | route |
| `leg_id` (nullable) | route rattachée à un leg type |
| `distance_nm` | orthodromique (saisie ou depuis le leg) |
| `nav_days` | `distance / (8 kn × 24)` |
| `opex_daily` | OPEX jour (navire/param) |
| `base_rate` | `opex_daily × nav_days / 978` |
| `is_manual` (bool) | surcharge manuelle du base_rate |

> Les **brackets** ne sont **plus** des `rate_grid_lines` : ils deviennent
> `brackets_json` sur la grille (ou une petite table `rate_grid_brackets`).
> Défauts : Shipper `<50 ×1.10 · 100 ×1.00 · 200 ×0.80 · 300 ×0.80 · 400 ×0.80 ·
> 500 ×0.70 · full 978 ×0.60` ; FF flat ×1.00.

## Formule (inchangée, par ligne-route)
```
nav_days = distance_nm / (8 × 24)
base_rate = opex_daily × nav_days / 978
rate[bracket] = base_rate × bracket.coeff × adjustment_index × coeff_format
```

## Points de code impactés (ordre conseillé)
1. **`app/models/commercial.py`** — refonte `RateGrid` (en-tête + `brackets_json`)
   et `RateGridLine` (route). Adapter les relations.
2. **`migrations/versions/20260618_0054_*.py`** — drop `rate_grid_lines`,
   recreate au nouveau schéma + add/drop colonnes `rate_grids`. (Pas de data à
   migrer.)
3. **`app/services/quoting.py`** :
   - `resolve_grid(...)` → matcher la **ligne-route** POL/POD dans la grille
     applicable (client/période), fallback grille défaut.
   - `compute_grid_quote(...)` → `base_rate` issu de la **ligne-route**,
     brackets lus dans `brackets_json` de la grille.
   - `_default_base_rate` / `ensure_default_grid` / `backfill_default_grids` →
     créer des lignes-routes au lieu de grilles mono-route.
4. **`app/routers/commercial_router.py`** :
   - create/edit **en-tête** de grille (sans pol/pod) ;
   - **CRUD lignes-routes** : `POST /grids/{id}/routes`, `…/routes/{rid}/edit`,
     `…/routes/{rid}/delete`, `…/routes/{rid}/recalculate` (OPEX) ;
   - recalcul global déjà en place → l'étendre à toutes les lignes.
5. **Templates** `staff/commercial/grid_form.html` + `grid_detail.html` —
   éditeur de **lignes-routes** (tableau add/edit/delete + recalc) + édition des
   **brackets** au niveau grille.
6. **`app/routers/devis_router.py`** + parcours **booking** — vérifier que la
   résolution de tarif passe par la ligne-route (POL/POD) et non l'en-tête.
7. **Tests** `tests/` — `resolve_grid` multi-routes, `compute_grid_quote` par
   route, recalcul OPEX.

## Critères d'acceptation
- Créer 1 grille Shipper « Client X · 2026 » avec **2 routes** (FRFEC→BRSSO,
  BRSSO→FRFEC), chacune avec sa distance → base_rate OPEX distinct par route.
- Devis 200 palettes sur la route 1 = `base_rate(route1) × 0.80 × adjustment_index`.
- Une seule grille active par client+période (déjà en place) ; recalcul OPEX
  par route ; `volume_commitment` validé (déjà en place).
- Devis public + booking : tarif résolu via la **route** de la grille applicable.

## Risque
Refactor du **cœur tarifaire** : touche devis **et** booking **et** site public.
À faire en **passe dédiée** (contexte frais), pas en fin de session longue.

---

## Prompt de relance (à coller dans une nouvelle session)

```
Contexte : repo mynewtowt (FastAPI/HTMX/Jinja2, Postgres/SQLAlchemy async,
Alembic). Lis d'abord docs/strategy/PLAN_GRILLES_MULTIROUTES.md ET
docs/strategy/NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md §707-765 (Module 6).

Objectif : refondre la grille tarifaire en MULTI-ROUTES (1 grille = 1 client +
1 période + N routes), conformément au Module 6. PAS de reprise de données →
rebuild propre du schéma (migration drop/recreate, pas de compat ascendante).

Travaille sur une NOUVELLE branche `feature/grilles-multiroutes` créée depuis
le dernier origin/main. Commits clairs (feat: …), push sur cette branche.
NE touche PAS à main directement.

Périmètre = le plan PLAN_GRILLES_MULTIROUTES.md (schéma cible + points de code
1→7 + critères d'acceptation). Étapes :
1. Modèles RateGrid (en-tête + brackets_json) / RateGridLine (route).
2. Migration 0054 (drop/recreate rate_grid_lines, colonnes rate_grids).
3. quoting.py : resolve_grid (match route), compute_grid_quote (base_rate de la
   route + brackets de la grille), ensure_default_grid/backfill.
4. commercial_router : en-tête + CRUD lignes-routes (+ recalc OPEX par route).
5. Templates grid_form / grid_detail : éditeur lignes-routes + brackets grille.
6. devis_router + booking : résolution via la route.
7. Tests resolve_grid/compute_grid_quote/recalc.

Déjà acquis sur main (NE PAS défaire) : cycle de vie grille (active unique +
verrou + « repasser en brouillon »), recalcul OPEX, volume_commitment,
surcharge IMDG %, minimum de facturation, coefficients format.

Contraintes : pas de await db.commit() dans les routes (db.flush + dependency),
require_permission sur chaque endpoint, pas de <script> inline (CSP — JS
externe), classes Kairos. Vérifie en fin : AST + parse Jinja + alembic single
head. Bump app/__init__.py __version__ et indique le SHA pour vérif déploiement
(GET /health).
```
