# Lot 4 — Consolidation des modules V3‑only & évolutions

Au‑delà de la parité V2, dette à solder sur les périmètres nouveaux et pistes d'évolution
capitalisant sur les gains V3. À traiter de façon itérative après les Lots 1‑2.

---

## Consolidation des modules V3‑only

### [EVO-01] Trancher le sort de la facturation client
- **Priorité :** P1 · **Réf :** `app/models/client_invoice.py`, `app/services/invoicing.py`, `/me/invoices` (301 → `/me/documents`)
- **Objectif :** modèle complet (HT/TVA/TTC, statuts) mais **dormant**. Selon **A5** : activer un export comptable réel **ou** retirer le code dormant.
- **Dépend de / Arbitrage :** **A5**.
- **Effort :** M

### [EVO-02] Unifier les congés (CrewLeave ↔ HrAbsence)
- **Priorité :** P1 · **Réf :** `app/models/crew.py` (`CrewLeave`, hérité V2), `app/models/hr_absence.py` (`HrAbsence`), `rh_router.py`
- **Objectif :** deux modèles de congés coexistent sans unification (« stub historique »). Fusionner derrière une vue/un service commun.
- **Effort :** M

### [EVO-03] Nettoyer les collisions de routes `erp_scaffold`
- **Priorité :** P1 · **Réf :** `app/routers/erp_scaffold_router.py`
- **Objectif :** plusieurs slugs (escale/crew/finance/mrv/claims/tracking/admin) en doublon avec de vrais routers — l'ordre d'inclusion dans `main.py` décide silencieusement qui répond. Ne garder que `analytics`.
- **Effort :** S

### [EVO-04] Veille — phase IA (P2)
- **Priorité :** P2 · **Réf :** `veille_router.py`, `app/services/{news_ingest,newsdata}.py`
- **Objectif :** synthèse/scoring IA annoncés mais absents (P1 = flux brut). Prioriser si le module doit dépasser l'agrégateur ; sinon documenter l'état P1.
- **Effort :** L

### [EVO-05] PWA offline réel
- **Priorité :** P2 · **Réf :** `pwa_router.py`, scaffold onboard
- **Objectif :** SW/manifest servis ; offline réel (IndexedDB sync, mode passerelle) au backlog.
- **Effort :** L

### [EVO-06] Corriger la documentation `CLAUDE.md`
- **Priorité :** P1 · **Réf :** `CLAUDE.md`
- **Objectif :** statuts inexacts (Cargo « ✅ audit/lock » ; Finance/KPI ; Insurance présentée à tort comme V3‑only ; CO₂→Anemos). Documenter les ré‑absorptions et les décisions de design (cf. `ARBITRAGES.md`).
- **Effort :** S

---

## Évolutions capitalisant sur les gains V3

### [EVO-07] Brancher le bot de bord sur Kairos AI
- **Réf :** `app/services/chatbot.py` (Claude Sonnet 4.6) ; bot V2 `_bot_auto_reply`
- **Objectif :** lors de la reprise de la messagerie de bord (ONB‑04), remplacer le placeholder par une intégration Kairos AI (réponses ETA/crew/cargo/escale contextuelles, outils read‑only).
- **Effort :** M

### [EVO-08] Étendre navigation/météo aux KPI
- **Réf :** `voyage_track.py`, `weather_history.py`, `kpi_router.py`
- **Objectif :** exploiter les métriques nav (avg SOG, elongation) et la météo historisée dans les KPI d'exploitation (FIN‑04).
- **Effort :** M

### [EVO-09] Généraliser les signatures IMO aux documents repris
- **Réf :** signature/lock SOF/noon/watch
- **Objectif :** appliquer le même mécanisme de signature/hash aux documents cargo (ONB‑02) et à la clôture (ONB‑05) restaurés.
- **Effort :** S

### [EVO-10] Backlog produit existant (CLAUDE.md)
- Certificats CO₂ PDF (largement couvert par Anemos) · générateurs DOCX (BL/offre commerciale) ·
  exports admin ZIP (recoupe ADM‑04) · purges DB ciblées (recoupe ADM‑04) · notifications email.
- **Effort :** variable
