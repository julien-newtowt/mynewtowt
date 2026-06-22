# Module Onboard / Captain (SOF, documents) + Claims

Persona : **capitaine / officier à bord** (+ traitement des réclamations).
Réf V2 : `app/routers/{onboard_router,claim_router}.py`, `app/models/{onboard,claim,notification}.py`,
`app/templates/onboard/{index,doc_form}.html`, `app/templates/claims/*`.
Cible V3 : `app/routers/{onboard_router,captain_router,claims_router}.py`,
`app/models/{sof_event,claim,...}.py`, `app/templates/staff/{onboard,captain,claims}/*`.

> Tout écran repris est réécrit en Kairos/Manrope, sans `<script>` inline.

---

## Lot 1 — P0

### [ONB-01] Édition + suppression d'un événement SOF non signé
- **Persona :** Capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `onboard_router.py` (`/sof/{id}/edit` inline, `DELETE /sof/{id}`)
- **Cible V3 :** `captain_router.py` (`/captain/legs/{id}/sof/...`), `app/models/sof_event.py`
- **Objectif :** V3 ne permet que création + signature → aucune correction d'une faute de saisie. Restaurer édition/suppression tant que `is_locked = False`.
- **Critères d'acceptation :** éditer label/date‑heure/notes d'un SOF non signé ; supprimer un SOF non signé ; impossible si signé.
- **Test de non‑régression :** P5 #2.
- **Effort :** M

### [ONB-02] Documents cargo : formulaires structurés par type
- **Persona :** Capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `onboard_router.py` + `templates/onboard/doc_form.html` (champs spécifiques par type via `data_json` ; NOR, NOR_RT, HOLDS_CERT, KEY_MEETING, PRE_MEETING, 6× LOP, MATES_RECEIPT, SOF ; picker Master/Officer depuis crew embarqué)
- **Cible V3 :** `captain_router.py` (`/captain/legs/{id}/docs`), `app/models/...` (réintroduire `data_json` ou champs typés), `app/templates/staff/captain/*`, `app/templates/pdf/cargo_doc_*.html`
- **Objectif :** V3 a remplacé les formulaires guidés par un `body` texte libre et réduit 13 types → 6 (perte de HOLDS_CERT/KEY_MEETING/PRE_MEETING, LOP fusionnés). Restaurer les modèles guidés.
- **Critères d'acceptation :** liste complète des types ; champs spécifiques par type ; valeurs légales pré‑remplies (mentions LOP/Mate's Receipt) ; signataire choisi parmi le crew embarqué ; export PDF par type.
- **Test de non‑régression :** P5 #3.
- **Effort :** L

### [ONB-03] Pièces jointes leg + zone « Documents agent d'escale »
- **Persona :** Capitaine · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `onboard_router.py` (`OnboardAttachment`, 8 catégories dont `port_agent`/`bl_signed`/`letter_protest`)
- **Cible V3 :** `captain_router.py`/`onboard_router.py`, nouveau modèle d'attachments leg, `safe_files.py`, migration
- **Objectif :** V3 a supprimé l'upload libre de fichiers attachés au leg et la zone de dépôt des documents reçus de l'agent (BL signés, lettres de protestation, constats).
- **Critères d'acceptation :** upload (validation magic‑bytes, ≤ 20 Mo, types whitelistés) ; catégorisation ; galerie download/delete ; zone filtrée port_agent/bl_signed/letter_protest.
- **Test de non‑régression :** P5 #4.
- **Effort :** M

## Lot 2 — P1

### [ONB-04] Messagerie de bord enrichie
- **Persona :** Capitaine + terre · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `onboard_router.py` (`OnboardMessage` scope **navire**, `@mentions` autocomplete, bot `_bot_auto_reply`, messages `is_system`, delete, `/messages/users`)
- **Cible V3 :** `captain_router.py` (messagerie scope **leg**), `app/services/chatbot.py` (pour le bot)
- **Objectif :** restaurer le fil navire (continuité inter‑legs), l'autocomplete des mentions, les messages système (journal des actions SOF/ATA/ETA/clôture), la suppression. Brancher le bot sur Kairos AI (ou logique contextuelle V2).
- **Critères d'acceptation :** fil par navire ; mention assistée ; messages système postés aux actions clés ; suppression auteur/admin.
- **Test de non‑régression :** P5 #5.
- **Effort :** L

### [ONB-05] Clôture d'escale : PDF récapitulatif + checklist + reopen + état verrouillé
- **Persona :** Capitaine · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `onboard_router.py` (`/closure/*` : 4 états open→review→approved→locked, checklist 13 types, `_generate_closure_pdf`, reopen)
- **Cible V3 :** `captain_router.py` (`/captain/legs/{id}/closure/*`), `app/templates/pdf/closure.html`
- **Objectif :** V3 s'arrête à `approved` sans checklist, sans PDF, sans reopen. Restaurer ces éléments (en conservant le déclenchement KPI/finance V3).
- **Critères d'acceptation :** checklist documentaire (✅/⬜) ; PDF récapitulatif (leg/cargo/SOF/docs/PJ/crew/validation) téléchargeable ; reopen possible ; état terminal verrouillant uploads/SOF.
- **Test de non‑régression :** P5 #6.
- **Effort :** M

### [ONB-06] Claims : détail financier + rattachements + SOF auto + timeline/PJ
- **Persona :** Capitaine / gestionnaire sinistres · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `claim_router.py`, `app/models/claim.py` (franchise/indemnity/company_charge + auto reste‑à‑charge + propagation `LegFinance.claims_cost` ; lien `order_assignment`/`crew_member` ; SOF `CLAIM_DECLARED` auto ; timeline 9 types + PJ par entrée)
- **Cible V3 :** `claims_router.py`, `app/models/claim.py`
- **Objectif :** V3 ne garde que provision/settled. Restaurer le détail financier et sa propagation à la finance du leg, le rattachement précis marchandise/personne, le SOF auto et la granularité de timeline.
- **Critères d'acceptation :** franchise + indemnité + reste‑à‑charge calculés ; `LegFinance.claims_cost` mis à jour si responsabilité compagnie ; lien crew_member/order ; SofEvent posé à la déclaration ; timeline avec types courrier/expertise + PJ par entrée.
- **Dépend de :** FIN‑01 (propagation finance).
- **Effort :** L

### [ONB-07] Notifications onboard in‑page
- **Persona :** Capitaine · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `app/models/onboard.py` (`OnboardNotification` crew/escale/cargo + dismiss)
- **Cible V3 :** `onboard_router.py`/`captain_router.py`, ou redirection vers le centre de notifications global
- **Objectif :** le bord ne reçoit plus d'alertes contextuelles in‑page. Recréer ou rediriger vers `Notification`.
- **Effort :** M

## Lot 3 — P2

### [ONB-08] Finitions
- Fuseau par événement SOF (ou affichage heure locale port) ; historisation SOF de la
  génération de documents ; export Word des docs cargo ; pièces jointes multiples par document ;
  champs contexte/lieu incident sur claims ; documenter le mapping de statuts claims V2→V3.
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Gains V3 à préserver (ne pas casser en reprenant l'existant)
Signatures/lock IMO (SOF/noon/watch) · Noon Report officiel + PWA offline · journal de quart ·
MRV auto · hooks SOF→statuts/bookings · conformité ISM/ISPS + registre visiteurs · next‑port
briefing · clôture→KPI/finance · claims war_risk/third_party + contrat d'assurance structuré +
historique de provision + reporting/CSV.
