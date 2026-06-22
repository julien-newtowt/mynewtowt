# Module Commercial / Pricing / Offres / Commandes

Persona : **responsable commercial / ship‑broker**.
Réf V2 : `app/routers/{commercial_router,pricing_router}.py`, `app/models/{commercial,order}.py`,
`app/utils/pipedrive.py`, `app/templates/commercial/*`.
Cible V3 : `app/routers/{commercial_router,devis_router}.py`, `app/models/{commercial,quote}.py`,
`app/services/{commercial,quoting,pricing,pipedrive_sync}.py`, `app/templates/staff/commercial/*`.

> V3 a profondément repivoté le commercial vers un modèle « devis/grille/options » propre et
> orienté client public (gain à préserver). Le travail consiste à **rebrancher le pipeline
> commande↔leg↔grille** sur le nouveau modèle, sans casser les acquis devis/options.

---

## Lot 1 — P0

### [COM-01] Écran d'affectation commande → leg
- **Persona :** Broker · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `commercial_router.py` (`/orders/{id}/assign` : legs filtrés par route, suggestion auto, badge « hors délai » si ETA > date livraison)
- **Cible V3 :** `commercial_router.py`, `app/models/commercial.py` (`OrderAssignment` existe mais n'est jamais écrit)
- **Objectif :** restaurer l'écran d'affectation/réaffectation — cœur du métier broker.
- **Critères d'acceptation :** liste des legs compatibles (filtrés par route souhaitée) ; suggestion ; alerte délai ; écriture de `OrderAssignment`.
- **Test de non‑régression :** P2 #3.
- **Dépend de :** COM‑02 (champs route/dates de la commande).
- **Effort :** L

### [COM-02] Réintroduire les champs riches de la commande (+ lien grille)
- **Persona :** Broker · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `app/models/order.py` (`palette_format`, `weight_per_palette`, `thc_included`, `booking_fee`, `documentation_fee`, `delivery_date_start/end`, `departure/arrival_locode`, `rate_grid_id`, `rate_grid_line_id`)
- **Cible V3 :** `app/models/commercial.py` (`Order`), `app/templates/staff/commercial/order_form.html`, migration
- **Objectif :** la commande V3 a perdu format/poids/THC/frais/dates/route et le **lien vers la grille** (impossible de tracer la grille appliquée ni de voir « commandes liées » sur la fiche grille).
- **Critères d'acceptation :** champs présents au modèle + form ; `rate_grid_id`/`rate_grid_line_id` renseignés à la création/conversion ; fiche grille affiche les commandes liées.
- **Migration :** ajout de colonnes.
- **Test de non‑régression :** P2 #2, #6.
- **Effort :** M

### [COM-03] Édition (et désactivation) d'un client
- **Persona :** Broker · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `pricing_router.py` (`/clients/{cid}/edit`)
- **Cible V3 :** `commercial_router.py`, `app/templates/staff/commercial/*`
- **Objectif :** V3 ne permet que la création → impossible de corriger un client (contact, TVA, adresse) sans SQL. (Le champ `address` existe au modèle mais n'est même pas au formulaire de création.)
- **Critères d'acceptation :** éditer tous les champs client (dont `address`) ; désactiver.
- **Test de non‑régression :** P2 #1.
- **Effort :** S

### [COM-04] Pièces jointes sur la commande
- **Persona :** Broker · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `commercial_router.py` (`/orders/{id}/upload`, `/attachment`, delete ; `attachment_filename/path`)
- **Cible V3 :** `commercial_router.py`, `safe_files.py`, migration
- **Objectif :** rattacher le bon de commande / contrat signé à la commande.
- **Critères d'acceptation :** upload validé, download, delete.
- **Test de non‑régression :** P2 #4.
- **Effort :** S

## Lot 2 — P1

### [COM-05] Écran de conversion offre → commande éditable
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `pricing_router.py` (`/offers/{oid}/create-order` : sélection route, calcul prix live par tranche, frais grille)
- **Cible V3 :** `commercial_router.py` (`/offers/{id}/convert` est un 1‑clic)
- **Objectif :** restaurer le contrôle fin à la conversion (route, qty, format, prix recalculé).
- **Test de non‑régression :** P2 #5.
- **Effort :** M

### [COM-06] Push Pipedrive Deal sur offre/commande
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `pricing_router.py`/`commercial_router.py` (`create_deal` sur ordre et offre)
- **Cible V3 :** `app/services/pipedrive_sync.py`, `commercial_router.py`
- **Objectif :** `pipedrive_deal_id` est propagé mais aucune route ne crée de Deal (seul l'import org en masse subsiste).
- **Critères d'acceptation :** Deal créé à l'envoi d'une offre et à la confirmation d'une commande.
- **Effort :** M

### [COM-07] Lookup tarif grille dans le formulaire commande
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `pricing_router.py` (`/api/rate-lookup` HTMX → prix unitaire + total + grille)
- **Cible V3 :** `commercial_router.py`, `app/services/quoting.py` (`compute_grid_quote`)
- **Objectif :** le broker saisit le prix à la main au lieu de le tirer de la grille active.
- **Test de non‑régression :** P2 #6.
- **Effort :** S

### [COM-08] Dashboard performance / conversion par grille + CA
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `commercial_router.py` (KPI par grille : offres émises/acceptées, ordres, CA, taux de conversion)
- **Cible V3 :** `commercial_router.py`, `app/templates/staff/commercial/index.html`
- **Objectif :** restaurer la visibilité commerciale (performance/conversion par grille, CA total).
- **Effort :** M

### [COM-11] Ventilation multi‑legs d'une commande (CA + capacité + PL)
- **Priorité :** P1 · **Lot :** 2
- **Contexte :** COM‑01 a été livré en **simple‑leg** (parité V2 : une commande ⇄ un
  leg, `palettes_count = booked_palettes`, réaffectation = remplacement). Le modèle
  `OrderAssignment` supporte plusieurs affectations par commande, mais l'activer
  réellement exige de rendre cohérents trois consommateurs aujourd'hui scalaires :
  1. **Finance** (`finance_rollup`) attribue `order.total_eur` au seul `order.leg_id`
     → répartir le CA au prorata des palettes par affectation ;
  2. **Capacité** lit déjà les `order_assignments` ventilés, mais `booked_palettes`
     doit être réconcilié avec la somme des `palettes_count` (garde anti‑sur‑réservation) ;
  3. **Packing list / stowage** résolvent le voyage via `order.leg_id` → introduire un
     leg par affectation (PL/BL stables même après réaffectation partielle).
- **Critères d'acceptation :** une commande ventilée 40/40 sur 2 legs facture 50/50 le CA,
  réserve 40+40 en capacité, et chaque PL/BL reste rattachée à son leg d'origine.
- **Effort :** L

### [COM-09] Auto‑création packing list + notifications à la confirmation de commande
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `commercial_router.py` (`_on_order_confirmed` : PL + batch pré‑rempli + notify opérations)
- **Cible V3 :** `commercial_router.py` (`/orders/{id}/confirm`)
- **Objectif :** brancher la création PL + notification (aujourd'hui étape manuelle, risque d'oubli).
- **Dépend de :** CARGO‑08.
- **Effort :** S

## Lot 3 — P2

### [COM-10] Confort & granularité
- Recherche Pipedrive ciblée dans le form client (en complément du sync) ; import multi‑routes
  depuis le planning dans la grille client ; override tarif par bracket (ou doc explicite) ;
  filtres listes grilles/offres ; statuts intermédiaires commande (loaded/delivered) pilotables.
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
Outil de devis public (PDF, rate‑limit, honeypot, leads) + ajustement staff · surcharge IMDG,
minimum de facturation, options tarifaires · sync Pipedrive en masse · recherche client texte +
fiche client · adresses BL structurées · verrouillage de grille active · dashboard remplissage des legs.
