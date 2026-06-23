# Module Stowage / Plan d'arrimage

Persona : **responsable arrimage / second capitaine**.
Réf V2 : `app/routers/stowage_router.py`, `app/models/stowage.py`, `app/templates/stowage/{plan,onboard,print,vessel_svg}.html`.
Cible V3 : `app/routers/stowage_router.py`, `app/models/stowage.py`, `app/services/{stowage,stowage_specs}.py`,
`app/templates/staff/stowage/*`, `app/templates/pdf/stowage_plan.html`.

---

## Lot 1 — P0

### [STO-01] Vue « à bord » du plan de chargement
- **Persona :** Second capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `stowage_router.py` (`/stowage/onboard/{leg_id}`, perm `captain:C`)
- **Cible V3 :** `stowage_router.py` (+ lien depuis `/onboard`), perm captain
- **Objectif :** la vue à bord dédiée a disparu ; l'accès passe par `/stowage/legs` (perm `cargo`). Recréer une vue onboard (ou rebrancher avec permission captain).
- **Critères d'acceptation :** écran consultable à bord, permission `captain`.
- **Effort :** M

### [STO-02] Réaffectation de zone (drag‑drop ou changement de zone)
- **Persona :** Second capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `stowage_router.py` (`/move/{plan_id}` + JS AJAX + feedback)
- **Cible V3 :** `stowage_router.py`, `app/templates/staff/stowage/plan.html`
- **Objectif :** impossible de déplacer une palette d'une zone à l'autre (il faut supprimer/recréer). Restaurer le déplacement (drag‑drop ou changement de zone par item, JS externe CSP‑safe).
- **Effort :** M

### [STO-03] Édition + suppression d'une affectation (item)
- **Persona :** Second capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `stowage_router.py` (`/unassign/{plan_id}`)
- **Cible V3 :** `stowage_router.py` (seul « Suggérer auto » réécrase tout)
- **Objectif :** corriger/retirer une affectation sans tout réécraser.
- **Critères d'acceptation :** `POST /plans/{id}/items/{item_id}/delete` + édition de zone/quantité.
- **Effort :** S

### [STO-04] Liste des batches non assignés + affectation directe
- **Persona :** Second capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `stowage_router.py` (`/assign/{batch_id}`) + section « Cargaisons non assignées »
- **Cible V3 :** `stowage_router.py`, `app/templates/staff/stowage/plan.html`
- **Objectif :** plus de vue « reste à arrimer » ni d'affectation d'un batch identifié à une zone (le form V3 générique impose la ressaisie des dims déjà connues).
- **Critères d'acceptation :** panneau des batches du leg non encore placés + bouton « affecter à zone X » sans ressaisie.
- **Effort :** M

## Lot 2 — P1

### [STO-05] Politique de blocage capacité/poids *(selon A3)*
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `stowage_router.py` (rejet HTTP 400 si capacité/poids dépassés)
- **Cible V3 :** `app/services/stowage.py` (`evaluate_plan` : avertir, jamais bloquer)
- **Objectif :** restaurer un blocage dur paramétrable sur les zones critiques (DG, résistance pont).
- **Dépend de / Arbitrage :** **A3**.
- **Effort :** M

### [STO-06] Bilinguisme FR/EN (plan + PDF + labels zones)
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `vessel_svg.html` + print bilingue, labels EN par zone
- **Cible V3 :** `app/templates/staff/stowage/*`, `app/templates/pdf/stowage_plan.html`
- **Objectif :** plan de chargement non communicable en EN (équipage/port étranger).
- **Effort :** M

### [STO-07] Capacités réelles par zone × format × gerbage
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `app/models/stowage.py` (`ZONE_CAPACITIES` issues de `easy_chargement_navire_complet.xlsx`)
- **Cible V3 :** `app/models/stowage.py` (`StowageZoneSpec` : capacité EPAL unique + coefficient)
- **Objectif :** capacité moins fidèle (gabarit gerbé/simple par format perdu). Réimporter la matrice xlsx dans `StowageZoneSpec` (ou table dédiée).
- **Effort :** M

### [STO-08] Formats BARRIQUE120/140 au select + select IMO
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `PALLET_FORMATS` (7 formats), `IMO_CLASSES` (24 classes bilingues)
- **Cible V3 :** `app/templates/staff/stowage/plan.html` (form)
- **Objectif :** BARRIQUE120/140 absents du select (coeffs présents) ; `imdg_class` en texte libre. Réutiliser les référentiels.
- **Effort :** S

### [STO-09] Arrimage avant cargo doc (fallback order→item)
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `stowage_router.py` (`_ensure_batches_for_orders`)
- **Cible V3 :** `app/services/stowage.py` (`suggest` n'aspire que les PL existantes)
- **Objectif :** permettre d'arrimer avant la création des docs cargo (placeholder order→item), ou documenter le prérequis PL.
- **Effort :** S

## Lot 3 — P2

### [STO-10] Visualisation & confort
- Vraie vue SVG **top‑down par pont** (backlog CLAUDE.md #3, non livré) ; coupe latérale profil ;
  API JSON occupation zones (selon consommateurs).
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
Référentiel `StowageZoneSpec` éditable par classe (admin) · workflow de statut du plan ·
moteur d'avertissements · item enrichi (HS/IMDG/UN/cubage/gerbé) · repérage visuel (locate
batch/order) réutilisé · PDF WeasyPrint · permissions cohérentes (`cargo`).
