# SPEC d'implémentation — Reprise P0 du module Cargo / Bill of Lading

**Module :** Cargo / Packing list / Bill of Lading / Portail expéditeur
**Périmètre :** tickets P0 du backlog — CARGO‑01, CARGO‑02, CARGO‑03, CARGO‑04, CARGO‑05, CARGO‑06 (+ SEC‑02)
**Arbitrage applicable :** **A6 = portail token riche + espace `/me`** (le portail token reste un espace de travail complet ; SEC‑02 devient obligatoire).
**Statut :** spécification prête à coder. Toute reprise respecte la *Definition of Done* du backlog (Kairos/Manrope, CSP‑strict, `require_permission`, `db.flush()`, audit, i18n, migration Alembic, tests).

---

## 0. État réel du code V3 (vérifié)

| Élément | Fichier | Constat |
|---|---|---|
| Modèles | `app/models/packing_list.py` | `PackingListBatch` **appauvri** (16 colonnes : pas d'adresses, pas de `type/description_of_goods`, pas de n° BL). `PackingListAudit` **existe** (schéma V3 : `actor`, `actor_name`, `field`, `old_value`, `new_value`, `at`). `PackingListDocument` **existe** avec `packing_list_id` **déjà nullable‑FK** (kinds bl/arrival_notice/invoice/customs/msds/other). `PortalMessage.is_read` présent mais non exploité. |
| BL / PDF | `app/services/pdf_generator.py` (l.68) | `render_bill_of_lading(*, booking, leg, vessel, pol, pod, client)` → lit `booking.items`. **Ne lit jamais `PackingListBatch`.** Renvoie `DocumentBytes(html, pdf, filename, mime)`. Idem `render_packing_list` (l.85). |
| Staff PL | `app/routers/cargo_packing_router.py` | Liste / détail / `from-order` / **add‑batch seul** / lock / unlock / messages. **Pas d'édition ni suppression de batch, pas de vue history, pas de BL/Arrival Notice.** |
| Génération doc staff | `app/routers/cargo_router.py` | BL/PL/invoice/anemos générés **depuis le booking** (`/cargo/booking/{ref}/bl.pdf`). Déconnecté des packing lists. |
| Portail | `app/routers/cargo_portal_router.py` | home / packing (add‑batch seul) / messages / privacy. **Pas d'upload de documents, pas de rate‑limit** (le docstring l'annonce mais `_load_or_410` ne l'appelle pas). |
| Service PL | `app/services/packing_list.py` | `get_by_token`, `record_audit`, `can_modify`, `lock`/`unlock`, `log_portal_access`. |
| Upload sûr | `app/services/safe_files.py` | `save_upload(content, original_name, *, subdir) -> (rel_path, mime)` + `resolve_path`. **Réutilisable tel quel.** |
| Rate‑limit | `app/services/rate_limit.py` | `exceeded(db, *, scope, identifier, max_attempts, window_minutes)` + `record(...)`. **Réutilisable tel quel.** |

Référence V2 (à porter) : `/tmp/oldver/mytowt-main/app/models/packing_list.py` (champs riches + `compute_dimensions`), `/tmp/oldver/mytowt-main/app/routers/cargo_router.py` (BL `TUAW_{voyage}_{seq:03d}`, Arrival Notice, portal docs).

---

## 1. Modèle de données — changements (1 migration Alembic)

### 1.1 `PackingListBatch` — colonnes à ajouter (toutes `nullable`, additif, non destructif)

```python
# app/models/packing_list.py — classe PackingListBatch

# --- Parties (BL : mentions obligatoires) [CARGO-02] ---
shipper_name:      Mapped[str | None]  = mapped_column(String(200))
shipper_address:   Mapped[str | None]  = mapped_column(Text)
shipper_postal:    Mapped[str | None]  = mapped_column(String(20))
shipper_city:      Mapped[str | None]  = mapped_column(String(100))
shipper_country:   Mapped[str | None]  = mapped_column(String(100))
notify_name:       Mapped[str | None]  = mapped_column(String(200))
notify_address:    Mapped[str | None]  = mapped_column(Text)
notify_postal:     Mapped[str | None]  = mapped_column(String(20))
notify_city:       Mapped[str | None]  = mapped_column(String(100))
notify_country:    Mapped[str | None]  = mapped_column(String(100))
consignee_name:    Mapped[str | None]  = mapped_column(String(200))
consignee_address: Mapped[str | None]  = mapped_column(Text)
consignee_postal:  Mapped[str | None]  = mapped_column(String(20))
consignee_city:    Mapped[str | None]  = mapped_column(String(100))
consignee_country: Mapped[str | None]  = mapped_column(String(100))

# --- Marchandise (BL) [CARGO-02 minimal] ---
type_of_goods:         Mapped[str | None] = mapped_column(String(200))
description_of_goods:  Mapped[str | None] = mapped_column(Text)

# --- Numérotation Bill of Lading [CARGO-01] ---
bl_number:    Mapped[str | None]      = mapped_column(String(50), index=True)  # ex. TUAW_1CFRBR6_001
bl_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

> Les champs goods étendus (`bio_products`, `cases_quantity`, `units_per_case`,
> `cargo_value_usd`, `surface_m2`/`volume_m3`/`density`) et les références
> (`customer_name`, `freight_forwarder`, `code_transitaire`, `po_number`,
> `ams_hbl_id`, `isf_date`) sont en **CARGO‑13 (P1)** — hors de cette spec P0.

### 1.2 `completion_pct` — rétablir la complétude documentaire

Remplacer le calcul V3 (« % batches avec poids > 0 ») par une complétude sur les
champs requis du connaissement (adapté du V2) :

```python
_REQUIRED_FIELDS = (
    "shipper_name", "shipper_address", "shipper_city", "shipper_country",
    "consignee_name", "consignee_address", "consignee_city", "consignee_country",
    "type_of_goods", "pallet_count", "weight_kg",
)
@property
def completion_pct(self) -> int:
    if not self.batches:
        return 0
    total = len(self.batches) * len(_REQUIRED_FIELDS)
    filled = sum(
        1 for b in self.batches for f in _REQUIRED_FIELDS
        if (v := getattr(b, f, None)) is not None and str(v).strip()
    )
    return round(100 * filled / total) if total else 0
```

### 1.3 `PackingListDocument` — réintroduire `file_size` / `notes` (optionnel, P2)
Hors P0. Pour CARGO‑06, les colonnes existantes (`packing_list_id`, `kind`, `label`,
`file_path`, `file_mime`, `uploaded_by`) **suffisent**.

### 1.4 Migration Alembic
- 1 révision : `ALTER TABLE packing_list_batches ADD COLUMN ...` pour les 19 colonnes
  ci‑dessus + index sur `bl_number`. Toutes nullables → **migration sûre, aucune reprise
  de données nécessaire** (base V3 récente).
- `downgrade` : `DROP COLUMN` symétrique.

---

## 2. CARGO‑02 — Adresses structurées + description marchandise

**Cible :** modèle (§1.1), formulaires staff & portail, audit.

1. **Formulaire staff** (`templates/staff/cargo/packing_list_detail.html`) : ajouter au
   formulaire d'ajout/édition de batch les 15 champs d'adresses (3 blocs shipper/notify/
   consignee) + `type_of_goods` + `description_of_goods`. Regrouper en `<fieldset>` Kairos.
2. **Formulaire portail** (`templates/portal/packing.html`) : mêmes champs côté expéditeur
   (c'est lui qui les renseigne) ; sections visuellement séparées, aide contextuelle.
3. **Routes** : étendre les signatures `add_batch` (staff l.128) et `portal_packing_add`
   (portail l.124) avec les nouveaux `Form(...)` ; idem les routes d'édition (CARGO‑03).
4. **Critère d'acceptation :** les 15 champs + 2 goods sont saisissables des deux côtés et
   repris par le BL (CARGO‑01) et l'Arrival Notice (CARGO‑05) ; `completion_pct` recalculé.

---

## 3. CARGO‑03 — Édition + suppression de batch (staff & portail) avec audit

**Helper d'audit field‑by‑field** (nouveau, `app/services/packing_list.py`) :

```python
# Champs éditables soumis à l'audit
AUDITABLE_FIELDS = (
    "pallet_format", "pallet_count", "description", "hs_code", "weight_kg",
    "cubage_m3", "length_cm", "width_cm", "height_cm", "hazardous", "imdg_class",
    "un_number", "stackable", "marks_and_numbers",
    "shipper_name", "shipper_address", "shipper_postal", "shipper_city", "shipper_country",
    "notify_name", "notify_address", "notify_postal", "notify_city", "notify_country",
    "consignee_name", "consignee_address", "consignee_postal", "consignee_city",
    "consignee_country", "type_of_goods", "description_of_goods",
)

async def apply_batch_update(
    db, *, batch, new_values: dict, actor: str, actor_name: str | None
) -> int:
    """Applique les changements en traçant chaque champ modifié. Retourne le nb de champs modifiés."""
    changed = 0
    for field in AUDITABLE_FIELDS:
        if field not in new_values:
            continue
        old = getattr(batch, field)
        new = new_values[field]
        if old == new:
            continue
        setattr(batch, field, new)
        await record_audit(
            db, packing_list_id=batch.packing_list_id, batch_id=batch.id,
            actor=actor, actor_name=actor_name,
            field=field, old_value=old, new_value=new,
        )
        changed += 1
    return changed
```

**Routes staff** (`cargo_packing_router.py`, perm `cargo:M` pour edit, `cargo:S` pour delete) :
```
POST /cargo/packing-lists/{pl_id}/batches/{batch_id}/edit     # apply_batch_update(actor="staff")
POST /cargo/packing-lists/{pl_id}/batches/{batch_id}/delete   # guard can_modify ; audit field="_delete_batch"
```
**Routes portail** (`cargo_portal_router.py`, accès token, guard `can_modify`) :
```
POST /p/{token}/packing/batches/{batch_id}/edit               # apply_batch_update(actor="client")
POST /p/{token}/packing/batches/{batch_id}/delete
```

- **Garde :** refuser (409) si `not can_modify(pl)` (PL verrouillée).
- **Sécurité portail :** vérifier que `batch.packing_list_id == pl.id` (un token ne peut
  éditer qu'un batch de SA packing list).
- **UI :** `templates/portal/packing.html` et `staff/cargo/packing_list_detail.html` passent
  d'une table lecture seule à des lignes éditables (bouton ✎ → formulaire pré‑rempli, bouton 🗑).
- **Critère d'acceptation :** édition/suppression possible tant que non verrouillé ; chaque
  champ modifié écrit une entrée `PackingListAudit`. **NRT : P3a #2, P3b #1.**

---

## 4. CARGO‑04 — Vue audit / historique

- **Route** (`cargo_packing_router.py`, perm `cargo:C`) :
  `GET /cargo/packing-lists/{pl_id}/history`.
- **Requête :** `select(PackingListAudit).where(packing_list_id==pl_id).order_by(at.desc())`.
- **Template** (nouveau) : `templates/staff/cargo/packing_list_history.html` — table
  `at` / `actor`+`actor_name` / `batch_id` / `field` / `old_value` → `new_value`.
- **Lien** depuis `packing_list_detail.html` (« Historique »).
- **Critère d'acceptation :** toutes les modifications tracées (CARGO‑03) sont visibles,
  antéchronologiques. **NRT : P3a #2.**

---

## 5. CARGO‑01 — Reconnecter le Bill of Lading à la packing list

### 5.1 Résolveur de contexte PL → voyage
Nouveau helper `app/services/packing_list.py` (gère rail A *order* et rail B *booking*) :
```python
async def resolve_pl_context(db, pl: PackingList):
    """Retourne (order, booking, leg, vessel, pol, pod) à partir d'une PL."""
    order   = await db.get(Order, pl.order_id)     if pl.order_id   else None
    booking = await db.get(Booking, pl.booking_id) if pl.booking_id else None
    leg_id  = (order.leg_id if order else None) or (booking.leg_id if booking else None)
    leg     = await db.get(Leg, leg_id) if leg_id else None
    vessel  = await db.get(Vessel, leg.vessel_id) if leg else None
    pol     = await db.get(Port, leg.departure_port_id) if leg else None
    pod     = await db.get(Port, leg.arrival_port_id)   if leg else None
    return order, booking, leg, vessel, pol, pod
```

### 5.2 Numérotation BL persistante + anti‑doublon par leg
```python
async def assign_bl_number(db, pl: PackingList, batch: PackingListBatch, leg) -> str:
    """Affecte (idempotent) un numéro TUAW_{leg_code}_{seq:03d}. Anti-doublon par leg."""
    if batch.bl_number:
        return batch.bl_number
    voyage = (leg.leg_code if leg and leg.leg_code else "NA")
    # seq = nb de BL déjà émis sur ce leg (toutes PL du leg) + 1
    seq = await _count_issued_bls_for_leg(db, leg_id=leg.id) + 1 if leg else 1
    batch.bl_number = f"TUAW_{voyage}_{seq:03d}"
    batch.bl_issued_at = datetime.now(UTC)
    await db.flush()
    return batch.bl_number
```
> `_count_issued_bls_for_leg` : compter les `PackingListBatch.bl_number IS NOT NULL` dont la
> PL parente pointe (via order/booking) sur ce `leg.id`. Implémentation : sous‑requête sur
> `packing_lists` jointe à `commercial_orders`/`bookings` filtrées `leg_id`.

### 5.3 Rendu BL depuis la packing list
Nouvelle fonction `app/services/pdf_generator.py` :
```python
def render_bill_of_lading_from_pl(*, pl, batch, leg, vessel, pol, pod, bl_number, issued_at) -> DocumentBytes:
    ctx = {
        "pl": pl, "batch": batch, "leg": leg, "vessel": vessel, "pol": pol, "pod": pod,
        "bl_number": bl_number, "issued_at": issued_at,
        "number_of_obl": 3,                     # « Number of Original B/L : 3 »
        "site_url": settings.site_url,
    }
    html, pdf = _render_pdf("pdf/bill_of_lading_pl.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"{bl_number}.pdf")
```
- **Template** (nouveau) `templates/pdf/bill_of_lading_pl.html` (Kairos PDF) : en‑tête B/L,
  parties **depuis le batch** (shipper/consignee/notify), marchandise (`type_of_goods`,
  `description_of_goods`, marks, HS, IMDG/UN, poids, palettes), voyage (leg_code, vessel,
  POL/POD), n° BL, « Number of Original B/L : 3 ».

### 5.4 Route staff
```
GET /cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.pdf     # perm cargo:C
```
Logique : charger PL+batch → `resolve_pl_context` → `assign_bl_number` (persiste si absent) →
`render_bill_of_lading_from_pl` → `Response(pdf, media_type, Content-Disposition inline)`.
Optionnel : enregistrer aussi un `PackingListDocument(kind="bl", label=bl_number, ...)` pour
le hub documents.

- **Critère d'acceptation :** un BL reprend les données du **batch** (parties, marchandise,
  poids, palettes) ; numéro `TUAW_{leg_code}_{seq:03d}` persistant ; re‑génération = même
  numéro ; 3 OBL. **NRT : P3a #4.**
- **Note :** l'ancien BL « depuis booking » (`/cargo/booking/{ref}/bl.pdf`) peut subsister
  pour les bookings sans PL, mais le **canal principal** devient le BL depuis la packing list.

---

## 6. CARGO‑05 — Arrival Notice (PDF)

- **Route staff** (`cargo_packing_router.py`, perm `cargo:C`) :
  `GET /cargo/packing-lists/{pl_id}/arrival-notice.pdf`.
- **Service** (`pdf_generator.py`) : `render_arrival_notice(*, pl, batches, leg, vessel, pol, pod)`
  → template `templates/pdf/arrival_notice.html` (en‑tête B/L, **consignee + notify** depuis
  les batches, marchandise, totaux palettes/poids).
- **Critère d'acceptation :** PDF généré depuis la PL ; parties, marchandise et totaux corrects.
  **NRT : P3a #5.**

---

## 7. CARGO‑06 — Upload de documents sur le portail token

**Arbitrage A6 :** le portail token doit le permettre (en plus de `/me`).

- **Routes** (`cargo_portal_router.py`, accès token via `_load_or_410`) :
```
GET    /p/{token}/documents                       # liste
POST   /p/{token}/documents/upload                # UploadFile
GET    /p/{token}/documents/{doc_id}/download
POST   /p/{token}/documents/{doc_id}/delete
```
- **Upload :**
```python
content = await file.read()
try:
    rel_path, mime = save_upload(content, file.filename, subdir="cargo-portal")
except UploadRejected as e:
    raise HTTPException(400, str(e))
db.add(PackingListDocument(
    packing_list_id=pl.id, kind=kind, label=(label or file.filename)[:200],
    file_path=rel_path, file_mime=mime, uploaded_by="client",
))
await db.flush()
```
  - `kind` ∈ {customs, msds, other} (select côté form).
  - Garde‑fous : `safe_files` valide extension/taille/magic‑bytes (≤ 20 Mo) ; refuser si
    `not can_modify(pl)` selon politique (a priori autorisé même submitted, interdit si locked).
- **Download :** `resolve_path(doc.file_path)` → `FileResponse` ; vérifier
  `doc.packing_list_id == pl.id` (cloisonnement par token).
- **Delete :** vérifier l'appartenance, supprimer l'enregistrement + le fichier
  (`resolve_path(...).unlink(missing_ok=True)`).
- **Template** (nouveau) `templates/portal/documents.html` (drag‑drop + liste + download + delete).
- **Nav portail** (`templates/portal/_layout.html`) : ajouter l'entrée « Documents ».
- **Critère d'acceptation :** dépôt/lecture/suppression d'un document via le lien token.
  **NRT : P3b #2.**

---

## 8. SEC‑02 — Rate‑limit du portail token (obligatoire avec A6)

Dans `cargo_portal_router._load_or_410`, **avant** toute résolution :
```python
ip = _client_ip(request)
if await rate_limit.exceeded(db, scope="portal_token", identifier=ip,
                             max_attempts=60, window_minutes=10):
    raise HTTPException(429, "Trop de requêtes")
await rate_limit.record(db, scope="portal_token", identifier=ip)
```
- Seuil indicatif : 60 hits / 10 min / IP (à ajuster). Sur **token invalide** (410), le record
  compte aussi → freine le balayage de tokens.
- **Critère d'acceptation :** marteler un token → 429 temporaire ; accès normal inchangé.

---

## 9. Templates — récapitulatif

| Template | Action |
|---|---|
| `staff/cargo/packing_list_detail.html` | + édition/suppression batch (CARGO‑03), + champs adresses/goods (CARGO‑02), + liens BL/Arrival Notice/Historique |
| `staff/cargo/packing_list_history.html` | **nouveau** (CARGO‑04) |
| `portal/packing.html` | + édition/suppression batch + champs adresses/goods |
| `portal/documents.html` | **nouveau** (CARGO‑06) |
| `portal/_layout.html` | + entrée nav « Documents » |
| `pdf/bill_of_lading_pl.html` | **nouveau** (CARGO‑01) |
| `pdf/arrival_notice.html` | **nouveau** (CARGO‑05) |

Contraintes : tout en classes Kairos, **aucun `<script>` inline** (le drag‑drop documents
passe par un fichier JS externe whitelisté CSP, ou un simple `<input type=file>` sans JS pour le P0).

---

## 10. i18n

- Ajouter les clés des nouveaux écrans (libellés adresses, goods, documents, historique, BL)
  aux 5 catalogues `app/i18n/{fr,en,es,pt_br,vi}.py`.
- Le **multilingue du portail** (rendu effectif des templates portail dans la langue choisie)
  est le ticket **CARGO‑12 (P1)** ; en P0, ajouter les clés FR et structurer pour i18n.

---

## 11. Tests

**Unitaires (`tests/unit`)**
- `assign_bl_number` : idempotence (2ᵉ appel = même numéro) ; incrément par leg ; format `TUAW_{leg_code}_{seq:03d}`.
- `apply_batch_update` : n'écrit un audit que pour les champs réellement modifiés ; ignore les valeurs inchangées.
- `completion_pct` : 0 sans batch ; 100 quand tous les champs requis remplis.

**Intégration (`tests/integration`)**
- Staff : create PL → add batch → edit batch (audit créé) → delete batch → history affiche les entrées.
- BL : générer le BL d'un batch → 200 PDF, `bl_number` persisté ; re‑générer → même numéro.
- Arrival Notice : 200 PDF avec consignee/notify.
- Portail : upload document (200) → download (200) → delete (200) ; upload type interdit → 400.
- SEC‑02 : N+1 hits sur un token → 429.
- Verrou : PL `locked` → edit/delete/upload batch refusés (409).

**Non‑régression persona** : dérouler intégralement **P3a** (#2,#3,#4,#5,#6) et **P3b** (#1,#2)
de `TESTS-NON-REGRESSION-PERSONAS.md`.

---

## 12. Séquencement intra‑module & estimation

```
1. Migration + modèle (CARGO-02 §1)                         [M]  ── socle
2. apply_batch_update + édition/suppression (CARGO-03)      [M]  ── dépend de 1
3. Vue history (CARGO-04)                                   [S]  ── dépend de 2
4. resolve_pl_context + assign_bl_number + BL (CARGO-01)    [L]  ── dépend de 1
5. Arrival Notice (CARGO-05)                                [M]  ── dépend de 1
6. Portail documents (CARGO-06) + SEC-02                    [M]  ── indépendant (faisable en //)
```
Chemin critique : 1 → 4 (BL). Les étapes 3, 5, 6 sont parallélisables après 1‑2.

---

## 13. Points de vigilance

- **XOR order/booking** (`ck_packing_lists_order_xor_booking`) : `resolve_pl_context` gère les
  deux rails — ne jamais supposer `order_id` non nul.
- **Cloisonnement portail** : toute route `/p/{token}/...` doit vérifier que l'objet manipulé
  (batch, document) appartient bien à la PL du token.
- **WeasyPrint** : import paresseux (déjà en place) ; eager‑load des relations avant rendu
  pour éviter tout lazy‑load dans le moteur PDF.
- **Cohérence `CLAUDE.md`** : une fois ce module repris, mettre à jour le statut Cargo
  (aujourd'hui « ✅ batches, audit, lock, messagerie » — inexact) (ticket EVO‑06).
- **Ne pas régresser les gains V3** : socle order⊕booking, services testables, `safe_files`
  durci, intégration `ship_map` du portail (confidentialité inter‑clients), PDF WeasyPrint.
