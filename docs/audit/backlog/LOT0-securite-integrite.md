# Lot 0 — Sécurité & intégrité (transverse, prioritaire)

Correctifs rapides refermant des régressions de surface. À traiter **immédiatement**, en
parallèle des autres lots. Effort global : faible ; risque si ignoré : élevé.

---

### [SEC-01] Rebrancher le rate‑limiting du login mot de passe
- **Persona :** Administrateur / sécurité
- **Priorité :** P1 · **Lot :** 0
- **Réf V2 :** `app/routers/auth_router.py` (rate‑limit DB 5 essais / 5 min / IP → 429)
- **Cible V3 :** `app/routers/staff_auth_router.py` (POST `/login`), `app/services/rate_limit.py`
- **Objectif :** en V3 seul `/login/mfa` est rate‑limité ; le POST `/login` (mot de passe) ne l'est plus → brute‑force non freiné.
- **Critères d'acceptation :** au‑delà de N tentatives échouées par IP sur la fenêtre, réponse 429 ; compteur DB‑backed ; succès réinitialise.
- **Test de non‑régression :** 6 tentatives de login erronées depuis une IP → la 6ᵉ renvoie 429.
- **Effort :** S

### [SEC-02] Rebrancher le rate‑limiting du portail expéditeur `/p/{token}`
- **Persona :** — (sécurité du portail)
- **Priorité :** P1 · **Lot :** 0
- **Réf V2 :** `app/utils/portal_security.py` (`check_token_rate_limit`, `record_token_attempt`, 10/5 min)
- **Cible V3 :** `app/routers/cargo_portal_router.py` (le service existe, n'est pas appelé)
- **Objectif :** rétablir la limite d'essais sur l'accès token (le portail logge l'accès mais n'appelle aucun rate‑limit).
- **Critères d'acceptation :** au‑delà du seuil d'accès/échecs par IP → 429 ; accès tracé dans `portal_access_logs` (SHA‑256, jamais en clair).
- **Test de non‑régression :** marteler un token invalide → blocage temporaire.
- **Effort :** S

### [SEC-03] Filtrer la sidebar staff par permission
- **Persona :** Collaborateur staff (tous rôles)
- **Priorité :** P0 · **Lot :** 0
- **Réf V2 :** `base.html` (`has_any_access(user, module)` par lien)
- **Cible V3 :** `app/templates/staff/_layout.html`
- **Objectif :** la sidebar V3 affiche tous les liens à tous → clics menant à des 403. Masquer chaque entrée/groupe selon les droits.
- **Critères d'acceptation :** un rôle sans accès à un module ne voit pas le lien ; un groupe entièrement inaccessible n'est pas rendu.
- **Test de non‑régression :** se connecter en rôle `marins` → seuls les modules autorisés apparaissent ; aucun lien ne mène à un 403.
- **Effort :** S

### [SEC-04] Restaurer l'intégrité des positions de tracking
- **Persona :** Opérateur suivi de flotte
- **Priorité :** P0 · **Lot :** 0
- **Réf V2 :** `app/models/vessel_position.py` (`UniqueConstraint(vessel_id, recorded_at)` + index `idx_vp_vessel_time`/`idx_vp_leg`)
- **Cible V3 :** `app/models/claim.py` (modèle `VesselPosition`), `app/routers/tracking_router.py`, migration Alembic
- **Objectif :** V3 fait l'idempotence en Python (1 SELECT/ligne) sans contrainte ni index → perf upload ZIP dégradée, risque de doublon concurrent, lectures non indexées.
- **Critères d'acceptation :** `UniqueConstraint(vessel_id, recorded_at)` + index recréés ; upsert `on_conflict_do_nothing` ; pas de doublon en upload concurrent.
- **Test de non‑régression :** uploader 2× le même fichier satcom → 0 doublon, insertion idempotente, temps stable.
- **Migration de données :** dédoublonner avant d'ajouter la contrainte unique.
- **Effort :** M

### [SEC-05] Filtre anti‑saut > 50 NM dans le calcul de distance réelle
- **Persona :** Opérateur suivi de flotte / data analyst
- **Priorité :** P1 · **Lot :** 0
- **Réf V2 :** `app/utils/navigation.py` (seuil 50 NM ignorant les sauts satcom aberrants)
- **Cible V3 :** `app/services/voyage_track.py` (`actual_distance_nm`)
- **Objectif :** V3 somme tous les segments → distance réelle surévaluée, écart réel/théorique faussé.
- **Critères d'acceptation :** segments > 50 NM exclus du cumul ; distance réelle cohérente avec V2 sur un même jeu de points.
- **Test de non‑régression :** injecter une position GPS aberrante sur un leg → la distance réelle ne bondit pas.
- **Effort :** S

### [SEC-06] Sécuriser l'API publique v1 (X‑API‑Key)
- **Persona :** Intégrateur B2B
- **Priorité :** P1 · **Lot :** 0
- **Réf V2 :** — (module nouveau V3)
- **Cible V3 :** `app/routers/api_v1_router.py`
- **Objectif :** l'auth `X‑API‑Key` est annoncée mais non appliquée sur les routes lues → API ouverte.
- **Critères d'acceptation :** routes `/api/v1/*` exigent une clé valide (comparaison temps constant) ; 401/403 sinon ; 503 si non configurée (cohérent avec les autres endpoints machine).
- **Test de non‑régression :** appel sans clé → refus ; avec clé valide → 200.
- **Effort :** S

### [SEC-07] Décider du sort des 4 endpoints GET tracking supprimés
- **Persona :** Opérateur suivi / intégrations
- **Priorité :** P1 · **Lot :** 0
- **Réf V2 :** `app/routers/tracking_router.py` (`/latest`, `/positions/{vessel_id}`, `/leg/{leg_id}/track`, `/navigation-kpis`)
- **Cible V3 :** `app/routers/tracking_router.py`
- **Objectif :** ces GET ont disparu (404) → consommateurs externes/JS cassés. Réimplémenter a minima `/latest` (utilisé par la carte) + l'agrégat KPI, **ou** documenter et versionner la rupture (cf. TRK‑01).
- **Critères d'acceptation :** décision tracée ; si réimplémentation, parité de payload documentée.
- **Dépend de :** arbitrage « compat Power Automate » (cf. `ARBITRAGES.md`).
- **Effort :** M
