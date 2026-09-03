# Runbook opérationnel

> Procédures opérationnelles pour exploiter et dépanner `mynewtowt`.

## 1. Comptes & accès

| Système | URL | Comment se connecter |
|---------|-----|---------------------|
| Application | `https://my.newtowt.eu` | Login user + MFA |
| Staging | `https://staging.my.newtowt.eu` | Login user + MFA |
| GitHub | `github.com/julien-newtowt/mynewtowt` | OAuth + MFA |
| Hébergeur OVH | `manager.ovh.com` | SSO interne |
| Stripe | `dashboard.stripe.com` | SSO interne |
| Sentry | `sentry.io/newtowt` | SSO interne |
| Grafana | `metrics.my.newtowt.eu` | LDAP interne |
| Metabase | `analytics.my.newtowt.eu` | LDAP interne |

Tout accès admin nécessite **MFA**. Rotation mensuelle des secrets en
Doppler.

## 2. Démarrage rapide d'un environnement local

```bash
git clone git@github.com:julien-newtowt/mynewtowt.git
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

> ⚠ **Migration de dépôt (2026-07)** : le repository a été transféré de
> `juliengonde-5g/mynewtowt` vers **`julien-newtowt/mynewtowt`**.
> `deploy.sh` ne code aucune URL en dur (il fetch le remote `origin` du
> clone local), mais **le clone présent sur chaque serveur pointe encore
> vers l'ancien dépôt**. Avant le premier déploiement post-transfert,
> re-pointer le remote sur prod ET staging :
>
> ```bash
> cd /opt/mynewtowt   # racine du clone serveur
> git remote set-url origin git@github.com:julien-newtowt/mynewtowt.git
> git remote -v       # vérifier
> git fetch origin    # doit répondre sans erreur
> ```
>
> (La redirection GitHub post-transfert fonctionne un temps, mais ne pas
> s'y fier : deploy keys / permissions suivent le NOUVEAU dépôt.)

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

### 4.0 Protection de la branche `main` — préalable à tout déploiement

> **À appliquer une fois, dans les réglages du dépôt.** Cette section décrit une
> configuration à poser manuellement : ni `deploy.sh`, ni la CI, ni un agent ne
> peuvent la mettre en place — c'est un réglage GitHub, accessible au seul
> propriétaire du dépôt.

#### Pourquoi

La journée du **2026-09-03** a produit cinq déploiements en échec pour deux
causes, et **les deux avaient été détectées par la CI avant le déploiement** :

| Heure UTC | Commit | Cause | La CI disait |
|---|---|---|---|
| 07:22 | `c31805a` | `IndentationError` (résolution de conflit gardant deux `except`) | rouge depuis 06:53 |
| 07:31 | `c31805a` | idem — même code redéployé | rouge |
| 08:34 | `93a6e6d` | idem — PR #171 empilée par-dessus | rouge |
| 09:28 | `1d480c6` | têtes Alembic multiples (PR #173) | rouge, sentinelle explicite |
| 10:41 | `1f082f9` | têtes Alembic multiples (PR #174) | rouge, sentinelle explicite |

`tests/regression/test_alembic_single_head.py` a signalé **les quatre**
collisions de têtes de l'historique du projet (07/08, 26/08, et deux fois le
03/09). Elle n'a jamais pu empêcher une fusion. **Un garde-fou qui n'est pas
opposable est un avertissement, pas un garde-fou** — c'est le constat central de
cet incident, et il ne se corrige pas dans le code.

Second effet, plus insidieux : tant que `main` est rouge pour une raison de
fond (ce jour-là : `black` sur deux fichiers, puis `anyio` non épinglé), **le
rouge d'une sentinelle utile se noie dans un rouge d'ambiance**. Personne ne
distingue plus le signal qui compte. Garder `main` vert n'est pas de l'hygiène :
c'est ce qui rend tous les autres contrôles lisibles.

#### Configuration à poser

`Settings → Branches → Add branch protection rule`, ou
`Settings → Rules → Rulesets` sur les dépôts récents.

| Réglage | Valeur | Ce que ça évite |
|---|---|---|
| Branch name pattern | `main` | — |
| **Require a pull request before merging** | ✅ | Un push direct sur `main` (le CLAUDE.md l'interdit déjà ; ceci le rend impossible) |
| ↳ Require approvals | `1` | Applique la politique de revue : Julien valide chaque PR |
| ↳ Dismiss stale approvals when new commits are pushed | ✅ | Une approbation qui ne porte plus sur le code fusionné |
| **Require status checks to pass before merging** | ✅ | **Les cinq échecs du tableau ci-dessus** |
| ↳ Checks requis | `lint`, `test`, `security` | — |
| ↳ **Require branches to be up to date before merging** | ✅ | Une PR ouverte sur une base périmée — les cinq PR du jour étaient à **27 commits** de `main` |
| **Do not allow bypassing the above settings** | ✅ | Le contournement administrateur, qui a permis les fusions sur rouge |
| **Allow force pushes** | ❌ | Réécriture d'historique partagé |
| **Allow deletions** | ❌ | Suppression accidentelle de `main` |

⚠️ **Ne pas exiger le check `build`.** Le job `build` de `.github/workflows/ci.yml`
porte `if: github.ref == 'refs/heads/main'` : il **ne s'exécute jamais sur une
pull request**. L'exiger mettrait chaque PR en attente d'un check qui n'arrivera
pas — blocage total et déroutant. Les trois checks à exiger sont exactement
`lint`, `test` et `security`, qui tournent sur l'événement `pull_request`.

Équivalent en ligne de commande, pour appliquer le réglage de façon tracée :

L'API de protection de branche attend des objets imbriqués : passer le corps en
JSON (`--input`), et non en paires `-f cle=valeur`, que `gh` n'aplatit pas
correctement pour cet endpoint.

```bash
cat <<'JSON' | gh api -X PUT \
  repos/julien-newtowt/mynewtowt/branches/main/protection --input -
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint", "test", "security"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

