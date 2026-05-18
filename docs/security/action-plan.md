# Plan d'action — Sécurisation my_TOWT

Référence : [`audit-v1.md`](audit-v1.md).

Priorisation par sprints + backlog continu. Chaque action référence le constat audit (`S-XX`).

## Sprint 1 — Critiques (J+7) ✅ LIVRÉ

Livré sur la branche `claude/security-sprint1`. Les actions ci-dessous sont terminées côté code ; la partie ops (rotation effective des secrets, mise à jour des flows Power Automate) reste à appliquer par l'équipe exploitation.

### A1.1 Rotation `SECRET_KEY` + refus démarrage si default (S-01) ✅

- `app/main.py` : `_validate_secrets()` appelé dans `lifespan` **lève `RuntimeError`** quand `APP_ENV=production` et `SECRET_KEY` = default. En non-prod, log `warning`.
- À faire côté ops : générer la clé avec `python3 -c "import secrets; print(secrets.token_hex(32))"`, la pousser dans `.env` prod, redéployer.
- Documenter rotation tous les 12 mois minimum.

### A1.2 Rotation mot de passe Postgres (S-02) ✅ (guard code)

- `_validate_secrets()` bloque aussi le démarrage si `DATABASE_URL` contient encore `towt_secure_2025`.
- `.env.example` mis à jour : placeholders `CHANGE-ME` + commentaires explicites.
- `docker-compose.yml` : les variables `POSTGRES_PASSWORD` et `DATABASE_URL` restent pilotées par `.env` (valeurs par défaut conservées comme fallback dev uniquement).
- **À faire côté ops** : poser un vrai mot de passe Postgres, idéalement géré par Doppler/Vault. Ne pas laisser le `.env` sur disque avec droits lax.

### A1.3 `must_change_password` enforcement (S-05) ✅

- `app/security_middleware.py` — `ForcePasswordChangeMiddleware` : redirige tout utilisateur authentifié avec `must_change_password=True` vers `/admin/my-account/change-password` (sauf static, login/logout, portails publics, API tracking).
- `app/routers/admin_router.py` : nouveau couple `GET`/`POST /admin/my-account/change-password` + template `templates/admin/change_password.html`.
- Politique : nouveau mot de passe ≥ 12 caractères, ≠ actuel. Le flag `must_change_password` est remis à `False` au succès.
- Le POST existant `/admin/my-account/password` applique désormais les mêmes règles et clear le flag.

### A1.4 Fermeture port DB (S-03) ✅

- `docker-compose.yml` : directive `ports: - "5433:5432"` supprimée du service `db`. Accès externe désormais uniquement via `docker exec` ou tunnel SSH.

### A1.5 Auth API tracking (S-04) ✅ (partiel)

- `Settings.TRACKING_API_TOKEN` ajouté (`app/config.py`), propagé dans `.env.example`.
- `tracking_router.py` : dépendance `require_tracking_token` (compare_digest) appliquée sur `POST /api/tracking/upload`.
- Sécurisation fail-closed : si `TRACKING_API_TOKEN` vide, `upload` retourne `503`.
- **Reste au Sprint 2** : appliquer une auth (session OU token) aux 4 endpoints GET du router, actuellement consommés par l'UI interne. Impact utilisateur nul pour le moment (GETs servent la carte interne).
- **À faire côté ops** : générer le token (`openssl rand -hex 24`), le pousser dans `.env` prod, mettre à jour le flow Power Automate pour envoyer le header `X-API-Token`.

## Sprint 2 — Élevés (J+21) ✅ LIVRÉ (A2.2 reporté en Sprint 2.5)

Livré sur la branche `claude/security-sprint2`. Un item (A2.2 — CSP nonce) a été reporté parce que 63 templates contiennent des `<script>`/`<style>` inline ; une migration atomique risquait la régression UI. Voir **Sprint 2.5** ci-dessous pour le plan d'atterrissage.

### A2.1 CSRF form strict (S-06) ✅

