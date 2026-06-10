# Security Review — Politique & contrôles

> Étape 7 — `/security-review`. Élève le niveau de sécurité au regard
> des données collectées (clients, salaires, sinistres, paiements).

## 1. Modèle de menace (STRIDE)

| Menace | Surface | Mitigation principale |
|--------|---------|----------------------|
| **S**poofing | Login, tokens portail, API key | MFA TOTP + WebAuthn ; tokens HMAC signés ; rotation API key |
| **T**ampering | Données BL, plan d'arrimage, prix | Audit log immutable + hash signature des PDF |
| **R**epudiation | Modifications de leg, signatures | Activity log signé HMAC + horodatage NTP |
| **I**nformation Disclosure | Données salariales, médicales, BL | RBAC strict + chiffrement at-rest (pgcrypto) |
| **D**enial of Service | Réservation publique, /chat, tracking | Rate limit + WAF + autoscaling |
| **E**levation of Privilege | Manipulation rôles, injections | RBAC + ORM exclusif + tests d'injection CI |

## 2. Données sensibles classifiées

| Classe | Exemples | Contrôles |
|--------|----------|-----------|
| **C1 Public** | Catalogue routes, capacités | Cache CDN, pas de PII |
| **C2 Interne** | Plannings ops, KPI flotte | Auth requise |
| **C3 Confidentiel** | Pricing client, marges | RBAC `commercial/manager` |
| **C4 Sensible PII** | Identités marins/clients | RBAC + audit ; suppression RGPD |
| **C5 Critique** | Salaires, données médicales équipage, données bancaires | Chiffrement at-rest + audit complet ; accès `RH/admin` |

Mappings :

- `users.hashed_password` → C5
- `crew_members.medical_*` → C5
- `client_accounts.hashed_password` → C5
- `client_invoices` → C4
- `bookings`, `orders`, `packing_lists` → C3
- `legs`, `vessels`, `ports` → C2
- Activity logs, rate limit → C2
- Catalogue public bookings → C1

## 3. Contrôles d'accès

### 3.1 Authentification multi-facteurs

- **Collaborateurs** :
  - Login + password bcrypt obligatoire.
  - **MFA TOTP** activable, **obligatoire pour rôles** `administrateur`,
    `manager_maritime`, `commercial` à partir de S+10.
  - WebAuthn (passkey) optionnel — recommandé pour admin.
- **Clients** :
  - Login + password bcrypt.
  - MFA TOTP recommandé, obligatoire pour comptes `key_account`.
- **Service-to-service** :
  - API key bearer + IP allowlist.
  - Rotation 90 j, alerte 30 j avant expiration.

### 3.2 Autorisation (RBAC)

Matrice étendue (cf. `app/permissions.py`) :

```
              planning commercial cargo escale finance kpi captain crew claims mrv rh booking
administrateur    CMS    CMS      CMS   CMS    CMS     CMS  CMS    CMS    CMS   CMS CMS CMS
operation         CM     CM       CMS   CMS    -       C    CM     CM     CMS   CM  C   CM
armement          C      -        -     C      -       C    C      CMS    -     C   C   -
technique         C      C        C     CMS    -       C    CM     C      C     CM  C   -
data_analyst      C      C        C     C      CMS     C    C      C      C     CM  C   C
marins            C      -        C     C      -       C    C      C      -     C   C   -
commercial        C      CMS      CM    C      -       C    C      -      -     -   C   CM
manager_maritime  CM     CM       CM    CM     -       C    CMS    CM     CM    CM  C   CM
```

Permissions au niveau **router** (entrée) ET niveau **service** (logique).

### 3.3 Compte client

- Niveaux d'accès : `viewer` (consultation), `editor` (création
  booking), `admin_company` (gestion équipe interne).
- Plusieurs utilisateurs par compte société : table `client_users` à
  prévoir (V3.1).

## 4. Sécurité des sessions

| Paramètre | Valeur |
|-----------|--------|
| Cookie name (staff) | `towt_session` |
| Cookie name (client) | `towt_client_session` |
| HttpOnly | ✅ |
| Secure | ✅ (sauf dev local) |
| SameSite | `Lax` |
| Max-Age | 8 h staff / 30 j client (avec refresh) |
| Signature | HMAC SHA-256 via `itsdangerous` + `SECRET_KEY` |
| Rotation `SECRET_KEY` | Tous les 6 mois, support 2 clés en parallèle |

## 5. Stockage des secrets

- `SECRET_KEY`, `DATABASE_URL`, `STRIPE_SK`, `ANTHROPIC_KEY` stockés
  dans **Doppler** (ou Hashicorp Vault) — jamais committés.
- Loadés au démarrage via `pydantic-settings`, refusent les valeurs
  par défaut (cf. règles V2 conservées).
