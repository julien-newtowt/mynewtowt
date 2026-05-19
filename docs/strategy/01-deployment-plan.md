# Plan de déploiement progressif — `mynewtowt`

> Étape 3 de la commande utilisateur — `/writing-plans` enrichi de
> `/systematic-debugging` à chaque jalon.

## 0. Principes directeurs

1. **Déploiement progressif** : aucune mise en production "big bang".
   Chaque jalon livre un sous-périmètre testé en staging et activé en
   production via feature flag.
2. **Continuité opérationnelle** : la V2 (TOWT) reste exploitée jusqu'à
   migration complète des données par module ; aucune coupure de service
   acceptable au-delà de 30 minutes.
3. **Réversibilité** : tout changement est précédé d'un snapshot SQL et
   d'un tag de release ; un rollback est exécutable en < 15 minutes.
4. **Zéro défaut utilisateur** : pré-prod active sur chaque PR
   (cf. `04-verification-before-completion.md`), CI verte obligatoire.

## 1. Stratégie d'environnements

| Env | URL | Données | Activation |
|-----|-----|---------|-----------|
| `local` | `localhost:8000` | docker-compose | Tous les devs |
| `ci` | éphémère sur PR | base seed-only | CI GitHub Actions |
| `staging` | `staging.my.newtowt.eu` | snapshot prod anonymisé J-1 | Internal + clients pilotes |
| `production` | `my.newtowt.eu` | données réelles, RGPD | Bascule manuelle après QA |

Diffusion : staging précède la prod **de 7 jours minimum** sur tout
module touchant les données client.

## 2. Découpage en vagues (12 semaines)

### Vague A — Fondations (S0 → S2)

| Sem | Livrable | Risque | Test critique |
|-----|----------|--------|---------------|
| S0 | Bootstrap repo + design system Kairos | faible | Lighthouse > 90 |
| S1 | Auth + permissions + audit logs | élevé | Pen-test interne |
| S2 | Référentiels (navires, ports, OPEX) | faible | Migration V2→V3 |

**Gate de vague A** :
- 100 % couverture unit-tests `auth.py`, `permissions.py`.
- Pas de secret en clair dans le repo (gitleaks).
- HMAC `SECRET_KEY` rotatable sans coupure.

### Vague B — Cœur ERP (S3 → S6)

| Sem | Livrable | Risque |
|-----|----------|--------|
| S3 | Planning + recalcul cascade ETD/ETA | moyen |
| S4 | Escale Import/Export | moyen |
| S5 | Cargo (orders, packing list, BL) | élevé |
| S6 | Onboard refonte 4 espaces + PWA | moyen |

**Gate de vague B** :
- Tests d'intégration sur les 4 cas d'usage de référence
  (cf. `04-verification-before-completion.md`).
- Documentation utilisateur à jour pour chaque module.
- Zéro 🔴 sur l'audit `/security-review`.

### Vague C — Nouveautés client (S7 → S9)

| Sem | Livrable | Risque |
|-----|----------|--------|
| S7 | **Plateforme réservation cale (booking)** — NOUVEAU | élevé |
| S8 | Portail client authentifié + dashboard CO₂ | élevé |
| S9 | Paiement Stripe + facturation auto | élevé |

**Gate de vague C** :
- Parcours réservation < 2 minutes pour un client B2B identifié.
- Test de charge : 100 réservations simultanées sans dégradation.
- RGPD : pages mentions, cookies, suppression compte fonctionnelles.

### Vague D — Plateforme intelligente (S10 → S12)

| Sem | Livrable | Risque |
|-----|----------|--------|
| S10 | Chatbot Kairos AI + RAG pgvector | moyen |
| S11 | Dashboards analytiques + filtrage avancé | moyen |
| S12 | Migration des données V2 → V3 + bascule prod | élevé |

**Gate de vague D** :
- Migration validée par double-run pendant 7 jours (V2 et V3 alimentés
  en parallèle, comparaison delta nulle).
- Formation utilisateurs réalisée (8 rôles × 2h).
- Bascule DNS `my.newtowt.eu` validée en heure creuse.

## 3. Cycle PR → production (par changement)

