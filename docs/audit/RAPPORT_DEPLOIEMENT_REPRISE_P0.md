# Rapport de déploiement — Reprise P0 V2 → V3 (`mynewtowt`)

> **Branche :** `claude/admiring-clarke-l7obrz` · **PR :** #50
> **État :** prêt pour revue de sécurité (`/security-review`) puis merge.
> **Régression :** `602 passed, 1 skipped` (base de départ : 463).
> **Migrations :** `20260622_0061` → `20260622_0067` (additives).

Ce document décrit l'intégralité du travail livré sur la PR avant merge :
chaque lot de reprise, les arbitrages structurants, l'état de parité vis-à-vis
de la version d'origine (V2 `mytowt-main`), et ce qui reste à traiter.

---

## 1. Objectif

Reprendre dans la V3 unifiée l'existant fonctionnel **staff (ERP interne)** de
l'application V2, perdu ou appauvri lors de la refonte. Méthode imposée :
travail « zéro défaut » — chaque lot **implémenté → testé → revu
(`/code-review`) → corrigé → contrôle de non-régression vs V2 → poussé** sur
une PR unique, sans merge tant que la revue de sécurité n'a pas eu lieu.

Le périmètre P0 a été figé par 7 arbitrages métier (cf.
`docs/audit/backlog/ARBITRAGES.md`).

---

## 2. Lots déployés

Chaque lot est un commit autoportant sur la PR #50.

### Lot 0 — Sécurité & intégrité (`SEC-01..06`)
Rate-limit login staff + portail token, contrainte d'unicité
`VesselPosition(vessel_id, recorded_at)` restaurée, filtre anti-saut satcom sur
la distance réelle, garde de clé API (`/api/v1`), flags de permission sidebar.

### Cargo — BL & portail (`CARGO-01..`)
Reconnexion du **Bill of Lading** au batch de packing list (adresses
structurées shipper/notify/consignee + marchandise), numérotation persistante
`TUAW_{leg}_{seq}` anti-doublon (contrainte unique + retry), édition/suppression
batch, historique d'audit, **Arrival Notice** PDF, dépôt de documents portail.

### Escale (`ESC-01/03/05` puis `ESC-02`)
Édition/suppression d'opérations & de shifts dockers, saisie manuelle des
heures réelles, cadence dockers (planifiée/réelle/écart). **ESC-02** : pilotage
du statut portuaire — pose **ATA/ATD** depuis l'escale, recalcul OPEX réel
(rollup), notification compagnie (EOSP/SOSP), **idempotent** ; aligné sur le
flux SOF du commandant (pas de cascade fantôme — cf. §4).

### Crew (`CREW-01/02/03` puis `CREW-04/05`)
Édition de la fiche marin, **Crew List PAF** (police aux frontières),
édition/suppression d'affectation, anti-chevauchement strict. **CREW-04 (A4)** :
embarquement **hors leg** (rattaché au navire). **CREW-05** : **billet**
(titre de transport) attaché à l'embarquement (upload/download/delete).

### MRV (`MRV-01..07`, arbitrage A1 hybride)
Compteurs DO (4) + calcul ME/AE/ROB chaîné + qualité ; export **DNV Veracity
18 colonnes** ; **Carbon Report PDF** (blocage scoping si erreur qualité) ;
édition/suppression d'événement ; paramètres densité. **MRV-07** :
auto-remplissage de la **position DMS** depuis le dernier point GPS du navire
(saisie manuelle prioritaire).

### Commercial (`COM-01/02/03/04`)
- **COM-03** : édition + désactivation client (dont l'adresse, absente même de
  la création).
- **COM-02** : champs riches de la commande (format/poids palette, THC, frais
  booking & documentaires, route souhaitée POL/POD, fenêtre de livraison,
  lien grille `rate_grid_id`/`rate_grid_line_id`).
- **COM-01** : écran d'affectation **commande → leg** (legs compatibles
  filtrés par route, suggestion, alerte « hors délai », écriture
  `OrderAssignment`). Choix **simple-leg** (parité V2) — cf. §4.
- **COM-04** : pièce jointe (bon de commande / contrat) sur la commande.

### Onboard / Captain (`ONB-01/03`)
- **ONB-01** : édition/suppression d'un SOF **non signé** (409 si verrouillé) ;
  l'événement MRV dérivé est réaligné/nettoyé.
