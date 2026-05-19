# Process optimization — Démarche d'amélioration continue

> Étape 8 — `/process-optimization`. Opportunités d'évolution,
> d'amélioration, d'optimisation de l'existant et du nouveau.

## 1. État des lieux V2 — Dette identifiée

### 1.1 Dette technique

| # | Dette | Sévérité | Effort |
|---|-------|----------|--------|
| D-01 | Pas d'Alembic en production (scripts SQL ad-hoc) | Élevée | 5 j |
| D-02 | `onboard_router.py` (1840 lignes) monolithique | Moyenne | 10 j |
| D-03 | `cargo_router.py` (1743 lignes) monolithique | Moyenne | 10 j |
| D-04 | `admin_router.py` (2045 lignes) monolithique | Moyenne | 10 j |
| D-05 | Pas de tests automatisés (couverture inconnue) | Élevée | 30 j |
| D-06 | Migration `Base.metadata.create_all` au démarrage | Élevée | 3 j |
| D-07 | Pas de CI/CD pipeline complet | Élevée | 5 j |
| D-08 | Module passagers présent mais désactivé (code zombie) | Faible | 3 j |
| D-09 | `static/css/app.css` monolithique sans tokens | Moyenne | 5 j |
| D-10 | Pas de monitoring applicatif (Sentry/Prom absents) | Élevée | 5 j |
| D-11 | Sécurité : MFA absent | Élevée | 5 j |
| D-12 | Données chiffrées : aucune colonne C5 chiffrée at-rest | Élevée | 5 j |

### 1.2 Dette fonctionnelle

| # | Dette | Cible V3 |
|---|-------|----------|
| F-01 | Pas de réservation publique en self-service | Plateforme booking |
| F-02 | Pas de compte client persistant | `client_accounts` |
| F-03 | Pas de portail dashboard pour clients | `/me` |
| F-04 | Pas de paiement en ligne | Stripe Checkout |
| F-05 | Pas de chatbot d'aide | Kairos AI |
| F-06 | Pas de ticketing escale | `tickets` module |
| F-07 | Onboard pas pensé "pont mobile" | PWA 4 espaces |
| F-08 | Pas de météo intégrée | Windy API |
| F-09 | Pas de feature flags | `feature_flags` table |
| F-10 | Reporting CO₂ pas exportable client | `co2_certificates` |

## 2. Opportunités d'optimisation V3

### 2.1 Quick wins (effort < 3 j chacun)

| # | Action | Bénéfice |
|---|--------|----------|
| Q-01 | Pré-compilation des templates Jinja2 | TTFB -100 ms |
| Q-02 | Cache CDN sur `/static/*` | bande passante -70 % |
| Q-03 | Gzip + Brotli sur nginx | payload -60 % |
| Q-04 | Lazy-load images cartes | LCP -300 ms |
| Q-05 | Index DB manquants sur queries fréquentes | p95 -200 ms |
| Q-06 | Pagination keyset au lieu d'offset | scans table évités |
| Q-07 | Connection pooling Postgres tuning | RPS +30 % |
| Q-08 | Compression HTTP/2 | payload -20 % |
| Q-09 | Service worker PWA | cache offline |
| Q-10 | HTTP cache headers (max-age) | requêtes -40 % |

### 2.2 Optimisations métier

#### O-01 — Pricing dynamique intelligent

**Idée** : ajuster automatiquement le prix par palette en fonction de
l'occupation prévisionnelle d'un leg.

```python
def dynamic_price(leg: Leg, t_days_to_etd: int) -> float:
    occupancy = leg.reserved_palettes / leg.capacity_palettes
    base = leg.public_price_per_palette_eur or vessel_default_price
    if t_days_to_etd > 30 and occupancy < 0.5:
        return base * 0.9  # early bird -10%
    if t_days_to_etd < 7 and occupancy > 0.85:
        return base * 1.3  # last seat surcharge +30%
    return base
```

Implémentation : service `app/services/pricing.py`, configurable via
`pricing_strategies` (table).

#### O-02 — Allocation cargo optimale

**Idée** : algorithme de remplissage cale maximisant la marge sous
contraintes (IMDG, poids, dimensions, stackability).

Approche : heuristique "First Fit Decreasing" 3D adaptée au navire
NEWTOWT, persistée dans `app/services/stowage_optimizer.py`.

Bonus : recommander un placement alternatif si une zone n'a plus de
place pour un format particulier.

