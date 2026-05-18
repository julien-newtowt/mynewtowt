# my_TOWT — Documentation post-liquidation TOWT

Suite à la mise en liquidation judiciaire de TOWT (Transport à la Voile), le programme my_TOWT entre dans une phase de refonte. Cette documentation regroupe les audits, plans d'action et spécifications V2 produits pendant cette phase.

## Plan stratégique de référence

Le plan global validé pour la refonte se trouve dans :

```
/root/.claude/plans/la-compagnie-towt-vient-tidy-pond.md
```

Il décrit l'enchaînement des 6 phases (reset DB · désactivation passagers · sécurité · UX · V2/PM · commercial · commandant) et la stratégie de livraison.

## Carte des documents

| Domaine | Document | Statut |
|---------|----------|--------|
| **Sécurité** | [`security/audit-v1.md`](security/audit-v1.md) | Audit V1 — 20 constats classés CVSS-like |
| | [`security/action-plan.md`](security/action-plan.md) | Plan d'action sécurisation Sprint 1/2/3 + backlog |
| **UX / Charte** | [`ux/audit.md`](ux/audit.md) | Audit interface actuelle (13 problèmes UX) |
| | [`ux/design-system-v2.md`](ux/design-system-v2.md) | Design system Kairos (palette, typo, composants) |
| | [`ux/mockups.md`](ux/mockups.md) | Mockups prioritaires V2 (dashboard, planning, escale, onboard) |
| **Project Manager / V2** | [`v2/roadmap.md`](v2/roadmap.md) | Roadmap V2 — feature flags, branches, jalons |
| | [`v2/router-audit.md`](v2/router-audit.md) | Audit des 19 routeurs (planning recalc, escale Import/Export, concordance) |
| | [`v2/chatbot-spec.md`](v2/chatbot-spec.md) | Spec agent conversationnel (Claude API + RAG) |
| | [`v2/ticketing-spec.md`](v2/ticketing-spec.md) | Spec ticketing escale (kanban, SLA, P1/P2/P3) |
| | [`v2/user-guide.md`](v2/user-guide.md) | Guide utilisateur par rôle (squelette V2) |
| **Commercial** | [`commercial/audit-documents.md`](commercial/audit-documents.md) | Audit documents sortants (planning partagé, BL, portail client) |
| **Commandant** | [`captain/audit.md`](captain/audit.md) | Audit onboard — sujets non traités |
| | [`captain/onboard-v2-spec.md`](captain/onboard-v2-spec.md) | Refonte onboard en 4 espaces (escale / nav / cargo / équipage) |

## Phase 1 livrée (cette branche)

- `scripts/reset_database.py` — wipe `public` + recréation schéma + seed minimal.
- `scripts/bootstrap_minimal.py` — admin + 4 navires + FRFEC/BRSSO + OPEX + facteurs CO₂.
- Feature flag `PASSENGERS_ENABLED` (default `False`) — accès passagers retirés sans suppression de code.

Voir le plan global pour les détails et la procédure de vérification.

## Phases 2-6

Documents stratégiques uniquement. L'implémentation de chacune se fera dans une branche dédiée après validation utilisateur.