- **ONB-03** : pièces jointes leg catégorisées (documents bord / **agent
  d'escale** : BL signés, lettres de protestation, constats…), upload validé
  (extension + taille + magic number), download anti-traversal, suppression.

### Finance / KPI (`FIN-01/02/03`, arbitrage A2)
- **FIN-01** : suivi **prévisionnel/réel** par poste sur `LegFinance` + marge
  prévisionnelle + écarts (propriétés calculées).
- **FIN-02** : export **CSV finance** (19 colonnes prév/réel/écart, filtré).
- **FIN-03** : **NOx/SOx évités** — service `emissions` (facteurs conv/voile
  paramétrables via `co2_variables`, repli constantes V2), affichés en KPI +
  export.

### Planning (`PLN-01/03`)
- **PLN-01** : **brochure commerciale PDF** (WeasyPrint), filtres navire/année,
  vue chrono ou par destination, FR/EN, summary box.
- **PLN-03** : export **CSV du planning réel** (filtré navire/année).

### Stowage (`STO-01/02/03`)
- **STO-01** : vue « **à bord** » lecture seule (permission captain).
- **STO-02** : réaffectation de zone d'une palette (sélecteur CSP-safe).
- **STO-03** : retrait d'une affectation ciblée.

### Admin (`ADM-01/02`)
- **ADM-01** : **CRUD navires** (création/édition/désactivation, audit).
- **ADM-02** : **moteur d'alertes** du dashboard — 6 familles (retard
  ATA>ETA+24h, ETA dépassée, escale non verrouillée, départ <48h sans
  opérations, conflit de port, commandes non affectées), tri par sévérité,
  deep-links.

---

## 3. Migrations (additives, sûres)

| Révision | Objet |
|---|---|
| `..0061` | Lot 0 — unicité `vessel_positions` |
| `..0062` | Cargo — adresses BL + numéro BL sur batches |
| `..0063` | MRV — compteurs DO + DMS + qualité |
| `..0064` | Commercial — champs riches commande + lien grille + unicité affectation |
| `..0065` | Onboard — table `leg_attachments` |
| `..0066` | Finance — colonnes prévisionnel par poste |
| `..0067` | Commercial PJ + Crew leg_id nullable/vessel_id + billet |

Toutes les colonnes ajoutées sont nullables ou à défaut → pas de reprise de
données bloquante. `crew_assignments.leg_id` passe nullable (batch alter).

---

## 4. Arbitrages & décisions structurantes

