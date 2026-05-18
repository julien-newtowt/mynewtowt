# Audit des routeurs — V2

Audit conduit sur les 19 routeurs existants (`app/routers/`) avec focus sur les 3 préoccupations métier majeures :

1. **Calendrier / planification & recalcul avances/retards.**
2. **Séparation Import/Export d'une escale.**
3. **Concordance des informations entre modules.**

## 1. Planning / recalcul avances/retards

### État actuel ✅ partiel
- `planning_router.py:119-185` — fonction `resequence_and_recalc()` existe.
- Cascade ETD/ETA des legs en aval quand on modifie un leg amont.
- Préserve les legs achevés (ATD posé).
- Recalcule ETA depuis ETD + paramètres de navigation (`default_speed`, `default_elongation`).
- Triggers : `planning_router.py:663` (édition leg) et `escale_router.py:205-225` (changement ATA/ATD escale).

### Gaps ⚠️

| # | Gap | Impact | Cible V2 |
|---|-----|--------|----------|
| P-1 | La cascade ne propage pas vers `EscaleOperation.planned_start/end` ni `DockerShift.planned_start/end` | Données opérationnelles stale après décalage | `propagate_dates_to_operations(leg_id)` post-recalc |
| P-2 | Pas de propagation vers `Order.delivery_date_start/end` | Engagements client erronés après retard | Notification client auto (email + portal) |
| P-3 | Pas de propagation vers `PackingListBatch.loading_date` | BL incohérents | Recalc batch dates |
| P-4 | Pas de notification temps réel aux modules couplés (crew, finance, cargo) | L'utilisateur ne voit pas l'impact aval | `DateShiftEvent` + push WebSocket / toast |
| P-5 | Vue Gantt mono-navire | Conflits port invisibles dans la vue principale | Refonte UI multi-navires (cf. `mockups.md` §2) |

### Fichiers à toucher
- `app/routers/planning_router.py:540-670` (édition leg).
- `app/routers/escale_router.py:205-225` (`propagate_from_leg`).
- Nouveau : `app/services/date_propagation.py` (logique partagée).
- Nouveau : `app/models/onboard.py` → `DateShiftEvent` (ou réutiliser `ETAShift` existant).

## 2. Escale Import / Export

### État actuel ❌ aucun
- `EscaleOperation.operation_type` (enum : `relations_externes`, `technique`, `armement`) — orienté métier mais pas direction cargo.
- `EscaleOperation.action` (`relation_presse`, `soutage`, `embarquement`, `debarquement`, `avitaillement`...) — `embarquement`/`debarquement` portent une sémantique direction mais ne sont pas typés et pas filtrables proprement.
- `DockerShift` — aucune notion de direction.
- Templates `escale/index.html` — timeline unique, pas de séparation visuelle.

### Inférence possible (provisoire)
- **Import** : palette dont `PackingListBatch.pod_code == leg.arrival_port.locode`.
- **Export** : palette dont `PackingListBatch.pol_code == leg.departure_port.locode`.

### Cible V2

| Action | Détail |
|--------|--------|
| Migration | `ALTER TABLE escale_operations ADD COLUMN direction VARCHAR(10);` (`IMPORT`, `EXPORT`, `BOTH`) |
| Migration | `ALTER TABLE docker_shifts ADD COLUMN direction VARCHAR(10);` |
| Backfill | Inférence depuis `action` (`embarquement`→EXPORT, `debarquement`→IMPORT) |
| UI | Vue escale en 2 colonnes (cf. `mockups.md` §3) |
| Routes | `/escale/{leg_id}/import`, `/escale/{leg_id}/export` (raccourcis filtrants) |
| KPI | Compteurs distincts palettes import / export par escale |

### Fichiers à toucher
- `app/models/operation.py:16-86` (ajout colonnes).
- `app/routers/escale_router.py:258-400` (queries filtrées par direction).
- `app/templates/escale/index.html` (refonte layout 2 col).
- Nouveau : `app/services/escale_direction.py` (inférence + helpers).

## 3. Concordance inter-modules

### Risque CRITIQUE identifié

