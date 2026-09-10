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

### DFT-20260910-001 — 35 clients sur 51 absents de la base (synchronisation Pipedrive tronquée en silence)

| Champ | Valeur |
|---|---|
| Date | 2026-09-10 |
| Reporter | Julien Gonde |
| Persona | Commercial (portefeuille clients) |
| Sévérité | **critique** — deux tiers du portefeuille invisibles dans l'ERP : ni grille tarifaire, ni offre, ni commande possibles pour ces clients |
| Module | commercial |
| Reproductible | oui (toujours, dès que le CRM dépasse 1 000 organisations) |
| Owner | dev |
| Status | **resolved** — branche `fix/pipedrive-sync-organisations-manquantes` |

**Symptôme.** L'export des deals porte **51 organisations** ; la liste clients
en affiche **16**. Le bilan de synchronisation affichait
`created=0&updated=16&skipped=984` — d'apparence normale.

**Ce qui a mis sur la piste.** `16 + 984 = 1000`, exactement le `max_items` de
`pipedrive.list_organizations`. Un total rond égal à un plafond n'est pas un
total, c'est une troncature.

**Cause racine.** `sync_clients` itérait sur le **listing des organisations** et
ne consultait `org_ids_with_deal` (issu de la liste des deals) qu'à l'intérieur
de cette boucle. Les organisations au-delà du millième rang n'étaient donc
jamais examinées — et comme Pipedrive pagine par ancienneté d'identifiant, les
clients manquants étaient les plus récemment créés au CRM. L'exhaustivité
dépendait du nombre de **non-clients** encombrant le CRM : dépendance exactement
inverse de celle qu'on veut. L'`org_id` de chaque deal était pourtant déjà en
mémoire, et jeté.

**Correctif.** Le deal fait le client, pas le listing. Une **passe de
rattrapage** va chercher par identifiant (`pipedrive.get_organization`) toute
organisation portant un deal que le listing n'a pas remontée. L'écriture est
extraite dans `_upsert_org`, partagée par les deux passes ; le client créé est
indexé avant le flush, sans quoi une organisation atteinte deux fois produirait
un doublon — donc une grille tarifaire dupliquée. Le coût d'appels suit
désormais le nombre de **clients**, plus la taille du CRM.

**Ce qui a laissé courir le défaut des mois.** Le bilan ne disait que ce qui
avait été *écrit*, jamais ce qui avait été *examiné*, et aucune borne ne
signalait qu'elle avait été atteinte. Les bornes subsistantes
(`ORG_LIST_MAX_ITEMS`, `_MAX_ORG_LOOKUPS`) sont journalisées **et** remontées
(`truncated`, `lookup_capped`) ; le bilan porte son dénominateur (`with_deal`)
et ses anomalies (`recovered`, `invalid`, `errors`), affichées à l'écran.

**Défaut adjacent signalé, non corrigé.** `client_type` est le seul champ dérivé
du CRM réécrit **inconditionnellement** à chaque synchronisation, alors que tous
les autres protègent la saisie manuelle (`_apply_crm_field`). Sans
`PIPEDRIVE_ORG_ACTIVITY_KEY` configurée, l'heuristique `IFF` peut ne rien
trouver : une correction manuelle en `freight_forwarder` retombe alors en
`shipper` à la synchronisation suivante, silencieusement. Deux issues —
renseigner la variable d'environnement, ou aligner le champ sur
`_apply_crm_field`. **Arbitrage à rendre.**

### DFT-20260908-001 — `/commercial/offers/new` : « Action refusée » à chaque changement de client, et cascade de filtrage inopérante

