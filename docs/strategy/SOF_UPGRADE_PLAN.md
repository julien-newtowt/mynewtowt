# SOF (Statement of Facts) — Analyse historique & plan de mise à niveau

> Rédigé le 2026-06-22. Sources : `docs/legacy/` (specs V2 archivées) vs code V3
> actuel (`app/models/sof_event.py`, `app/routers/captain_router.py`,
> `app/routers/escale_router.py`, templates `pdf/sof_*.html`).

## 1. Méthode & sources

Comme pour les claims, le **code** V2 n'est pas archivé ; seules les **specs** le
sont. On croise l'intention fonctionnelle décrite dans les archives avec
l'implémentation V3 réelle.

Références legacy exploitées :
- `docs/legacy/captain/onboard-v2-spec.md` : événements SOF (EOSP, SOSP, pilot
  on/off, tug, gangway, loading/discharging…), documents portuaires (NOR,
  mate's receipt, letters of protest — « checklist avec badges signés/en
  attente »), vue `/onboard/escale` agrégée pour le commandant, split
  Import/Export.
- `docs/legacy/v2/user-guide.md` : démarrage d'escale → *« Création auto des
  opérations standard (NOR, pilotage) »* puis *« Clôture : ATD + cargo
  documents complets »* — NOR rattaché à la **laytime** pour la facturation
  armateur.
- `docs/legacy/v2/router-audit.md` : `SofEvent` = « événements indépendants »
  (immuables), horodatage sûr.
- `docs/legacy/captain/audit.md` : SOF = *« moments ponctuels (EOSP, SOSP,
  pilot on/off) »* — distincts du journal de quart.

## 2. Ce que fait déjà la V3 (état des lieux — solide)

La V3 est, sur la **capture d'événements**, **plus avancée** que la spec V2 :

