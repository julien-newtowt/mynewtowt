# SPEC d'implémentation — Reprise P0 du module Crew / Équipage

**Module :** Crew / Équipage (fiches marins, affectations, compliance Schengen, billets transport, export PAF)
**Périmètre :** tickets P0 — CREW‑01 (édition fiche marin), CREW‑02 (export PDF « Crew List » PAF, WeasyPrint), CREW‑03 (champs morts visa/seaman book/naissance/nationalité), CREW‑04 (édition + suppression d'affectation) ; + P1 couplés CREW‑05 (PJ billet upload/download), CREW‑06 (API équipage par navire JSON), CREW‑08 (anti‑overlap + suppression/désactivation marin).
**Arbitrage applicable :** **A4 = autoriser l'embarquement hors leg** → `CrewAssignment.leg_id` devient **nullable** + affectation directe à un navire (`vessel_id`), comme en V2. Le garde‑fou conformité V3 (Schengen + passeport, override tracé) est **conservé et étendu** au mode « navire seul ».
**Statut :** spécification prête à coder. Respecte la *Definition of Done* du backlog (Kairos/Manrope, CSP‑strict, `require_permission`, `db.flush()` jamais `commit`, `services.activity.record()`, i18n 5 langues, migration Alembic, tests).

---

## 0. État réel du code V3 (vérifié)

| Élément | Fichier (lignes) | Constat |
|---|---|---|
| Modèle `CrewMember` | `app/models/crew.py` (l.24‑52) | Champs **déjà présents** : `full_name`, `role`, `nationality` CHAR(2), `date_of_birth`, `passport_number`/`passport_expires_at`, `schengen_status`/`schengen_days_in_window`/`schengen_window_end`, **`visa_us_expires_at`**, **`visa_br_expires_at`**, **`seaman_book_number`**, **`seaman_book_expires_at`**, `email`, `phone`, `is_active`, `notes`, `marad_id`. **Aucune migration de schéma marin nécessaire — les champs CREW‑03 EXISTENT déjà, ils ne sont juste ni saisis ni édités.** |
| Modèle `CrewAssignment` | `app/models/crew.py` (l.55‑68) | `crew_member_id`, **`leg_id` NOT NULL (FK)**, `role_on_board`, `embark_at`/`disembark_at` (DateTime tz), `embark_port_id`/`disembark_port_id`, `notes`. **Pas de `vessel_id`.** ⇒ A4 impose `leg_id` nullable + ajout `vessel_id`. |
| Modèle `CrewTicket` | `app/models/crew_ticket.py` (l.22‑45) | `crew_member_id` (CASCADE), `assignment_id`, `leg_id`, `mode`, `reference`, `carrier`, `departure_at`/`arrival_at`, `departure_location`/`arrival_location`, `cost_eur`, **`file_path` (présent mais ORPHELIN)**, `notes`. Pas de `file_name`/`file_mime` → garder le nom dérivé du chemin. |
| Router | `app/routers/crew_router.py` | Routes existantes : `GET /crew`(l.63), `POST /crew/sync-marad`(l.156), `GET /crew/compliance`(l.202), `GET /crew/calendar`(l.268), `GET /crew/new`(l.315), **`POST /crew/members`(l.326) = CRÉATION SEULE** (n'expose que full_name/role/nationality/passport_number/passport_expires_at/email/phone — **pas** visa/seaman/naissance), `GET /crew/members/{id}`(l.453), `POST /crew/members/{id}/assignments`(l.469, **création seule, leg_id obligatoire, garde‑fou compliance + override tracé**), `POST /crew/members/{id}/tickets`(l.566, **n'enregistre PAS de fichier**). **Manque : édition marin, désactivation/suppression marin, édition/suppression affectation, PDF PAF, upload/download PJ billet, API JSON par navire.** |
| Service compliance | `app/services/crew_compliance.py` | `refresh_schengen_for_members`/`refresh_member_schengen` (snapshot persisté 90/180), `passport_blocking_reason`, `vessel_readiness`, `normalize_role`, `REQUIRED_ROLES`, `ROLE_LABELS`, `ROLE_SYNONYMS`, `SCHENGEN_COUNTRIES`. **À PRÉSERVER intégralement** — réutilisé par le garde‑fou A4. ⚠️ Plusieurs requêtes y filtrent `CrewAssignment.leg_id` → adapter pour ignorer les affectations « navire seul » (`leg_id IS NULL`). |
| Upload sûr | `app/services/safe_files.py` | `save_upload(content, original_name, *, subdir) -> (rel_path, mime)` + `resolve_path(rel_path)` (anti‑traversal) + `UploadRejected`. **Réutilisable tel quel** (CREW‑05). |
| Templates | `app/templates/staff/crew/` | `index.html` (liste + bordées + alertes + Marad), `detail.html` (fiche + assignment form + tickets, **read‑only sur visa/seaman**), `new.html` (**form de création divergent : rôles EN `captain/chief_mate/ab/...` ≠ enum FR `CREW_ROLES`**, expose date_of_birth/passport mais **pas** visa/seaman, et **n'est pas câblé** — la route POST ignore date_of_birth), `compliance.html`, `calendar.html`. |
| Permissions | `app/permissions.py` via `require_permission("crew", "C"\|"M"\|"S")` | C=Consult, M=Modify, S=Suppress. Conventions V3 : lecture `C`, création/édition `M`, suppression `S`. |
| Migration head | `migrations/versions/20260619_0054_rategrid_multiroutes.py` | `down_revision` de la nouvelle révision = `"20260619_0054"`. |

### Incohérences V3 à corriger au passage
- **`new.html` est mort/divergent** : il poste vers `/crew/new` (qui n'existe pas en POST ; la route POST est `/crew/members`) et propose des rôles anglais hors enum. À **réécrire** en form unifié create/edit (CREW‑01/03).
- **`CrewTicket.file_path` orphelin** : déclaré mais jamais alimenté (CREW‑05).
- **`detail.html`** affiche `a.leg_id` brut et un select leg obligatoire → à adapter pour A4 (navire OU leg).

### Références V2 (à porter)
`/tmp/oldver/mytowt-main/app/routers/crew_router.py` :
- `member_edit_form`/`member_edit_submit` (l.198‑250) — édition fiche marin.
- `member_delete` (l.254‑270, perm `crew:S`, `db.delete`).
- `assign_submit` (l.292‑351) — **affectation à un `vessel_id`** (pas de leg), **anti‑overlap F6** (l.312‑327), garde dates F28 (l.307), statut calculé F29 (l.329‑337).
- `assignment_edit_form`/`assignment_edit_submit` (l.355‑401) + `assignment_delete` (l.404‑419).
- `border_police_export` (l.578‑669) — **PDF reportlab bilingue FR/EN**, paysage A4, colonnes #/Nom/Prénom/Rôle/Nationalité/N° passeport/Exp. passeport/Visa/Exp. visa/Embarquement, total + total étrangers, teal `#095561`.
- `crew_for_vessel_api` (l.423‑456) — **JSON `{on_board:[], available:[]}`** par navire.
- `ticket_create` (l.674‑795) avec upload fichier + auto‑PAF Fécamp + `ticket_download` (l.798) + `ticket_delete` (l.808).

`/tmp/oldver/mytowt-main/app/models/crew.py` : `CrewAssignment` V2 = **`vessel_id` NOT NULL** + `embark_date`/`disembark_date` (Date) + `embark_leg_id`/`disembark_leg_id` nullables + `status`.

> **Note de portage** — V2 utilise `first_name`/`last_name`, V3 a un `full_name` unique. **On conserve `full_name`** (gain V3). V2 a un booléen `is_foreign` ; en V3 le « marin étranger » se déduit de la nationalité (`nationality NOT IN SCHENGEN_COUNTRIES`) — **on ne réintroduit PAS `is_foreign`**, on dérive via `crew_compliance.SCHENGEN_COUNTRIES`.

---

## 1. Modèle de données — changements (1 migration Alembic)

### 1.1 `CrewAssignment` — appliquer A4 (embarquement hors leg)

```python
# app/models/crew.py — classe CrewAssignment
leg_id:    Mapped[int | None] = mapped_column(ForeignKey("legs.id"), nullable=True, index=True)   # ← était NOT NULL
vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id"), nullable=True, index=True) # ← NOUVEAU (A4)
```

- **Invariant applicatif** (pas une contrainte DB, validé en route) : `leg_id IS NOT NULL OR vessel_id IS NOT NULL` (au moins l'un des deux). Une affectation « leg » renseigne idéalement aussi `vessel_id` (résolu depuis `leg.vessel_id`) pour simplifier les regroupements par navire et l'API CREW‑06.
- **Aucun autre champ ajouté** : `embark_at`/`disembark_at` (DateTime tz) + `embark_port_id`/`disembark_port_id` couvrent déjà le besoin. On **ne** réintroduit **pas** le `status` V2 (dérivé : à venir / actif / terminé via `embark_at`/`disembark_at` vs `now`).

### 1.2 `CrewMember` / `CrewTicket` — RIEN à migrer
- **CREW‑03** : les 6 champs (`visa_us_expires_at`, `visa_br_expires_at`, `seaman_book_number`, `seaman_book_expires_at`, `date_of_birth`, `nationality`) **existent déjà** (cf. §0). Travail purement **formulaire + route**, zéro migration.
- **CREW‑05** : `CrewTicket.file_path` **existe déjà**. On stocke le chemin relatif `safe_files` ; le nom d'affichage = `reference` ou nom de fichier dérivé. Pas de colonne ajoutée.

### 1.3 Migration Alembic — nouvelle révision (`down_revision = "20260619_0054"`)

```python
def upgrade() -> None:
    # A4 — rendre leg_id nullable
    op.alter_column("crew_assignments", "leg_id",
                    existing_type=sa.Integer(), nullable=True)
    # A4 — affectation directe à un navire
    op.add_column("crew_assignments",
                  sa.Column("vessel_id", sa.Integer(), sa.ForeignKey("vessels.id"), nullable=True))
    op.create_index("ix_crew_assignments_vessel_id", "crew_assignments", ["vessel_id"])

def downgrade() -> None:
    op.drop_index("ix_crew_assignments_vessel_id", table_name="crew_assignments")
    op.drop_column("crew_assignments", "vessel_id")
    # ⚠ downgrade leg_id → NOT NULL échoue si des lignes leg_id IS NULL existent.
    # Nettoyer ou refuser explicitement (les affectations "navire seul" n'ont pas de leg).
    op.alter_column("crew_assignments", "leg_id",
                    existing_type=sa.Integer(), nullable=False)
```

> **Sûre et additive** côté upgrade (relâchement de contrainte + colonne nullable). Le downgrade est documenté comme destructif si des affectations hors leg existent.

---

## 2. CREW‑01 — Édition de la fiche marin

**Cible :** `app/routers/crew_router.py`, `app/templates/staff/crew/new.html` (réécrit en form unifié) ou nouveau `app/templates/staff/crew/member_form.html`.

### 2.1 Refactor : un seul formulaire create/edit
Réécrire `new.html` → **`member_form.html`** (paramètre `member` = `None` en création, objet en édition), aligné sur l'enum **réel** `CREW_ROLES` (`capitaine`, `second`, `chef_mecanicien`, `cook`, `lieutenant`, `bosco`, `marin`, `eleve_officier`) et exposant **tous** les champs (CREW‑03 §3). `action` calculée : `/crew/members` (create) ou `/crew/members/{id}/edit`.

### 2.2 Routes

```python
@router.get("/members/{member_id}/edit", response_class=HTMLResponse)
async def crew_edit_form(member_id, request, db, user=Depends(require_permission("crew", "M"))):
    m = await db.get(CrewMember, member_id)        # 404 si None
    return templates.TemplateResponse("staff/crew/member_form.html",
        {"request": request, "user": user, "roles": CREW_ROLES, "member": m})

@router.post("/members/{member_id}/edit")
async def crew_update(
    member_id, request,
    full_name: str = Form(...), role: str = Form(...),
    nationality: str | None = Form(None),
    date_of_birth: str | None = Form(None),
    passport_number: str | None = Form(None),
    passport_expires_at: str | None = Form(None),
    visa_us_expires_at: str | None = Form(None),       # CREW-03
    visa_br_expires_at: str | None = Form(None),       # CREW-03
    seaman_book_number: str | None = Form(None),       # CREW-03
    seaman_book_expires_at: str | None = Form(None),   # CREW-03
    email: str | None = Form(None), phone: str | None = Form(None),
    notes: str | None = Form(None),
    db=Depends(get_db), user=Depends(require_permission("crew", "M")),
):
    m = await db.get(CrewMember, member_id)            # 404 si None
    if role not in CREW_ROLES:
        raise HTTPException(400, "invalid role")
    # appliquer chaque champ (helper _parse_date réutilisé, cf. §3.2)
    m.full_name = full_name.strip(); m.role = role
    m.nationality = (nationality or "").strip().upper()[:2] or None
    m.date_of_birth = _parse_date(date_of_birth)
    m.passport_number = (passport_number or "").strip() or None
    m.passport_expires_at = _parse_date(passport_expires_at)
    m.visa_us_expires_at = _parse_date(visa_us_expires_at)
    m.visa_br_expires_at = _parse_date(visa_br_expires_at)
    m.seaman_book_number = (seaman_book_number or "").strip() or None
    m.seaman_book_expires_at = _parse_date(seaman_book_expires_at)
    m.email = (email or "").strip() or None
    m.phone = (phone or "").strip() or None
    m.notes = (notes or "").strip() or None
    await db.flush()
    await activity_record(db, action="update", user_id=user.id,
        user_name=user.full_name or user.username, user_role=user.role,
        module="crew", entity_type="crew_member", entity_id=m.id,
        entity_label=m.full_name, ip_address=_client_ip(request))
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)
```

**Câbler aussi la route de création** `POST /crew/members` (l.326) pour qu'elle reçoive les mêmes champs CREW‑03 (aujourd'hui elle n'en prend que 7) — factoriser via un helper `_apply_member_fields(m, **forms)`.

- **Permission :** édition `crew:M`.
- **Garde de sécurité :** 404 si marin absent ; `role` whitelisté contre `CREW_ROLES` ; `nationality` tronquée 2 car. + upper ; dates via `_parse_date` (jamais `fromisoformat` nu côté route — voir §3.2).
- **Lien UI :** bouton « ✎ Éditer » dans `detail.html` (header) et dans la liste `index.html`.
- **Critère d'acceptation :** depuis la fiche, modifier nom/rôle/passeport/visa/seaman/naissance/contact, persistance + audit `update`. **NRT : P6 #1.**

---

## 3. CREW‑03 — Exposer les champs morts (visa US/BR, seaman book, naissance, nationalité)

**Cible :** `member_form.html`, routes create (l.326) + edit (§2.2).

### 3.1 Formulaire complet (`member_form.html`)
Trois `<fieldset>` Kairos :
1. **Identité** : `full_name` (requis), `role` (select `CREW_ROLES`), `nationality` (ISO‑2, aide `|flag`), `date_of_birth` (`type=date`), `email`, `phone`.
2. **Documents d'identité / immigration** : `passport_number`, `passport_expires_at`, `visa_us_expires_at`, `visa_br_expires_at`, `seaman_book_number`, `seaman_book_expires_at` (tous `type=date` pour les expirations).
3. **Divers** : `notes`.

Pré‑remplissage en édition : `value="{{ member.<champ> or '' }}"`, dates au format ISO (`member.passport_expires_at.isoformat()` si non None).

### 3.2 Garde‑fou parsing dates (helper router)
```python
def _parse_date(val: str | None) -> _date | None:
    if not val or not val.strip():
        return None
    try:
        return _date.fromisoformat(val.strip())
    except ValueError:
        return None
```
(Portage du `parse_date` V2, l.29 — narrow `except ValueError`.) Évite le 500 sur date malformée.

- **Permission :** `crew:M` (champs portés par create + edit).
- **Critère d'acceptation :** les 6 champs sont saisissables en création **et** en édition, relus dans `detail.html` (déjà affichés en read‑only l.31‑35), et pris en compte par le PDF PAF (visa) et la compliance (déjà câblée l.246‑255 sur `seaman_book_expires_at`). **NRT : P6 #1.**

---

## 4. CREW‑04 — Édition + suppression d'affectation (+ A4)

**Cible :** `app/routers/crew_router.py`, `app/templates/staff/crew/detail.html`, nouveau fragment `app/templates/staff/crew/_assignment_form.html`.

### 4.1 Adapter la création (`POST /crew/members/{id}/assignments`, l.469) à A4
Aujourd'hui : `leg_id: int = Form(...)` obligatoire + `db.get(Leg, leg_id)` (404 sinon). Nouveau contrat **« leg OU navire »** :

```python
@router.post("/members/{member_id}/assignments")
async def crew_assign(
    member_id, request,
    leg_id: int | None = Form(None),          # ← devient optionnel (A4)
    vessel_id: int | None = Form(None),       # ← NOUVEAU (A4)
    role_on_board: str | None = Form(None),
    embark_at: str | None = Form(None),
    disembark_at: str | None = Form(None),
    override_compliance: str | None = Form(None),
    db=..., user=Depends(require_permission("crew", "M")),
):
    member = await db.get(CrewMember, member_id)        # 404
    leg = await db.get(Leg, leg_id) if leg_id else None
    if leg_id and leg is None:
        raise HTTPException(404)
    # A4 : au moins un rattachement
    if leg is None and vessel_id is None:
        return _render_detail_error(... "Renseignez un leg OU un navire.")
    # navire résolu : explicite, sinon hérité du leg
    resolved_vessel_id = vessel_id or (leg.vessel_id if leg else None)
    ...
    # GARDE-FOU COMPLIANCE V3 — INCHANGÉ (Schengen + passeport + override tracé)
    await refresh_member_schengen(db, member)
    blocking = [...]                                     # cf. l.503-516 actuel
    ...
    # ANTI-OVERLAP (CREW-08) — cf. §6.2
    a = CrewAssignment(crew_member_id=member_id, leg_id=leg_id,
                       vessel_id=resolved_vessel_id,
                       role_on_board=..., embark_at=embark_dt, disembark_at=disembark_dt)
    db.add(a); await db.flush()
    await activity_record(db, action="crew_assignment_override" if overridden else "create", ...,
        entity_type="crew_assignment", entity_id=a.id,
        entity_label=f"member={member_id} " + (f"leg={leg_id}" if leg_id else f"vessel={resolved_vessel_id}"),
        detail=(f"OVERRIDE compliance — motifs : {' '.join(blocking)}" if overridden else None), ...)
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)
```

> **Le garde‑fou conformité (l.498‑535) et l'override tracé (`action="crew_assignment_override"`, `detail=...`) sont PRÉSERVÉS** — c'est un gain V3 à ne pas casser. Pour une affectation « navire seul », `passport_blocking_reason` s'applique sur la `deadline` (disembark sinon embark sinon today), et le snapshot Schengen reste pertinent (basé sur les assignments existants).

### 4.2 Édition + suppression

```python
@router.get("/assignments/{aid}/edit", response_class=HTMLResponse)   # crew:M
async def crew_assignment_edit_form(aid, request, db, user=Depends(require_permission("crew","M"))):
    a = await db.get(CrewAssignment, aid)                 # 404
    # charger member + leg_options + vessels actifs pour le select
    ...

@router.post("/assignments/{aid}/edit")                   # crew:M
async def crew_assignment_update(
    aid, request,
    leg_id: int | None = Form(None), vessel_id: int | None = Form(None),
    role_on_board: str | None = Form(None),
    embark_at: str | None = Form(None), disembark_at: str | None = Form(None),
    db=..., user=Depends(require_permission("crew","M")),
):
    a = await db.get(CrewAssignment, aid)                 # 404
    # même invariant A4 + même anti-overlap (en excluant a.id) + parse dates
    a.leg_id = leg_id
    a.vessel_id = vessel_id or (leg.vessel_id if leg else None)
    a.role_on_board = (role_on_board or "").strip() or None
    a.embark_at = _parse_dt(embark_at); a.disembark_at = _parse_dt(disembark_at)
    await db.flush()
    await activity_record(db, action="update", ..., entity_type="crew_assignment", entity_id=a.id, ...)
    return RedirectResponse(url=f"/crew/members/{a.crew_member_id}", status_code=303)

@router.post("/assignments/{aid}/delete")                 # crew:S
async def crew_assignment_delete(aid, request, db, user=Depends(require_permission("crew","S"))):
    a = await db.get(CrewAssignment, aid)                 # 404
    member_id = a.crew_member_id
    await db.delete(a); await db.flush()
    await activity_record(db, action="delete", ..., entity_type="crew_assignment", entity_id=aid,
        entity_label=f"member={member_id}", ...)
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)
```

- **Garde de sécurité :** validation datetime via helper `_parse_dt` (try/except → re‑render erreur 400, cf. l.486‑496) ; invariant A4 ; anti‑overlap excluant l'affectation courante en édition.
- **UI (`detail.html`) :** sur chaque ligne de la table « Embarquements » (l.56‑64), ajouter colonne Navire + boutons ✎ (ouvre `_assignment_form.html` pré‑rempli — `<details>` ou HTMX `hx-get`, **sans `<script>` inline**) et 🗑 (POST delete, confirm via `forms.js`). Le form de création (l.94‑126) gagne un **select Navire** (« — ou un navire — ») à côté du select Leg.
- **Critère d'acceptation :** créer une affectation à un **navire sans leg** (A4) ; éditer dates/rôle/rattachement ; supprimer. Garde‑fou compliance + override tracé toujours actifs. **NRT : P6 #2.**

---

## 5. CREW‑02 — Export PDF « Crew List » pour la PAF (WeasyPrint)

**Cible :** `app/services/pdf_generator.py` (nouvelle fonction), nouveau template `app/templates/pdf/crew_list.html`, route dans `crew_router.py`, bouton dans `index.html`.

> **Réimplémentation en WeasyPrint** (la V2 utilisait reportlab — proscrit en V3 ; le stack impose WeasyPrint, cf. `CLAUDE.md`). Bilingue FR/EN, A4 paysage (`@page { size: A4 landscape; }` dans le CSS du template).

### 5.1 Service
```python
# app/services/pdf_generator.py
def render_crew_list(*, vessel, members_rows, today, foreign_count) -> DocumentBytes:
    """members_rows = liste de dicts {seq, last_name, first_name|full_name, role_label,
    nationality, passport_number, passport_expiry, visa_label, visa_expiry, embark_at}."""
    ctx = {
        "vessel": vessel, "rows": members_rows, "today": today,
        "total": len(members_rows), "foreign_count": foreign_count,
        "issued_at": datetime.now(UTC), "site_url": settings.site_url,
    }
    html, pdf = _render_pdf("pdf/crew_list.html", ctx)
    return DocumentBytes(html=html, pdf=pdf,
        filename=f"CrewList_{vessel.code}_{today.strftime('%Y%m%d')}.pdf")
```

### 5.2 Sélection des marins « à bord » (A4‑compatible)
Marins embarqués sur le navire `vessel_id` à `today` = affectations **actives** rattachées au navire (directement via `vessel_id`, **ou** via un `leg` du navire) :

```python
now = datetime.now(UTC)
leg_ids_subq = select(Leg.id).where(Leg.vessel_id == vessel_id)
assigns = (await db.execute(
    select(CrewAssignment).where(
        CrewAssignment.embark_at.is_not(None),
        (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now),
        (CrewAssignment.vessel_id == vessel_id) | (CrewAssignment.leg_id.in_(leg_ids_subq)),
    )
)).scalars().all()
```
Puis charger les `CrewMember` correspondants, construire `members_rows`, calculer `foreign_count = sum(1 for m if (m.nationality or '').upper() not in SCHENGEN_COUNTRIES)` (dérivé — pas de `is_foreign`). Visa affiché : « US » si `visa_us_expires_at`, « BR » si `visa_br_expires_at` (sinon « — »).

### 5.3 Route
```python
@router.get("/crew-list/{vessel_id}.pdf")            # perm crew:C
async def crew_list_pdf(vessel_id, request, db, user=Depends(require_permission("crew", "C"))):
    vessel = await db.get(Vessel, vessel_id)         # 404 si None
    ... # sélection §5.2, build rows
    doc = render_crew_list(vessel=vessel, members_rows=rows, today=_date.today(),
                           foreign_count=foreign_count)
    await activity_record(db, action="export", user_id=user.id,
        user_name=user.full_name or user.username, user_role=user.role,
        module="crew", entity_type="crew_list", entity_id=vessel_id,
        entity_label=f"Crew List {vessel.name}", ip_address=_client_ip(request))
    return Response(content=doc.pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'})
```
(Mirroir exact de `escale_sof_pdf`, `escale_router.py` l.519‑522.)

### 5.4 Template `pdf/crew_list.html`
- En‑tête bilingue : **« CREW LIST / LISTE D'ÉQUIPAGE »**, sous‑titre `Vessel: {{ vessel.name }} ({{ vessel.code }}) — Date: {{ today|date }}`.
- Table colonnes : `# · Nom/Name · Prénom/First Name · Rôle/Role · Nationalité/Nationality · N° Passeport/Passport No. · Exp. Passeport/Passport Exp. · Visa · Exp. Visa/Visa Exp. · Embarquement/Embarkation`.
- Pied : `Total: {{ total }} crew members on board` + `Foreign nationals / Ressortissants étrangers: {{ foreign_count }}`.
- Style Kairos PDF (teal `--teal #0D5966` pour l'en‑tête de table, Manrope) — **pas de couleur V2 `#095561`**, utiliser le token charte.

- **Permission :** `crew:C` (lecture/export).
- **Garde de sécurité :** 404 si navire absent ; nationalité dérivée (pas de fuite hors PII strictement réglementaire — document destiné à la PAF).
- **Critère d'acceptation :** PDF A4 paysage bilingue avec marins à bord (mode leg **et** mode navire A4), passeport, visa, embarquement, total + total étrangers ; export tracé `action="export"`. **NRT : P6 #3.**

---

## 6. CREW‑08 (P1) — Anti‑overlap d'embarquement + suppression/désactivation marin

### 6.1 Désactivation + suppression marin
```python
@router.post("/members/{member_id}/deactivate")     # crew:M — soft, recommandé (préserve l'historique)
async def crew_deactivate(member_id, request, db, user=Depends(require_permission("crew","M"))):
    m = await db.get(CrewMember, member_id)          # 404
    m.is_active = False; await db.flush()
    await activity_record(db, action="deactivate", ..., entity_type="crew_member",
        entity_id=m.id, entity_label=m.full_name, ...)
    return RedirectResponse(url="/crew", status_code=303)

@router.post("/members/{member_id}/delete")          # crew:S — hard
async def crew_delete(member_id, request, db, user=Depends(require_permission("crew","S"))):
    m = await db.get(CrewMember, member_id)          # 404
    # Garde : refuser la suppression si affectations actives (intégrité)
    active = (await db.execute(select(func.count()).select_from(CrewAssignment).where(
        CrewAssignment.crew_member_id == member_id,
        CrewAssignment.embark_at.is_not(None),
        (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > datetime.now(UTC)),
    ))).scalar() or 0
    if active:
        return _render_detail_error(... "Débarquez le marin avant suppression.")  # 409
    name = m.full_name
    await db.delete(m); await db.flush()             # CrewTicket CASCADE ; CrewAssignment à nettoyer/garder
    await activity_record(db, action="delete", ..., entity_type="crew_member",
        entity_id=member_id, entity_label=name, ...)
    return RedirectResponse(url="/crew", status_code=303)
```
> ⚠️ `CrewTicket.crew_member_id` est `ON DELETE CASCADE` (l.27). `CrewAssignment.crew_member_id` ne l'est **pas** → soit nettoyer les affectations dans la route, soit privilégier la **désactivation** (par défaut). Le filtre `is_active` est déjà appliqué partout (`index`, `compliance`, `calendar`).

### 6.2 Anti‑overlap (portage F6 V2, l.312‑327) — datetime tz V3
Helper réutilisable, appelé par create (§4.1) **et** edit (§4.2, `exclude_id=a.id`) :

```python
async def _has_overlap(db, *, member_id, embark_dt, disembark_dt, exclude_id=None) -> bool:
    lo = embark_dt or datetime(MINYEAR, 1, 1, tzinfo=UTC)
    hi = disembark_dt or datetime(MAXYEAR, 12, 31, tzinfo=UTC)
    stmt = select(CrewAssignment).where(
        CrewAssignment.crew_member_id == member_id,
        CrewAssignment.embark_at.is_not(None),
        CrewAssignment.embark_at <= hi,
        (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at >= lo),
    )
    if exclude_id is not None:
        stmt = stmt.where(CrewAssignment.id != exclude_id)
    return (await db.execute(stmt)).scalars().first() is not None
```
Dans la route, **avant** `db.add`/persistance :
- garde dates : si `embark_dt` et `disembark_dt` et `embark_dt > disembark_dt` → 400 « embarquement avant débarquement » (F28).
- si `_has_overlap(...)` → re‑render `detail.html` avec erreur 409 : « Ce marin est déjà affecté sur la période. Clôturez l'affectation existante. » **Non contournable par l'override compliance** (l'override ne concerne que Schengen/passeport, pas le double‑booking physique).

- **Critère d'acceptation :** impossible d'embarquer deux fois le même marin sur des périodes qui se chevauchent ; édition d'une affectation existante autorisée (s'exclut elle‑même) ; désactivation/suppression tracées. **NRT : P6 #5.**

---

## 7. CREW‑05 (P1) — Upload + download de la PJ d'un billet

**Cible :** `crew_router.py` (étendre `crew_ticket_create` l.566 + 2 routes), `safe_files` (réutilisé), `detail.html`.

### 7.1 Upload (étendre la création de billet)
La route `POST /crew/members/{id}/tickets` (l.566) reçoit déjà tous les champs ; ajouter la prise en charge du fichier (pattern `attach_cargo_doc`, captain_router l.770‑812) :

```python
from app.services.safe_files import UploadRejected, save_upload
form = await request.form()
upload = form.get("file")
if upload is not None and hasattr(upload, "read") and getattr(upload, "filename", ""):
    content = await upload.read()
    try:
        rel_path, _mime = save_upload(content, upload.filename, subdir="crew_tickets")
    except UploadRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    t.file_path = rel_path
await db.flush()
await activity_record(db, action="create", ..., entity_type="crew_ticket", entity_id=t.id,
    entity_label=f"billet {t.mode} member={member_id}", detail=getattr(upload, "filename", None), ...)
```
> ⚠️ Le form de billet doit passer en `enctype="multipart/form-data"`. La route **doit lire `request.form()`** (pas seulement les `Form(...)`) pour récupérer l'`UploadFile`, comme `attach_cargo_doc`.

### 7.2 Download
```python
@router.get("/tickets/{ticket_id}/attachment")       # perm crew:C
async def crew_ticket_download(ticket_id, db, user=Depends(require_permission("crew", "C"))):
    from fastapi.responses import FileResponse
    from app.services.safe_files import UploadRejected, resolve_path
    t = await db.get(CrewTicket, ticket_id)
    if t is None or not t.file_path:
        raise HTTPException(404)
    try:
        path = resolve_path(t.file_path)
    except (UploadRejected, FileNotFoundError):
        raise HTTPException(404) from None
    return FileResponse(path=str(path), filename=path.name)
```
(Mirroir exact de `download_cargo_doc_attachment`, captain_router l.815‑839.)

### 7.3 (option) Suppression du billet — `POST /crew/tickets/{id}/delete` (`crew:S`), `db.delete` + `resolve_path(...).unlink(missing_ok=True)` best‑effort.

- **Permissions :** upload `crew:M` (via la création), download `crew:C`, delete `crew:S`.
- **Gardes de sécurité :** validation `safe_files` (extension + taille + magic‑bytes) ; `resolve_path` anti‑traversal ; pas de nom de fichier client exposé sur le disque (`token_hex`). 404 si pas de PJ.
- **UI (`detail.html`) :** table billets (l.128‑147) → form passe en `multipart` + `<input type="file" name="file">` ; colonne « PJ » avec lien `📎 Télécharger` (`/crew/tickets/{{ t.id }}/attachment`) si `t.file_path`.
- **Critère d'acceptation :** joindre un PDF/justificatif à un billet, le re‑télécharger ; fichier invalide → 400. **NRT : P6 #4.**

---

## 8. CREW‑06 (P1) — API équipage par navire (JSON)

**Cible :** `crew_router.py` (nouvelle route), consommée par escale/onboard (cf. SPEC‑ESCALE §5 ESC‑06).

```python
@router.get("/api/vessel/{vessel_id}")                # perm crew:C
async def crew_for_vessel_api(vessel_id, db, user=Depends(require_permission("crew", "C"))):
    from fastapi.responses import JSONResponse
    now = datetime.now(UTC)
    leg_ids_subq = select(Leg.id).where(Leg.vessel_id == vessel_id)
    on_board_assigns = (await db.execute(select(CrewAssignment).where(
        CrewAssignment.embark_at.is_not(None),
        (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now),
        (CrewAssignment.vessel_id == vessel_id) | (CrewAssignment.leg_id.in_(leg_ids_subq)),
    ))).scalars().all()
    on_board_ids = {a.crew_member_id for a in on_board_assigns}
    members = {m.id: m for m in (await db.execute(
        select(CrewMember).where(CrewMember.id.in_(on_board_ids))
    )).scalars()} if on_board_ids else {}
    all_active = (await db.execute(
        select(CrewMember).where(CrewMember.is_active.is_(True)).order_by(CrewMember.full_name)
    )).scalars().all()
    on_board = [{"id": m.id, "name": m.full_name, "role": ROLE_LABELS.get(normalize_role(m.role), m.role)}
                for mid in on_board_ids if (m := members.get(mid))]
    available = [{"id": m.id, "name": m.full_name, "role": ROLE_LABELS.get(normalize_role(m.role), m.role)}
                 for m in all_active if m.id not in on_board_ids]
    return JSONResponse({"on_board": on_board, "available": available})
```
- **Permission :** `crew:C`. **Garde :** réservé staff (pas de token public). Réutilise `normalize_role`/`ROLE_LABELS` (gain V3) pour les libellés.
- **Critère d'acceptation :** `GET /crew/api/vessel/{id}` renvoie `{on_board, available}` cohérent avec les affectations actives (modes leg + navire). Consommé par les formulaires escale/onboard.

---

## 9. Templates — récapitulatif

| Template | Action |
|---|---|
| `staff/crew/member_form.html` | **réécriture** de `new.html` : form unifié create/edit, enum **réel** `CREW_ROLES`, **tous** les champs CREW‑03 (visa US/BR, seaman book, naissance, nationalité) ; `action` create vs edit (CREW‑01/03) |
| `staff/crew/detail.html` | + bouton « Éditer le marin » ; + colonne Navire + boutons ✎/🗑 sur les affectations (CREW‑04) ; + select Navire dans le form d'embarquement (A4) ; form billets → `multipart` + `<input file>` + colonne « PJ » download (CREW‑05) ; + boutons Désactiver/Supprimer marin (CREW‑08) |
| `staff/crew/_assignment_form.html` | **nouveau** (fragment édition affectation, HTMX `hx-get`, sans `<script>` inline) |
| `staff/crew/index.html` | + bouton/menu « Crew List PAF » par navire (lien `/crew/crew-list/{vessel_id}.pdf`) ; bouton ✎ Éditer par ligne marin |
| `pdf/crew_list.html` | **nouveau** (CREW‑02, WeasyPrint, A4 paysage, bilingue FR/EN, Kairos teal) |

Contraintes : tout en classes Kairos (`.card`, `.btn`, `.field`, `.data-table`, `.alert`, `.pill`, `.fieldset`…), **zéro `<script>` inline** (édition d'affectation via HTMX `hx-get`/`hx-post` de fragments + `forms.js` pour anti‑double‑submit et confirmations de suppression ; `_csrf` injecté en hidden comme l'existant). Le bouton « Forcer malgré la non‑conformité » (override) reste un simple checkbox (l.118‑123, à conserver).

---

## 10. i18n (5 catalogues)

Ajouter à `app/i18n/{fr,en,es,pt_br,vi}.py` les clés des nouveaux libellés :
- Form marin : `crew_form_identity`, `crew_form_documents`, `crew_visa_us`, `crew_visa_br`, `crew_seaman_book`, `crew_seaman_book_exp`, `crew_date_of_birth`, `crew_edit`, `crew_deactivate`, `crew_delete`.
- Affectation : `crew_assign_vessel`, `crew_assign_or_vessel`, `crew_assign_edit`, `crew_assign_delete`, `crew_overlap_error`, `crew_dates_order_error`.
- Billets : `crew_ticket_attachment`, `crew_ticket_download`, `crew_ticket_upload`.
- Export PAF : `crew_list_pdf`, `crew_list_title`, `crew_foreign_nationals`.

Les libellés **internes au PDF PAF** sont volontairement **bilingues FR/EN en dur** dans `pdf/crew_list.html` (document réglementaire destiné à la PAF — pas de dépendance à la langue d'UI de l'opérateur).

---

## 11. Tests

**Unitaires (`tests/unit`)**
- `_parse_date` / `_parse_dt` : ISO valide → date/datetime ; vide/None → None ; malformé → None (pas d'exception).
- `_has_overlap` : recouvrement total/partiel → True ; périodes disjointes → False ; `exclude_id` ignore l'affectation courante ; `disembark_at IS NULL` (toujours à bord) traité comme borne haute.
- `render_crew_list` : `foreign_count` dérivé de la nationalité hors `SCHENGEN_COUNTRIES` ; visa label US/BR/—.
- Compliance inchangée : `refresh_schengen_for_members` ignore correctement les affectations `leg_id IS NULL` (régression A4 — vérifier qu'une affectation « navire seul » ne casse pas le calcul).

**Intégration (`tests/integration`)**
- CREW‑01/03 : create marin avec visa US/BR + seaman book + naissance → champs persistés ; `GET /members/{id}/edit` pré‑rempli ; POST edit modifie → audit `update`.
- CREW‑04 : create affectation **navire seul** (A4, `leg_id` absent) → 303 + `vessel_id` posé ; edit → champs modifiés ; delete → 303, affectation absente ; garde‑fou compliance bloque (sans override) puis passe (avec override, `action="crew_assignment_override"`).
- CREW‑08 : 2ᵉ embarquement chevauchant → re‑render erreur (pas de création) ; édition de l'affectation existante autorisée ; `deactivate` → `is_active=False` ; `delete` avec affectation active → refus 409.
- CREW‑02 : `GET /crew/crew-list/{vessel_id}.pdf` → 200 `application/pdf`, marins à bord (mode leg **et** mode navire) présents, total + foreign_count corrects, audit `export`.
- CREW‑05 : POST billet avec fichier valide → `file_path` posé ; `GET /tickets/{id}/attachment` → 200 ; fichier invalide → 400 ; download sans PJ → 404.
- CREW‑06 : `GET /crew/api/vessel/{id}` → `{on_board, available}` cohérent ; marin à bord absent de `available`.
- Permissions : edit/PDF/API en `crew:C`/`M` OK ; suppression sans `crew:S` → 403.

**Non‑régression persona** : dérouler intégralement **P6** (#1→#5) de `docs/audit/backlog/TESTS-NON-REGRESSION-PERSONAS.md`.

---

## 12. Séquencement intra‑module & estimation

```
1. Migration + modèle (A4 : leg_id nullable + vessel_id)         [S]  ── socle
2. member_form unifié + CREW-01 (edit) + CREW-03 (champs morts)  [M]  ── indépendant de 1
3. CREW-04 (assign edit/delete + A4 create) + CREW-08 overlap    [M]  ── dépend de 1
4. CREW-02 (PDF PAF WeasyPrint + template)                       [M]  ── dépend de 1 (sélection A4)
5. CREW-05 (PJ billet upload/download)                           [S]  ── indépendant
6. CREW-06 (API JSON par navire)                                 [S]  ── dépend de 1
7. CREW-08 (deactivate/delete marin)                             [S]  ── dépend de 1
```
Chemin critique : **1 → 3** (A4 + affectations). Les étapes 2 et 5 sont parallélisables sans 1. 4/6/7 suivent 1.

---

## 13. Points de vigilance — gains V3 à préserver

- **Compliance Schengen réelle** (`crew_compliance.py`) : ne PAS la dégrader. ⚠️ Ses requêtes filtrent `CrewAssignment.leg_id` (l.190‑200, l.306) — après A4, **vérifier** que les affectations `leg_id IS NULL` (navire seul) sont **ignorées proprement** dans le calcul Schengen (pas de leg → pas de port → pas de jour Schengen comptabilisé : comportement acceptable et documenté en V1). Idem `vessel_readiness` (filtre via legs du navire) : les affectations « navire seul » n'y figureront pas tant qu'on ne joint pas sur `vessel_id` — **à étendre** pour que CREW‑02/06 et l'armement réglementaire restent cohérents.
- **Garde‑fou embarquement avec override tracé** : la mécanique l.498‑562 (`refresh_member_schengen` → `blocking` → `override_compliance` → `action="crew_assignment_override"` + `detail`) est un acquis réglementaire. **Conservée telle quelle** ; l'anti‑overlap (CREW‑08) est un **second** garde‑fou **non** contournable par l'override.
- **Sync Marad (lecture seule)** : `MaradCrewSchedule`, `marad_id`, `sync-marad`, affichage `marad_schedules` dans `detail.html` — ne rien casser. Les affectations créées manuellement (A4) restent distinctes du miroir Marad.
- **Fiche détaillée** (`detail.html` : documents/visas, certifications, assignments, marad, billets) : enrichie, pas refondue.
- **`full_name` unique** (vs `first_name`/`last_name` V2) conservé ; le PDF PAF V2 sépare nom/prénom → en V3, **scinder `full_name`** pour les colonnes Nom/Prénom (best‑effort : dernier token = nom, le reste = prénom) **ou** afficher `full_name` dans une seule colonne « Nom complet / Full name » (recommandé, plus robuste).
- **Pas de `is_foreign`** : le « marin étranger » est **dérivé** de `nationality NOT IN SCHENGEN_COUNTRIES` (source unique `crew_compliance.SCHENGEN_COUNTRIES`).
- **WeasyPrint, pas reportlab** : le PDF PAF est réimplémenté en Jinja+WeasyPrint (import paresseux déjà en place dans `pdf_generator.py`), couleurs **tokens charte** (`--teal`, Manrope), pas les hex V2.
- **Cohérence `CLAUDE.md`** : après reprise, le statut Crew (« ✅ bordées + compliance Schengen + calendar ») devra mentionner l'édition fiche/affectation, l'export PAF restauré et l'embarquement hors leg (A4).
- **Auto‑PAF Fécamp V2** (création auto d'une opération escale + notif quand marin étranger arrive à Fécamp, `ticket_create` l.729‑768) : **hors P0** — à recâbler dans le module Escale/Onboard (couplage CREW‑06 ↔ ESC‑06), pas dans cette reprise.