```
┌─────────┐   ┌──────────┐   ┌─────────────┐   ┌─────────┐   ┌──────────┐
│ feature │ → │ PR + CI  │ → │ /sec-review │ → │ staging │ → │ prod /  │
│ branch  │   │ + tests  │   │ + /review   │   │ 24h     │   │ ramped  │
└─────────┘   └──────────┘   └─────────────┘   └─────────┘   └──────────┘
                                                                   │
                                                                   ▼
                                                            ┌──────────────┐
                                                            │ monitoring   │
                                                            │ + alerting   │
                                                            └──────────────┘
```

À chaque étape, le pipeline exécute :

1. **Lint** : `ruff`, `black`, `mypy --strict`, `eslint`.
2. **Tests unitaires** : `pytest tests/unit -x --cov`.
3. **Tests d'intégration** : `pytest tests/integration` contre Postgres
   éphémère (testcontainers).
4. **Tests e2e** : `playwright test` (golden paths × 8 personas).
5. **Build** : Docker image, scan vuln (`trivy`), sign (cosign).
6. **Migration dry-run** : `alembic upgrade head --sql` puis revue.
7. **Smoke tests staging** : 12 endpoints critiques répondent < 500 ms.

## 4. Feature flags

Tous les modules nouveaux ou refondus sont gated par un flag DB :

```sql
CREATE TABLE feature_flags (
  key VARCHAR PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  rollout_pct INT NOT NULL DEFAULT 0 CHECK (rollout_pct BETWEEN 0 AND 100),
  audience JSONB DEFAULT '{}'::JSONB,
  description TEXT,
  updated_by INTEGER REFERENCES users(id),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Flags V3 initiaux :

- `kairos_design_system` (rollout par rôle)
- `booking_platform` (rollout par segment client)
- `chatbot_kairos_ai`
- `onboard_v3_layout`
- `escale_import_export_split`
- `analytics_v2_dashboards`
- `mfa_required` (forçage 2FA admin → tous)
- `stripe_payments`

## 5. Stratégie de migration des données V2 → V3

### 5.1 Approche : double-run, comparaison delta

```
┌──────┐     replication CDC      ┌──────┐
│  V2  │ ────────────────────────>│ V3   │
│ Prod │                          │ Prod │
└──────┘                          └──────┘
   │                                 │
   ▼                                 ▼
 lecture                          lecture
 utilisateurs                     observateurs
