# SPEC d'implémentation — Reprise P0 du module Escale

**Module :** Escale / Port call (opérations portuaires, shifts dockers, statut portuaire)
**Périmètre :** tickets P0 — ESC‑01 (édition/suppression), ESC‑02 (statut portuaire / ATA‑ATD + propagation + finance + notifications), ESC‑03 (saisie heures réelles) ; + champs P1 couplés ESC‑04 (intervenant/durées) et ESC‑05 (productivité dockers), et ESC‑07 (fuseau).
**Arbitrage applicable :** **A3 = blocage configurable par zone** (concerne surtout Stowage ; ici sans impact direct). Réutilise les services V3 existants — pas de logique dupliquée.
**Statut :** spécification prête à coder. Respecte la *Definition of Done* du backlog (Kairos/Manrope, CSP‑strict, `require_permission`, `db.flush()`, audit, i18n, migration, tests).

---

## 0. État réel du code V3 (vérifié)

| Élément | Fichier | Constat |
|---|---|---|
| Modèles | `app/models/escale.py` | `EscaleOperation` : leg_id, direction, operation_type, action, label, notes, planned_start/end, actual_start/end, status, cost_forecast/actual, created_at. **Manque** `intervenant`, durées, lien crew. `DockerShift` : leg_id, direction, hold, company, nb_dockers, **palettes_target**, **palettes_done**, planned/actual_start/end, cost_eur, notes. **Manque** les propriétés de cadence. |
| Router | `app/routers/escale_router.py` | create operation / **start (now)** / **end (now)** / create docker / progress / lock / unlock / sof.pdf. **Pas d'édition ni de suppression** (op & shift). **Pas de flux statut portuaire / ATA‑ATD.** Garde `_assert_escale_unlocked` en place. Hook SOF `_sync_sof_from_operation` (FLX‑04) à réutiliser. |
| Leg | `app/models/leg.py` | `ata`, `atd`, `status`, `escale_locked_at/by`, `distance_nm`, `transit_speed_kn`, `elongation_coef`, `port_stay_planned_hours`. |
| Cascade | `app/services/date_cascade.py` | `cascade_from_leg(db, leg, *, delta: timedelta) -> dict` (legs aval + escale ops + shifts + notif clients). |
| Finance | `app/services/finance_rollup.py` | `rollup_for_leg(db, leg) -> LegFinance` (signature stable, get‑or‑create + recalcul). |
| Cycle voyage | `app/services/voyage_events.py` | `on_vessel_arrived(db, leg)` (EOSP → ata + bookings → discharged) et `on_vessel_departed(db, leg)` (SOSP → atd + bookings → at_sea). **Idempotents** (n'agissent que si ata/atd est nul). Déjà appelés par captain via les SOF. |
| Notifications | `app/services/notifications.py` | `create(...)`, `notify_eosp(db, leg_code, leg_id)`, `notify_sosp(db, leg_code, leg_id)`. |
| Template | `app/templates/staff/escale/index.html` | 2 colonnes (opérations / dockers) + bandeau lock. À enrichir. ⚠️ `staff/escale/detail.html` = **code mort** à supprimer. |

Référence V2 (à porter) : `/tmp/oldver/mytowt-main/app/routers/escale_router.py`
(`update_port_status` l.455, `propagate_from_leg` l.205, `update_finance_actual_duration` l.161,
`handle_crew_assignment` l.227, edit/delete op l.643‑738, edit/delete docker l.791‑880) et
`/tmp/oldver/mytowt-main/app/models/operation.py` (`intervenant`, durées, `planned_rate`/`actual_rate`/`rate_delta_pct`, M2M `operation_crew`).

---

## 1. Modèle de données — changements (1 migration Alembic)

### 1.1 `EscaleOperation` — colonnes à ajouter (nullable, additif)
```python
intervenant:            Mapped[str | None]   = mapped_column(String(200))   # [ESC-04]
planned_duration_hours: Mapped[float | None] = mapped_column()              # [ESC-04]
actual_duration_hours:  Mapped[float | None] = mapped_column()              # [ESC-04]
```
> Le lien opération ↔ équipage (table M2M `operation_crew`) est **ESC‑06** — dépend de
> CREW‑06 (API équipage). Hors de cette spec P0 ; à traiter avec le module Crew.

### 1.2 `DockerShift` — propriétés de cadence (AUCUNE migration)
Les colonnes nécessaires existent déjà (`palettes_target`, `palettes_done`, planned/actual
start/end). Ajouter 3 `@property` (portage direct du V2) :
```python
@property
def planned_rate(self) -> float | None:
    if self.palettes_target and self.planned_start and self.planned_end:
        h = (self.planned_end - self.planned_start).total_seconds() / 3600
        return round(self.palettes_target / h, 1) if h > 0 else 0.0
    return None

@property
def actual_rate(self) -> float | None:
    if self.palettes_done and self.actual_start and self.actual_end:
        h = (self.actual_end - self.actual_start).total_seconds() / 3600
        return round(self.palettes_done / h, 1) if h > 0 else 0.0
    return None

@property
def rate_delta_pct(self) -> float | None:
    pr, ar = self.planned_rate, self.actual_rate
    return round((ar - pr) / pr * 100, 1) if (pr and ar and pr > 0) else None
```

### 1.3 Migration Alembic
1 révision : `ALTER TABLE escale_operations ADD COLUMN intervenant / planned_duration_hours /
actual_duration_hours` (nullables). `downgrade` symétrique. **Sûre, additive.**

---

## 2. ESC‑01 — Édition + suppression des opérations et des shifts

### 2.1 Opérations
**Routes** (`escale_router.py`) :
```
POST /escale/operations/{op_id}/edit     # perm escale:M
POST /escale/operations/{op_id}/delete   # perm escale:S
```
- `edit` : `Form(...)` pour tous les champs éditables — `direction`, `operation_type`,
  `action`, `label`, `notes`, `planned_start/end`, **`actual_start/end` (ESC‑03)**, `intervenant`,
  `cost_forecast/actual`, `status`. Parser les datetimes via le helper TZ (ESC‑07).
- Recalcule `planned_duration_hours`/`actual_duration_hours` si bornes présentes.
- **Garde :** `_assert_escale_unlocked(leg)` (escale verrouillée → 400) ; 404 si op absente.
- Re‑synchronise le SOF : appeler `_sync_sof_from_operation(db, request, user, op)` après édition.
- `delete` : `_assert_escale_unlocked` puis `await db.delete(op)` ; `activity_record(action="delete")`.

### 2.2 Shifts dockers
**Routes** :
```
POST /escale/dockers/{shift_id}/edit     # perm escale:M
POST /escale/dockers/{shift_id}/delete   # perm escale:S
```
- `edit` : `direction`, `company`, `nb_dockers`, `palettes_target`, **`palettes_done`**, `hold`
  (`_normalize_hold`), `planned_start/end`, **`actual_start/end`**, `cost_eur`, `notes`.
- Mêmes gardes (`_assert_escale_unlocked`, 404) + `activity_record`.

### 2.3 UI (`staff/escale/index.html`)
- Sur chaque ligne d'opération/shift : bouton ✎ (ouvre un formulaire pré‑rempli — modale Kairos
  ou ligne éditable, **sans `<script>` inline** ; HTMX `hx-get` d'un fragment ou `<details>`),
  bouton 🗑 (POST delete avec confirm via `forms.js`).
- **Critère d'acceptation :** éditer/supprimer une opération et un shift hors escale verrouillée ;
  audit tracé. **NRT : P4 #3.**

---

## 3. ESC‑03 — Saisie manuelle des heures réelles

- Couvert par les formulaires d'édition (§2) : `actual_start` / `actual_end` deviennent des
  champs **datetime éditables** (avec fuseau, ESC‑07), pas seulement `now()` via Démarrer/Terminer.
- Conserver les boutons Démarrer/Terminer (raccourci `now()`) **et** permettre l'édition manuelle.
- Mettre à jour `status` cohéremment : `actual_start` sans `actual_end` → `in_progress` ;
  `actual_end` → `completed`.
- **Critère d'acceptation :** saisir a posteriori une heure réelle arbitraire. **NRT : P4 #4.**

---

## 4. ESC‑02 — Statut portuaire / pose ATA‑ATD (+ propagation, finance, notifications)

> **Principe d'architecture :** réutiliser les helpers V3 partagés avec le commandant
> (`voyage_events.on_vessel_arrived/departed`) pour que **escale et captain restent cohérents**
> (source unique, idempotent). L'agent d'escale apporte l'**UI de saisie horodatée** + le
> déclenchement de la **cascade**, du **rollup finance** et des **notifications**.

### 4.1 Route
```
POST /escale/legs/{leg_id}/port-status     # perm escale:M
  Form: new_status ∈ {"a_quai", "pilote_depart"}, status_time (datetime + tz via ESC-07)
```

### 4.2 Logique
```python
leg = await db.get(Leg, leg_id)            # 404 si absent
_assert_escale_unlocked(leg)
t = parse_tz_datetime(status_time) or datetime.now(UTC)

if new_status == "a_quai":
    leg.ata = t
    leg.status = "in_progress"
    await on_vessel_arrived(db, leg)        # idempotent : ata déjà posée, avance bookings (discharged)
    delta = (t - leg.eta) if leg.eta else timedelta(0)
    await cascade_from_leg(db, leg, delta=delta)     # legs aval + escale ops + shifts + notif clients
    await rollup_for_leg(db, leg)                    # OPEX réel + marge
    await notify_eosp(db, leg.leg_code, leg.id)      # notification arrivée

elif new_status == "pilote_depart":
    if leg.ata is None:                     # garde V2 (F37) — pas d'ATD sans ATA
        raise HTTPException(400, "Renseigner d'abord le statut 'à quai' (ATA).")
    leg.atd = t
    leg.status = "completed"
    await on_vessel_departed(db, leg)       # idempotent : atd posée, avance bookings (at_sea)
    delta = (t - leg.etd) if leg.etd else timedelta(0)
    await cascade_from_leg(db, leg, delta=delta)
    await rollup_for_leg(db, leg)
    await notify_sosp(db, leg.leg_code, leg.id)

await db.flush()
await activity_record(db, action="port_status", module="escale",
                      entity_type="leg", entity_id=leg.id, entity_label=leg.leg_code,
                      detail=f"→ {new_status} @ {t.isoformat()}", ...)
```
- **Override d'horodatage :** poser `leg.ata`/`leg.atd` **avant** d'appeler le hook (qui ne
  fixe la valeur que si nulle) → l'heure choisie par l'agent prime, le hook se limite à
  l'avancement des bookings + log idempotent.
- **De‑dup notifications :** si le commandant a déjà déclenché l'EOSP/SOSP via SOF, la
  notification a pu être émise. Option : émettre la notification **dans** le hook partagé, ou la
  garder ici en acceptant un doublon rare. **Décision recommandée :** déplacer `notify_eosp`/
  `notify_sosp` dans `on_vessel_arrived/departed` (source unique) et ne PAS les rappeler ici.
  À cadrer avec le module Onboard (cohérence captain ↔ escale).

### 4.3 UI (`staff/escale/index.html`)
- **Barre de progression de statut** (3 étapes : pilote arrivée → à quai → pilote départ) avec
  bouton de confirmation **horodaté** (champ datetime + tz) et bouton « Maintenant ».
- Afficher ATA/ATD courants et le résultat de cascade (legs aval impactés) en toast (`HX-Trigger`).
- **Critère d'acceptation :** faire progresser le statut depuis l'escale pose ATA/ATD, propage
  aux legs aval, recalcule l'OPEX réel et notifie. **NRT : P4 #1, #2.**

---

## 5. ESC‑04 / ESC‑05 — Intervenant, durées, productivité dockers (P1, couplés)

- **ESC‑04** : `intervenant` + `planned/actual_duration_hours` (modèle §1.1) ajoutés aux
  formulaires create/edit d'opération et affichés dans la table (`👤 intervenant`, `~Xh` / `Xh`).
- **ESC‑05** : exposer `planned_rate`, `actual_rate`, `rate_delta_pct` (propriétés §1.2) en
  colonnes de la table dockers (cadence pal/h + écart %). **NRT : P4 #5.**

---

## 6. ESC‑07 — Multi‑timezone sur les datetimes (dépend de UX‑01)

- Tous les champs datetime des formulaires escale (port‑status, planned/actual op & shift)
  utilisent le partial `staff/_time_input.html` (UTC / Paris / Port local) défini par **UX‑01**.
- Helper `parse_tz_datetime(value)` côté router pour convertir la saisie locale en UTC.
- **Dépend de :** UX‑01 (Lot 1 Design). **NRT : P4 #6.**

---

## 7. Nettoyage

- **Supprimer** `app/templates/staff/escale/detail.html` (code mort : routes/variables inexistantes).

---

## 8. Templates — récapitulatif

| Template | Action |
|---|---|
| `staff/escale/index.html` | + barre statut portuaire horodatée (ESC‑02) ; + boutons ✎/🗑 op & shift (ESC‑01) ; + champs intervenant/durées (ESC‑04) ; + colonnes cadence dockers (ESC‑05) ; champs datetime → `_time_input` (ESC‑07) |
| `staff/escale/_op_form.html` | **nouveau** (fragment édition opération, HTMX) |
| `staff/escale/_shift_form.html` | **nouveau** (fragment édition shift) |
| `staff/escale/detail.html` | **supprimer** (code mort) |

Contraintes : classes Kairos, **aucun `<script>` inline** (édition via HTMX `hx-get`/`hx-post`
de fragments + `forms.js` pour l'anti‑double‑submit et les confirmations de suppression).

---

## 9. i18n
Ajouter aux 5 catalogues (`fr/en/es/pt_br/vi`) les libellés : statut portuaire (pilote
arrivée / à quai / pilote départ), intervenant, durée prévue/réelle, cadence (pal/h), écart %,
boutons éditer/supprimer.

---

## 10. Tests

**Unitaires**
- `DockerShift.planned_rate/actual_rate/rate_delta_pct` : valeurs nominales, division par zéro, None si bornes manquantes.
- `parse_tz_datetime` : conversion UTC/Paris/Port local.

**Intégration**
- Édition d'une opération (tous champs, dont `actual_start` manuel) → persistée + SOF re‑synchronisé ; suppression → 200, op absente.
- Édition/suppression d'un shift.
- Escale **verrouillée** → edit/delete/port‑status refusés (400).
- `port-status a_quai` : pose `ata`, status `in_progress`, legs aval décalés (cascade), `LegFinance` recalculé, notification EOSP émise, bookings → discharged.
- `port-status pilote_depart` sans ATA → 400 ; avec ATA → pose `atd`, status `completed`, bookings → at_sea, notification SOSP.
- Idempotence : double `port-status` même statut → pas de double avancement de bookings (hooks idempotents).

**Non‑régression persona** : dérouler **P4** (#1→#6) de `TESTS-NON-REGRESSION-PERSONAS.md`.

---

## 11. Séquencement intra‑module & estimation

```
1. Migration + modèle (ESC-04 cols) + propriétés cadence (ESC-05)   [S]
2. Édition/suppression op & shift (ESC-01) + heures manuelles (ESC-03) [M]  ── dépend de 1
3. Statut portuaire / ATA-ATD (ESC-02)                               [L]  ── réutilise services V3
4. Timezone (ESC-07)                                                 [S]  ── dépend de UX-01
5. Suppression code mort detail.html                                 [XS]
```
Chemin critique : 1 → 3 (ESC‑02). ESC‑06 (couplage crew + billetterie + PAF) est **différé** au
module Crew (dépend de CREW‑06).

---

## 12. Points de vigilance

- **Cohérence captain ↔ escale :** ATA/ATD et avancement des bookings passent par les **mêmes**
  helpers `voyage_events` que le commandant — ne pas dupliquer la logique ni les notifications
  (cf. §4.2 de‑dup). À cadrer conjointement avec la spec Onboard.
- **Cascade :** `cascade_from_leg` attend un `delta` (timedelta) ; calculer `delta = t − eta`
  (arrivée) / `t − etd` (départ). Best‑effort, ne lève pas — le résultat (legs impactés) est
  affiché en toast.
- **Verrouillage :** toutes les nouvelles écritures passent par `_assert_escale_unlocked`.
- **Gains V3 à préserver :** SOF auto (FLX‑04), occupation par cale (B3), verrouillage tracé,
  PDF SOF WeasyPrint, rollup finance, cascade élargie.
- **CLAUDE.md :** après reprise, le statut Escale (« ✅ operations + dockers + lock ») reste
  valable mais devra mentionner le pilotage ATA/ATD restauré.