- `app/csrf.py` réécrit : le middleware consomme le body des requêtes POST/PUT/PATCH/DELETE (en dehors des préfixes exemptés), extrait le champ `csrf_token` (urlencoded + multipart via `Request.form()`), valide avec `secrets.compare_digest` contre le cookie, puis replaye le body pour les handlers aval — les appels `await file.read()` / `await request.form()` continuent de fonctionner normalement.
- Buffer de body plafonné à 10 MiB (`MAX_CSRF_BODY_BYTES`) ; au-delà, la requête est refusée avec consigne d'utiliser le header `x-csrf-token`.
- Body JSON sans header → refusé (`CSRF_HEADER_NAME` requis pour les appels AJAX/fetch).

### A2.2 CSP renforcée (S-07) ⏸ Reporté en Sprint 2.5

- Audit effectué : **63 templates** sur 120+ contiennent des `<script>` ou `<style>` inline (compteur `grep -c "<script>|<style>" app/templates -r`). Une migration de masse en une seule PR mettrait le front à risque.
- Plan d'atterrissage (branche `claude/security-sprint2_5-csp`) :
  1. Externaliser l'inline de `base.html` (HTMX CSRF injection, CSS sidebar-clock) vers `/static/js/csrf-htmx.js` et `/static/css/base-inline.css`.
  2. Ajouter un middleware léger générant un `request.state.csp_nonce` par requête.
  3. Étape progressive : modifier le template le plus sensible (dashboard, planning) en premier, vérifier visuellement + Lighthouse, propager module par module.
  4. Retirer `'unsafe-inline'` de `script-src` puis `style-src` une fois toutes les tuiles migrées.

### A2.3 Hash des tokens portail (S-08) ✅

- `app/models/portal_access_log.py` : colonne `token` remplacée par `token_hash CHAR(64)` (sha256 hex) avec `index=True`.
- `app/utils/portal_security.py` : fonction `hash_portal_token()` ajoutée, `log_portal_access` stocke désormais uniquement le hash.
- `app/routers/admin_router.py` (RGPD erase) : la recherche des logs associés à un booking utilise `token_hash == hash_portal_token(booking.token)`.
- **À faire côté ops (bases existantes)** : le reset + bootstrap recrée le schéma. Pour une base conservée :
  ```sql
  ALTER TABLE portal_access_logs ADD COLUMN token_hash CHAR(64);
  UPDATE portal_access_logs SET token_hash = encode(digest(token, 'sha256'), 'hex');
  ALTER TABLE portal_access_logs DROP COLUMN token;
  CREATE INDEX ix_portal_access_logs_token_hash ON portal_access_logs(token_hash);
  ```

### A2.4 Alembic baseline (S-09) ✅

- `alembic==1.14.0` ajouté à `requirements.txt`.
- Scaffolding : `alembic.ini` à la racine, `migrations/env.py` (async), `migrations/script.py.mako`, `migrations/README.md`.
- Révision `0001_baseline_post_sprint2.py` — no-op qui ancre l'historique Alembic à l'état de schéma produit par `Base.metadata.create_all` à la fin du Sprint 2.
- `env.py` lit `DATABASE_URL` depuis `app.config.get_settings()`, expose `Base.metadata` comme cible, supporte mode offline (génération SQL) + online async.
- **À faire côté ops** : après un reset, `docker exec towt-app-v2 alembic stamp 0001_baseline_post_sprint2`. Les nouvelles migrations partent du module existant (`alembic revision --autogenerate -m "xxx"`).
- **Sprint 3+** : retirer `Base.metadata.create_all` de `init_db` et le remplacer par `alembic upgrade head` au démarrage (une fois confiance acquise sur le process).

### A2.5 Rate-limit persistant (S-10) ✅ (option B — table DB)

- Nouvelle table `rate_limit_attempts(id, scope, identifier, attempted_at)` + index composite `(scope, identifier, attempted_at)`.
- Service `app/services/rate_limit.py` : `is_rate_limited()`, `record_attempt()`, `clear_attempts()`. Purge opportuniste des rows > 24 h à chaque écriture.
- `app/routers/auth_router.py` : les dicts `_login_attempts` en mémoire sont remplacés par le service. Sur login réussi, `clear_attempts()` efface les tentatives pour cette IP.
- `app/utils/portal_security.py` : `check_token_rate_limit()` et `record_token_attempt()` deviennent async, prennent une `AsyncSession`. Les 2 callers (`cargo_router.py`, `passenger_ext_router.py`) mis à jour pour `await`.
- Option Redis (plus performante pour scale-out) gardée en backlog Sprint 3+ si le volume le justifie.

