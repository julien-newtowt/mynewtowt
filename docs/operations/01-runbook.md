# Runbook opérationnel

> Procédures opérationnelles pour exploiter et dépanner `mynewtowt`.

## 1. Comptes & accès

| Système | URL | Comment se connecter |
|---------|-----|---------------------|
| Application | `https://my.newtowt.eu` | Login user + MFA |
| Staging | `https://staging.my.newtowt.eu` | Login user + MFA |
| GitHub | `github.com/juliengonde-5g/mynewtowt` | OAuth + MFA |
| Hébergeur OVH | `manager.ovh.com` | SSO interne |
| Stripe | `dashboard.stripe.com` | SSO interne |
| Sentry | `sentry.io/newtowt` | SSO interne |
| Grafana | `metrics.my.newtowt.eu` | LDAP interne |
| Metabase | `analytics.my.newtowt.eu` | LDAP interne |

Tout accès admin nécessite **MFA**. Rotation mensuelle des secrets en
Doppler.

## 2. Démarrage rapide d'un environnement local

```bash
git clone git@github.com:juliengonde-5g/mynewtowt.git
cd mynewtowt
cp .env.example .env
docker compose up -d
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.seed
open http://localhost:8000
```

Compte par défaut local : `admin@local` / `change-me-now`.

## 3. Démarrage / arrêt en production

```bash
# Statut
./scripts/status.sh

# Démarrer
./scripts/start.sh

# Arrêter
./scripts/stop.sh

# Redémarrer en cas de fuite mémoire
./scripts/restart.sh

# Maintenance mode
./scripts/maintenance.sh on
./scripts/maintenance.sh off
```

## 4. Déploiement

Scripts livrés dans `scripts/` :

| Script | Rôle | Détail |
|--------|------|--------|
| `deploy.sh` | Déploiement prod ou staging | 9 étapes idempotentes avec gating health + smoke |
| `rollback.sh` | Restauration snapshot | MTTR cible < 15 min |
| `smoke-tests.sh` | Vérifications post-deploy | Probes HTTP de 12 endpoints critiques |
| `maintenance.sh` | Bandeau maintenance | `on` / `off` / `status` |

```bash
# Déploiement production sur la version HEAD courante
./scripts/deploy.sh

# Déploiement staging d'un tag précis
./scripts/deploy.sh --env staging --version v3.0.1

# Rollback au dernier snapshot pris par deploy.sh
./scripts/rollback.sh

# Rollback à un snapshot précis
./scripts/rollback.sh --list
./scripts/rollback.sh backups/pre-v3.0.0-20260518T160000Z.dump

# Mode maintenance manuel (sans redeploy)
./scripts/maintenance.sh on
./scripts/maintenance.sh off
./scripts/maintenance.sh status

# Tests de fumée externes
./scripts/smoke-tests.sh https://staging.my.newtowt.eu
```

### 4.1 Workflow `deploy.sh`

1. Pre-flight : docker / git / curl présents, `.env` valide, refus
   prod si SECRET_KEY ou DB password faibles, ≥ 2 GB disque libre.
2. Snapshot PostgreSQL `pg_dump -Fc` → `backups/pre-<ver>-<ts>.dump`,
   rotation > `BACKUP_RETENTION_DAYS` (30 j par défaut).
3. Tag git `release/<ver>-<ts>` (non bloquant).
4. Build image via `docker compose build app`.
5. Maintenance ON (bandeau 503 sauf `/health` et `/static/*`).
6. Alembic `upgrade head` dans un conteneur `run --rm`. Si échec :
   restore snapshot et exit 1.
7. Rolling restart `up -d --force-recreate app`.
8. Wait `/health` pendant `HEALTH_TIMEOUT_SECONDS` (90 s). Échec ⇒
   rollback + exit 2.
9. Smoke tests (12 endpoints). Échec ⇒ rollback + exit 2.
10. Maintenance OFF + report.

## 5. Backups

### 5.1 Backup quotidien automatique

Cron sur le host :

```
0 3 * * * /opt/mynewtowt/scripts/backup.sh
```

Workflow :

1. `pg_dump -Fc` du conteneur `db`.
2. Chiffrement GPG (clé publique opérations).
3. Upload S3 OVH bucket `mynewtowt-backups` chiffré SSE-S3.
4. Rotation locale 7 j / S3 90 j chaud + 7 ans froid.
5. Notification Slack `#ops` (succès/échec).

### 5.2 Restore