- Refuser de démarrer si :
  - `SECRET_KEY` < 32 caractères
  - `SECRET_KEY` dans `WEAK_SECRETS`
  - `DATABASE_URL` contient `change_me` ou `towt_secure_2025`
  - `STRIPE_SK` commence par `sk_test_` en prod
  - `ANTHROPIC_KEY` manquant si `chatbot_kairos_ai` actif

## 6. Chiffrement at-rest

| Donnée | Mécanisme |
|--------|-----------|
| Postgres entier | LUKS sur le volume Docker (host) |
| Colonnes C5 (salaires, médical) | `pgcrypto` `pgp_sym_encrypt` avec clé KMS |
| PDF BL/facture | S3 bucket chiffré SSE-S3 |
| Backups | `pg_dump` chiffré GPG vers stockage froid |

Rotation des clés de chiffrement annuelle ou sur événement (départ
admin).

## 7. Chiffrement en transit

- HTTPS obligatoire : nginx + Let's Encrypt (TLS 1.3 only).
- HSTS `max-age=31536000; includeSubDomains; preload`.
- Connexions DB en TLS (`sslmode=require`).
- API externes (Stripe, Anthropic, Windy) via HTTPS uniquement.

## 8. Headers de sécurité

```nginx
add_header Content-Security-Policy "
  default-src 'self';
  script-src 'self' https://unpkg.com https://js.stripe.com;
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com;
  img-src 'self' data: https://*.tile.openstreetmap.org https://api.mapbox.com;
  connect-src 'self' https://api.stripe.com https://api.mapbox.com https://nominatim.openstreetmap.org;
  frame-src https://js.stripe.com;
  frame-ancestors 'self';
  base-uri 'self';
  form-action 'self' https://checkout.stripe.com;
" always;
add_header X-Content-Type-Options nosniff;
add_header X-Frame-Options SAMEORIGIN;
add_header Referrer-Policy strict-origin-when-cross-origin;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(self)";
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload";
```

CSP audité par `Mozilla Observatory` — cible note A+.

## 9. Protection applicative

### 9.1 CSRF

- Double-submit cookie (`towt_csrf`) + header `x-csrf-token`.
- HTMX injecte automatiquement le token via `hx-headers` global dans
  `base.html`.
- Token regénéré à chaque login.

### 9.2 Rate limiting

| Scope | Limite | Sanction |
|-------|--------|----------|
| `login` | 5 échecs / 15 min / IP | Verrou 15 min |
| `client_login` | 5 échecs / 15 min / IP | Verrou 15 min |
| `portal_token` | 10 / min / IP | 429 |
| `api_v1` | 100 / min / key | 429 |
| `booking_create` (anon) | 10 / jour / IP | 429 |
| `chat_message` | 50 / jour / user | 429 + quota mensuel |

Persistant en table `rate_limit_attempts`. Rotation lazy + cron purge
quotidien.

### 9.3 Anti-injection

- Pas de SQL brut. Toutes les requêtes via SQLAlchemy ORM ou
  `text(...).bindparams()`.
- Whitelist explicite `ALLOWED_TABLES` pour le module admin.
- Validation Pydantic à toutes les entrées.
- HTML escaping par défaut (Jinja2 autoescape).
- Sanitization XSS sur les champs riches via `bleach`.

### 9.4 File upload

- Whitelist d'extensions par contexte : PDF, JPG, PNG, XLSX.
- Limite de taille par fichier (10 MB par défaut, 50 MB pour FDS PDF).
- Antivirus ClamAV sur le serveur (cron toutes 4h, quarantaine).
- Fichiers stockés hors webroot (`/var/app/uploads/`), servis via
  endpoint authentifié (pas accès direct).
- Magic bytes vérifiés (`python-magic`) pour confirmer le mime-type.

### 9.5 Anti-bruteforce token portail

- Tokens portail format UUID hex 24 caractères (entropy 96 bits).
- `portal_access_logs` enregistre `sha256(token)`, pas le token clair.
- Rate limit + alerte si > 50 essais sur un même token hash en 24 h.

## 10. Logs & audit

### 10.1 Activity logs (immutables)

Table `activity_logs` append-only, jamais d'UPDATE/DELETE depuis l'app.
Hash chaîné (`prev_hash + current_payload → sha256`) pour détecter
toute manipulation a posteriori.

Événements tracés :

- Login OK / échec / logout
- Création / modification / suppression d'entités sensibles
  (users, legs, bookings, invoices, claims)
- Accès à une route C4/C5
- Export de données (CSV, ZIP)
- Activation/désactivation feature flag
- Lecture portail client
- Modification permissions

### 10.2 Audit trimestriel

- Revue des comptes inactifs > 90 j → désactivation.
- Revue des permissions accordées.
- Revue des API keys et rotation.
- Revue des feature flags actifs.

### 10.3 SIEM (V3.1)

Intégration Grafana Loki ou Wazuh pour corrélation logs nginx +
application + Postgres.

## 11. Sécurité du booking et du paiement

### 11.1 Booking

