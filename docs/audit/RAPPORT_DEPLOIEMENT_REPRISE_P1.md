# Rapport de déploiement — Reprise P0 + P1 V2 → V3 (`mynewtowt`)

> **Branche de travail :** `claude/admiring-clarke-l7obrz`
> **État :** déployé sur `main` par lots autoportants (PR #50 → #68), chaque PR
> mergée en squash après CI verte (`lint` + `security` + `test`).
> **Régression :** `710 passed, 1 skipped` (base de départ P0 : 463 ; fin P0 : 602).
> **Migrations :** `20260622_0061` → `20260623_0071` (toutes additives).
> **Méthode :** « zéro défaut » — chaque lot **implémenté → testé → revu
> (`/code-review`) → corrigé → contrôle de non-régression vs V2 → poussé →
> CI verte → mergé**.

Ce document récapitule l'intégralité de la reprise fonctionnelle V2 → V3 :
les lots P0 (parité fonctionnelle staff) puis P1 (évolutions priorisées), la
posture de sécurité maintenue, l'état de parité vis-à-vis de la version
d'origine (V2 `mytowt-main`) et le reste à traiter (P1 résiduel + P2).

---

## 1. Objectif & méthode

Reprendre dans la V3 unifiée l'existant fonctionnel **staff (ERP interne)** et
**portail expéditeur** perdu ou appauvri lors de la refonte, sans régression de
sécurité. Travail organisé en lots autoportants, un module à la fois, dans
l'ordre : **Commercial → Onboard → Admin → Finance → Stowage → Crew → Cargo →
Escale → UX**.

Chaque restauration est doublée d'un **test de parité V2** dans
`tests/regression/test_v2_parity.py` (tableau de bord exécutable de la parité
V2↔V3). Le dictionnaire `_PENDING` des gaps P0 est **vide** : 100 % de la
parité fonctionnelle P0 est restaurée et vérifiée.

---

## 2. Parité P0 — fonctionnel staff restauré (PR #50 → #58)

| Domaine | Reprise livrée |
|---|---|
| **Sécurité (SEC-01..06)** | rate-limit login + portail token ; unicité `VesselPosition(vessel_id, recorded_at)` ; filtre anti-saut satcom ; garde clé API `/api/v1` ; flags de visibilité sidebar |
| **Cargo (CARGO-01..06)** | Bill of Lading rebranché au batch (adresses structurées + marchandise), n° `TUAW_{leg}_{seq}` anti-doublon, édition/suppression batch + audit, vue historique, Arrival Notice PDF, dépôt documents portail |
| **Escale (ESC-01..03)** | édition/suppression opérations & shifts dockers, saisie manuelle des heures réelles, pilotage statut portuaire (ATA/ATD) idempotent |
| **Crew (CREW-01..05)** | édition fiche marin, Crew List PAF, édition/suppression affectation, embarquement hors leg, billet de transport |
| **MRV (MRV-01..07)** | edit/delete event, export DNV 18 colonnes, Carbon Report PDF, compteurs DO, auto GPS→DMS |
| **Commercial (COM-01..04, 09)** | édition/désactivation client, champs riches de commande, affectation routes, pièce jointe commande, auto-packing-list à la confirmation |
| **Onboard (ONB-01..03, 05)** | SOF edit/delete, documents cargo guidés, pièces jointes leg, clôture d'escale + récap PDF |
| **Finance / KPI (FIN-04/05)** | section Exploitation (écart planning, durée, vitesse), équivalences CO₂ |
| **Stowage (STO-05)** | politique de blocage capacité configurable (feature flag) |
| **Tracking / Planning (TRK / PLN)** | positions live + `import_batch`, délais & regroupement par port |
| **Admin (ADM-04)** | exports CSV/ZIP whitelistés + purges DB ciblées (whitelist, jamais les comptes) |
| **UX (UX-01, UX-02)** | saisie de fuseau horaire (SOF/escale) ; catalogue i18n **vietnamien complet** (510 clés, parité fr/en/es/pt-br) |

---

## 3. Évolutions P1 livrées (PR #59 → #68, cette campagne)

### Cargo
- **CARGO-08** (#59) — pré-remplissage du 1er batch de la packing list depuis la
  commande (parties, volume, marchandise) à la confirmation. Idempotent.
- **CARGO-13** (#60) — champs goods riches (`cases_quantity`, `units_per_case`,
  `cargo_value_usd`) + dimensions dérivées (surface/volume/densité, propriétés
  calculées). Migration `0070`.
- **CARGO-09** (#61) — import/export **Excel** (openpyxl) : export PL, export
  voyage, template vierge, import remplaçant les batches. Anti-injection de
  formule + double garde de taille (Content-Length + revérif post-lecture).
- **CARGO-10/11** (#62) — écrans portail expéditeur **Suivi voyage** (3 phases
  prévu/estimé/réel + position satellite), **Guide** (9 sections) et **fiche
  navire**.

### Escale
- **ESC-04** (#63) — `intervenant` + durées prévue/réelle des opérations.
  Migration `0071`.
- **ESC-06** (#64) — couplage opération ↔ équipage (embarquement/débarquement →
  `CrewAssignment`), billetterie + alertes de cohérence, passage **PAF** auto
  aux ports français (idempotent par leg).

### UX / i18n
- **UX-01** (#65) — partial réutilisable `tz_datetime` (UTC/Paris/Port local +
  aperçu UTC) branché dans escale & SOF ; recâble `towt-tz.js` (livré mais mort).
- **UX-04/05** (#66) — cloche de notifications branchée au flux réel +
  sélecteur de langue en UI staff.
- **UX-03** (#67) — horloge sidebar « prochain port » rebranchée (UTC + fuseau du
  port de destination, alimente aussi la conversion « Port local »).
- **UX-02** (#68) — catalogue vietnamien complet (cf. P0 ci-dessus).

---

## 4. Posture de sécurité (maintenue à chaque lot)

- **CSP-strict** : aucun `<script>` / `onclick` inline ajouté ; confirmations
  destructives via `data-confirm` porté par le `<form>` (forms.js).
- **Uploads** : garde `content_length_exceeds_max` (413) **avant** lecture +
  revérification de la taille réelle après lecture (anti zip-bomb / chunké) ;
  `safe_files` (validation + noms aléatoires + anti-traversal).
- **Exports tableur** : neutralisation de l'injection de formule
  (`csv_safe.sanitize_cell`) sur CSV **et** XLSX.
- **Pas de f-string SQL** pour identifiants ; whitelist + API d'expression.
- **Permissions** : `require_permission(module, niveau)` sur chaque endpoint ;
  suppressions destructives au niveau `S`.
- **Audit** : `services.activity.record()` sur les écritures staff ;
  `record_audit` field-by-field côté cargo ; portail jamais loggé en clair.
- **`await db.flush()`** dans les routes (jamais `commit`).

Chaque lot a été passé en revue ciblée (correctness + sécurité/conventions) ;
deux findings réels détectés et corrigés avant merge (CARGO-09 :
`data-confirm` sur le `<button>` au lieu du `<form>` ; absence de revérif de
taille post-lecture).

---

## 5. État des migrations

Toutes additives (colonnes nullable / nouvelles tables), sûres en production :

```
20260622_0061  sécurité — unicité vessel_position
…              (P0)
20260623_0068  cargo_documents.data_json (ONB-02)
20260623_0069  vessel_positions.import_batch (TRK-05)
20260623_0070  packing_list_batches : cases_quantity / units_per_case / cargo_value_usd (CARGO-13)
20260623_0071  escale_operations.intervenant (ESC-04)
```

---

## 6. Reste à traiter

### P1 résiduel
- **CARGO-12** — multilingue **du portail** expéditeur (les écrans portail sont
  en français ; le catalogue vi est désormais prêt → dépendance levée).
- **ESC-07** — multi-timezone sur les datetimes : **largement couvert** par
  UX-01 (le partial `tz_datetime` est branché sur les formulaires d'escale) ;
  reste l'extension aux formulaires planning/ETA.
- Reliquats par module : `COM-05/06/07/08/11`, `ONB-04/06/07`, `ADM-05/06`,
  `FIN-06/07`, `STO-06/07/09`, `MRV-08`, `TRK-02/03/04`, `PLN-04`.

### P2 (finitions)
- `UX-06` (polish charte), `ESC-08` (cockpit d'escale), `CARGO-14` (confort
  cargo), et les finitions par module (cf. `docs/audit/backlog/`).

---

## 7. Synthèse

- **17 lots** déployés sur `main` (PR #50 → #68), chacun CI-vert et revu.
- **Parité P0 vis-à-vis de la V2 : 100 %** (dictionnaire `_PENDING` vide).
- **710 tests** passent (couverture de parité + intégration + rendu de templates).
- **Sécurité** : aucune régression introduite ; posture CSP/upload/SQL/permissions
  préservée et renforcée sur les nouveaux canaux (Excel, portail).