`restrictions: null` est **obligatoire** (et non omissible) : l'endpoint refuse
la requête si la clé manque. `enforce_admins: true` est la traduction API de
« Do not allow bypassing » — c'est la ligne qui compte.

Vérification :

```bash
gh api repos/julien-newtowt/mynewtowt/branches/main/protection \
  | python3 -m json.tool
```

#### Ce que « up to date before merging » change au quotidien

C'est le réglage le plus utile ici, et celui qui coûte le plus cher en confort :
GitHub exige que la branche ait intégré `main` **avant** de fusionner, donc la
CI rejoue après chaque mise à jour. Sur une file de PR, chaque fusion invalide
la suivante.

C'est exactement le problème qu'il faut payer. Les cinq PR du 03/09 avaient une
CI **verte ou rouge datant d'avant 27 commits de `main`** : ces verdicts ne
disaient plus rien de l'état réel. Et c'est ce réglage qui aurait exigé le
rechaînage des migrations **au moment de la fusion**, quand la tête réelle est
connue — la discipline que ce runbook réclame au §6.2 sans pouvoir l'imposer.

Coût mesuré : la CI tourne en ~12 minutes. Sur une file de 4 PR, cela ajoute
environ 3 rejeux, soit ~36 minutes d'attente cumulée. À comparer aux cinq
déploiements perdus et à l'heure d'indisponibilité du 03/09.

#### Urgence : fusionner malgré un check rouge

Cette procédure existe pour que la protection ne soit **jamais désactivée** au
premier incident — c'est ainsi qu'elle se perd.

1. **Établir que le rouge n'est pas celui de la PR.** Rejouer le check en local
   sur la branche (`ruff check app tests`, `black --check app tests`,
   `pytest tests/unit tests/integration tests/regression`) et constater que
   l'échec reproduit à l'identique sur `main` seul. Un rouge hérité de `main`
   n'est pas une raison de fusionner sur rouge : c'est une raison de corriger
   `main` d'abord, par une PR dédiée — c'est ce qu'a fait la PR #185.
2. Si le déblocage ne peut pas attendre, **lever la protection nommément et pour
   la durée du geste** (`Do not allow bypassing` décoché, fusion, recoché), en
   consignant dans la PR : qui, quand, pourquoi, et quel check a été outrepassé.
3. **Jamais** de fusion sur un `test` rouge dont l'échec porte sur la PR
   elle-même. La sentinelle Alembic et la sentinelle
   `tests/unit/test_delete_leg_models.py` décrivent des pannes de déploiement,
   pas des préférences de style.

#### Contrôle avant fusion, tant que la protection n'est pas posée

```bash
pytest tests/regression/test_alembic_single_head.py -q --no-cov
ruff check app tests && black --check app tests
python -c "from app.main import app; print(len(app.routes), 'routes')"
```