```bash
# Liste des backups
./scripts/list-backups.sh

# Restore (mode maintenance auto + restore + redémarrage)
./scripts/restore.sh backup_2026_05_17_03_00.dump.gpg
```

Test mensuel automatique sur staging.

## 6. Migration de base

### 6.1 Standard

```bash
# Sur staging
docker compose exec app alembic upgrade head

# Sur prod (avec verrou)
./scripts/migrate-prod.sh
```

`migrate-prod.sh` :

1. Active maintenance mode.
2. Snapshot Postgres.
3. `alembic upgrade head`.
4. Smoke tests.
5. Désactive maintenance mode.
6. Notification Slack.

### 6.2 Rollback migration

```bash
docker compose exec app alembic downgrade -1
```

Si la migration n'est pas reversible, restaurer le snapshot DB.

## 7. Monitoring

### 7.1 Dashboards Grafana

- **App Health** : `metrics.my.newtowt.eu/d/app-health`
- **DB Performance** : `metrics.my.newtowt.eu/d/db-perf`
- **Business KPIs** : `metrics.my.newtowt.eu/d/biz-kpi`
- **Release Health** : `metrics.my.newtowt.eu/d/release-health`

### 7.2 Alertes critiques

| Alerte | Source | Action immédiate |
|--------|--------|-----------------|
| App down | Prom | `./scripts/restart.sh` + check logs |
| DB down | Prom | Check Postgres logs, FS, mémoire |
| Disk > 85 % | Prom | `./scripts/cleanup-logs.sh` |
| Cert TLS < 14 j | Cron | `./scripts/renew-cert.sh` |
| Backup failed | Slack | Vérifier crontab + perm S3 |
| Error rate > 5 % | Sentry | Investiguer top errors |

### 7.3 Logs

```bash
# Tail app logs
docker compose logs -f app

# Filtrer erreurs
docker compose logs app | grep ERROR | jq .

# Logs nginx
sudo journalctl -u nginx -f
```

## 8. Incidents fréquents

### 8.1 "L'app ne répond plus"

1. `./scripts/status.sh` → quel conteneur est down ?
2. Si `app` :
   - `docker compose logs app --tail 200`
   - Si OOM → `./scripts/restart.sh`
   - Si Postgres errors → check `db` container
3. Si `db` :
   - `docker compose logs db --tail 200`
   - Si FS plein → `./scripts/cleanup-logs.sh`
4. Si nginx :
   - `sudo systemctl status nginx`
   - `sudo nginx -t && sudo systemctl restart nginx`

### 8.2 "Un user est bloqué"

1. Lui demander son username + erreur affichée.
2. `docker compose exec app python -m scripts.check_user <username>`
3. Si `must_change_password` → expliquer flow.
4. Si compte désactivé → réactiver via admin.
5. Si rate limit (5 échecs) → patience 15 min ou reset manuel :
   ```bash
   docker compose exec db psql -U postgres -d towt \
     -c "DELETE FROM rate_limit_attempts WHERE identifier='<ip>';"
   ```

### 8.3 "Booking bloqué en submitted"

1. Vérifier email équipe ops envoyé.
2. Vérifier `bookings.status` = `'submitted'`.
3. Demander à un commercial de confirmer via `/booking/{id}`.
4. Si paiement Stripe en attente, vérifier `client_invoices.status`.

### 8.4 "Le client ne voit pas son BL"

1. Vérifier que `bookings.status >= 'loaded'`.
2. Vérifier qu'un BL a été généré (`SELECT pdf_url FROM bills_of_lading WHERE booking_id=...`).
3. Si pas généré, le générer manuellement :
   ```bash
   docker compose exec app python -m scripts.regenerate_bl BK-2026-0042
   ```

### 8.5 "Le chatbot ne répond pas"

1. Vérifier feature flag `chatbot_kairos_ai`.
2. Vérifier crédit Anthropic restant (dashboard Anthropic).
3. Vérifier quotas user :
   ```bash
   docker compose exec app python -m scripts.chat_quota <user>
   ```
4. Vérifier Sentry pour erreurs récentes du module chat.

### 8.6 "Plan d'arrimage refuse une palette"

1. Vérifier classification IMDG / hors-format de la palette.
2. Vérifier capacité restante en zones SUP_AV (pour dangereux).
3. Si zone saturée, contacter superintendant pour solution alternative.

## 9. Données de test

### 9.1 Réinitialiser staging avec données réelles anonymisées

