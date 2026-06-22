# Module Planning / Partages

Persona : **planificateur d'armement / chef d'exploitation**.
Réf V2 : `app/routers/{planning_router,planning_ext_router}.py`, `app/models/{planning_share,leg}.py`,
`app/templates/planning/*`.
Cible V3 : `app/routers/{planning_router,scenario_router}.py`, `app/models/{planning_share,planning_scenario,leg}.py`,
`app/services/{planning,date_cascade,leg_filter}.py`, `app/templates/staff/planning/*`, `app/templates/public/*`.

---

## Lot 1 — P0

### [PLN-01] Brochure commerciale imprimable (PDF)
- **Persona :** Planificateur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `planning_router.py` (`/planning/pdf/commercial`), `templates/planning/pdf_commercial.html` (FR/EN, filtres navire/origine/destination, vues chrono/route/destination, summary box, sélection leg‑à‑leg)
- **Cible V3 :** `planning_router.py`, `app/templates/pdf/planning_commercial.html` (WeasyPrint)
- **Objectif :** fonction centrale de diffusion commerciale — disparue (seule la vue publique par token subsiste).
- **Critères d'acceptation :** génération PDF avec filtres, vues groupées (chrono/route/destination), FR/EN, summary box, sélection d'un sous‑ensemble de legs.
- **Test de non‑régression :** P1 #3.
- **Effort :** M

### [PLN-02] Saisie ATD/ATA + statut sur le leg
- **Persona :** Planificateur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `planning_router.py` (formulaire édition : section « Réalisé » ATD/ATA + `<select status>`)
- **Cible V3 :** `planning_router.py`, `app/templates/staff/planning/*`
- **Objectif :** le wizard V3 ne saisit ni ATD/ATA ni statut manuel. Soit réintroduire ces champs (form/détail leg), soit exposer explicitement le flux délégué à captain/escale.
- **Critères d'acceptation :** saisie ATD/ATA + statut accessible au planificateur ; cohérent avec ESC‑02/ONB.
- **Test de non‑régression :** P1 #2.
- **Effort :** M

## Lot 2 — P1

### [PLN-03] Export CSV du planning réel
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `planning_router.py` (`/planning/export/csv`, 15 colonnes)
- **Cible V3 :** `planning_router.py`
- **Objectif :** seul l'export *scénario* existe ; restaurer l'export du planning réel (filtré navire/année).
- **Test de non‑régression :** P1 #4.
- **Note :** recalculer `computed_distance`/`estimated_duration_hours` (supprimés du modèle V3) à la volée.
- **Effort :** S

### [PLN-04] Fiche destinataire + historique des partages
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `planning_ext_router.py`/`templates/planning/share_history.html` (`recipient_name/company/email/notes`, `legs_ids`, `lang`)
- **Cible V3 :** `app/models/planning_share.py`, `planning_router.py`, migration
- **Objectif :** `PlanningShare` V3 a perdu le suivi destinataire, la sélection leg‑à‑leg et la langue. Restaurer.
- **Critères d'acceptation :** champs destinataire au form + tableau d'historique (qui a reçu quoi, créé par) ; `legs_ids` ; `lang` (corrige aussi le partage public EN cassé).
- **Test de non‑régression :** P1 #5.
- **Effort :** M

### [PLN-05] Détection de retard (≥ 4 h vs référence)
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `planning_router.py` (`notify_delay` : comparaison ETA/ETD vs `eta_ref`/`etd_ref`)
- **Cible V3 :** `planning_router.py` (les champs `eta_ref`/`etd_ref` existent mais sont morts)
- **Objectif :** réactiver l'alerte automatique de dérive planning + notification.
- **Test de non‑régression :** P1 #6.
- **Effort :** S

### [PLN-06] Vue « par port »
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `planning_router.py` (`/planning/ports` : recherche d'un port, toutes ses escales)
- **Cible V3 :** `planning_router.py` (`/planning/conflicts` ne montre que les conflits)
- **Objectif :** permettre d'interroger « quelles escales sur FRFEC ? » au‑delà des seuls conflits.
- **Effort :** S

## Lot 3 — P2

### [PLN-07] Confort
- Vue carte des routes sur l'écran planning (MapLibre, polylignes POL→POD, légende) ;
  raccourcis ports pilotés par `Port.is_shortcut` (au lieu des LOCODE codés en dur) ;
  toggle Tableau/Gantt/Carte ; vérifier la politique de droits (V2 réservait l'écriture à admin/manager).
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
Scénarios what‑if (drag‑drop Gantt, comparaison au réel) · validation d'intégrité renforcée ·
conflits serveur sur intervalles · cascade élargie (escale/dockers/notif clients) · jours
ouvrés portuaires · création depuis carte · partage avec période/expiration/compteur.