#### O-03 — Détection désengagement client

**Idée** : alerte commercial si un client `recurring` n'a pas booké
depuis 90 jours.

```sql
SELECT c.id, c.company_name, MAX(b.created_at) AS last_booking
FROM client_accounts c
LEFT JOIN bookings b ON b.client_account_id = c.id
WHERE c.segment = 'recurring'
GROUP BY c.id, c.company_name
HAVING MAX(b.created_at) < NOW() - INTERVAL '90 days'
   OR MAX(b.created_at) IS NULL;
```

Tournée hebdo, ticket auto pour commercial avec template de relance.

#### O-04 — Pré-remplissage packing list

**Idée** : à la confirmation booking, créer automatiquement un Order +
PackingList draft avec les items du booking.

Évite une double-saisie côté staff. Cf. `app/services/booking.py`.

#### O-05 — ETA shift prédictive

**Idée** : utiliser l'historique des vitesses + météo prévue (Windy)
pour suggérer un ETA plus précis qu'ETD + distance × vitesse moyenne.

Approche : régression linéaire simple sur `noon_reports` historiques
du même couloir vélique.

Bénéfice : -50 % d'ETA shifts > 12 h.

### 2.3 Optimisations UX

#### U-01 — Recherche universelle Cmd+K

Décrite en `mockups.md` §5. Mesurée : économise ~3 clics par recherche.

#### U-02 — Onboarding personnalisé par rôle

5-7 étapes de tour guidé adapté au rôle au premier login. Mesure :
adoption fonctionnalités clés +40 %.

#### U-03 — PDF templates uniformisés

Tous les PDFs (BL, packing list, facture, certificat CO₂, manifest)
sortent du même template WeasyPrint avec header/footer Kairos.

Gain : maintien centralisé, charte respectée à 100 %.

#### U-04 — Notifications priorisées

Centre de notifications avec catégories (booking, escale, ticket,
système). Marquage lu/non lu. Préférences par canal et par catégorie.

#### U-05 — Mode offline complet PWA

Service worker + IndexedDB pour cache des données critiques pont
(noon report, journal de quart, check-lists, manifest). Sync auto au
retour réseau.

### 2.4 Optimisations data

#### D-01 — Vues matérialisées

Pour les KPIs lourds (CO₂ cumulé annuel, marge mensuelle), créer des
vues matérialisées rafraîchies hourly :

```sql
CREATE MATERIALIZED VIEW analytics.mv_monthly_co2 AS
SELECT
  DATE_TRUNC('month', issued_at) AS month,
  client_account_id,
  SUM(co2_avoided_kg) AS co2_avoided_kg
FROM co2_certificates
GROUP BY 1, 2;

CREATE INDEX ON analytics.mv_monthly_co2 (month, client_account_id);
```

Refresh : `REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.mv_monthly_co2;`
toutes les heures via cron.

#### D-02 — Pagination keyset

Pour `/me/bookings` qui peut afficher 100+ entrées :

```sql
-- Mauvais : OFFSET 1000
SELECT * FROM bookings WHERE client_account_id = $1
ORDER BY created_at DESC LIMIT 50 OFFSET 1000;

-- Bon : WHERE id < last_seen_id
SELECT * FROM bookings
WHERE client_account_id = $1 AND id < $2
ORDER BY id DESC LIMIT 50;
```

#### D-03 — Search index Postgres

Pour la recherche universelle Cmd+K, créer un index GIN tsvector :

```sql
ALTER TABLE bookings ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector('french', reference || ' ' || coalesce(shipper_reference, ''))
  ) STORED;
CREATE INDEX idx_bookings_search ON bookings USING GIN (search_vector);
```

### 2.5 Optimisations sécurité

Cf. `docs/security/01-security-review.md` § 17 — plan de sprints sécurité.

### 2.6 Optimisations de coût

| Sujet | Optimisation | Économie |
|-------|--------------|----------|
| Anthropic API | Prompt caching (90 % cache hit) | -75 % API cost |
| Anthropic API | Modèle Haiku pour Q&A simples | -90 % vs Sonnet |
| Stripe | Bundling charges (1 facture mensuelle B2B) | fees -20 % |
| Mapbox | Self-host tiles avec MapTiler/OSM | -100 EUR/mois |
| Backup S3 | Migration vers Glacier après 30 j | -90 % storage |
| Sentry | Sampling 10 % en prod | -80 % quota |

