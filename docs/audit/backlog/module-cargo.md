# Module Cargo / Packing list / Bill of Lading / Portail expéditeur

Personas : **agent cargo/logistique** (staff) + **expéditeur/shipper** (portail token).
Module le plus régressé (18/36 routes V2 disparues, ~40 colonnes `PackingListBatch` supprimées).
Réf V2 : `app/routers/cargo_router.py`, `app/models/{packing_list,portal_message,portal_access_log}.py`,
`app/utils/portal_security.py`, `app/templates/cargo/*`.
Cible V3 : `app/routers/{cargo_router,cargo_packing_router,cargo_portal_router}.py`,
`app/services/{packing_list,documents,messaging,safe_files}.py`, `app/models/{packing_list,booking}.py`,
`app/templates/{staff/cargo,portal,pdf}/*`.

---

## Lot 1 — P0

### [CARGO-01] Reconnecter le Bill of Lading à la packing list
- **Persona :** Agent cargo · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `cargo_router.py` (`/cargo/{id}/bol/{batch}`, n° `TUAW_{voyage}_{seq:03d}`, anti‑doublon par leg, 3 OBL)
- **Cible V3 :** `cargo_router.py` (génération BL), `app/templates/pdf/bill_of_lading.html`, `app/models/packing_list.py`
- **Objectif :** le BL PDF V3 lit `booking.items` et ignore les `PackingListBatch` saisis par l'expéditeur. Brancher la génération du BL sur les batches de la packing list.
- **Critères d'acceptation :** un BL généré reprend les données du `PackingListBatch` (parties, marchandise, poids, dims) ; numérotation persistante `TUAW_{voyage}_{seq:03d}` ; anti‑doublon par leg ; 3 OBL ; un BL re‑généré conserve son numéro.
- **Test de non‑régression :** P3a #4.
- **Dépend de :** CARGO‑02 (adresses).
- **Effort :** L

### [CARGO-02] Réintroduire les adresses structurées shipper/notify/consignee
- **Persona :** Agent cargo + Expéditeur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `app/models/packing_list.py` (15 champs : `{shipper,notify,consignee}_{name,address,postal,city,country}`)
- **Cible V3 :** `app/models/packing_list.py` (`PackingListBatch`), formulaires staff + portail, migration Alembic
- **Objectif :** restaurer les mentions **obligatoires** du connaissement, supprimées du batch V3.
- **Critères d'acceptation :** les 15 champs présents au modèle, saisissables côté staff et portail, repris au BL/Arrival Notice ; complétude documentaire recalculée.
- **Migration :** ajouter les colonnes (nullable) ; pas de reprise de données V2 nécessaire si base neuve.
- **Test de non‑régression :** P3a #3, P3b #1.
- **Effort :** M

### [CARGO-03] Édition + suppression de batch (staff & portail) avec audit
- **Persona :** Agent cargo + Expéditeur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `cargo_router.py` (`/cargo/{id}/edit` audit field‑by‑field, `/p/{token}/batch/{id}` save/delete), `PackingListAudit`
- **Cible V3 :** `cargo_packing_router.py`, `cargo_portal_router.py`, `app/services/packing_list.py`
- **Objectif :** V3 ne permet que l'**ajout** d'un batch. Restaurer l'édition et la suppression, en traçant chaque champ modifié.
- **Critères d'acceptation :** éditer/supprimer un batch tant que la PL n'est pas verrouillée ; chaque modification écrit une entrée `PackingListAudit` (champ, ancienne→nouvelle valeur, auteur, date).
- **Test de non‑régression :** P3a #2, P3b #1.
- **Effort :** M

### [CARGO-04] Vue audit / historique de la packing list
- **Persona :** Agent cargo · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `cargo_router.py` (`/cargo/{id}/history`)
- **Cible V3 :** `cargo_packing_router.py` (+ template `staff/cargo/packing_list_history.html`)
- **Objectif :** `PackingListAudit` est alimenté mais **aucune route/écran ne l'affiche**. Exposer la table d'audit.
- **Critères d'acceptation :** écran listant date / auteur / batch / champ / ancienne → nouvelle valeur, trié antéchronologique.
- **Test de non‑régression :** P3a #2.
- **Effort :** S

### [CARGO-05] Restaurer l'Arrival Notice (PDF)
- **Persona :** Agent cargo · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `cargo_router.py` (`/cargo/{id}/arrival-notice`, ReportLab)
- **Cible V3 :** `cargo_router.py`, `app/templates/pdf/arrival_notice.html` (WeasyPrint)
- **Objectif :** réimplémenter l'avis d'arrivée (header B/L, parties, cargo, totaux).
- **Critères d'acceptation :** PDF généré depuis la PL ; parties, marchandise et totaux corrects.
- **Test de non‑régression :** P3a #5.
- **Effort :** M