La dernière ligne est celle qui manquait le 03/09 : elle échoue immédiatement si
un module de l'application ne s'importe pas, ce que `/health` ne peut dire
qu'après un déploiement.

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
   retour arrière applicatif, **maintenance non levée**, exit 2.
9. Smoke tests (12 endpoints). Même traitement qu'au point 8.
10. Maintenance OFF + mémorisation de la révision saine + report.

### 4.1 bis Retour arrière applicatif (`rollback_app`)

Jusqu'au 2026-09-03, cette étape n'existait que sur le papier : la fonction
n'émettait que deux avertissements réclamant une intervention manuelle, et la
maintenance était levée **avant** elle. Un déploiement raté laissait donc la
production morte et découverte — c'est ce qui a transformé une erreur de
syntaxe en plus d'une heure d'indisponibilité.

Comportement réel désormais :

- La révision qui tournait avant le déploiement est capturée par `sync_code`
  **avant tout checkout** (`PREVIOUS_VERSION`), avec repli sur
  `backups/.last-release` — la dernière révision ayant passé health **et**
  smoke tests.
- En cas d'échec, `rollback_app` **recompile et redéploie** cette révision,
  puis vérifie `/health`. Il n'y a pas d'image précédente à repuller : le
  service `app` est déclaré `build: .` sans `image:`, donc chaque build écrase
  le tag. La révision git est la seule trace de l'état d'avant.
- La maintenance n'est levée **que si** le retour arrière est sain. Sinon le
  script sort en 2 en nommant la dernière révision saine connue.
- Si des migrations ont été appliquées pendant le déploiement (tête Alembic
  comparée avant/après), le retour arrière du code seul le signale : l'ancien
  code se retrouve face au nouveau schéma, ce qui n'est sûr que si les
  migrations sont rétro-compatibles. La commande `rollback.sh <snapshot>` à
  lancer est affichée.
- Rien à faire pour revenir sur la branche de déploiement : le retour arrière
  travaille en HEAD détaché, et le `sync_code` suivant refait
  `checkout main` + `merge --ff-only`.

⚠️ **Limite connue, non corrigée.** Le marqueur de maintenance vit dans
`/tmp/.maintenance` **à l'intérieur du conteneur app**
(`app/middlewares/maintenance.py`). Un `--force-recreate` le détruit, et un
conteneur qui a quitté ne sert plus rien : pendant la fenêtre où l'application
est morte, les visiteurs reçoivent un 502 du reverse proxy, pas la page
d'attente. Ne pas lever la maintenance reste néanmoins utile — c'est ce qui
évite d'exposer un backend non validé quand le conteneur, lui, tourne. Rendre
le marqueur persistant (volume, ou prise en charge côté Caddy) est un
correctif distinct, non traité ici.

### 4.2 Contrainte anti-chevauchement (`legs_no_vessel_overlap`)

La migration `0094` pose une contrainte d'exclusion Postgres empêchant
deux legs non annulés d'un même navire de se chevaucher. **Si des
chevauchements existent déjà en base, la migration les liste dans la
sortie d'Alembic et saute la contrainte** (le déploiement passe quand
même ; la validation applicative couvre les nouvelles écritures).

Après correction des legs dans `/planning`, vérifier puis poser la
contrainte manuellement :

```sql
-- 1. Lister les chevauchements restants (doit renvoyer 0 ligne)
SELECT a.leg_code, a.etd, a.eta, b.leg_code, b.etd, b.eta
FROM legs a JOIN legs b ON a.vessel_id = b.vessel_id AND a.id < b.id
WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
  AND a.etd < b.eta AND a.eta > b.etd;

-- 2. Poser la contrainte
ALTER TABLE legs ADD CONSTRAINT legs_no_vessel_overlap
EXCLUDE USING gist (vessel_id WITH =, tstzrange(etd, eta) WITH &&)
WHERE (status <> 'cancelled') DEFERRABLE INITIALLY DEFERRED;
```

```bash
# Vérifier que la contrainte est en place
docker compose exec db psql -U towt -d towt -c "\d legs" | grep legs_no_vessel_overlap
```

## 5. Backups

### 5.1 Backup quotidien automatique

Cron sur le host :

```
0 3 * * * /opt/mynewtowt/scripts/backup.sh
15 3 * * * docker compose -f /opt/mynewtowt/docker-compose.yml exec -T app \
           python -m scripts.verify_signatures --json \
           >> /var/log/mynewtowt/signature-audit.log 2>&1 \
           || /usr/local/bin/alert-ops "signature violation detected"
```

