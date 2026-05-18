# Roadmap V2 — Kairos

**Branche cible** : `v2/main` (à créer après merge Phase 1 sur `main`).

**Hypothèse de travail** : 1 dev senior backend + 1 dev frontend + 1 designer UX, full-time, 12 semaines.

## Jalons

| Sem. | Jalon | Livrables |
|------|-------|-----------|
| 1 | Bootstrap V2 | Branche `v2/main`, env Docker dédié, Alembic baseline, CI GitHub Actions |
| 2 | Sécurité Sprint 1 | Cf. [`../security/action-plan.md`](../security/action-plan.md) |
| 3-4 | Design system Kairos | `tokens.css`, `kairos.css`, refonte sidebar + dashboard |
| 5 | Refonte planning + recalcul cascade | Drag-and-drop, propagation aux modules couplés |
| 6 | Escale Import/Export | Schéma `direction`, UI 2 colonnes, audit dates stale |
| 7 | Refonte Onboard 4 espaces | Nav, Cargo, Crew, Escale + landing |
| 8 | Chatbot Kairos AI | Endpoint /chat, RAG pgvector, tool-use lecture |
| 9 | Ticketing escale | Kanban, SLA auto-escalade, notifications |
| 10 | Commercial — docs sortants | Templates PDF Kairos, Arrival Notice, portail client V2 |
| 11 | QA + perf | Lighthouse > 90, Core Web Vitals, accessibilité WCAG AA |
| 12 | Release V2 | Migration prod (rebrand), formation utilisateurs, doc finale |

## Feature flags V2

Table `feature_flags(key VARCHAR PK, enabled BOOL, rollout_pct INT, audience JSONB)`.

Flags initiaux :
- `passengers_module` (default false — déjà en place sous forme env var en Phase 1, à migrer en table V2)
- `kairos_design_system` (rollout progressif par user/role)
- `chatbot_kairos_ai`
- `escale_import_export_split`
- `ticketing_v1`
- `onboard_v2_layout`

Helper Python : `feature_enabled(key, user)` qui résout `enabled` × `rollout_pct` × `audience.roles`.

## Dépendances externes

| Service | Usage | Coût mensuel |
|---------|-------|-------------|
| Anthropic Claude API | Chatbot + RAG | ~50-200 EUR |
| pgvector (extension PG) | Vector store RAG | 0 (self-hosted) |
| Mapbox / Maptiler | Cartes flotte | ~50 EUR |
| Windy API | Météo embarquée | ~30 EUR |
| Doppler / Vault | Secrets management | 0-50 EUR |
| Sentry | Erreurs prod | 0-26 EUR |

## Critères de succès V2

- Lighthouse Performance ≥ 90 sur dashboard, planning, onboard.
- Time-to-interactive < 2 s sur 3G fast.
- WCAG AA validé (axe-core CI).
- Zéro 🔴 et zéro 🟠 sur audit sécurité post-Sprint 2.
- 100 % des tests fonctionnels critiques (création leg, escale, BL) automatisés.
- Adoption utilisateur Gen Z mesurée par NPS interne ≥ 40.