## 3. Démarche d'amélioration continue (Kaizen)

### 3.1 Rituel hebdomadaire

- **Lundi matin** (1 h) : revue Sentry + Grafana de la semaine.
- **Mardi midi** (30 min) : revue tickets escale ouverts.
- **Mercredi soir** (1 h) : refacto club — 1 dette technique mineure
  traitée.
- **Jeudi** : pas de réunion (deep work).
- **Vendredi** (30 min) : démo + rétro hebdo.

### 3.2 Rituel mensuel

- **Premier vendredi** (2 h) : codir tech — KPIs, roadmap, prochains
  jalons.
- **Dernier vendredi** (1 h) : "Customer feedback day" — synthèse des
  retours utilisateurs internes + clients, priorisation backlog.

### 3.3 Rituel trimestriel

- **OKRs review** — objectifs Q et résultats clés.
- **Pen-test interne** — équipe sécurité.
- **Refacto sprint** (5 j dédiés) — dette technique critique.

### 3.4 Suggestions utilisateurs

Bouton flottant "Suggestion" sur toutes les pages → ouvre un ticket
GitHub étiqueté `enhancement` :

```html
<button class="floating-suggest" data-page="/me">
  💡 Suggérer une amélioration
</button>
```

Workflow :

1. User clique → formulaire 3 champs (titre, description, screenshot).
2. POST `/feedback` → crée issue GitHub via API.
3. Triage hebdo par PM.
4. Suggestions retenues → backlog avec mention auteur.

### 3.5 NPS interne

Enquête NPS interne mensuelle :

- 1 question : "Recommanderiez-vous Kairos à un collègue ?" (0-10).
- 1 question ouverte facultative.
- Envoyée le 1er lundi du mois.
- Scores publiés sur `/admin/nps` + tendance.

## 4. Backlog d'optimisations V3.1+

| # | Idée | Effort | Bénéfice |
|---|------|--------|----------|
| B-01 | API GraphQL pour clients B2B grand compte | M | Intégration ERP |
| B-02 | Marketplace co-chargement | L | Nouveau revenu |
| B-03 | Certificats CO₂ blockchain | L | Trust + différenciation |
| B-04 | Routing voile (polaires + waypoints) | L | Économie carburant |
| B-05 | Vocal in/out chatbot (Whisper + ElevenLabs) | M | UX pont (mains libres) |
| B-06 | Programme de fidélité | M | Retention |
| B-07 | Stripe Subscriptions (abo capacités) | M | Revenu récurrent |
| B-08 | Partenaires transporteurs terrestres | L | One-stop-shop |
| B-09 | Mobile native iOS (clients) | L | Engagement |
| B-10 | Multi-tenant (autre armateur) | XL | Scale offre |

## 5. Mesure de l'impact des optimisations

Chaque optimisation déployée doit :

1. **Définir une métrique** mesurable avant (baseline).
2. **Forecaster un impact** (cible chiffrée).
3. **Mesurer post-déploiement** sur ≥ 4 semaines.
4. **Documenter** dans `docs/strategy/optim-log.md`.

Template :

```
## Optim Q1-2026-#03 — Lazy load cartes
- Date : 2026-03-15
- Owner : @frontend-team
- Métrique : LCP /routes
- Baseline : 2.1 s
- Cible : < 1.5 s
- Mesure post : 1.3 s ✅
- Coût : 2 j dev
- ROI : LCP -38 %, retours utilisateurs positifs
```

## 6. Anti-optimisations (à éviter)

- ⛔ Microservices pour 200 utilisateurs internes.
- ⛔ Caching agressif au prix de la cohérence.
- ⛔ Réécriture en React/Vue pour "améliorer la perf perçue".
- ⛔ Sharding DB avant 10 K bookings/jour.
- ⛔ Optimiser sans mesure (don't optimize prematurely).
- ⛔ Stocker des secrets pour "performance" (toujours via env vars).

## 7. KPI process amélioration continue

Dashboard `/admin/process-kpi` :

- Dette technique tracée : count d'items
  (cible : croissance monotone décroissante).
- Lead time PR open → merged (cible < 48 h).
- Cycle time issue open → closed (cible < 2 semaines).
- Échecs CI (cible < 5 %).
- Couverture tests (cible > 80 %).
- Vélocité (story points / sprint).
- Bugs en prod par sprint (cible : < 3).
