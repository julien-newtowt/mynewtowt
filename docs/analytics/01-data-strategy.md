# Stratégie data, analytics & décision

> Étape 6 — `/variance-analysis` + `/build-dashboard`. Politique de
> traitement des données collectées par l'application pour en faire une
> plateforme d'analyse et de décision.

## 1. Cartographie des données

### 1.1 Sources primaires (collectées)

| Domaine | Volume estimé/an | Cadence |
|---------|-----------------|---------|
| Legs & navigations | ~200 legs/an | Quasi-temps réel |
| Vessel positions | ~500 K points | 5 min |
| Bookings | 5–20 K/an | Temps réel |
| Orders historiques | ~3 K/an | Temps réel |
| Packing lists | ~3 K/an | Temps réel |
| SOF events | ~50 K/an | Temps réel |
| OPEX & finance | ~12 K écritures/an | Quotidien |
| MRV (émissions) | ~1 K mesures/an | Quotidien |
| KPI agrégés | recalcul N+1 | Quotidien |
| Tickets escale | ~5 K/an | Temps réel |
| Activity logs | ~500 K/an | Temps réel |
| Claims | ~50/an | Temps réel |

### 1.2 Sources externes

| Source | Donnée | Latence |
|--------|--------|---------|
| Windy API | Météo prévision | < 1 h |
| AIS (MarineTraffic / NWE) | Position vessels concurrents | 10 min |
| Pipedrive | Pipeline commercial | webhook temps réel |
| Stripe | Paiements | webhook temps réel |
| Exchange rates (ECB) | EUR/USD/BRL | quotidien |
| Eurostat trade flows | Indicateurs macro | mensuel |

### 1.3 Données dérivées (calculées)

| Métrique | Formule | Source |
|----------|---------|--------|
| `occupancy_pct` | `reserved_palettes / capacity_palettes × 100` | Bookings + Vessel |
| `on_time_pct` | legs avec `\|ATA-ETA\| < 24h / total legs` | Legs |
| `revenue_per_leg` | `Σ confirmed_price - port_fees - opex_share` | Finance |
| `margin_pct` | `(revenue - cost) / revenue` | Finance |
| `co2_avoided_per_leg` | `tonnage × distance × (conv_ef - towt_ef)` | KPI |
| `mttr_p1` | `Σ (resolved_at - created_at) / nb_tickets_p1` | Tickets |
| `nps_internal` | enquête mensuelle | RH |

## 2. Architecture data

### 2.1 Couches

```
┌────────────────────────────────────────────────────┐
│ Application (FastAPI)                              │
│   ↓ écritures temps réel                           │
├────────────────────────────────────────────────────┤
│ Postgres 16 (OLTP) — base opérationnelle           │
│   ↓ CDC + ETL hourly                                │
├────────────────────────────────────────────────────┤
│ Postgres analytics (schéma `analytics.*`) — OLAP   │
│   ↓ vues matérialisées + dbt                       │
├────────────────────────────────────────────────────┤
│ Dashboards (Metabase / interne) + exports CSV/XLSX │
└────────────────────────────────────────────────────┘
```

Choix : **Postgres unique** avec schémas séparés `public` (OLTP) +
`analytics` (OLAP). Pas de Snowflake / BigQuery pour V3 — volume
insuffisant. Migration possible en V4 si croissance >10x.

### 2.2 Outils

| Outil | Rôle | Hébergement |
|-------|------|-------------|
| **dbt** | Transformations SQL versionnées | conteneur séparé |
| **Metabase** | Dashboards self-service | `analytics.my.newtowt.eu` |
| **OpenTelemetry** | Tracing applicatif | Tempo (Grafana) |
| **Prometheus** | Métriques infra | `metrics.my.newtowt.eu` interne |
| **pgvector** | Embeddings RAG chatbot | extension Postgres |

### 2.3 Modèle dimensionnel (étoile)

```
                     ┌──────────────┐
                     │  dim_vessel   │
                     └──────┬───────┘
┌──────────┐                │
│ dim_port │ ── leg ────────┼─── dim_date
└──────────┘                │
                     ┌──────▼──────┐
                     │ fct_voyage  │   (1 ligne par leg)
                     └─────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  fct_booking         fct_emission         fct_finance
   (1 par booking)    (1 par leg)          (1 par leg)
```

dbt models prévus :

- `models/staging/stg_*.sql` — 1 par table source.
- `models/marts/dim_vessel.sql`, `dim_port.sql`, `dim_date.sql`,
  `dim_client.sql`.
- `models/marts/fct_voyage.sql`, `fct_booking.sql`, `fct_emission.sql`,
  `fct_finance.sql`, `fct_ticket.sql`.
- `models/marts/agg_monthly_flotte.sql`.

