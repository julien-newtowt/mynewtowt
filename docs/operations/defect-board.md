# Defect Board

Tableau de tri des défauts détectés en pré-prod et prod. Mis à jour à
chaque session de debugging systématique.

## Format

| Champ | Description |
|-------|-------------|
| ID | `DFT-YYYYMMDD-XXX` |
| Date | Date de signalement |
| Reporter | Persona ou nom user |
| Persona | Lequel des 8 personas est touché |
| Sévérité | critique / majeure / mineure / triviale |
| Module | planning / booking / cargo / ... |
| Reproductible | oui (toujours) / partiel / non |
| Owner | équipe ou personne |
| Status | open / investigating / fix-in-progress / resolved |
| ETA fix | date prévue de résolution |

## Open

_(aucun défaut connu à ce jour — la plateforme V3 vient d'être livrée.)_

## In investigation

_(vide)_

## Recently resolved (last 30 days)

_(vide — historique se remplira avec les premières releases)_

## Patterns récurrents (rétrospective)

| Pattern | Fréquence | Action préventive |
|---------|-----------|-------------------|
| Régressions sur permissions M/S | 0 | Tests RBAC obligatoires sur tout changement de matrice |
| Migrations non-réversibles | 0 | `alembic downgrade -1` testé en CI |
| TTL session client mal calculé | 0 | Test E2E refresh token + expiration |
| Race condition double-booking | 0 | `SELECT FOR UPDATE` + test de concurrence k6 |

(Liste à compléter dès qu'un défaut récurrent est identifié.)