- **A1 (MRV hybride)** : compteurs DO **et** noon report cohabitent.
- **A2 (Finance)** : double colonne prévisionnel/réel par poste.
- **ESC-02 / cascade** : la pose d'ATA/ATD **n'altère pas** ETD/ETA, donc **ne
  cascade pas** les dates aval (la cascade reste réservée au décalage d'ETA) —
  comportement aligné sur le flux SOF du commandant. Notifications EOSP/SOSP
  **idempotentes**. *(Correctif issu de la revue : suppression d'une cascade
  fantôme qui notifiait à tort les clients du leg source.)*
- **COM-01 (affectation) : simple-leg.** Une commande ⇄ un leg ;
  `palettes_count` dérive de la commande ; réaffecter remplace. Choix retenu
  pour la cohérence (capacité/finance/packing lisent `order.leg_id`) et la
  fidélité à la V2. La **ventilation multi-legs** (répartition du CA,
  réconciliation capacité, stabilité PL/BL) est tracée en **P1 (COM-11)**.
- **A4 (Crew)** : embarquement hors leg autorisé (leg_id nullable + vessel_id).

Toutes les revues `/code-review` de la session (10 findings sur le seul lot
Commercial, p. ex.) ont été traitées : divergence `confirmed_at`, contrainte
d'unicité `(order_id, leg_id)`, radio désactivé/coché → 422, drop silencieux de
date invalide, off-by-one tz sur l'alerte « hors délai », garde leg déjà parti.

---

## 5. Contrôle de non-régression vs V2

Le fichier **`tests/regression/test_v2_parity.py`** est un tableau de bord
**vivant et exécutable** : chaque fonctionnalité V2 restaurée y est asservie
par un test, et les fonctionnalités non reprises sont `skip` avec motif.

- **Restaurées et vérifiées :** Lot 0, Cargo, ESC-01/02/03/05,
  CREW-01..05, MRV-01..07, COM-01/02/03/04, ONB-01/03, FIN-01/02/03,
  PLN-01/03, STO-01/02/03, ADM-01/02, **ONB-02**.
- **Restant : aucun.** ✅ Toute la parité **P0** vis-à-vis de la V2 est
  restaurée (`_PENDING` vide dans `test_v2_parity`).

**ONB-02** (livré) — documents cargo guidés : 12 types structurés (NOR,
NOR-RT, Holds Certificate, Key/Pre-Loading Meeting, 6 Letters of Protest,
Mate's Receipt), champs spécifiques par type sérialisés en `data_json`,
mentions légales pré-remplies (réserve de droits LOP, « apparent good order »
Mate's Receipt), signataire choisi parmi l'équipage embarqué, export PDF
générique piloté par schéma. Migration `0068`.

Suite complète : **618 passed, 1 skipped** (départ 463). ~14 fichiers de tests
d'intégration de reprise + le tableau de parité.

---

## 6. Reste à faire (hors périmètre de cette PR)

1. **P0 — terminé** (ONB-02 inclus). Plus aucun gap de parité P0.
2. **P1 / P2** — backlog complet par module dans `docs/audit/backlog/*`
   (ex. ONB-04 messagerie de bord, ONB-05 clôture PDF+checklist, ONB-06 claims
   détaillés, FIN-04..07 KPI consolidé, COM-05..11 conversion/pipedrive/
   ventilation multi-legs, ADM-03..07 exports/imports/RGPD, PLN-02/04/05…).

---

## 7. Revue de sécurité (`/security-review`)

Revue effectuée sur l'ensemble du diff de reprise (3 passes : fichiers,
autorisation/CSRF/IDOR, injection/PDF/CSV/SQL). **Aucune vulnérabilité
haute/critique.** Surfaces vérifiées propres : SQL paramétré (pas de f-string
identifiant), pas de path-traversal (`resolve_path` ancré + noms aléatoires),
IDOR enfant→parent correctement scopé (`att.leg_id == leg_id`,
`item.plan_id == plan_id`…), mass-assignment bloqué (allowlists + `status`
forcé), pas de SSTI/XSS PDF (autoescape, pas de `|safe`), garde clé API v1
constant-time fail-closed, redirections serveur uniquement.

**3 correctifs appliqués** (commit de durcissement) :

| Sévérité | Constat | Correctif |
|---|---|---|
| Moyenne | Upload lu intégralement en mémoire avant contrôle de taille (DoS/OOM) | Pré-filtre `Content-Length` → **413** avant lecture (3 routes upload), aligné sur `tracking_router` |
| Basse | Injection de formules CSV (noms navire/port libres) | Helper `csv_safe.sanitize_row` (préfixe `'` sur cellules **texte** à risque ; nombres négatifs préservés) sur exports planning/finance/kpi |
| Basse | Suppressions gardées en `M` au lieu de `S` | Routes de suppression (SOF, PJ leg, affectation, PJ commande, item stowage, billet) passées en **`require_permission(..., "S")`** |

Observations résiduelles **informatives** (conformes au design documenté, non
corrigées) : RBAC au niveau **module** (pas de scoping par objet) — modèle
applicatif existant ; vue stowage « à bord » lisible via permission `captain`
(lecture seule, audience attendue) ; alertes dashboard cross-module visibles de
tout staff (comme les widgets existants). Le sniffing magic-number reste
indicatif mais l'impact XSS est neutralisé par `Content-Disposition: attachment`.

Tests de non-régression sécurité ajoutés (`tests/integration/test_security_hardening.py`).

## 8. Checklist avant merge

- [x] **`/security-review`** — fait (cf. §7), 3 correctifs intégrés, aucune
      vulnérabilité haute/critique résiduelle.
- [ ] Appliquer les migrations `0061→0067` en pré-prod (Alembic) et vérifier le
      schéma (colonnes nullables, FK, index, contraintes d'unicité).
- [ ] Recette persona : broker (affectation/PJ commande), commandant (SOF
      édition + PJ leg + vue stowage à bord), contrôle de gestion (prév/réel +
      CSV), data analyst (NOx/SOx + alertes dashboard), armement (CRUD navires).
- [ ] Vérifier le stockage fichiers (`settings.upload_dir`) provisionné et
      sauvegardé en production (PJ leg/commande/billet).
- [ ] Confirmer la politique CSP inchangée (aucun `<script>` inline ajouté ;
      confirmations via `data-confirm`).