## 3. Dashboards livrés

### 3.1 Dashboard exécutif `/dashboard/exec` (manager_maritime + admin)

```
┌─────────────────────────────────────────────────────────────────┐
│  Vue flotte — 4 navires                                         │
├─────────────────────────────────────────────────────────────────┤
│  KPIs mois en cours                                             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │ Remplissage  │ │ Marge        │ │ CO₂ évité    │            │
│  │  78 %        │ │ +12 %        │ │ -47 t        │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│                                                                 │
│  Carte mondiale (positions live des 4 navires)                  │
│                                                                 │
│  Variance vs N-1 (occupation, prix moyen, ticket SLA)           │
│                                                                 │
│  Top 10 clients par tonnage                                     │
│  Top 5 alertes ouvertes                                         │
└─────────────────────────────────────────────────────────────────┘
```

Filtres : période (M, T, A), navire, route, segment client.

### 3.2 Dashboard commercial `/dashboard/sales`

- Funnel landing → booking confirmé.
- Pipeline Pipedrive (deals open / won / lost).
- Top routes converties.
- Taux d'annulation par fenêtre.
- LTV (lifetime value) par segment client.
- Revenu projeté vs réalisé.

### 3.3 Dashboard opérations `/dashboard/ops`

- Calendrier 90 j des escales (Gantt port × jour).
- Tickets P1 ouverts par navire/port.
- SLA respect (P1, P2, P3) sur 30 j.
- Top 5 ports avec retards.
- Heatmap retards par jour de la semaine.

### 3.4 Dashboard finance `/dashboard/finance`

- Variance budget vs réalisé par leg.
- Coût palette transportée mensuel.
- OPEX vs revenu par navire.
- DSO clients (délai paiement moyen).
- Encours factures (paid, issued, overdue).
- Couverture assurance vs claims provisionnés.

### 3.5 Dashboard MRV / CO₂ `/dashboard/mrv`

- Émissions par leg / par navire / cumul annuel.
- CO₂ évité vs flotte conventionnelle (gCO₂/t.km).
- Conformité reporting EU MRV (DNV CSV format).
- Top 10 clients par CO₂ évité (donne le badge "champion décarboné").

### 3.6 Dashboard RH `/dashboard/rh`

- Effectif embarqué / à terre.
- Rotation moyenne par marin.
- Compliance Schengen (warnings, non_compliant).
- Tickets crew (médical, transport).
- Taux d'absentéisme par profil.

### 3.7 Dashboard client (espace privé)

- Cumul mensuel : palettes expédiées, CO₂ évité.
- Comparatif vs alternative conventionnelle.
- Factures payées vs en attente.
- Score "engagement décarboné" (gamification).

## 4. Capacités de filtrage avancé

Toutes les vues data exposent :

- **Time range picker** : jour, semaine, mois, trimestre, année, custom
  range avec comparaison N-1 par défaut.
- **Filtres multi-select** : navires, ports, routes, statuts.
- **Drill-down** : clic sur agrégat → table détail.
- **Export** : CSV, Excel, PDF (avec en-tête société + dates filtre).
- **Persistance** : chaque user peut sauver ses filtres préférés.
- **Partage** : URL signée avec filtres encodés (token JWT 7j).

## 5. Animation du reporting

### 5.1 Animations sur dashboards

- **Transition de filtre** : 250 ms ease-out, fade + slide.
- **Bar/line charts** : draw animé 600 ms à l'apparition.
- **KPI cards** : counter animé (0 → valeur) sur 800 ms.
- **Heatmap** : cellules apparaissent en cascade 30 ms each.
- **Map** : navires animés en SVG fluide (interp 1s entre positions).

Respect strict de `prefers-reduced-motion : reduce`.

### 5.2 Refresh data

- Dashboards "live" (exec, ops) : SSE + refresh 30 s.
- Dashboards "historiques" (finance, MRV) : refresh manuel (bouton +
  badge "data à H-N").
- Refresh data `analytics.*` : dbt run hourly + cron 04:00 UTC nightly.

## 6. Variance analysis — méthode

### 6.1 Trois types de variance à exposer

1. **Variance vs plan** : réalisé vs prévisionnel sur la même période.
2. **Variance vs historique** : N vs N-1 (même mois année précédente).
3. **Variance vs cohorte** : navire X vs moyenne flotte.

### 6.2 Affichage

Chaque KPI s'accompagne :

```
┌─────────────────────────┐
│ Remplissage              │
│   78 %                   │
│ ▲ +6 pts vs N-1          │
│ ▼ -3 pts vs plan         │
│                          │
│ ─── sparkline 12 mois ── │
└─────────────────────────┘
```