### Sprint 1 leftover — Auth session sur `/api/tracking/*` GETs ✅

- Les 4 endpoints `GET /api/tracking/positions/{vessel_id}`, `/latest`, `/leg/{leg_id}/track`, `/navigation-kpis` exigent désormais `Depends(get_current_user)`. Les appels internes (UI) continuent de fonctionner via le cookie de session ; les appels anonymes reçoivent `303 /login`.
- `POST /api/tracking/upload` reste sur `X-API-Token` (Power Automate).

## Sprint 3 — Moyens (J+60)

### A3.1 Révocation session server-side (S-11)

- Table `user_sessions(id, user_id, jti, created_at, last_seen_at, revoked_at, ip_hash, user_agent)`.
- `create_session_token()` : insert + retourne `jti` dans le payload `itsdangerous`.
- `get_current_user` : vérifie `jti` non-revoqué + non-expiré.
- Endpoint `POST /admin/my-account/logout-all` → set `revoked_at = now()` pour toutes les sessions du user.
- Auto-revoke sur changement mot de passe / rôle.

### A3.2 Maintenance mode en BDD (S-12)

- Table `app_settings(key VARCHAR PRIMARY KEY, value JSONB, updated_at)`.
- Migrer `MAINTENANCE_FLAG` fichier → row `('maintenance', {"enabled": true, "message": "..."})`.
- Cache 5 s in-memory pour éviter une requête DB par hit.

### A3.3 Politique mots de passe (S-14)

- Ajouter `zxcvbn-python` aux requirements.
- Validation côté serveur : `zxcvbn.zxcvbn(password)['score'] >= 3`.
- Blacklist top-1k mots de passe fuités (haveibeenpwned `pwned-passwords-top-1k.txt` embarqué).
- Historique des 5 derniers hashs dans `user_password_history`.

### A3.4 Quota upload (S-15)

- Middleware `BodySizeLimitMiddleware` dans `app/main.py` :
  ```python
  if int(request.headers.get("content-length", 0)) > MAX_UPLOAD_SIZE:
      return JSONResponse({"detail": "Payload too large"}, 413)
  ```
- Limites : 10 MB par défaut, 50 MB pour `/api/tracking/upload` (override par dépendance).

### A3.5 CORS HTTPS-only (S-16)

- Retirer `http://51.178.59.174` de `allow_origins`.
- Forcer redirect HTTP → HTTPS au niveau nginx (déjà fait probablement, à vérifier).

### A3.6 Anonymisation IP (S-17)

- Ajouter `IP_HASH_PEPPER` dans `Settings` (rotatif).
- `hash_ip(ip) = sha256(ip + PEPPER).hexdigest()[:16]`.
- Migrer `portal_access_log.ip_address` (string IP brute) → `ip_hash CHAR(16)`.
- Idem pour `activity_log` si applicable.

## Backlog continu

### B1 Headers COOP/COEP/CORP (S-18)

Ajouter dans `SecurityHeadersMiddleware` (`app/main.py:105`) :
```python
response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
# COEP "require-corp" demande audit images externes (tiles OSM)
```

### B2 Scan secrets pre-commit + CI

- `gitleaks` en pre-commit hook (`pre-commit run --all-files`).
- `gitleaks detect` en CI GitHub Actions sur chaque PR.

### B3 SAST en CI

- `bandit -r app/` (sécurité Python).
- `semgrep --config=p/python --config=p/security-audit`.
- `pip-audit` (CVE deps).
- Bloquant en CI sur findings high-severity.

### B4 Pentest externe annuel

- Premier pentest : 3 mois après go-live V2.
- Société recommandée : Synacktiv, Quarkslab, Devoteam Risk.
- Périmètre : webapp + API tracking + portails token.
- Bug bounty privé HackerOne / YesWeHack en complément.

### B5 RTO/RPO documentés

- RPO cible : 1 h (backup pg_dump horaire au lieu du quotidien actuel).
- RTO cible : 4 h (procédure restore documentée).
- Test restore trimestriel obligatoire.

### B6 Security.txt

`/.well-known/security.txt` déjà en place (`app/main.py:175-181`). À mettre à jour lors du rebrand V2 (nouveau email `security@kairos.app` ou similaire).
