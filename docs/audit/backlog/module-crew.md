# Module Crew / Équipage

Persona : **bosco / responsable équipage (crewing)**.
Réf V2 : `app/routers/crew_router.py`, `app/models/crew.py`, `app/templates/crew/*`.
Cible V3 : `app/routers/crew_router.py`, `app/models/{crew,crew_ticket}.py`,
`app/services/crew_compliance.py`, `app/templates/staff/crew/*`.

---

## Lot 1 — P0

### [CREW-01] Édition de la fiche marin
- **Persona :** Bosco · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `crew_router.py` (`/crew/members/{mid}/edit`)
- **Cible V3 :** `crew_router.py`, `app/templates/staff/crew/*`
- **Objectif :** V3 ne permet que la création (`/crew/new`). Aucun écran d'édition → impossible de mettre à jour passeport/visa/coordonnées après création.
- **Critères d'acceptation :** éditer tous les champs d'une fiche marin ; audit tracé.
- **Test de non‑régression :** P6 #1.
- **Effort :** M
- **Lié :** CREW‑03 (champs à exposer).

### [CREW-02] Export PDF « Crew List » pour la PAF (réglementaire)
- **Persona :** Bosco · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `crew_router.py` (`/crew/border-police/{vessel_id}`, reportlab, bilingue FR/EN, nom/passeport/visa/embarquement, comptage étrangers)
- **Cible V3 :** `crew_router.py`, `app/templates/pdf/crew_list.html` (WeasyPrint)
- **Objectif :** liste d'équipage obligatoire à présenter à la police aux frontières / autorités portuaires — disparue.
- **Critères d'acceptation :** PDF bilingue par navire à une date ; colonnes nom/prénom/nationalité/passeport/visa/embarquement ; total étrangers.
- **Test de non‑régression :** P6 #3.
- **Effort :** M

### [CREW-03] Formulaire complet (visa US/BR, seaman book, naissance, nationalité)
- **Persona :** Bosco · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `crew_router.py` form member (visa, dates…)
- **Cible V3 :** `crew_router.py`, `app/templates/staff/crew/new.html` (+ écran d'édition CREW‑01)
- **Objectif :** les colonnes V3 `visa_us_expires_at`, `visa_br_expires_at`, `seaman_book_number/expires_at`, `date_of_birth`, `nationality` **ne sont saisies par aucun formulaire** → données mortes.
- **Critères d'acceptation :** tous ces champs saisissables en création **et** édition.
- **Test de non‑régression :** P6 #1.
- **Effort :** S

### [CREW-04] Édition + suppression d'une affectation
- **Persona :** Bosco · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `crew_router.py` (`/crew/assignments/{aid}/edit`, `DELETE /crew/assignments/{aid}`)
- **Cible V3 :** `crew_router.py`
- **Objectif :** impossible de corriger/annuler un embarquement (date erronée, prolongation).
- **Critères d'acceptation :** éditer navire/leg/dates ; supprimer (perm S).
- **Dépend de / Arbitrage :** A4 (embarquement hors leg).
- **Test de non‑régression :** P6 #2.
- **Effort :** M

## Lot 2 — P1

### [CREW-05] Upload + download de la pièce jointe d'un billet
- **Persona :** Bosco · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `crew_router.py` (`/crew/tickets/create` upload, `/crew/tickets/{tid}/download`)
- **Cible V3 :** `crew_router.py` (`/crew/members/{id}/tickets`), `safe_files.py`, modèle `crew_ticket` (`file_path` orphelin)
- **Objectif :** la colonne `file_path` existe mais aucune route ne l'alimente. Restaurer upload + download.
- **Test de non‑régression :** P6 #4.
- **Effort :** S

### [CREW-06] API équipage par navire
- **Persona :** Bosco + escale/onboard · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `crew_router.py` (`/crew/api/vessel/{id}` JSON on_board/available)
- **Cible V3 :** `crew_router.py`
- **Objectif :** source consommée par les écrans escale/onboard pour sélectionner l'équipage embarqué — disparue.
- **Dépend pour :** ESC‑06.
- **Effort :** S

### [CREW-07] Auto‑opération PAF Fécamp + alerte billet/escale
- **Persona :** Bosco · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `crew_router.py` (auto‑op PAF pour personnel étranger à Fécamp + alerte billet hors fenêtre)
- **Cible V3 :** `crew_router.py`, `escale_router.py`
- **Objectif :** automatisme réglementaire « passage PAF requis » + garde‑fou cohérence billet/escale.
- **Effort :** M

### [CREW-08] Anti‑overlap d'embarquement + suppression/désactivation marin + suppression billet
- **Persona :** Bosco · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `crew_router.py` (contrôle chevauchement, `DELETE member`, `DELETE ticket`)
- **Cible V3 :** `crew_router.py`
- **Objectif :** un marin peut être affecté sur 2 navires en même temps ; pas de retrait de fiche/billet erroné.
- **Critères d'acceptation :** refus d'embarquement chevauchant ; désactivation (`is_active`) ou suppression marin ; suppression billet.
- **Test de non‑régression :** P6 #5.
- **Effort :** M

## Lot 3 — P2

### [CREW-09] UX & confort
- Marqueur « étranger » dérivé (nationalité hors Schengen) ; vue billetterie globale + saisie
  inline ; vue calendrier individuelle (durées, % activité) dans la fiche ; barre de filtre par
  rôle ; badges rôle labellisés/colorés (`role_label`) ; colonne « jours embarqués / an ».
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver
Compliance Schengen réelle (90/180 persisté) + garde‑fou embarquement avec override tracé ·
armement réglementaire par navire (`vessel_readiness`) · fiche marin détaillée + certifications ·
sync Marad (lecture seule) · billets enrichis (carrier/coût/lieux) · affectation au leg · drapeaux.

## Note d'architecture
Congés marins (`CrewLeave`) migrés vers RH (non régressif vs V2 qui ne les gérait pas) mais
séparation de droits `crew` ↔ `rh` à valider (cf. ARBITRAGES, décisions documentées).
