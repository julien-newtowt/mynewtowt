# Module MRV (émissions UE) — réglementaire

Persona : **data analyst / responsable MRV‑RSE**.
Réf V2 : `app/routers/mrv_router.py`, `app/models/{mrv,emission_parameter}.py`, `app/templates/mrv/{index,leg_detail}.html`.
Cible V3 : `app/routers/mrv_router.py`, `app/models/mrv.py`,
`app/services/{mrv_export,mrv_sync,carbon,co2}.py`, `app/templates/staff/mrv/*`.

> ⚠️ Module à enjeu **réglementaire** (DNV Veracity, EU MRV). Trancher **A1** (source de
> vérité compteurs DO vs noon reports) avant de démarrer MRV‑04/05/06.

---

## Lot 1 — P0

### [MRV-01] Export DNV CSV 18 colonnes (+ correctif IMO)
- **Persona :** Data analyst · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `mrv_router.py` (`/mrv/export/dnv-csv`, 18 colonnes Veracity : IMO, Date/Time UTC, Voyage From/To, Event, Time since prev, Distance, Cargo, ME/AE conso, MDO ROB, Lat/Lon DMS, nom de fichier daté/navire)
- **Cible V3 :** `app/services/mrv_export.py` (`to_dnv_csv`, 9 colonnes) + bug `vessel_imo` toujours vide
- **Objectif :** le format V3 (9 colonnes, IMO vide) est incompatible avec l'ingestion DNV Veracity. Restaurer le format 18 colonnes exact et corriger l'IMO.
- **Critères d'acceptation :** 18 colonnes dans l'ordre Veracity ; IMO renseigné via le navire du leg ; nom de fichier daté + navire.
- **Test de non‑régression :** P7 #2.
- **Effort :** M

### [MRV-02] Carbon Report PDF + blocage qualité
- **Persona :** Data analyst · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `mrv_router.py` (`/mrv/export/carbon-report`, ReportLab paysage : résumé + table d'events ; HTTP 400 si ≥1 event en erreur)
- **Cible V3 :** `mrv_router.py`, `app/templates/pdf/carbon_report.html` (WeasyPrint)
- **Objectif :** V3 a remplacé le PDF par un `.txt` de 4 lignes + une page HTML par leg, sans blocage qualité. Restaurer un livrable PDF présentable et le garde‑fou.
- **Critères d'acceptation :** PDF (résumé ME/AE/total/CO₂/facteur/densité + table d'events) ; filtre navire/année ; génération bloquée si des events sont en erreur qualité.
- **Test de non‑régression :** P7 #3.
- **Effort :** M

### [MRV-03] Édition + suppression d'un event MRV
- **Persona :** Data analyst · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `mrv_router.py` (`/mrv/events/{id}/edit`, `DELETE /mrv/events/{id}`, recalcul auto)
- **Cible V3 :** `mrv_router.py`
- **Objectif :** aucune route edit/delete en V3 → données non amendables.
- **Critères d'acceptation :** éditer/supprimer un event ; recalcul des dérivés en chaîne.
- **Test de non‑régression :** P7 #1.
- **Effort :** M

## Lot 2 — P1

### [MRV-04] Source de vérité : compteurs DO + calcul conso/ROB *(selon A1)*
- **Persona :** Data analyst · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `mrv_router.py` (`compute_consumption`, `compute_rob`) + `app/models/mrv.py` (4 compteurs port/stbd ME, fwd/aft gen)
- **Cible V3 :** `app/models/mrv.py`, `mrv_router.py`, migration
- **Objectif :** selon A1, réintroduire les 4 compteurs DO + calcul ME/AE + ROB calculé (la donnée d'entrée primaire machine a disparu).
- **Dépend de / Arbitrage :** **A1**.
- **Test de non‑régression :** P7 #1.
- **Effort :** L

### [MRV-05] Contrôle qualité multi‑règles, appliqué à tous les events
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `mrv_router.py` (`validate_quality` : compteurs monotones, ROB déclaré/calculé, cargo transit ; statut ok/warning/error)
- **Cible V3 :** `app/services/mrv_sync.py` (1 seule règle ROB, warning max, events auto seulement)
- **Objectif :** restaurer les règles + statut `error` bloquant, appliqué aussi aux saisies manuelles.
- **Dépend de :** MRV‑04 (selon A1).
- **Effort :** M

### [MRV-06] UI d'édition des paramètres MRV
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `mrv_router.py` (`/mrv/params/save` : densité MDO, déviation admissible, facteur CO₂)
- **Cible V3 :** `mrv_router.py` ou `/admin`, `app/models/mrv.py` (`MRVParameter` lu seulement)
- **Objectif :** plus aucune UI pour régler densité MDO et seuil de déviation (codé en dur `2.0`).
- **Test de non‑régression :** P7 #4.
- **Effort :** S

### [MRV-07] Position DMS + auto‑remplissage GPS
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `mrv_router.py` (`coords_from_port`, `nearest_gps_position` ±6 h ; champs lat/lon deg/min/NS‑EW)
- **Cible V3 :** `app/models/mrv.py`, `mrv_router.py`, lien `vessel_positions`
- **Objectif :** coordonnées exigées par DNV — supprimées. Réintroduire saisie + auto‑remplissage (port pour départ/arrivée, sinon GPS le plus proche).
- **Test de non‑régression :** P7 #5.
- **Dépend de :** MRV‑01 (colonnes DMS de l'export).
- **Effort :** M

## Lot 3 — P2

### [MRV-08] Vues & confort
- Vue détail leg (table d'events ligne‑à‑ligne + badges qualité) ; agrégat conso/CO₂ par leg
  sur le dashboard ; suggestions SOF→MRV cliquables (ou doc full‑auto) ; bunkering date + cargo MRV.
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
Sync auto noon/SOF → MRVEvent (idempotente via `noon_report_id`/`sof_event_id`) · Carbon Report
par leg (intensités /NM, /t, /t·nm + CO₂ évité) · facteur CO₂ versionné (`/admin/co2`) ·
modèles XLSX officiels TOWT · densité MDO paramétrable.
