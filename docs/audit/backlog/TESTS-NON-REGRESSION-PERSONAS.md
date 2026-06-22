# Tests de non‑régression par persona

Chaque parcours est une **check‑list d'acceptation** : à la fin du Lot 1 (P0) du module
concerné, l'opérateur doit pouvoir dérouler son scénario sans rupture. À intégrer aux tests
d'intégration (`pytest`) et à la recette manuelle. Statut cible : tous ✅ avant mise en prod.

> Légende : chaque item référence le(s) ticket(s) qui le débloque(nt).

## P1 — Planificateur d'armement
1. Créer une chaîne de legs (POL=POD précédent), voir le Gantt année — *(déjà OK V3)*
2. Saisir l'**ATD/ATA réel** et le **statut** d'un leg → `PLN-02`
3. Générer la **brochure commerciale PDF** (filtres navire/origine/destination, FR/EN, sélection de legs) → `PLN-01`
4. **Exporter le planning en CSV** → `PLN-03`
5. Partager un planning, renseigner le **destinataire**, retrouver l'**historique** → `PLN-04`
6. Être **alerté d'un retard** ≥ 4 h vs référence → `PLN-05`

## P2 — Responsable commercial / broker
1. Créer **et modifier** un client → `COM-03`
2. Créer une commande **complète** (format palette, poids, frais, route, dates) → `COM-02`
3. **Affecter/réaffecter** la commande à un leg (legs filtrés route + suggestion + alerte délai) → `COM-01`
4. **Joindre** un bon de commande signé à la commande → `COM-04`
5. Convertir une offre en commande **avec ajustement** → `COM-05`
6. Retrouver le **lien grille** appliqué et le **lookup tarif** dans le form → `COM-02`, `COM-07`

## P3a — Agent cargo / logistique
1. Créer une packing list depuis une commande (batch pré‑rempli) → `CARGO-08`
2. **Éditer et supprimer** un batch ; consulter l'**audit** des modifications → `CARGO-03`, `CARGO-04`
3. Saisir les **adresses** shipper/notify/consignee → `CARGO-02`
4. Générer un **Bill of Lading** alimenté par la packing list (n° `TUAW_…`, anti‑doublon) → `CARGO-01`
5. Générer l'**Arrival Notice** → `CARGO-05`
6. **Exporter/importer** la PL en Excel (+ voyage entier) → `CARGO-09`

## P3b — Expéditeur / shipper (portail token)
1. **Saisir, corriger et supprimer** ses batches → `CARGO-03`
2. **Déposer un document** (douane, MSDS) via le lien token → `CARGO-06`
3. **Importer** sa packing list via le template Excel → `CARGO-09`
4. Consulter le **suivi voyage** (3 phases + position) et le **guide** → `CARGO-10`, `CARGO-11`
5. Utiliser le portail dans **sa langue** (en/es/pt‑br/vi) → `CARGO-12`

## P4 — Agent d'escale / portuaire
1. **Faire progresser le statut portuaire** (pilote arrivée → à quai → pilote départ) et poser **ATA/ATD** → `ESC-02`
2. Voir la **propagation** aux legs suivants, le **recalcul OPEX** et les **notifications** → `ESC-02`
3. Créer, **éditer et supprimer** une opération et un shift docker → `ESC-01`
4. Saisir une **heure réelle a posteriori** → `ESC-03`
5. Suivre la **productivité dockers** (pal/h, écart %) → `ESC-05`
6. Saisir les heures avec **fuseau** (UTC/Paris/Port local) → `ESC-07`, `UX-01`

## P5 — Capitaine / officier à bord
1. Saisir un **noon report** + journal de quart, hors‑ligne — *(gain V3, OK)*
2. **Corriger/supprimer** un événement SOF non signé → `ONB-01`
3. Remplir un **document cargo guidé** (NOR, LOP, HOLDS_CERT, Mate's Receipt) → `ONB-02`
4. **Déposer les documents de l'agent d'escale** (BL signé, lettre de protestation) + **photos** → `ONB-03`
5. Utiliser la **messagerie de bord** (fil navire, mentions, messages système) → `ONB-04`
6. Clôturer l'escale avec **checklist + PDF récapitulatif** → `ONB-05`

## P6 — Bosco / responsable équipage
1. Créer **et éditer** une fiche marin (passeport, **visa US/BR**, seaman book, naissance) → `CREW-01`, `CREW-03`
2. **Éditer et supprimer** une affectation → `CREW-04`
3. Produire l'**export PDF « Crew List » pour la PAF** → `CREW-02`
4. **Joindre et télécharger** la PJ d'un billet → `CREW-05`
5. Bénéficier de l'**anti‑overlap** d'embarquement → `CREW-08`

## P7 — Data analyst / MRV‑RSE
1. Saisir un event MRV ; le **corriger/supprimer** → `MRV-03`
2. Exporter le **DNV CSV 18 colonnes** (IMO renseigné) → `MRV-01`
3. Générer le **Carbon Report PDF** (bloqué si erreurs qualité) → `MRV-02`
4. Régler les **paramètres MRV** (densité, seuil déviation) → `MRV-06`
5. Voir la **position DMS** + auto‑remplissage GPS → `MRV-07`

## P8 — Contrôleur de gestion / finance
1. Saisir/comparer **prévisionnel vs réalisé** par poste et par leg → `FIN-01`
2. **Exporter la finance en CSV** → `FIN-02`
3. Consulter le **détail exposition assurance** (provisions/indemnités/franchises) → `FIN-06`
4. Voir le **NOx/SOx évités** et les **équivalences CO₂** → `FIN-03`, `FIN-05`

## P9 — Administrateur système
1. **Créer/modifier un navire** (capacité, vitesse, IMO, flag, élongation) → `ADM-01`
2. **Importer des utilisateurs** (Excel) et un **planning CSV** → `ADM-05`
3. **Exporter / purger** la base (global/sélectif, cleanups) → `ADM-04`
4. Configurer/tester le **token Pipedrive** depuis l'UI → `ADM-07`
5. Filtrer l'**activity‑log par utilisateur** + pagination → `ADM-08`

## P10 — Collaborateur staff / manager (dashboard)
1. Voir le **moteur d'alertes** (retards, ETA dépassées, conflits port, départs imminents, escales non verrouillées, commandes non affectées) → `ADM-02`
2. Voir les **KPI métier** (CA prévisionnel, CO₂ évité, taux de remplissage) → `ADM-03`
3. Voir les **notifications cargo (PL soumises) et compagnie (ATA/ATD)** → `ADM-03`

## P11 — Opérateur suivi de flotte
1. Voir les **positions live** (statut à quai/en mer par couleur) → `TRK-04`
2. Consulter l'historique filtrable + **météo** — *(gain V3, OK)*
3. Voir les **KPI navigation agrégés** par an (avg SOG, distances par leg) → `TRK-02`
4. Voir `avg_speed`/`real_elongation` dans le tableau navigation → `TRK-03`
