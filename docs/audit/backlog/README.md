# Backlog de reprise V2 → V3 — Index

Ce dossier décompose le [rapport d'audit](../AUDIT_V2_V3_RAPPORT_ECARTS_ET_PLAN.md) en
**tickets actionnables**. Objectif : « reprendre à minima l'existant de la V2 » sur la partie
staff, sans casser les gains de la V3.

## Organisation

| Fichier | Contenu |
|---|---|
| [`LOT0-securite-integrite.md`](LOT0-securite-integrite.md) | Correctifs transverses sécurité & intégrité (rapides, prioritaires) |
| [`module-cargo.md`](module-cargo.md) | Cargo / Packing list / BL / Portail expéditeur |
| [`module-escale.md`](module-escale.md) | Escale / Port call |
| [`module-onboard-captain-claims.md`](module-onboard-captain-claims.md) | Onboard / Captain (SOF, docs) + Claims |
| [`module-crew.md`](module-crew.md) | Équipage |
| [`module-commercial.md`](module-commercial.md) | Commercial / Pricing / Offres / Commandes |
| [`module-mrv.md`](module-mrv.md) | MRV (émissions UE) |
| [`module-finance-kpi.md`](module-finance-kpi.md) | Finance + KPI |
| [`module-planning.md`](module-planning.md) | Planning / Partages |
| [`module-stowage.md`](module-stowage.md) | Plan d'arrimage |
| [`module-admin-auth-dashboard.md`](module-admin-auth-dashboard.md) | Admin / Auth / Dashboard staff |
| [`module-tracking-navigation.md`](module-tracking-navigation.md) | Tracking flotte + Navigation |
| [`module-design-ux-i18n.md`](module-design-ux-i18n.md) | Design system / UX / i18n |
| [`LOT4-evolutions.md`](LOT4-evolutions.md) | Consolidation des modules V3‑only + évolutions |
| [`ARBITRAGES.md`](ARBITRAGES.md) | 7 décisions métier bloquantes pour certains tickets |
| [`TESTS-NON-REGRESSION-PERSONAS.md`](TESTS-NON-REGRESSION-PERSONAS.md) | 12 parcours persona en check‑lists d'acceptation |

## Convention de ticket

```
### [ID] Titre court
- **Persona :** qui est débloqué
- **Priorité :** P0 (bloquant) | P1 (majeur) | P2 (confort)
- **Lot :** 0 | 1 | 2 | 3 | 4
- **Réf V2 :** fichier(s) source de l'existant à reprendre
- **Cible V3 :** fichier(s) à créer/modifier
- **Objectif :** ce qu'on restaure (comportement attendu)
- **Critères d'acceptation :** liste vérifiable
- **Test de non-régression :** scénario persona à rejouer
- **Migration de données :** le cas échéant
- **Dépend de / Arbitrage :** ticket(s) ou décision métier préalable
- **Effort :** S (≤1j) | M (2‑4j) | L (>4j)
```

## Numérotation par module

| Préfixe | Module | Préfixe | Module |
|---|---|---|---|
| `SEC` | Lot 0 sécurité/intégrité | `MRV` | MRV |
| `CARGO` | Cargo/Packing/Portail | `FIN` | Finance/KPI |
| `ESC` | Escale | `PLN` | Planning |
| `ONB` | Onboard/Captain/Claims | `STO` | Stowage |
| `CREW` | Équipage | `ADM` | Admin/Auth/Dashboard |
| `COM` | Commercial/Pricing | `TRK` | Tracking/Navigation |
| `UX` | Design/UX/i18n | `EVO` | Évolutions (Lot 4) |

## Definition of Done (commune à tous les tickets)

1. Comportement V2 restauré **ou** alternative validée par le métier (cf. `ARBITRAGES.md`).
2. Écran **réécrit en charte Kairos / Manrope**, **zéro `<script>` inline** (CSP‑strict), composants `kairos.css`.
3. Route protégée par `Depends(require_permission(module, "C"|"M"|"S"))`.
4. Mutations : `validate → modify → await db.flush() → RedirectResponse(303)` (jamais `db.commit()`), header `HX-Redirect` si HTMX.
5. Write actions tracées via `services.activity.record()`.
6. SQL dynamique : whitelist + `bindparams()` (jamais de f‑string sur table/colonne).
7. i18n : clés ajoutées aux 5 catalogues (fr/en/es/pt‑br/vi).
8. Migration Alembic fournie si schéma modifié.
9. Test de non‑régression persona vert (cf. `TESTS-NON-REGRESSION-PERSONAS.md`).
10. `pytest -q` au vert.

## Séquencement recommandé

```
Lot 0 (SEC-*)          en parallèle, immédiat
Lot 1 (P0 cœur métier) ① Cargo + Escale  ② Crew + MRV  ③ Commercial + Onboard  ④ Finance/Planning/Stowage/Admin
Lot 2 (P1)             après stabilisation Lot 1 de chaque module
Lot 3 (P2)             itératif
Lot 4 (EVO-*)          consolidation modules V3-only
```

> ⚠️ Plusieurs tickets P0 dépendent d'une **décision métier** (`ARBITRAGES.md`). Les trancher
> avant de démarrer le Lot 1 du module concerné (notamment MRV, Finance, Cargo/portail, Crew).