| Capacité | V3 actuelle |
|---|---|
| Catalogue d'événements | **26 types** (`SOF_EVENT_TYPES`) : EOSP, SOSP, NOR, NOR_RT, FREE_PRATIQUE, PILOT_ON/OFF, TUG_ON/OFF, FIRST_LINE, ALL_FAST, GANGWAY_UP/DOWN, ARRIVE/DEPART_PILOT_STATION, ANCHORED, WEIGH_ANCHOR, BERTHED, UNBERTHED, BUNKER_START/END, **LOADING_START/END, DISCHARGING_START/END**, DRAFT_SURVEY, OTHER |
| Champs par événement | `event_type`, `label`, `occurred_at` (tz), `port_id`, `latitude/longitude`, `notes`, `recorded_by` |
| Inviolabilité | **Signature commandant** (`signed_at/by`), **hash SHA-256** anti-altération, `is_locked` → backend rejette UPDATE/DELETE après signature |
| PDF | SOF escale (`pdf/sof_escale.html`) **et** SOF capitaine (`pdf/sof_captain.html`) — WeasyPrint |
| Intégration escale | **FLX-04** : `ESCALE_ACTION_TO_SOF` génère le SOF équivalent à une opération d'escale (NOR, pilot, gangway…) |
| Documents cargo | NOR, NOR_RT, LOP (general/draft), Mate's Receipt — générateurs PDF dédiés |
| ETA shifts | `EtaShift` (historique des décalages d'ETA, motifs) |

➡️ Sur ces points, **aucun rattrapage nécessaire** : la V3 dépasse la spec V2.

## 3. Le vrai écart : la finalité « réglementaire/commerciale » du SOF

Un Statement of Facts ne sert pas qu'à **lister** des événements : sa finalité
est le **décompte des staries** (*laytime*) et le calcul des **surestaries**
(*demurrage*) / **despatch** pour la facturation entre affréteur et armateur.
La spec V2 le sous-entendait (NOR → laytime → facturation). **C'est ce volet qui
manque** en V3.

| # | Capacité attendue | V3 actuelle | Écart |
|---|---|---|---|
| S1 | **Décompte des staries** (laytime) à partir des événements (NOR + turn-time → début ; LOADING/DISCHARGING_START→END) | ❌ Aucun calcul | **Majeur** — le SOF n'est pas exploité pour son usage premier |
| S2 | **Périodes d'interruption** (pluie, panne, shifting, attente doc/cargo, week-end non ouvré) qui **suspendent** la starie | ❌ Événements ponctuels seulement, pas d'intervalles d'arrêt motivés | Majeur — sans suspensions, le laytime est faux |
| S3 | **Termes de charte-partie** par leg : starie allouée (h), cadence (t/j), termes (SHINC/SHEX, WWD), turn-time, **taux de surestarie** (€/j), taux despatch | ❌ Aucun champ | Indispensable au calcul |
| S4 | **Surestarie / despatch** : laytime utilisé vs alloué → montant dû, et **remontée dans `LegFinance`** | ❌ Absent (LegFinance n'a pas de poste laytime) | Majeur — impact marge non capté |
| S5 | **Vue capitaine filtrée** par contexte (pilotage / cargo / documents) | 🟡 Liste chronologique ; filtres contextuels à confirmer | Mineur (UX) |
| S6 | **Contrôles de cohérence** de la chronologie (NOR avant LOADING_START, ordre BERTHED/ALL_FAST, horodatages croissants) | 🟡 Validation partielle | Mineur — fiabilise le SOF |
| S7 | **Tableau SOF normalisé** (From / To / Elapsed / % laytime / Remarks) dans le PDF | 🟡 Chronologie présente ; colonnes durée/laytime à enrichir | Mineur |
| S8 | **Lien jours fermés port** (cf. `PortConfig.closed_saturday/sunday`) comme exceptions de laytime | ❌ Pas relié au décompte | Mineur — cohérence avec le moteur planning |

## 4. Plan de mise à niveau (phasé)

### Phase P1 — Moteur de staries (priorité haute) — S1, S3

1. **Termes de laytime par leg** (`LegLaytimeTerms` ou champs sur `LegFinance`) :
   `allowed_laytime_hours`, `load_rate_t_per_day`, `discharge_rate_t_per_day`,
   `turn_time_hours`, `terms` (`SHINC`/`SHEX`/`WWD`…), `demurrage_rate_eur_day`,
   `despatch_rate_eur_day`, `cargo_qty_t`.
2. **Service `services/laytime.py`** — calcul à partir des `SofEvent` du leg :
   - début de starie : `NOR.occurred_at` + turn-time (ou commencement effectif
     des opérations selon les termes) ;
   - fin : `LOADING_END` / `DISCHARGING_END` ;
   - laytime brut = intervalle, moins les périodes exclues (P2) ;
   - comparaison au laytime alloué → **demurrage** (dépassement) ou **despatch**.
   - Pur/testable (entrées : liste d'événements + termes ; sortie : décompte).
3. **Restitution** : encart « Décompte des staries » sur la fiche escale/SOF
   (laytime utilisé / alloué, % , résultat surestarie/despatch).

### Phase P2 — Interruptions & exceptions (priorité haute) — S2, S8

4. **Périodes d'arrêt** (`SofStoppage` : `from`, `to`, `reason`, `counts_as`
   {exclue|demi|pleine}) saisies depuis la chronologie — météo, panne, shifting,
   attente documents/cargo. Intégrées au moteur de laytime (déduction).
5. **Exceptions calendaires** : selon les termes (SHEX/WWD) et
   `PortConfig.closed_saturday/sunday`, exclure automatiquement week-ends/jours
   fermés du décompte — cohérent avec le moteur de planification (réutilise la
   logique « jours ouvrés »).

### Phase P3 — Finance, UI & fiabilité (priorité moyenne) — S4–S7

6. **Intégration finance (S4)** : poste `laytime_result_eur` sur `LegFinance`
   (surestarie = coût/recette), remonté par `finance_rollup` dans la marge du
   leg (comme les claims).
7. **Vue capitaine filtrée (S5)** : onglets Pilotage / Cargo / Documents sur la
   chronologie SOF (filtre par sous-ensembles d'`event_type`).
8. **Contrôles de cohérence (S6)** : avertissements non bloquants (NOR manquant
   avant LOADING_START, horodatage décroissant, ALL_FAST sans BERTHED…).
9. **PDF SOF enrichi (S7)** : tableau normalisé From/To/Elapsed/Remarks +
   encart décompte staries + signature — format opposable à l'agent/affréteur.

## 5. Estimation & séquencement

| Lot | Écarts | Charge | Dépendances |
|---|---|---|---|
| P1.1 | S3 | S (champs + migration) | `LegFinance` |
| P1.2 | S1 | M (service + tests + UI) | `SofEvent` |
| P2.4 | S2 | M (modèle + saisie + intégration) | service laytime |
| P2.5 | S8 | S | `PortConfig`, moteur jours ouvrés |
| P3.6 | S4 | S | `finance_rollup` |
| P3.7 | S5 | S | template capitaine |
| P3.8 | S6 | S | — |
| P3.9 | S7 | S | WeasyPrint |

## 6. Schéma de données cible (synthèse)

```
legs
└── leg_laytime_terms        (P1 — starie allouée, cadences, taux dem./desp.)
sof_events                   (existant — base du décompte)
└── sof_stoppages            (P2 — interruptions motivées, déductions)
leg_finances
└── laytime_result_eur       (P3 — surestarie/despatch dans la marge)
```

## 7. Recommandation

Le SOF V3 est **excellent en capture/signature/PDF** — il ne faut pas le
refaire. La mise à niveau doit cibler sa **finalité commerciale** : démarrer par
**P1 (termes + moteur de staries)** puis **P2 (interruptions/exceptions)**, qui
transforment une chronologie en **outil de décompte staries/surestaries** —
exactement la précision « réglementaire » que la version historique sous-tendait
(NOR → laytime → facturation armateur). P3 boucle l'intégration finance, l'UX
capitaine et la valeur probante du PDF.