```

1. Déployer V3 en mode lecture-seule, alimenté en continu par CDC
   (Debezium ou triggers Postgres).
2. Comparer les vues clés (planning, cargo, finance) entre V2 et V3 :
   delta toléré = 0 enregistrements en différence après stabilisation.
3. Activer les écritures V3 sur les seuls modules nouveaux (booking,
   chatbot, dashboards) pendant 7 jours.
4. Basculer les écritures progressivement (rôle par rôle) : commercial
   d'abord, opération ensuite, etc.
5. Couper la V2 quand 100 % des écritures sont sur V3 et que personne
   ne s'est connecté à V2 depuis 7 jours.

### 5.2 Mapping tables critiques

| V2 | V3 | Notes |
|----|----|-------|
| `users` | `users` | + colonnes `mfa_secret`, `webauthn_credentials` |
| `legs` | `legs` | + `is_bookable`, `public_capacity_palettes`, `public_price_per_palette` |
| `orders` | `orders` | + `bookings_id` FK |
| `packing_lists` | `packing_lists` | structure inchangée |
| ⛔ `passenger_*` | (supprimé) | données archivées dans `archive_v2.passengers_*` |
| (nouveau) | `clients_accounts` | comptes clients authentifiés |
| (nouveau) | `bookings` | réservations cale |
| (nouveau) | `booking_items` | détail palettes / tonnes / cubage |
| (nouveau) | `client_invoices` | factures émises au client |
| (nouveau) | `co2_certificates` | certificats CO₂ téléchargés |
| (nouveau) | `feature_flags` | gating progressif |
| (nouveau) | `tickets` | ticketing escale |
| (nouveau) | `chat_conversation` | historique chatbot |

### 5.3 Scripts de migration

Les scripts se trouvent dans `migrations/` :

- `migrations/v2_to_v3/001_schema_baseline.sql` — création du schéma V3.
- `migrations/v2_to_v3/002_users_migration.py` — migration utilisateurs
  + génération `mfa_secret` (forcé à `null` ; activation au prochain login).
- `migrations/v2_to_v3/003_legs_migration.py` — flag `is_bookable`
  initialisé à `FALSE` (activation manuelle par commercial).
- `migrations/v2_to_v3/004_orders_migration.py` — backfill `client_id`
  via match nom + email.
- `migrations/v2_to_v3/005_archive_v2.sql` — déplacement du module
  passagers dans `archive_v2` (schéma dédié, lecture seule).

## 6. Stratégie de debugging systématique (`/systematic-debugging`)

À chaque jalon, et avant la bascule en production, on déroule le
protocole `systematic-debugging` en 5 étapes :

### Étape 1 — Cartographier les chemins critiques

Lister exhaustivement les chemins utilisateurs qui doivent fonctionner :
golden paths × 8 personas. La liste est tenue dans
`tests/e2e/golden_paths.csv` et chaque ligne référence un test
Playwright + un screenshot de référence.

### Étape 2 — Forcer les conditions de défaut

Pour chaque chemin :

- Tester avec réseau dégradé (`tc qdisc add ... delay 300ms loss 5 %`).
- Tester en navigation privée + cookies vides.
- Tester en pavé tactile / clavier seul (a11y).
- Tester en données extrêmes (commande à 0 palettes, leg à 365 j).

### Étape 3 — Capturer les défauts dans un tableau de tri

```
ID | Chemin | Persona | Sévérité | Root cause hypothèse | Owner | ETA fix
```

Tableau persisté dans `docs/operations/defect-board.md` mis à jour à
chaque session de debugging.

### Étape 4 — Identifier la *cause racine* avant fix

Règle : avant d'écrire le moindre fix, on documente :

1. La **séquence reproductible** (commande shell ou capture).
2. Le **diff de comportement** observé vs attendu.
3. Les **3 hypothèses** plausibles classées par probabilité.
4. La **validation expérimentale** qui réfute / confirme chaque hypothèse.

Pas de fix "à l'aveugle". Cf. `docs/operations/debugging-playbook.md`.

### Étape 5 — Tester le fix sur le golden path et les 3 chemins adjacents

Un fix sur le booking ne se merge que s'il n'a cassé ni le panier,
ni l'historique commandes, ni la facturation. Tests d'intégration
obligatoires sur le triplet [feature, amont, aval].

## 7. Critères de "Done" par PR

Une PR n'est mergeable que si :

- [x] CI verte (lint + types + tests unitaires + intégration + e2e).
- [x] Couverture > 80 % sur les fichiers touchés.
- [x] `/security-review` rendu, sans 🔴, 🟠 résolus ou justifiés.
- [x] `/review` rendu, suggestions appliquées ou justifiées.
- [x] Doc utilisateur mise à jour si UI publique.
- [x] Migrations Alembic + rollback testés.
- [x] Screenshot before/after attaché si UI.
- [x] Flag créé si l'impact n'est pas trivialement réversible.
- [x] Pas de TODO laissé dans le code (`# TODO` interdits sans ticket).
- [x] Pas d'introduction de dépendance non listée dans `requirements.txt`.

## 8. Plan de rollback

Pour chaque release prod :

1. Snapshot Postgres : `pg_dump -Fc towt > backups/pre-release-$VERSION.dump`.
2. Tag git : `release/$VERSION-$(date +%Y%m%d-%H%M)`.
3. Image Docker : tag immuable `ghcr.io/newtowt/mynewtowt:$VERSION`.
4. Procédure rollback : `./scripts/rollback.sh $VERSION` qui :
   - Stoppe le conteneur app.
   - Restaure le snapshot Postgres.
   - Redeploie l'image précédente.
   - Vérifie que `/health` répond OK.

Objectif : MTTR rollback < 15 minutes.

## 9. Calendrier d'activation flags (prod)

```
S+0  ┌─ kairos_design_system        rollout 10 % users
S+1  │   kairos_design_system        rollout 100 %
S+2  ├─ escale_import_export_split  rollout 20 %
S+3  ├─ onboard_v3_layout           rollout 50 % (équipages volontaires)
S+4  ├─ booking_platform            rollout 5 % clients pilotes
S+5  ├─ booking_platform            rollout 25 %
S+6  ├─ booking_platform            rollout 100 %
S+7  ├─ chatbot_kairos_ai           rollout 100 % internes
S+8  ├─ stripe_payments             rollout 5 %
S+9  ├─ analytics_v2_dashboards     rollout 100 %
S+10 └─ mfa_required (admins)       rollout 100 % obligatoire
```
