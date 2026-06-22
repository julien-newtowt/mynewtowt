# Claims — Analyse des écarts V2 (archives) → V3 actuelle & plan de rattrapage

> Rédigé le 2026‑06‑22. Sources : `docs/legacy/` (specs V2 archivées) vs code
> V3 actuel (`app/models/claim.py`, `app/routers/claims_router.py`,
> `app/templates/staff/claims/`).

## 1. Méthode & sources

Le code de la V2 n'est pas archivé — seules les **specs** le sont. L'analyse
croise donc l'intention fonctionnelle décrite dans les archives avec
l'implémentation V3 réelle.

Références legacy exploitées :
- `docs/legacy/v2/user-guide.md` §3.11 « Claims » : *« Déclaration sinistre
  (cargo, crew, hull). Documents (factures, expertises). Timeline + provision.
  Lien assureur. »*
- `docs/legacy/guide_plan_chargement.md` §« Claims / Sinistres » : récupération
  **automatique** de la position de la marchandise depuis le plan de chargement
  pour un claim cargo, avec lien vers le plan « pour faciliter l'analyse des
  dommages et la détermination des responsabilités ».
- `docs/legacy/captain/onboard-v2-spec.md` : *« SOF events filtrés cargo :
  loading, discharging, **claims**, hold inspection »* et *« Incidents équipage :
  **claims** liés à l'équipage + tickets médicaux »* → claims accessibles
  **depuis l'onboard**, filtrés au leg.
- `docs/legacy/captain/audit.md` : *« claims accessibles via `/claims` mais
  **pas filtrés au leg actif** »* (déjà identifié comme manque en V2).
- `docs/legacy/ux/design-system-v2.md` : Claims rattaché au domaine **Cargo**
  dans la sidebar.

## 2. Ce que fait déjà la V3 (état des lieux)

La V3 est, sur plusieurs points, **plus riche** que la spec V2 :