| Module | Modèle | FK leg | Champs date | Risque |
|--------|--------|--------|-------------|--------|
| Finance | `LegFinance` | unique | aucun | ✅ safe (pas de date dupliquée) |
| KPI | `LegKPI` | unique | aucun | ✅ safe |
| Escale | `EscaleOperation` | nullable | `planned_start/end`, `actual_start/end` | ❌ **STALE** — non-recalc |
| Escale | `DockerShift` | nullable | `planned_start/end`, `actual_start/end` | ❌ **STALE** |
| Cargo | `Order` | nullable | `delivery_date_start/end` | ❌ **STALE** |
| Cargo | `PackingListBatch` | indirect (Order→Leg) | `loading_date` | ❌ **STALE** |
| Onboard | `ETAShift` | yes | snapshots `old/new` | ✅ safe (historique) |
| Onboard | `SofEvent` | yes | `event_date/time` | ✅ safe (événements indépendants) |
| Crew | `CrewTicket` | yes | `ticket_date` | ✅ safe (validé contre leg dates) |

### Approche V2 recommandée — option (a) snapshots + recalc-on-query

- Ne pas stocker de `planned_start/end` absolus dans `EscaleOperation` / `DockerShift`.
- Stocker `offset_from_leg_start_seconds` (relatif à `leg.eta`).
- Calculer la date absolue à l'affichage : `leg.eta + timedelta(seconds=offset)`.
- Un changement d'ETA n'invalide plus les enfants — cohérence garantie par construction.

### Approche V2 alternative — option (b) triggers applicatifs

- Hook SQLAlchemy `event.listen(Leg, 'after_update', recalc_children)` qui recalcule `EscaleOperation`/`DockerShift`/`Order` enfants à chaque update.
- Plus invasif, plus de magie — mais préserve l'API actuelle.

**Recommandation** : option (a) pour les nouvelles tables / nouveau modèle, option (b) en complément pour les tables existantes pendant la transition.

## Audit synthétique des autres routeurs

| Routeur | Lignes | Verdict | Observation V2 |
|---------|--------|---------|----------------|
| `planning_router.py` | 1093 | 🟡 à refactor | Split en `planning_router` + `gantt_router` + `share_router`. Extraire `resequence_and_recalc` en service. |
| `escale_router.py` | 985 | 🟡 à refactor | Direction Import/Export. Extraire `propagate_from_leg` en service. |
| `onboard_router.py` | 1840 | 🔴 trop gros | Split en 4 (escale/nav/cargo/crew) — cf. `../captain/onboard-v2-spec.md`. |
| `cargo_router.py` | 1743 | 🔴 trop gros | Split par sous-domaine (orders, packing-list, portal-client, BL). |
| `pricing_router.py` | 1318 | 🟡 | Splitter pricing client / pricing cabin (passenger). |
| `mrv_router.py` | 969 | 🟢 OK | Stable. |
| `kpi_router.py` | 962 | 🟢 OK | Stable. |
| `commercial_router.py` | 621 | 🟢 OK | Vérifier intégration Pipedrive. |
| `crew_router.py` | 773 | 🟢 OK | Stable. |
| `stowage_router.py` | 641 | 🟢 OK | Stable. |
| `passenger_router.py` | 1009 | ⚪ désactivé | Code conservé, accès retiré (Phase 1). |
| `passenger_ext_router.py` | 557 | ⚪ désactivé | Idem. |
| `claim_router.py` | 300 | 🟢 OK | Sélecteur passagers gating-flag (Phase 1). |
| `finance_router.py` | 452 | 🟡 | `compute_pax_revenue_for_leg` retourne 0 si flag off (Phase 1). |
| `dashboard_router.py` | 439 | 🟡 à refondre | Cible bento (cf. `../ux/mockups.md` §1). |
| `tracking_router.py` | 446 | 🔴 sécurité | Auth manquante (cf. `../security/audit-v1.md` S-04). |
| `admin_router.py` | 2045 | 🔴 trop gros | Split en `admin_router` + `account_router` (déjà partiel) + `settings_router` + `data_export_router`. |
| `auth_router.py` | 125 | 🟠 à étendre | Ajout `/change-password` + middleware `must_change_password` (Sprint 1 sécurité). |

## Fichiers de service à créer (V2)

- `app/services/date_propagation.py` — recalc cascade + sync modules couplés.
- `app/services/escale_direction.py` — inférence Import/Export.
- `app/services/notifications.py` — push WebSocket + email.
- `app/services/feature_flags.py` — résolution `feature_enabled(key, user)`.
- `app/services/chatbot.py` — orchestration Claude API + RAG.
- `app/services/ticketing.py` — workflow open→resolved + SLA escalade.
