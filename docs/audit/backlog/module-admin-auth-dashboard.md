# Module Admin / Auth / Dashboard staff

Personas : **administrateur système** + **collaborateur staff / manager** (dashboard d'accueil).
Réf V2 : `app/routers/{admin_router,auth_router,dashboard_router}.py`, `app/permissions.py`,
`app/maintenance.py`, `app/security_middleware.py`, `app/templates/{admin/*,auth/login.html,dashboard.html}`.
Cible V3 : `app/routers/{admin_router,staff_auth_router,staff_dashboard_router,modules_router,notifications_router}.py`,
`app/permissions.py`, `app/models/{user,role_permission,...}.py`, `app/templates/staff/{admin/*,login.html,dashboard.html}`.

> Voir aussi `LOT0-securite-integrite.md` : SEC‑01 (rate‑limit login), SEC‑03 (filtrage sidebar).

---

## Lot 1 — P0

### [ADM-01] CRUD Navires
- **Persona :** Administrateur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `admin_router.py` (`/admin/vessels/create`, `/{id}/edit`) + `templates/admin/vessel_form.html`
- **Cible V3 :** `admin_router.py`, `app/templates/staff/admin/vessel_form.html`
- **Objectif :** aucune route/écran V3 → un navire ne peut être créé/modifié que par `scripts/seed_demo.py`. Bloquant pour l'exploitation (nouvelle unité, ajustement capacité/vitesse/élongation/IMO/flag).
- **Critères d'acceptation :** créer/éditer un navire avec tous ses attributs ; audit tracé.
- **Test de non‑régression :** P9 #1.
- **Effort :** M

### [ADM-02] Moteur d'alertes du dashboard
- **Persona :** Collaborateur staff / manager · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `dashboard_router.py` (`compute_alerts`, ~120 l : 6 familles — retard ATA>ETA>24h, ETA dépassée sans ATA, escale non verrouillée, départ <48h sans opérations, conflit de port, commandes non affectées ; tri par sévérité, deep‑links)
- **Cible V3 :** `staff_dashboard_router.py`, `app/templates/staff/dashboard.html`
- **Objectif :** la vue proactive « qu'est‑ce qui ne va pas aujourd'hui ? » a totalement disparu. La réimplémenter.
- **Critères d'acceptation :** 6 familles d'alertes calculées ; cartes cliquables (deep‑link) ; tri danger/warning/info.
- **Test de non‑régression :** P10 #1.
- **Effort :** M

## Lot 2 — P1

### [ADM-03] KPI métier + notifications cargo/compagnie sur le dashboard
- **Persona :** Collaborateur / manager · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `dashboard_router.py` (6 KPI : legs, commandes, escales/mois, **CA prévisionnel**, **CO₂ évité**, **taux remplissage** ; notifications cargo PL soumises + compagnie ATA/ATD ; table prochains départs)
- **Cible V3 :** `staff_dashboard_router.py`, `app/templates/staff/dashboard.html`
- **Objectif :** dashboard appauvri (4 KPI sans CA/CO₂/remplissage ; plus de notifs cargo/compagnie ni de table prochains départs).
- **Test de non‑régression :** P10 #2, #3.
- **Effort :** M

### [ADM-04] Exports / Purges / Cleanups DB
- **Persona :** Administrateur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `admin_router.py` (`/admin/export/{global,selective,files}`, `/admin/database/{purge-selective,reset,stats,cleanup-*}`)
- **Cible V3 :** `admin_router.py` (backlog roadmap #4/#5)
- **Objectif :** sauvegarde/portabilité/RGPD, nettoyage de campagne, hygiène DB (la table `activity_logs` grossit sans purge UI).
- **Critères d'acceptation :** export ZIP CSV (global + sélectif par module + fichiers) ; purge ciblée avec whitelist `ALLOWED_TABLES` + `bindparams()` + double confirmation ; cleanups temporels.
- **Test de non‑régression :** P9 #3.
- **Effort :** L

### [ADM-05] Imports (utilisateurs Excel + planning CSV)
- **Persona :** Administrateur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `admin_router.py` (`/admin/users/import` + template XLSX ; `/admin/import/planning` CSV + auto‑création ports)
- **Cible V3 :** `admin_router.py`
- **Objectif :** onboarding en masse + alimentation initiale du planning impossibles (création 1‑à‑1).
- **Critères d'acceptation :** import Excel users (template + validation ligne‑à‑ligne + rapport) ; import planning CSV (legs + ports + coords).
- **Test de non‑régression :** P9 #2.
- **Effort :** M

### [ADM-06] Décision droits `data_analyst` + réglages émissions/MRV/Pipedrive
- **Persona :** Administrateur / data · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `permissions.py` (`data_analyst` ∈ `ADMIN_ROLES`) ; `admin_router.py` (`/admin/emissions/update`, `/admin/mrv/update`, Pipedrive set+test)
- **Cible V3 :** `permissions.py`, `admin_router.py`
- **Objectif :** `data_analyst` perd l'accès admin en V3 → s'il gérait CO₂/émissions/MRV, rupture. Trancher A7 et réexposer les réglages NOx/SOx + MRV + Pipedrive (set + test token, aujourd'hui en `.env`).
- **Dépend de / Arbitrage :** **A7** ; lié FIN‑03, MRV‑06.
- **Effort :** M

## Lot 3 — P2

### [ADM-07] Réglages & hygiène
- Écran Pipedrive (set + test token) ; lock/unlock escales en masse ; activity‑log filtre user +
  pagination ; table prochains départs ; auditer le bypass admin du middleware maintenance +
  message de maintenance personnalisé ; **discoverabilité** : exposer en sidebar `/admin/co2`,
  `/opex`, `/insurance`, `/maintenance`, `/permissions`, `/activity-logs` ; DB stats ; self‑service langue.
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
MFA TOTP staff (codes de récupération, reset admin, device de confiance) · alertes email
nouvel appareil · tableau de bord sécurité · **éditeur de matrice de permissions** (overrides
DB, cache, fail‑closed) · CO₂ versionné · référentiel d'arrimage · feature flags · session
role‑aware · `assigned_vessel_id` · admin éclaté en pages dédiées · CSP‑strict.