```bash
# Sur le host prod
./scripts/anonymize-snapshot.sh > /tmp/anon.dump

# Sur staging
./scripts/restore-from-prod.sh /tmp/anon.dump
```

Le script `anonymize-snapshot.sh` masque :

- Emails (préfixe → `user_<id>@anon.local`).
- Noms (faker fr_FR).
- Numéros de téléphone.
- Adresses (faker fr_FR).
- VAT / SIRET.
- Mot de passe (force `must_change_password=true`).

### 9.2 Seed de démo

```bash
docker compose exec app python -m scripts.seed_demo
```

Crée :

- 4 navires + ports principaux.
- 1 commercial, 1 manager, 1 admin (mot de passe `demo123`).
- 1 client B2B `demo@example.com` / `demo123`.
- 8 legs sur les 6 prochains mois, dont 3 réservables.
- 12 bookings dans tous les statuts.

## 10. Procédures sensibles

### 10.1 Désactiver un user

```bash
docker compose exec app python -m scripts.disable_user <username>
```

Effet : `is_active=false`. Préserve l'historique audit.

### 10.2 Réinitialiser mot de passe

```bash
docker compose exec app python -m scripts.reset_password <username>
```

Génère un mot de passe temporaire, force `must_change_password=true`,
envoie email.

### 10.3 Rotation SECRET_KEY

```bash
./scripts/rotate-secret-key.sh
```

Workflow :

1. Génère nouvelle clé (32 octets).
2. Met à jour Doppler.
3. Configure double-clé (ancienne + nouvelle) dans l'app pour 24 h.
4. Redémarre app.
5. Après 24 h, retire l'ancienne clé.

### 10.4 Purge compte client (RGPD)

```bash
docker compose exec app python -m scripts.rgpd_delete <client_email>
```

Workflow :

1. Anonymise toutes les colonnes PII.
2. Conserve `id` et FK pour cohérence (bookings, invoices conservés
   pour obligation légale 10 ans).
3. Génère un certificat de suppression (PDF).
4. Notifie le client.

### 10.5 Export RGPD

```bash
docker compose exec app python -m scripts.rgpd_export <client_email>
```

Produit un ZIP contenant :

- `profile.json` (compte)
- `bookings.json` (toutes ses réservations)
- `invoices.json` (factures + PDF dans `pdf/`)
- `certificates.json` (CO₂)
- `messages.json` (conversations chatbot le concernant)

## 11. Communications

### 11.1 Status page

`status.my.newtowt.eu` (alimenté par UptimeRobot ou similar).

- Incident en cours → publier un message en clair.
- Maintenance planifiée → annoncer 48 h avant.
- Resolved → publier post-mortem dans les 48 h.

### 11.2 Notifications utilisateurs

| Type | Canal | Délai |
|------|-------|-------|
| Incident en cours | Bandeau in-app + email | Immédiat |
| Maintenance planifiée | Email + bandeau | 48 h avant |
| Nouvelle release | Email feature highlight | Jour J |
| Fin de support V2 | Email + bandeau | 30 j avant |

## 12. Contacts d'urgence

| Rôle | Personne | Contact |
|------|----------|---------|
| Lead Tech | (à compléter) | (à compléter) |
| DPO | (à compléter) | dpo@newtowt.eu |
| OPS NEWTOWT | (à compléter) | (à compléter) |
| Sécurité | (à compléter) | security@newtowt.eu |
| Hébergeur OVH | support OVH | 1007 (FR) |
| Stripe Support | support@stripe.com | dashboard |
| Anthropic Support | support@anthropic.com | console |

## 13. Templates d'emails opérationnels

Stockés dans `app/templates/emails/operational/` :

- `incident.html` — bandeau d'incident utilisateur.
- `maintenance_scheduled.html` — maintenance annoncée.
- `password_reset.html` — reset MdP.
- `mfa_setup.html` — activation MFA.
- `quota_exceeded.html` — quota chatbot dépassé.

## 14. Cycle de release

### 14.1 Semantic versioning

- **MAJOR** : breaking change (`v3 → v4`).
- **MINOR** : nouvelle feature backward-compatible.
- **PATCH** : bug fix.

### 14.2 Cadence

- Patches : à la demande (urgent).
- Minor : ~mensuel.
- Major : annuel.

### 14.3 Release notes

Générées via `git log` annoté + revue humaine. Publiées dans
`docs/operations/release-notes/` et email aux utilisateurs.
