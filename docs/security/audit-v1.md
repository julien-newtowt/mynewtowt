# Audit de sécurité — my_TOWT V1

**Date** : 2026-04-17
**Périmètre** : code source `/home/user/mytowt` à la date de la mise en liquidation TOWT, branche `claude/reset-database-disconnect-UTD94`.
**Référentiel** : OWASP Top 10 2021, RGPD art. 5/32, principes least-privilege.

## Méthodologie

Revue manuelle ciblée sur :
- Authentification & sessions (`app/auth.py`, `app/routers/auth_router.py`).
- Protection CSRF (`app/csrf.py`).
- Headers de sécurité & CORS (`app/main.py`).
- Maintenance & gestion des accès (`app/maintenance.py`, `app/permissions.py`).
- Endpoints publics & portails token-based (`tracking_router.py`, portails `/p/`, `/passenger/`, `/planning/share/`).
- Configuration & secrets (`app/config.py`, `docker-compose.yml`).
- Exposition de la base de données.

Pas de scan dynamique (DAST) ni de pentest externe à ce stade — recommandation post-V2 (cf. action plan).

## Classification

🔴 Critique · 🟠 Élevé · 🟡 Moyen · 🟢 Info.

## Constats

### S-01 🔴 — `SECRET_KEY` par défaut en dur

**Fichier** : `app/config.py:17`

```python
SECRET_KEY: str = "towt_secret_key_change_in_production_2025"
```

Cette clé sert à signer les cookies de session via `itsdangerous` (`app/auth.py:13`). Si la clé par défaut est utilisée en production, **n'importe qui peut forger un cookie de session valide pour n'importe quel `user_id`** et obtenir un accès administrateur.

`app/main.py:65-73` ne fait qu'émettre un warning dans les logs. Il faut **refuser le démarrage** si `APP_ENV=production` et `SECRET_KEY` par défaut.

### S-02 🔴 — Mot de passe Postgres par défaut en dur

**Fichiers** : `app/config.py:14`, `docker-compose.yml:8,27`

```yaml
- POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-towt_secure_2025}
```

Le mot de passe `towt_secure_2025` est documenté publiquement (CLAUDE.md, code source). Toute fuite du dépôt expose la base.

### S-03 🔴 — Port Postgres exposé à l'hôte

**Fichier** : `docker-compose.yml:31-32`

```yaml
ports:
  - "5433:5432"
```

Le port 5433 est binder en `0.0.0.0` par défaut. Combiné à S-02, l'exposition est exploitable depuis le réseau hôte / Internet selon la conf firewall VPS OVH.

### S-04 🔴 — Endpoint `/api/tracking/upload` sans authentification

**Fichiers** : `app/routers/tracking_router.py:128`, `app/main.py:209`

```python
app.include_router(tracking_router)  # API — no auth (called by Power Automate)
```

L'endpoint accepte un `UploadFile` CSV de positions navire **sans aucun token, ni auth, ni rate-limit**. Tout attaquant peut :
- Injecter de fausses positions GPS (perturbation tracking, sabotage analytique).
- Créer des entrées en masse pour saturer la table `vessel_positions` (DoS applicatif).

### S-05 🔴 — Mot de passe admin par défaut documenté

**Référence** : `CLAUDE.md` (section "Default login").

```
admin / towt2025
```

Aucun mécanisme de rotation forcée au premier login (avant cette branche). Cette branche introduit `User.must_change_password` (champ DB) — il manque le middleware d'enforcement (à livrer Sprint 1).

### S-06 🟠 — CSRF double-submit : forms sans header

**Fichier** : `app/csrf.py:117-131`

Le middleware compare le token cookie au header `X-CSRF-Token` pour les requêtes HTMX. Pour les **formulaires HTML classiques sans header**, la validation est déléguée au champ form `csrf_token` injecté par `csrf_input()`. Mais aucune dépendance route ne vérifie réellement ce champ — la protection repose uniquement sur `SameSite=Lax`.

`SameSite=Lax` autorise `top-level navigation POST` (ex. clic sur un lien menant à un site malveillant qui auto-soumet une form). Vulnérabilité limitée mais réelle.

### S-07 🟠 — CSP autorise `'unsafe-inline'`

**Fichier** : `app/main.py:108-116`

```
script-src 'self' 'unsafe-inline' https://unpkg.com;
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
```

`'unsafe-inline'` neutralise la défense XSS de la CSP. Les templates contiennent de nombreux `<script>` et `<style>` inline (`base.html:12-18` notamment).

### S-08 🟠 — Tokens portail stockés en clair

