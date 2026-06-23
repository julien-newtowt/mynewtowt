# Module Tracking flotte + Navigation / Performance

Persona : **opérateur de suivi de flotte / fleet ops**.
Réf V2 : `app/routers/tracking_router.py`, `app/models/vessel_position.py`, `app/utils/navigation.py`, `app/templates/dashboard.html`.
Cible V3 : `app/routers/{tracking_router,navigation_router}.py`, `app/models/{claim.py (VesselPosition),weather.py}`,
`app/services/{vessel_position,voyage_track,voyage_events,weather,weather_history,geo}.py`,
`app/templates/staff/{tracking,navigation}/*`.

> Voir `LOT0-securite-integrite.md` : SEC‑04 (unique/index positions), SEC‑05 (anti‑saut), SEC‑07 (endpoints GET).

---

## Lot 1 / Lot 0 — P0 (traités en Lot 0)
- SEC‑04 — `UniqueConstraint(vessel_id, recorded_at)` + index + upsert idempotent.
- SEC‑05 — filtre anti‑saut > 50 NM dans `voyage_track.actual_distance_nm`.
- SEC‑07 — décision sur les 4 endpoints GET supprimés.

## Lot 2 — P1

### [TRK-01] Réintroduire `/api/tracking/latest` (+ contrat de réponse upload)
- **Persona :** Opérateur suivi / intégrations · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `tracking_router.py` (`/latest` ; réponse upload `{vessel, vessel_id, total_points, inserted, skipped_duplicates, date_range}`)
- **Cible V3 :** `tracking_router.py`
- **Objectif :** la carte/intégrations dépendent de `/latest` (404 en V3). Réintroduire l'endpoint ; documenter/versionner les clés JSON de la réponse d'upload (renommées en V3 : `skipped`, `rows_detected`, `file`).
- **Critères d'acceptation :** `/latest` renvoie la dernière position par navire ; contrat upload documenté (et 401→403 sur token invalide documenté).
- **Test de non‑régression :** P11 #1.
- **Effort :** M

### [TRK-02] Vue KPI navigation agrégée par année
- **Persona :** Opérateur suivi / data · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `tracking_router.py` (`/navigation-kpis` : par leg = point_count, avg/max SOG, distances ; tous legs à GPS d'une année)
- **Cible V3 :** `navigation_router.py` (sélection leg par leg, pas d'agrégat année)
- **Objectif :** restaurer la vue « tous les legs à GPS de l'année » pour le suivi de performance de flotte.
- **Critères d'acceptation :** tableau agrégé annuel (avg SOG, point_count, distances) par leg.
- **Test de non‑régression :** P11 #3.
- **Effort :** M

### [TRK-03] Afficher `avg_speed_kn` et `real_elongation`
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `tracking_router.py` (`/leg/track` : avg/max SOG, `real_elongation` = ratio GPS/ortho)
- **Cible V3 :** `app/services/voyage_track.py` (`compute_metrics.avg_speed_kn` calculé mais non rendu), `app/templates/staff/navigation/index.html`
- **Objectif :** le tableau Navigation montre distance/durée/écart mais pas la vitesse moyenne par leg ni le ratio d'allongement.
- **Critères d'acceptation :** colonnes avg SOG + real_elongation affichées.
- **Test de non‑régression :** P11 #4.
- **Effort :** S

### [TRK-04] Codage couleur de statut (à quai / en mer) sur les marqueurs live
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `dashboard.html` (marqueurs colorés par SOG : à quai/lent/navigation)
- **Cible V3 :** `app/templates/staff/tracking/index.html`, `static/js/fleet-map.js`
- **Objectif :** sur `/tracking`, les marqueurs portent le code navire mais plus le statut par couleur.
- **Test de non‑régression :** P11 #1.
- **Effort :** S

### [TRK-05] Réintroduire `import_batch` (+ `created_at`) sur la position
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `vessel_position.py` (`import_batch` = nom du fichier source, `created_at`)
- **Cible V3 :** `app/models/claim.py` (`VesselPosition`), migration
- **Objectif :** traçabilité/audit/purge par lot d'import (cohérent avec le backlog « purges DB ciblées »).
- **Effort :** S

## Lot 3 — P2
- Aligner/versionner les clés JSON de la réponse d'upload si des flux PA en dépendent ;
  documenter le 401→403.

---

## Compatibilité Power Automate (note)
L'**ingestion** (écriture) est **rétro‑compatible** et enrichie (CSV/ZIP/XLSX, délimiteur auto,
colonnes tolérantes, `TRACKING_VESSEL_MAP`) — pas de régression sur le format satcom V2.
La **lecture** (réponse JSON + 4 endpoints GET) est **rompue** → tout flux PA qui en dépend
doit être réécrit (cf. SEC‑07, TRK‑01).

## Gains V3 à préserver
Ingestion multi‑format robuste · page `/tracking` (live + historique filtrable navire×leg×période) ·
module Navigation/Performance (carte multi‑legs, route théorique, tableau comparatif) ·
météo historisée (`vessel_weather`, cron 30 min) · MapLibre + charte Kairos.