### [CARGO-06] Restaurer l'upload de documents sur le portail token
- **Persona :** Expéditeur · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `cargo_router.py` (`/p/{token}/documents` upload/download/delete)
- **Cible V3 :** `cargo_portal_router.py`, `app/services/documents.py`, `safe_files.py`, modèle `PackingListDocument` (déjà `packing_list_id`)
- **Objectif :** le dépôt de documents (douane, MSDS) a disparu du portail token (ne survit que dans `/me`). Le rétablir.
- **Critères d'acceptation :** upload validé (`safe_files`, ≤ 20 Mo, types whitelistés), liste, download, delete ; rattachement `packing_list_id`.
- **Dépend de / Arbitrage :** A6 (cible portail).
- **Test de non‑régression :** P3b #2.
- **Effort :** M

## Lot 2 — P1

### [CARGO-08] Pré‑remplissage du batch à la création de la PL
- **Persona :** Agent cargo · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `cargo_router.py` (`/cargo/create` : 1 batch pré‑rempli voyage/vessel/POL/POD/freight)
- **Cible V3 :** `cargo_packing_router.py` (`/from-order/{order_id}`)
- **Objectif :** la PL V3 est créée vide. Pré‑remplir au moins voyage/POL/POD.
- **Critères d'acceptation :** création depuis une commande → batch initial pré‑rempli.
- **Effort :** S

### [CARGO-09] Import/Export Excel (PL, voyage, template portail)
- **Persona :** Agent cargo + Expéditeur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `cargo_router.py` (`/cargo/{id}/excel`, `/import-excel`, `/voyage/{leg}/excel`, `/p/{token}/template`, `/p/{token}/import`)
- **Cible V3 :** `cargo_packing_router.py`, `cargo_portal_router.py`, service openpyxl
- **Objectif :** restaurer le canal de saisie de masse (un voyage = dizaines de batches).
- **Critères d'acceptation :** export PL (colonnes éditables), export voyage entier, template pré‑rempli téléchargeable, import remplaçant les batches (mapping colonnes).
- **Test de non‑régression :** P3a #6, P3b #3.
- **Effort :** L

### [CARGO-10] Restaurer l'écran portail « Suivi voyage »
- **Persona :** Expéditeur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `cargo_router.py` (`/p/{token}/voyage` : 3 phases planning + position navire + équipage)
- **Cible V3 :** `cargo_portal_router.py`, `app/templates/portal/voyage.html`
- **Objectif :** V3 réduit le suivi à 2 lignes ETD/ETA. Restaurer la page dédiée (réutiliser `voyage_track`/position pour la carte).
- **Critères d'acceptation :** 3 phases (prévu/màj/réel), carte position, itinéraire.
- **Dépend de / Arbitrage :** A6.
- **Test de non‑régression :** P3b #4.
- **Effort :** M

### [CARGO-11] Restaurer l'écran portail « Guide » (+ fiche navire)
- **Persona :** Expéditeur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `cargo_router.py` (`/p/{token}/guide` 9 sections, `/p/{token}/vessel`)
- **Cible V3 :** `cargo_portal_router.py`, `app/templates/portal/{guide,vessel}.html`
- **Objectif :** restaurer le guide (process, palettisation, grille tarifaire, AMS/ISF US, FAQ, contact) et la fiche navire (page statique, fort impact onboarding client).
- **Test de non‑régression :** P3b #4.
- **Effort :** M

### [CARGO-12] Réactiver le multilingue du portail
- **Persona :** Expéditeur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** portail V2 i18n fr/en/es/pt‑br/vi
- **Cible V3 :** `app/templates/portal/*`, catalogues i18n
- **Objectif :** le portail V3 est figé en français (clientèle export US/BR/VN).
- **Critères d'acceptation :** sélection de langue persistée ; tous les écrans portail traduits.
- **Dépend de :** UX‑02 (catalogue vi).
- **Test de non‑régression :** P3b #5.
- **Effort :** M

### [CARGO-13] Réintroduire les champs goods riches + complétude documentaire
- **Persona :** Agent cargo + Expéditeur · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `packing_list.py` (`type_of_goods`, `description_of_goods`, `cases_quantity`, `units_per_case`, `cargo_value_usd`, surface/volume/density auto)
- **Cible V3 :** `app/models/packing_list.py`, `app/services/packing_list.py`
- **Objectif :** restaurer les champs douane/BL et la complétude documentaire (V3 ne calcule que « % batches avec poids > 0 »).
- **Migration :** ajout de colonnes ; `compute_dimensions()` pour surface/volume/density.
- **Effort :** M

## Lot 3 — P2

### [CARGO-14] Confort & finitions
- Suppression PL staff (`DELETE /cargo/{id}`, perm S) ; alertes IMDG / écart palettes ;
  marquage messages lus + badge non‑lus (`is_read` non exploité) ; CO₂ saved sur le portail ;
  auto‑fill dimensions par type de palette ; restaurer `file_size`/`notes` sur `PackingListDocument`.
- **Priorité :** P2 · **Effort :** M (groupé)