Le 2e job (verify_signatures) scanne tous les SOF / noon report / watch log
signés et compare le hash stocké au recalcul. Exit code 1 = au moins une
violation → alerte ops. Exit code 2 = erreur DB.

Workflow backup :

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

### 6.2 « Multiple head revisions are present » — le déploiement s'arrête

Symptôme (étape 6 de `deploy.sh`, snapshot restauré automatiquement) :

```
ERROR [alembic.util.messaging] Multiple head revisions are present for given
argument 'head'; please specify a specific target revision, '<branchname>@head'
```

**Cause** : deux branches de fonctionnalité ont chaîné leurs migrations sur le
**même parent** et ont été fusionnées séparément — `main` porte alors deux têtes
et `alembic upgrade head` refuse de choisir. Constaté le 07/08/2026 (MRV ×
crewing), le 26/08/2026 (BL × QHSE), puis **deux fois le 03/09/2026** —
Assistance × planification (déploiement de `1d480c6`), puis legacy MRV × le
reste (déploiement de `1f082f9`). **Ce n'est pas une panne de base** : rien n'a
été appliqué, la restauration du snapshot est un no-op de précaution.

⚠️ **Une révision de fusion ne vaut que pour les têtes existant au moment où
elle est écrite.** La seconde occurrence du 03/09 vient de là : `20260903_0139`
avait réuni deux des trois enfants de `20260901_0136`, le troisième arrivant
avec la PR suivante. Avant de poser une fusion, vérifier qu'aucune branche non
fusionnée ne porte encore une migration chaînée sur le même parent —
`git grep 'down_revision = ' origin/…` sur les branches ouvertes.

**Diagnostic** (aucune connexion à la base nécessaire) :

```bash
docker compose run --rm app alembic heads     # doit renvoyer UNE ligne
docker compose run --rm app alembic history | head
docker compose run --rm app alembic current   # où est réellement la prod
```

**Correctif** : poser une **révision de fusion** sans DDL
(`alembic merge -m "merge heads" <tête1> <tête2>`), la relire, la faire passer en
PR, redéployer. Modèles : `20260807_0113`, `20260826_0119`, `20260903_0139`,
`20260903_0140`.

⚠️ **Ne pas rechaîner une révision déjà fusionnée sur `main`** (changer son
`down_revision` pour la tête courante) : toute base qui la porte déjà
considérerait l'autre chaîne comme appliquée, et ses tables manqueraient
**silencieusement**. Le rechaînage ne vaut que pour une révision encore sur sa
branche de travail.

**La sentinelle CI ne suffit pas, et le 03/09/2026 l'a montré.**
`tests/regression/test_alembic_single_head.py` a bien détecté la seconde tête sur
la PR #173 — mais la PR a été fusionnée alors que sa CI était rouge, et la panne
a atteint le déploiement. La sentinelle dit la vérité ; elle ne protège que si le
verdict de la CI est **opposable**. Deux conséquences pratiques :

- activer une **protection de branche** sur `main` exigeant les checks verts,
  sans quoi la sentinelle n'est qu'un avertissement ;
- tant que `main` est rouge pour une autre raison (c'était le cas ce jour-là :
  `black` sur deux fichiers, `anyio` non épinglé), le rouge de la sentinelle est
  noyé dans un rouge de fond. **Remettre `main` au vert est ce qui rend les
  garde-fous lisibles** — un `main` durablement rouge les neutralise tous.

Contrôle à faire avant chaque fusion, tant que la protection de branche n'est pas
en place :

```bash
pytest tests/regression/test_alembic_single_head.py -q --no-cov
```

### 6.3 Rollback migration

```bash
docker compose exec app alembic downgrade -1
```

Si la migration n'est pas reversible, restaurer le snapshot DB.

⚠️ **Sur une révision de fusion (`mergepoint`), `downgrade -1` échoue** en
`FAILED: Ambiguous walk` — le pas relatif ne sait pas laquelle des deux branches
remonter. Il faut nommer la cible :

```bash
# La base est sur 20260826_0119 (mergepoint) : on défait la fusion en nommant
# la branche où l'on veut retomber.
docker compose exec app alembic downgrade 20260817_0118
docker compose exec app alembic current   # → 20260722_0106 + 20260817_0118
```

Vérifié le 2026-08-26 sur base neuve : la fusion se défait et se rejoue sans
toucher aux données (aucun DDL).

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