- Anti-double booking : verrou transactionnel `FOR UPDATE` sur le leg.
- Validation côté serveur de la capacité avant `confirmed`.
- Anti-fraude : check VAT/SIRET via API publique (V3.1).
- Limitation à 10 bookings/jour/IP non-authentifié.

### 11.2 Paiement Stripe

- Stripe Checkout hosted (scope PCI minimal — SAQ-A).
- Aucun numéro de carte transitant par nos serveurs.
- Webhook Stripe signé HMAC vérifié.
- Idempotency key obligatoire.
- Réconciliation quotidienne avec Stripe Dashboard.

## 12. Sécurité du chatbot Kairos AI

- Outils en lecture seule uniquement (V1).
- Chaque appel d'outil re-vérifie les permissions du user (jamais
  confiance dans le LLM).
- Détection prompt injection :
  - Pattern matching simple sur `ignore previous`, `system:`,
    `forget instructions`, `you are now`, etc.
  - Refus + log + alerte si pattern détecté.
- Logging exhaustif des prompts + réponses pour audit RGPD.
- Quota mensuel global Anthropic (200 EUR) avec alerte 80 %.
- Données sensibles (C5) masquées avant injection dans le prompt
  RAG (regex sur SSN-like, IBAN-like, etc.).

## 13. Backup & restore

- `pg_dump` quotidien chiffré GPG, retention 90 j.
- Hebdomadaire vers S3 froid (Glacier) retention 7 ans.
- Test de restore mensuel automatique en staging.
- RPO : 24 h. RTO : 4 h.
- Documentation runbook : `docs/operations/01-runbook.md`.

## 14. Incident response

### 14.1 Niveaux

| Niveau | Définition | SLA notification |
|--------|-----------|------------------|
| **SEV-1** | Fuite de données / indisponibilité critique | 15 min |
| **SEV-2** | Dégradation forte / faille exploitable | 1 h |
| **SEV-3** | Bug fonctionnel important sans fuite | 4 h |
| **SEV-4** | Mineur | jour ouvré |

### 14.2 Procédure

1. Détection (alerte Sentry, user signalement, audit).
2. Triage par on-call (cf. `docs/operations/02-oncall.md`).
3. Containment (désactiver feature flag, bloquer IP, révoquer clé).
4. Eradication (patch).
5. Recovery (vérif + monitoring renforcé 24h).
6. Post-mortem 48 h après resolution (sans culpabilité).
7. Communication client si SEV-1/2 RGPD (sous 72 h CNIL).

### 14.3 Contacts

- Sécurité : `security@newtowt.eu`
- DPO : `dpo@newtowt.eu`
- CERT : signalement via `/.well-known/security.txt`
- Page status : `status.my.newtowt.eu`

## 15. Conformité RGPD

| Droit | Implémentation |
|-------|----------------|
| Accès | `GET /me/account/export` (ZIP données) |
| Rectification | `GET /me/account/edit` |
| Suppression | `POST /me/account/delete` (purge + 30 j réversibilité) |
| Portabilité | `GET /me/account/export?format=json` |
| Opposition | Préférences notifications dans `/me/account` |
| Limitation traitement | DPO sur demande, ticket SLA 30 j |

Registre des traitements : `docs/security/02-rgpd-registry.md`.

## 16. Tests de sécurité

### 16.1 En continu (CI)

- `bandit` — analyse statique Python.
- `safety check` — vulnérabilités dépendances Python.
- `gitleaks` — détection secrets dans le repo.
- `trivy` — scan vulnerabilité image Docker.
- `npm audit` (si dépendances JS).

### 16.2 Périodiques

- Pen-test interne trimestriel (équipe sécurité interne ou stagiaire).
- Pen-test externe annuel (cabinet certifié ANSSI ou CREST).
- Bug bounty informel — récompense en bouteilles de vin/chocolats.

### 16.3 Disclosure policy

Voir `/.well-known/security.txt` (exposé publiquement) :

```
Contact: mailto:security@newtowt.eu
Expires: 2026-12-31T23:59:59.000Z
Preferred-Languages: fr, en
Canonical: https://my.newtowt.eu/.well-known/security.txt
Policy: https://my.newtowt.eu/about/security-policy
```

## 17. Plan d'action sécurité — Sprints

| Sprint | Action |
|--------|--------|
| Sec-S1 | MFA TOTP + WebAuthn pour staff |
| Sec-S2 | Chiffrement at-rest colonnes C5 |
| Sec-S3 | CSP renforcée + Observatory A+ |
| Sec-S4 | Rate limits étendus + bot detection |
| Sec-S5 | Audit pentest externe |
| Sec-S6 | Implémentation activity_logs chaînés |
| Sec-S7 | RGPD self-service (export, suppression) |
| Sec-S8 | SIEM Loki + alertes Grafana |
| Sec-S9 | Disclosure policy & bug bounty |
| Sec-S10 | Audit annuel SOC 2 lite (V3.2) |