Sparklines en SVG inline, couleur `--accent` si positif, `--warn` si
négatif, `--text-1` si neutre.

### 6.3 Alertes automatiques

Cron horaire calcule les variances et lève un événement
`analytics.alert` si :

- Remplissage < 60 % à J-21 d'un ETD.
- Marge prévisionnelle < 8 % sur 3 legs consécutifs.
- DSO > 60 j sur un client `key_account`.
- SLA P1 < 95 % sur 7 j glissants.
- CO₂ par t.km > +10 % vs moyenne 90 j (anomalie nav).

L'événement notifie le rôle responsable + s'affiche en tuile rouge sur
le dashboard exécutif.

## 7. Qualité des données

### 7.1 Contrôles automatiques

- **Schéma** : Pydantic + Alembic enforcent types et FK.
- **Cohérence temporelle** : ETD < ETA, ATD < ATA, etc., contrôlés en
  application + check constraints SQL.
- **Référentiels** : ports, formats palette, monnaies sont
  des enums DB.
- **Doublons** : index UNIQUE sur `reference` (booking, invoice, leg_code).
- **Complétude** : score data quality par module visible dans
  `/admin/data-quality`.

### 7.2 Audit dbt

```yaml
# models/schema.yml
models:
  - name: fct_voyage
    columns:
      - name: leg_id
        tests: [not_null, unique]
      - name: vessel_id
        tests:
          - relationships:
              to: ref('dim_vessel')
              field: vessel_id
```

`dbt test` exécuté à chaque dbt run, alerte si fail.

### 7.3 Gouvernance

- Catalogue de données dans `docs/analytics/data-catalog.md` (à
  enrichir au fil de l'eau).
- DPO référent : `dpo@newtowt.eu`.
- Politique de rétention : cf. §10.

## 8. Reporting réglementaire

### 8.1 EU MRV (UE 2015/757)

- Export annuel automatique au format DNV CSV
  (`mrv_router.py` existant à étendre V3).
- Vérificateur tiers : envoi en avril N+1.
- Champs requis : voyage_id, fuel_consumed, co2_emitted, distance,
  cargo_carried, time_at_sea, etc.

### 8.2 RGPD

- Registre des traitements : `docs/analytics/rgpd-registry.md`.
- Droits user : accès, rectification, portabilité (export ZIP), oubli.
- Endpoint `/me/account/export` (ZIP de toutes les données du user).
- Endpoint `/me/account/delete` (purge avec délai 30 j de réversibilité).

### 8.3 Bilan carbone Bilan Carbone® annuel

Génération automatique du bilan carbone N pour chaque client recurrent
(certificat + tableau Bilan Carbone scope 3).

## 9. APIs analytics (interne)

| Endpoint | Rôle |
|----------|------|
| `GET /api/analytics/kpi/{kpi_id}` | Valeur live d'un KPI |
| `GET /api/analytics/kpi/{kpi_id}/series` | Série temporelle |
| `GET /api/analytics/variance` | Bulk variances dashboards |
| `GET /api/analytics/export/{report_id}` | Export CSV/XLSX |
| `POST /api/analytics/saved-filter` | Sauver un filtre |
| `GET /api/analytics/shared/{token}` | Accès lien partagé |

Auth : session interne + scope `analytics:read`.

## 10. Rétention & archivage

| Donnée | Rétention live | Archivage |
|--------|----------------|----------|
| Logs applicatifs | 90 j | S3 froid 7 ans |
| Activity logs | 1 an | S3 froid 10 ans |
| Vessel positions | 2 ans | S3 froid 10 ans |
| Bookings | actif + 10 ans | conservé en DB |
| Invoices | 10 ans (légal) | conservé en DB + S3 PDF |
| BL signés | 30 ans | conservé en DB + S3 PDF |
| Claims | actif + 10 ans | conservé en DB |
| Client account inactif > 5 ans | proposition oubli RGPD | purge auto |

## 11. Décisions soutenues par la data

Cas d'usage concrets implémentés :

1. **Pricing dynamique** : ajustement prix palette en fonction de
   l'occupation prévisionnelle à J-30, J-15, J-7 (algo dans
   `app/services/pricing.py`).
2. **Routing optimisé** : analyse historique des vents pour suggérer un
   décalage d'ETD au commercial (alerte sur dashboard).
3. **Détection de désengagement** : client `recurring` qui n'a pas
   réservé depuis 90 j → alerte commercial pour relance.
4. **Allocation cale** : algo de remplissage maximisant la marge sous
   contraintes IMDG + poids + dimensions (heuristique `bin-packing` 3D
   simplifiée, cf. `app/services/stowage_optimizer.py`).
5. **Recommandation client** : "Voici les 3 routes qui correspondent à
   votre historique" sur la landing connectée.