| Champ | Valeur |
|---|---|
| Date | 2026-09-07 (signalé) → 2026-09-08 (cascade) |
| Reporter | Julien Gonde |
| Persona | Commercial (établissement d'une offre tarifaire) |
| Sévérité | **majeure** en surface (écran inutilisable) — **critique** pour le défaut découvert dessous (prix faux sur la booking note) |
| Module | commercial |
| Reproductible | oui (toujours) |
| Owner | dev |
| Status | **resolved** — 422 + repli de prix : PR #202 ; sens de la cascade : branche `fix/offre-cascade-client-grille-voyage` |

**Symptôme.** Chaque changement de client affichait « Action refusée —
rechargez la page », et la liste des grilles tarifaires ne se filtrait jamais.

**Cause racine (1/3).** `hx-include` envoie **tous** les champs désignés, vides
compris : `leg_id=` partait comme chaîne vide, qu'un paramètre `int | None`
refuse — **422 avant d'entrer dans la route**. Le `detail` d'un 422 est une
*liste* ; `toast.js` ne sait lire qu'un `detail` textuel et affiche son repli
générique. L'opérateur lisait un refus d'autorisation pour un champ qu'il
n'avait pas rempli. Alias `app.utils.query.OptionalInt/Float/Bool` + sentinelle
`tests/regression/test_htmx_include_blank_tolerant.py`.

**Cause racine (2/3).** Le filtre annoncé (« filtrée par client + leg ») n'était
appliqué nulle part — puis, une fois appliqué, il l'était **dans le mauvais
sens** : il demandait de désigner le voyage avant le tarif. La cascade descend
maintenant **client → grille → voyage**, l'ordre dans lequel l'opérateur
travaille.

**Cause racine (3/3), la plus grave.** `offer_create` retombait sur
`grid.lines[0]` — la **première route de la grille** — quand la grille ne
couvrait pas le POL→POD du voyage. Un Fécamp→Santos pouvait donc être coté au
tarif d'un Le Havre→Fort-de-France, sans un mot, et c'est ce prix qui part sur
la booking note. La route refuse désormais en nommant la grille et la route
manquante ; la cascade rend le cas difficile à atteindre depuis l'écran, mais le
refus reste la garde d'un formulaire rejoué.

**Piège écarté en chemin** (jamais parti en production, consigné pour la
prochaine cascade). Chaîner les deux fragments par `HX-Trigger` déclenche
l'événement **avant** le remplacement des options : la liste des voyages
aurait été bornée par la grille du client *précédent*. `HX-Trigger-After-Swap`
lit l'état d'après le swap.

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
| Status | **resolved en production le 2026-09-04** ; correctif permanent en attente de fusion (branche `hotfix/qhse-validation-rules-seed`) |

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

**Correctif.** Migration `20260907_0143` (instantané figé, idempotente) +
bannière/bouton d'init affichés dès que le référentiel est *incomplet* et plus
seulement vide (`/mrv/parametres` — réparation possible sans déploiement) +
deux sentinelles (`tests/regression/test_validation_rules_seeded.py`).

**Résolution en production (2026-09-04, sans déploiement).** La route de
réparation `POST /mrv/parametres/init` (`seed_reference_data`, idempotente et
purement additive) existait déjà en production depuis le lot MRV 2 — seul son
bouton était masqué par le défaut d'affichage ci-dessus. Elle a été déclenchée
par un administrateur depuis la console du navigateur, l'action restant tracée
dans `activity_logs` sous `mrv_validation_seed`. Vérifications :

- `/mrv/parametres` affiche désormais `R27`-`R30` et leurs seuils, **aux valeurs
  exactes de l'instantané figé de la migration** (7 j, 24 h, 20 nm, 0,05 t, 3) —
  production et migration convergent au chiffre près ;
- `/qhse/import` : **90 signalements importés**, 1 ligne quarantainée (résidu de
  fin de classeur), 4 marquées « test présumé » — première exécution réelle de
  `RQ02` sur des données de production.

La migration `20260907_0143` reste nécessaire : elle rend le correctif permanent
pour toute base reconstruite ou restaurée, et sera sans effet au déploiement
(elle n'insère que l'absent).

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
| **`hx-include` face à un `int \| None`** | 1 (DFT-20260908-001) | `hx-include` envoie les champs vides. Utiliser les alias de `app.utils.query` — un 422 n'atteint pas la route et son `detail` en liste devient « Action refusée » à l'écran. Sentinelle : `tests/regression/test_htmx_include_blank_tolerant.py` |
| **Fragment HTMX chaîné avec `HX-Trigger`** | 0 (écarté sur DFT-20260908-001) | `HX-Trigger` tire l'événement *avant* le swap : la requête suivante lit l'état d'avant. Pour chaîner un second fragment sur le contenu qui vient d'arriver, `HX-Trigger-After-Swap` |
| **Plafond de pagination pris pour un critère de sélection** | 1 (DFT-20260910-001) | Une borne de pagination est un garde-fou, pas un filtre. L'atteindre doit être **journalisé et remonté** (`truncated`), et l'exhaustivité ne doit jamais en dépendre. Signal d'alerte : un `total` de synchronisation exactement égal à un plafond codé |
| **Bilan qui ne dit que ce qui a été écrit** | 1 (DFT-20260910-001) | Tout compte rendu d'import porte son **dénominateur** (combien d'entrées éligibles) en plus de ses écritures. « 16 créés » ne permet pas de voir qu'il en manque 35 |
| TTL session client mal calculé | 0 | Test E2E refresh token + expiration |
| Race condition double-booking | 0 | `SELECT FOR UPDATE` + test de concurrence k6 |

(Liste à compléter dès qu'un défaut récurrent est identifié.)