| Capacité | V3 actuelle |
|---|---|
| Types | `cargo, crew, hull, war_risk, third_party, other` (6, vs 3 en V2) |
| Workflow | 6 statuts `open → in_review → provisioned → settled / rejected → closed` |
| Timeline | ✅ `claim_timeline` (entrées datées, auteur, type, corps) |
| Provision / règlement | ✅ `provision_eur`, `settled_eur`, `settled_at` |
| Assureur | 🟡 champs **texte libre** `insurer`, `insurer_claim_ref` |
| Position cargo | ✅ `cargo_position` (zone d'arrimage, renseignable) |
| Rattachement | `leg_id`, `booking_id` |
| Notes | ✅ ajout de notes (timeline) |

## 3. Analyse des écarts (là où la V2 était « plus précise »)

| # | Capacité V2 (archives) | V3 actuelle | Écart |
|---|---|---|---|
| E1 | **Documents** rattachés : factures, **expertises** | ❌ Aucun modèle de pièce jointe | **Bloquant** — pas de stockage de preuves/expertises |
| E2 | **Lien assureur** (vers la police) | 🟡 Texte libre uniquement | Pas de FK vers `InsuranceContract` (module Finance) → pas de contrôle de couverture |
| E3 | Position cargo **récupérée automatiquement** depuis le plan de chargement + **lien** vers le plan | 🟡 `cargo_position` saisi/repris mais sans auto‑remplissage ni lien cliquable systématique | Auto‑résolution depuis `StowageItem` à fiabiliser + deep‑link |
| E4 | Claims **accessibles depuis l'onboard**, **filtrés au leg actif** ; présents comme **événement SOF** (cargo) | ❌ Aucune intégration captain/onboard | Le commandant ne peut pas déclarer/voir un claim du leg en mer |
| E5 | Claims **équipage** = incidents onboard (+ tickets médicaux) | 🟡 Type `crew` existe mais pas de passerelle incidents/onboard | Pas de déclaration depuis l'espace équipage |
| E6 | « Timeline + provision » → suivi financier | 🟡 Provision/règlement présents mais **pas de réévaluation** tracée ni d'impact `LegFinance` | Pas d'historique des révisions de provision, pas de déduction marge |
| E7 | (implicite) Notifications sinistre | ❌ Aucune notification | Manager/assureur non alertés à l'ouverture / au règlement |
| E8 | (implicite) Reporting | ❌ Pas de KPI claims | Pas de vue agrégée (nb par type, délai de règlement, provisions totales) |

> Note : la V2 elle‑même listait E4 comme un manque (« pas filtrés au leg
> actif »). Le rattrapage doit donc **dépasser** la V2 sur ce point.

## 4. Plan de rattrapage (phasé)

### Phase P1 — Précision documentaire & assureur (priorité haute)

1. **E1 — Pièces jointes de claim** (`ClaimDocument`)
   - Modèle `claim_documents` : `id, claim_id (FK CASCADE), doc_type
     (facture|expertise|photo|courrier|autre), filename, content_type,
     storage_path|bytes, uploaded_by, uploaded_at, notes`.
   - Réutiliser `app/utils/file_validation.py` (déjà utilisé par cargo/packing).
   - Routes : `POST /claims/{id}/documents` (upload, `claims` `M`),
     `GET /claims/{id}/documents/{doc_id}` (download owner‑gated),
     `POST /claims/{id}/documents/{doc_id}/delete` (`S`).
   - UI : section « Pièces (factures, expertises) » dans `claims/detail.html`.
   - Migration Alembic + entrée timeline auto (`kind="document"`).
2. **E2 — Lien assureur structuré**
   - Ajouter `insurance_contract_id` (FK `insurance_contracts.id`, nullable) au
     modèle `Claim` ; conserver `insurer`/`insurer_claim_ref` en repli.
   - Formulaire : `<select>` des `InsuranceContract` actifs ; affichage du
     n° de police + couverture dans le détail.
   - Migration + pré‑remplissage assureur depuis le contrat sélectionné.

### Phase P2 — Intégration opérationnelle (priorité moyenne)

3. **E3 — Auto‑résolution position cargo + deep‑link**
   - À la création d'un claim `cargo` lié à un `leg`/`booking`, résoudre la
     zone via `services.stowage.locate_*` et pré‑remplir `cargo_position` ;
     afficher un lien `→ Plan de chargement` (`/stowage/legs/{leg_id}`).
4. **E4 + E5 — Accès onboard / leg + déclaration équipage**
   - Filtre `?leg=` sur `/claims` (réutiliser le pattern `_leg_filter`).
   - Carte « Incidents / claims du leg » dans l'onboard (`captain`/`crew`),
     avec bouton « Déclarer un sinistre » pré‑rempli (leg, type).
   - Optionnel : matérialiser le claim comme **événement SOF** (`sof_event`
     `kind="claim"`) pour la chronologie portuaire.
5. **E7 — Notifications**
   - `services.notifications.create` ciblant le rôle gestionnaire à
     l'ouverture, au passage `provisioned` et au `settled` ; e‑mail assureur
     best‑effort (réutiliser `services.email`).

### Phase P3 — Finance & pilotage (priorité basse)

6. **E6 — Provision tracée + impact finance**
   - Table `claim_provision_history` (montant, motif, auteur, date) ;
     chaque modif de `provision_eur` journalise une révision.
   - Remontée de la provision/du règlement dans `LegFinance` (coût exceptionnel)
     pour refléter la marge nette du leg.
7. **E8 — Reporting claims**
   - Vue agrégée `/claims/stats` : nb par type/statut, délai moyen de
     règlement, provisions totales vs réglées, ratio sinistralité par navire.
   - Export CSV ; éventuelle carte KPI sur le dashboard manager.

## 5. Estimation & séquencement

| Lot | Écarts | Charge indicative | Dépendances |
|---|---|---|---|
| P1.1 | E1 | M (modèle+migration+routes+UI+validation fichier) | `file_validation` |
| P1.2 | E2 | S | `InsuranceContract` |
| P2.3 | E3 | S | `services.stowage` |
| P2.4 | E4/E5 | M | onboard/captain, `_leg_filter`, SOF |
| P2.5 | E7 | S | `notifications`, `email` |
| P3.6 | E6 | M | `LegFinance` |
| P3.7 | E8 | S | — |

## 6. Schéma de données cible (synthèse)

```
claims                         (existant — + insurance_contract_id)
└── claim_documents            (P1 — factures, expertises, photos)
└── claim_timeline             (existant)
└── claim_provision_history    (P3 — révisions de provision)
```

## 7. Recommandation

Démarrer par **P1 (E1 documents + E2 assureur)** : ce sont les deux écarts qui
rendaient la V2 « plus précise » et qui sont aujourd'hui **absents** (preuves
d'expertise et couverture assurance), à fort enjeu juridique/financier. P2/P3
suivent pour l'intégration terrain et le pilotage.
