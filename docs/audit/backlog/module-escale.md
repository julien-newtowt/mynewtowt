# Module Escale / Port call

Persona : **agent d'escale / agent portuaire**.
Réf V2 : `app/routers/escale_router.py`, `app/models/operation.py`, `app/templates/escale/*`.
Cible V3 : `app/routers/escale_router.py`, `app/models/escale.py`, `app/templates/staff/escale/*`,
`app/templates/pdf/sof_escale.html`.

> ⚠️ Nettoyer le **code mort** `app/templates/staff/escale/detail.html` (références à des routes/variables inexistantes).

---

## Lot 1 — P0

### [ESC-01] Édition + suppression des opérations et des shifts dockers
- **Persona :** Agent d'escale · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `escale_router.py` (`/operations/{id}/edit`, `DELETE /operations/{id}`, `/dockers/{id}/edit`, `DELETE /dockers/{id}`)
- **Cible V3 :** `escale_router.py`, `app/templates/staff/escale/index.html`
- **Objectif :** V3 ne permet que la création (+ start/end `now()`). Toute erreur est irréversible. Restaurer édition + suppression.
- **Critères d'acceptation :** éditer tous les champs d'une opération/d'un shift (hors escale verrouillée) ; supprimer (perm S) ; audit tracé.
- **Test de non‑régression :** P4 #3.
- **Effort :** M

### [ESC-02] Pilotage du statut portuaire + pose ATA/ATD (+ propagation, OPEX, notifications)
- **Persona :** Agent d'escale · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `escale_router.py` (`/legs/{lid}/port-status` : pilote arrivée → à quai → pilote départ ; `propagate_from_leg` ; `update_finance_actual_duration` ; `notify_arrival/departure`)
- **Cible V3 :** `escale_router.py`, `app/services/date_cascade.py`, `app/services/finance_rollup.py`, `app/services/notifications.py`
- **Objectif :** restaurer le cœur du métier d'escale : faire progresser le statut, poser ATA/ATD, propager aux legs aval, recalculer l'OPEX réel, notifier la compagnie. (Vérifier d'abord si une partie est portée par onboard/captain ; sinon rendre le flux accessible à l'agent d'escale.)
- **Critères d'acceptation :** barre de progression de statut avec horodatage + fuseau ; ATA/ATD posés ; cascade visible ; OPEX réel mis à jour ; notifications émises.
- **Test de non‑régression :** P4 #1, #2.
- **Dépend de :** ESC‑07 (fuseau).
- **Effort :** L

### [ESC-03] Saisie manuelle des heures réelles des opérations
- **Persona :** Agent d'escale · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `escale_router.py` (champ `actual_start` éditable, saisie rétroactive)
- **Cible V3 :** `escale_router.py`
- **Objectif :** V3 ne pose que `now()` via Démarrer/Terminer → impossible de saisir a posteriori (cas standard de saisie consolidée en fin d'escale).
- **Critères d'acceptation :** saisie/édition manuelle de `actual_start`/`actual_end` à une heure arbitraire.
- **Test de non‑régression :** P4 #4.
- **Effort :** S

## Lot 2 — P1

### [ESC-04] Réintroduire `intervenant` + durées prévue/réelle
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `operation.py` (`intervenant`, `planned_duration_hours`, `actual_duration_hours`)
- **Cible V3 :** `app/models/escale.py`, formulaires + affichage, migration
- **Objectif :** restaurer le nom/société de l'intervenant (affiché partout en V2) et les durées.
- **Effort :** S

### [ESC-05] Productivité dockers (pal/h + écart %)
- **Persona :** Agent d'escale · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `operation.py` (propriétés `planned_rate`, `actual_rate`, `rate_delta_pct`)
- **Cible V3 :** `app/models/escale.py` (`DockerShift`), `app/templates/staff/escale/index.html`
- **Objectif :** réintroduire l'indicateur clé de cadence docker (V3 n'a qu'une barre cible/réalisé).
- **Test de non‑régression :** P4 #5.
- **Effort :** S

### [ESC-06] Couplage opération ↔ équipage + billetterie + auto‑PAF Fécamp
- **Persona :** Agent d'escale · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `escale_router.py` (sélection crew embarquement/débarquement → `CrewAssignment` ; `/tickets/*` + alertes dates + auto‑op PAF)
- **Cible V3 :** `escale_router.py`, lien vers `crew_router`
- **Objectif :** l'embarquement/débarquement saisi à l'escale ne crée plus l'affectation équipage ; la billetterie et l'auto‑PAF Fécamp ont disparu.
- **Critères d'acceptation :** sélection crew → `CrewAssignment` créée ; vue billetterie (lecture seule) + alertes incompatibilité dates ; auto‑opération PAF si règle valide.
- **Dépend de :** CREW‑06 (API équipage/navire).
- **Effort :** M

### [ESC-07] Multi‑timezone + bornes à quai sur les datetimes
- **Persona :** Agent d'escale · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `includes/time_input.html` (tz‑select UTC/Paris/Port local)
- **Cible V3 :** `app/templates/staff/escale/*`, partial `staff/_time_input.html` (cf. UX‑01)
- **Objectif :** V3 utilise des `datetime-local` bruts → régression UX maritime majeure.
- **Dépend de :** UX‑01.
- **Test de non‑régression :** P4 #6.
- **Effort :** S

## Lot 3 — P2

### [ESC-08] Cockpit d'escale (confort)
- Timeline « flux opérationnel » (5 étapes) ; activités parallèles (4 lanes) ; vue 3 catégories
  d'opérations ; métriques performance navigation ; commandes commerciales du leg ; liens
  Packing Lists / impression stowage FR‑EN ; dépendance Type→Action (`ACTIONS_BY_TYPE`).
- **Priorité :** P2 · **Effort :** M (groupé)
