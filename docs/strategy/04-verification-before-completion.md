# Vérification avant complétion — Zéro défaut utilisateur

> Étape 4 — `/verification-before-completion`. Stratégie du zéro défaut
> utilisateur à chaque PR.

## 1. Principe

Une PR n'est pas "terminée" parce que les tests passent. Elle est
terminée quand on est capable d'**affirmer** que :

1. Le golden path fonctionne sur staging.
2. Aucun chemin adjacent n'a régressé.
3. Le monitoring est en place pour détecter une dérive en prod.
4. Le rollback est prêt et documenté.

## 2. Checklist obligatoire par PR

### 2.1 Avant de pousser

- [ ] J'ai exécuté le golden path local sur ma feature.
- [ ] J'ai testé au moins 3 chemins adjacents (en amont, en aval, en
      contournement).
- [ ] J'ai lancé `pytest tests/unit tests/integration` localement.
- [ ] J'ai lancé `playwright test` sur le golden path principal.
- [ ] J'ai relu mon diff (`git diff main`).
- [ ] J'ai relancé l'app en frais (`docker compose down && up`) et
      vérifié que ça démarre sans erreur.
- [ ] J'ai pris un screenshot avant/après si UI.
- [ ] J'ai mis à jour la doc utilisateur si nécessaire.

### 2.2 Description PR

Template `.github/PULL_REQUEST_TEMPLATE.md` :

```markdown
## Quoi
<résumé 1-2 lignes>

## Pourquoi
<lien ticket / besoin métier>

## Golden path testé
- [ ] <persona> : <chemin>

## Chemins adjacents testés
- [ ] <persona> : <chemin>
- [ ] <persona> : <chemin>
- [ ] <persona> : <chemin>

## Migration / breaking changes
- [ ] Aucune migration
- [ ] Migration backward-compatible
- [ ] Migration non-backward-compatible (justification : ...)

## Feature flag
- [ ] Pas nécessaire (trivialement réversible)
- [ ] Flag `<key>` créé, default OFF
- [ ] Flag `<key>` modifié

## Screenshots
<si UI>

## Monitoring
- [ ] Métriques exposées
- [ ] Alerte ajoutée si erreur fonctionnelle critique
- [ ] Log structuré ajouté

## Rollback
<procédure spécifique si non standard>
```

### 2.3 Reviews obligatoires

| Type | Reviewer | Bloque le merge |
|------|----------|-----------------|
| Code review | 1 dev senior | ✅ |
| Security review (`/security-review`) | Bot Claude + 1 humain si 🔴 | ✅ |
| UX review | Designer si UI publique | ✅ pour UI publique |
| Tests CI | GitHub Actions | ✅ |
| Lighthouse CI | Job CI | si page publique : ≥ 90 |

## 3. Hiérarchie des tests

```
                      ┌─────────────┐
                      │ Tests       │
                      │ Exploratoires│ ← humain, manuel, terrain
                      └─────────────┘
                            ▲
                            │
                  ┌──────────────────┐
                  │   Tests E2E      │ ← Playwright, golden paths
                  │   (~50 tests)    │
                  └──────────────────┘
                            ▲
                            │
              ┌──────────────────────────┐
              │ Tests d'intégration      │ ← TestClient + Postgres
              │ (~300 tests)             │
              └──────────────────────────┘
                            ▲
                            │
       ┌───────────────────────────────────────┐
       │ Tests unitaires                       │ ← pytest pur
       │ (~1500 tests)                         │
       └───────────────────────────────────────┘
```

## 4. Tests unitaires

### 4.1 Cibles obligatoires

- 100 % couverture sur `app/services/*`.
- 100 % couverture sur `app/auth.py`, `app/permissions.py`,
  `app/csrf.py`.
- 80 % minimum sur `app/routers/*` (le reste : intégration).
- 100 % couverture sur les pure functions de pricing / capacity /
  CO₂.

### 4.2 Conventions

- 1 fichier de tests = 1 fichier de module.
- Nommage : `test_<fonction>__<scenario>`.
- AAA : Arrange / Act / Assert.
- Fixtures pytest dans `tests/conftest.py`.

```python
async def test_booking_confirm__capacity_exceeded__raises():
    # Arrange
    leg = await make_leg(capacity_palettes=10, reserved=8)
    user = await make_client_account()
    booking = await make_booking(leg=leg, total_palettes=5, status='submitted')
    # Act / Assert
    with pytest.raises(CapacityExceeded):
        await booking_service.confirm(booking.reference, user)
```

## 5. Tests d'intégration

- Lancés avec `pytest tests/integration` sur Postgres éphémère
  (`testcontainers`).
- Couvrent les flux HTTP + DB + auth réels.
- Pas de mock sur la DB.
- Fixtures de seed : `tests/fixtures/seed.sql`.

Exemple :

```python
async def test_post_booking__creates_booking_and_returns_303(client):
    # login as client account
    await client.post("/me/login", data={...})
    # POST booking
    r = await client.post("/booking/new/step-4-confirm", data={...})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/me/bookings/BK-")
```

## 6. Tests E2E (Playwright)

### 6.1 Golden paths à automatiser

| Persona | Path | Critère succès |
|---------|------|----------------|
| Prospect | Landing → search → booking step 1 | Voir 3+ legs en résultat |
| Client | Connexion → /me → nouvelle résa → step 4 | Booking créé, redirige vers /me |
| Client | /me → mes documents → DL facture PDF | PDF non vide, > 10 KB |
| Staff | Login → /dashboard → click leg | Page leg détail s'affiche |
| Staff | /planning → drag and drop leg | ETD mis à jour en DB |
| Staff | /escale → ouvre ticket P1 | Ticket créé, status open |
| Staff | /booking → confirme booking | Status passe submitted → confirmed |
| Captain | /onboard → noon report saisie | Persisté en DB |