**Fichier** : `app/utils/portal_security.py:46`

```python
log_entry = PortalAccessLog(
    portal_type=portal_type,
    token=token,
    ...
)
```

`portal_access_log.token` stocke le token brut. Une fuite BDD = accès lecture/écriture immédiat à tous les portails clients (commandes, packing lists, satisfaction).

### S-09 🟠 — Pas d'Alembic, migrations raw SQL ad-hoc

**Référence** : `migration.sql`, `CLAUDE.md` ("Migrations: raw SQL `ALTER TABLE` (no Alembic yet)").

Risque de divergence schéma prod / dev / staging, perte d'historique des changements DDL, impossibilité de rollback propre.

### S-10 🟠 — Rate-limit login en mémoire process

**Fichier** : `app/routers/auth_router.py:20`

```python
_login_attempts: dict[str, list[float]] = defaultdict(list)
```

Le compteur est reset à chaque redémarrage du conteneur, ne survit pas entre instances (si scale-out futur), et n'est pas auditable.

### S-11 🟡 — Pas de révocation server-side des sessions

**Fichier** : `app/auth.py:26-35`

`itsdangerous` est stateless : un cookie signé reste valide pendant 8h même après désactivation de l'utilisateur (`User.is_active=False`). En réalité, `get_current_user` filtre `is_active=True` (`app/auth.py:53`), donc la révocation marche pour les comptes désactivés. **MAIS** il n'y a pas de mécanisme de **logout global** ni de **forced re-auth** lors d'un changement de mot de passe ou de rôle.

### S-12 🟡 — Maintenance flag = fichier système

**Fichier** : `app/maintenance.py:19`

```python
MAINTENANCE_FLAG = "/app/data/maintenance.flag"
```

Si le volume `/app/data` est partagé en écriture (mauvaise conf docker-compose), un attaquant pourrait basculer la maintenance.

### S-13 🟡 — Rôle inconnu = fail-close silencieux

**Fichier** : `app/permissions.py:170-173`

`_resolve_role()` mappe les rôles legacy ; un rôle inconnu retourne un set de permissions vide. Le comportement est correct (deny-by-default) mais aucune trace log → debugging difficile, et un user peut se retrouver bloqué sans signal explicite.

### S-14 🟡 — Pas de politique mots de passe

Aucune validation de complexité (longueur, classes de caractères), aucune vérification contre les bases de fuites (haveibeenpwned), aucun historique. Le hash bcrypt protège le stockage mais pas le choix.

### S-15 🟡 — Pas de limite de taille sur `UploadFile`

**Fichier** : `app/routers/tracking_router.py:128` (et autres routes upload : claims, cargo).

`await file.read()` lit l'intégralité du fichier en mémoire sans limite. Un attaquant peut envoyer un fichier de plusieurs GB → OOM container.

### S-16 🟡 — CORS autorise HTTP clair

**Fichier** : `app/main.py:96`

```python
allow_origins=["https://my.towt.eu", "http://51.178.59.174"],
```

L'origine HTTP est autorisée alongside HTTPS — risque MITM sur la version HTTP.

### S-17 🟡 — IP loggées non-anonymisées

**Fichier** : `app/utils/portal_security.py:49`

`PortalAccessLog.ip_address` stocke l'IP brute. RGPD art. 5 (minimisation) recommande hashing/anonymisation pour les logs > 30 jours.

### S-18 🟢 — Pas de headers COOP/COEP/CORP

**Fichier** : `app/main.py:104-122`

`Cross-Origin-Opener-Policy`, `Cross-Origin-Embedder-Policy`, `Cross-Origin-Resource-Policy` absents — protection contre Spectre/cross-origin attacks non-renforcée.

### S-19 🟢 — Intégrations externes : rotation secrets

**Fichier** : `app/config.py:25` (`PIPEDRIVE_API_TOKEN`), `app/utils/revolut.py`.

À vérifier : politique de rotation, stockage en gestionnaire de secrets (Vault/Doppler), révocation après liquidation TOWT.

### S-20 🟢 — Pas de tests sécurité automatisés

Absence de `bandit`, `semgrep`, `pip-audit`, `gitleaks` dans la CI — le code n'est pas scanné systématiquement.

## Synthèse

| Sévérité | Nombre | % |
|----------|--------|---|
| 🔴 Critique | 5 | 25 % |
| 🟠 Élevé | 5 | 25 % |
| 🟡 Moyen | 7 | 35 % |
| 🟢 Info | 3 | 15 % |
| **Total** | **20** | 100 % |

Voir [`action-plan.md`](action-plan.md) pour la planification de remédiation.
