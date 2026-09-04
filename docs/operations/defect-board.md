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

### DFT-20260904-001 — Import QHSE inopérant en production (500)

| Champ | Valeur |
|---|---|
| Date | 2026-09-04 |
| Reporter | Yasmin Ponce |
| Persona | Responsable QHSE (analyse des signalements) |
| Sévérité | **critique** (fonctionnalité totalement inopérante en prod) |
| Module | qhse |
| Reproductible | oui (toujours, sur une base migrée avant le 2026-07-22) |
| Owner | dev |
| Status | fix-in-progress (branche `hotfix/qhse-validation-rules-seed`) |

**Symptôme.** `POST /qhse/import` répond `500 Internal Server Error`. Aucune
ligne écrite. Le hub `/qhse` et le tableau de bord fonctionnent normalement.

**Cause racine.** `quality_check_results.rule_id` porte une FK vers
`validation_rules.rule_id`. Le socle de règles a été semé en production par la
migration `20260709_0097`, qui **importe la constante `RULE_SEED` du code
applicatif** à l'exécution. Les règles QHSE `RQ01`-`RQ03` ont été ajoutées à
`RULE_SEED` le 2026-07-22 (commit `1145d73`) — après le passage de `0097` en
production, et sans migration de rattrapage. La base de production porte donc
les 35 règles MRV et aucune règle QHSE. Le lot de câblage RQ01-03 (PR #195) les
exécute réellement : l'`INSERT` du résultat viole la FK et emporte la
transaction entière.

**Pourquoi la CI était verte.** En dev/test, `seed_reference_data` peuple les 38
règles au boot. Et une base reconstruite depuis la chaîne complète de migrations
les a aussi, puisque `0097` relit le `RULE_SEED` *courant* — vérifié
empiriquement : chaîne neuve → 38 règles, dont les trois RQ. Seule une base
migrée avant le 2026-07-22 est incomplète, et il n'en existe qu'une : la
production. Le défaut était **structurellement invisible à tout test**.

**Portée réelle — confirmée en production le 2026-09-04.** Le défaut ne se
limite pas à QHSE. L'écran `/mrv/parametres` de production s'arrête à `R26` :
le référentiel y est resté à l'état du 2026-07-09, soit **31 règles**, et il en
manque **7** — `R27`, `R28`, `R29`, `R30` (MRV, ajoutées les 15-16 juillet) en
plus de `RQ01`-`RQ03`. Or `R28`/`R29`/`R30` sont de scope `event`, celui
qu'exécute `run_rules` à la **finalisation d'un événement MRV**
(`event_capture.py:283`) : ce chemin échoue donc de la même façon depuis la
mi-juillet. Jusqu'à 11 seuils sont également absents — sans conséquence de
verdict, `get_threshold` retombant sur les défauts codés, vérifiés **identiques
aux valeurs semées** (31/31, valeur et unité).

Aucune des 7 règles manquantes n'est `bloquant` (5 `warning`, 1 `info`, et les
deux `bloquant` sont de scope `qhse`, où elles n'empêchent pas l'import) : les
semer ne peut donc bloquer aucun workflow, seulement rétablir des contrôles
inopérants.

**Correctif.** Migration `20260904_0142` (instantané figé, idempotente) +
bannière/bouton d'init affichés dès que le référentiel est *incomplet* et plus
seulement vide (`/mrv/parametres` — réparation possible sans déploiement) +
deux sentinelles (`tests/regression/test_validation_rules_seeded.py`).

**Recommandation non traitée (décision à prendre).** Aujourd'hui, un référentiel
incomplet fait perdre **tout** l'import (transaction entière) sur une erreur qui
n'a rien à voir avec le contenu du fichier. Deux options, non retenues ici parce
qu'elles engagent la sémantique du moteur de règles et non ce seul correctif :
(a) `run_rules` ignore les règles absentes en base et **le signale** dans le
compte rendu d'import (l'import survit, les contrôles manquants sont annoncés —
jamais silencieux) ; (b) seed du référentiel au boot dans **tous** les
environnements et plus seulement en développement (`seed_reference_data` est
idempotent et purement additif) — ce qui supprimerait la classe entière de
défaut, au prix d'une écriture en base au démarrage de la production.

**Défauts adjacents traités.** (1) `seeded = bool(rules)` sur les seuls scopes
MRV rendait un référentiel partiel irréparable depuis l'interface. (2) Un GET sur
`/qhse/import` répondait un 422 JSON « invalid integer » (capture par
`/qhse/{report_id}`), indiscernable d'une panne réelle — il a brouillé le
diagnostic.

## Patterns récurrents (rétrospective)

| Pattern | Fréquence | Action préventive |
|---------|-----------|-------------------|
| Régressions sur permissions M/S | 0 | Tests RBAC obligatoires sur tout changement de matrice |
| Migrations non-réversibles | 0 | `alembic downgrade -1` testé en CI |
| **Migration qui importe une constante du code applicatif** | 1 (DFT-20260904-001) | Une migration est un **instantané** : valeurs en dur. Son effet ne doit pas dépendre de sa date d'exécution — sinon les bases anciennes divergent des neuves, et aucun test ne le voit. Sentinelle : `tests/regression/test_validation_rules_seeded.py` |
| **Catalogue codé enrichi sans migration de rattrapage** | 1 (DFT-20260904-001) | Toute nouvelle entrée d'un référentiel semé en base (règles, seuils, paramètres) exige une migration additive idempotente — le seed au boot ne couvre que le dev |
| TTL session client mal calculé | 0 | Test E2E refresh token + expiration |
| Race condition double-booking | 0 | `SELECT FOR UPDATE` + test de concurrence k6 |

(Liste à compléter dès qu'un défaut récurrent est identifié.)