### 6.2 Convention

```typescript
// tests/e2e/01-landing-to-booking.spec.ts
import { test, expect } from '@playwright/test';

test('prospect peut découvrir une route', async ({ page }) => {
  await page.goto('/');
  await page.fill('[name=from]', 'FR');
  await page.fill('[name=to]', 'US');
  await page.click('button:has-text("Rechercher")');
  await expect(page.locator('[data-test=booking-card]')).toHaveCount({ min: 1 });
});
```

### 6.3 Exécution

- Local : `npx playwright test` headed.
- CI : tous les tests headless, screenshot/video sur échec uploadé en
  artifact.
- Browsers : chromium + webkit (Safari iOS).

## 7. Tests d'accessibilité

`axe-playwright` sur chaque page principale :

```typescript
import AxeBuilder from '@axe-core/playwright';

test('landing a11y', async ({ page }) => {
  await page.goto('/');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
```

Cible WCAG 2.2 AA. Violations bloquent le merge sur les routes
publiques.

## 8. Tests de charge

Avant chaque release majeure :

```bash
# 100 utilisateurs simultanés, 10 minutes, parcours booking
k6 run tests/load/booking-flow.js --vus 100 --duration 10m
```

Critères :

- p95 latence < 1 s
- Error rate < 0,5 %
- 0 5xx
- DB connections actives < 50

## 9. Tests d'intégration externes

Mocking par défaut, mode "vraie API" sur demande :

- **Stripe** : mode `test` Stripe (clé `sk_test_...`).
- **Anthropic** : VCR cassettes (`vcrpy`) pour rejouer interactions.
- **Pipedrive** : sandbox Pipedrive.

## 10. Pré-prod staging

### 10.1 Auto-deploy

Chaque PR mergée sur `main` déclenche un déploiement automatique sur
staging :

- URL : `staging.my.newtowt.eu`.
- Données : snapshot prod anonymisé J-1.
- Feature flags : tous activés.
- Sentry : projet `mynewtowt-staging`.

### 10.2 Période de soak

7 jours minimum sur staging avant prod pour :

- Modules touchant données client (booking, factures, paiement).
- Modifications de permissions.
- Migrations non backward-compatible.

48 h pour les autres.

### 10.3 Smoke tests staging

À chaque déploiement, exécution automatique de :

```bash
./scripts/smoke-tests.sh https://staging.my.newtowt.eu
```

Vérifie :

- `/health` répond 200 OK avec `{"status":"ok"}`.
- `/` répond 200 avec contenu HTML.
- `/login` répond 200.
- `/me` redirige vers `/me/login` (auth required).
- `/api/v1/health` répond 200.
- DB migrations à jour.

## 11. Surveillance post-déploiement

### 11.1 24 h après chaque release

Monitoring renforcé pendant 24 h :

- Sentry alert sur error rate × 2 vs baseline.
- Slack channel `#prod-deploy-watch` notifié en continu.
- On-call sur stand-by.

### 11.2 Métriques business à surveiller

| Métrique | Baseline | Alerte si... |
|----------|----------|--------------|
| Bookings/jour | 5-15 | -50 % vs 7d avg |
| Taux confirmation | 80 % | < 60 % |
| /chat erreurs | 0,1 % | > 2 % |
| /me sessions | 50/h | -80 % |

### 11.3 Tableau de bord "Release Health"

Vue dédiée Grafana avec :

- Comparaison N vs N-1 sur les KPIs critiques.
- Erreurs Sentry top 10.
- Temps de réponse p50/p95/p99.
- Taux 4xx/5xx.

## 12. Rollback

### 12.1 Critères

Rollback immédiat si :

- Erreur fonctionnelle critique reproduisible (perte de données,
  authentification cassée).
- Error rate > 5 %.
- Latence p95 > 3 s.

### 12.2 Procédure

```bash
./scripts/rollback.sh <PREVIOUS_VERSION>
```

Exécute :

1. `docker compose down app`
2. Restore image précédente : `docker pull ghcr.io/newtowt/mynewtowt:<PREV>`
3. Si migration : restore snapshot DB.
4. `docker compose up app`
5. `curl /health` jusqu'à 200.
6. Notification Slack.

MTTR rollback : < 15 minutes.

## 13. Politique des bugs

### 13.1 Classification

| Sévérité | Définition | SLA fix |
|----------|-----------|---------|
| **Critique** | Perte de données / sécurité / blocage métier | < 4 h |
| **Majeur** | Fonctionnalité indisponible, contournement possible | < 24 h |
| **Mineur** | Bug visuel, valeur incorrecte | < 7 j |
| **Trivial** | Texte, typo | best effort |

### 13.2 Workflow

1. Signalé → ticket GitHub étiqueté `bug` + sévérité.
2. Triage quotidien par lead dev.
3. Reproduction obligatoire avec scénario.
4. Si critique : créer immédiatement test E2E qui échoue, puis fix.
5. Post-mortem si critique (sans culpabilité).

## 14. Indicateurs de qualité

Suivis sur dashboard Grafana :

- Coverage globale (cible 80 %, alerte < 75 %).
- Nombre de tests (cible : croissance monotone).
- Durée build CI (cible < 10 min).
- Mean time between failures (MTBF) : > 30 j.
- Bugs critiques en prod par release : ≤ 1.
- Échec rollback : 0.
