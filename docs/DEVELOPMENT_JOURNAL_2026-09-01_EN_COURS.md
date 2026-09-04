# Journal de développement — 2026-09-01 → en cours

> Rapport de passation. Une entrée par journée de travail. Voir
> `PROJECT_CONTEXT.md` pour l'état du projet, `CLAUDE.md` pour les consignes
> opérationnelles, `docs/architecture/` pour les ADR.
>
> Période précédente : `DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`.

---

## 2026-09-01 → 2026-09-02 — Reprise, socle méthodologique, assistance

**Objectif** : resynchroniser l'environnement après les fusions de Julien,
désolidariser les consignes permanentes du contexte daté, puis reprendre un
module.

**Livrables** :
- `CLAUDE.md` — la section « Consignes temporaires — manager absent » est
  scindée : les consignes **permanentes** (posture, workflow Git, portes de
  qualité, audit de compatibilité, workflow PR, journal & ADR, style de
  communication) deviennent une section « Operating Instructions » non indexée
  sur une période ; le contenu daté est retiré. Deux règles ajoutées, tirées de
  pièges réellement rencontrés : une branche déjà poussée s'intègre par un
  **merge de `main` dedans** (jamais un rebase, qui exigerait un force push), et
  la lecture d'une suite rouge (`ruff`/`black` ne portent que sur `app` et
  `tests` en CI ; ~19 tests PDF échouent sous Windows faute de GTK/Pango).
- Environnement local resynchronisé (dérive du graphe Alembic corrigée par
  reconstruction d'une base neuve puis bascule par `ALTER DATABASE … RENAME`,
  jamais un `alembic stamp` à l'aveugle).
- Module **Assistance** (`/support`) : correction du surlignage de navigation
  (`sidebar.js` comparait un `href` porteur de `?query` à `location.pathname`),
  cloisonnement du menu par rôle, deux indicateurs ajoutés au tableau de bord
  des demandes. PR #192.

**Décision actée avec Yasmin** : la liste de priorisation P0/P1/P2 de
`CLAUDE.md` est retirée — elle classait en P2 un tableau de bord déjà construit
et restait indexée sur une échéance expirée.

**Arbitrage de séquence** : les Opérations étaient la priorité annoncée, mais
l'exploration a montré 21 commits en 48 h sur le planning par des travaux
parallèles. QHSE et Assistance étaient sans activité récente : reprise là,
pour éviter une collision d'intégration.

---

## 2026-09-03 → 2026-09-04 — QHSE : de la fondation à l'outil d'analyse

**Objectif** : rendre le module QHSE réellement exploitable sur des données
réelles, sans jamais en faire une seconde source d'écriture.

### Lot 1 — Réconciliation des ré-imports (D10) et exécution réelle des règles

**Contexte.** Deux dettes du cahier des charges, confirmées dans le code :
`qhse_reports` était vide (aucun import réel n'avait jamais eu lieu), et le
routeur documentait lui-même sa limite — « ré-importer le même fichier crée de
nouveaux rapports ».

**Livrables** : migration `20260903_0141` (`qhse_import_batches`,
`qhse_reports.source_code` + `import_batch_id`), `_import_row` en
create-or-update, `CorrectiveAction`/`RootCauseEvaluation` en upsert (leur
`report_id` est `UNIQUE`), et `run_rules(db, "qhse", …)` réellement appelé —
RQ01-RQ03 étaient enregistrées mais jamais exécutées contre une ligne persistée.

**Point de vigilance traité avant d'écrire le code** : la clé naturelle
`(navire, jour, sujet)` aurait fusionné trois constats PSC distincts que le
cahier des charges signale lui-même. La clé retenue inclut la description, et a
été validée sur les 190 lignes réelles — 190 clés distinctes, zéro collision.

### Lot 2 — Second format d'export FMS reconnu

Dataset réel (`Anemos_QHSE Reports History.xlsx`) : vue imprimable par navire,
navire en bloc de titre, dates `JJ/MM/AAAA`, une seule date de clôture globale.
En-têtes différents, alias vers les mêmes colonnes canoniques — le reste du
pipeline ne voit aucune différence.

**Décision de Yasmin (fondatrice pour la suite)** : *« l'idée c'est que MyTOWT
soit l'outil d'analyse mais pas d'écriture. Donc ça ne sert à rien d'y ajouter
des colonnes vu que c'est Marad qui restera l'outil pour renseigner toute
donnée. »* Trois colonnes de ce format restent donc délibérément sans alias.

### Lot 3 — Premier tableau de bord

`/qhse/dashboard` + fiche de détail. Reprend le patron visuel de
`dashboard_perf` (SVG server-rendered, aucune lib CDN). **Q2 sciemment
omise** : `report_source` n'étant jamais positionné à `internal_audit`, le
graphe aurait affiché « 0 % audit » — une fausse précision. PR #195, fusionnée
par Julien.

### Incident de production `DFT-20260904-001`

**Symptôme.** Après fusion, `POST /qhse/import` répondait **500** en
production, aucune ligne écrite.

**Cause racine.** `quality_check_results.rule_id` porte une FK vers
`validation_rules.rule_id`. Le socle de règles a été semé en production par la
migration `20260709_0097`, qui **importe la constante `RULE_SEED` du code
applicatif** à l'exécution. Les règles ajoutées au catalogue après son passage
(`R27`-`R30` les 15-16 juillet, `RQ01`-`RQ03` le 22) n'y sont jamais arrivées :
la production portait 31 règles au lieu de 38.

**Ce qu'il faut en retenir.** Le défaut était **structurellement invisible aux
tests** — le seed au boot peuple tout en dev, et une base reconstruite depuis la
chaîne complète est correcte aussi, puisque `0097` relit le `RULE_SEED` courant.
Ma propre vérification « chaîne complète depuis base vierge » lors de la PR #195
ne pouvait donc pas le voir. Portée plus large que QHSE : `R28`-`R30` sont de
scope `event`, donc la **finalisation d'un événement MRV** échouait de la même
façon depuis la mi-juillet.

**Résolution en production, sans déploiement.** La route de réparation
`POST /mrv/parametres/init` (`seed_reference_data`, idempotente et purement
additive) existait déjà en production — mais son bouton était masqué :
`seeded = bool(rules)` ne regardait que les scopes MRV, si bien qu'un
référentiel *partiel* n'était réparable depuis aucun écran. Déclenchée par un
administrateur, elle a rétabli les 7 règles et 11 seuils manquants. Vérifié :
`/mrv/parametres` affiche `R27`-`R30` aux valeurs exactes de l'instantané figé
de la migration, et l'import passe (90 signalements, 1 ligne quarantainée,
4 marquées « test présumé »).

**Correctif permanent** : PR #197 — migration `20260904_0142` (rattrapage
idempotent, valeurs en dur, contenu **généré** depuis l'écart mesuré entre le
catalogue de l'époque `0097` et le catalogue courant), bannière d'init affichée
dès que le référentiel est *incomplet*, `GET /qhse/import` redirigé vers le hub
(il répondait un 422 JSON indiscernable d'une panne réelle, et a brouillé le
diagnostic), et **quatre sentinelles** contre la récidive.

### Lot 4 — Origine de l'émetteur (Q2) et écran qualité

**Livrables** : `classify_issuer_origin` (bord / siège / autorité externe /
indéterminé, dérivé à la lecture — aucune colonne, aucune migration), bloc Q2
au tableau de bord, et nouvel écran `/qhse/qualite` listant ce qu'il reste à
corriger **dans le FMS**, motif nommé par signalement.

**Décision de Yasmin** : `TOWT COMPANY` = le siège. J'ai donc encodé **le
fait** (bord / siège / autorité externe) plutôt que l'interprétation
« opérationnel / audit » du cahier des charges : classer le siège en audit
interne aurait produit ~37 %, très proche des ~33 % attendus — une coïncidence
séduisante, pas une validation. Cf. **ADR-015**.

**Sur données réelles** : bord 49 (54,4 %), siège 25 (27,8 %), autorité externe
13 (14,4 %), indéterminé 3 (3,3 %) — conforme au comptage brut des 9 chaînes
d'émetteur distinctes, ligne à ligne.

**Piège révélé par la donnée réelle** : l'écran qualité listait **90
signalements sur 90**, parce que « responsable non identifié » valait 90.
Ce n'était pas 90 oublis — l'export « historique par navire » ne porte **aucune**
colonne de responsable. Le constat est désormais compté et expliqué à part :
la liste est passée à **36/90** (33 sans cause racine, 4 tests présumés,
2 sans description corrective) et devient un plan de travail.

**Vérification** : les trois écrans rendus en **HTTP authentifié** contre l'app
complète sur les 90 vrais signalements — les tests d'intégration appellent les
fonctions de route et inspectent le contexte, ils n'exercent pas le rendu Jinja.

---

## État à date (2026-09-04)

**Fusionné dans `main`** : PR #192 (assistance), #195 (QHSE lots 1-3).

**En attente de révision** : PR #197 (correctif du référentiel de validation —
production déjà réparée à chaud, la PR rend le correctif permanent).

**En cours** : branche `feature/qhse-origine-emetteur-et-qualite` (lot 4).

**Suite** : 3354 tests verts (conteneur Linux, PDF compris), `ruff`/`black`
verts, parité des 5 catalogues i18n.

### Ce qui reste ouvert, et pourquoi

- **Arbitrage à rendre par Julien** : semer le référentiel de validation au boot
  dans **tous** les environnements (et plus seulement en dev) supprimerait la
  classe entière de défaut de l'incident, au prix d'une écriture en base au
  démarrage de la production. Non tranché — cf. ADR-015, décision 4.
- **Troisième format d'export QHSE** (`Fleetview`, multi-navires avec lignes de
  section `Location: X (n)`) : identifié, non reconnu par l'ingestion.
- **Nom du responsable perdu à l'import** : l'export complet porte
  `CorrectiveActionResponsiblePerson`, mais le modèle ne conserve que la FK
  `responsible_user_id` — un responsable réel sans compte MyTOWT disparaît.
  Un miroir en lecture qui écarte une donnée de la source mérite examen ;
  non traité, car cela suppose de trancher si l'on conserve le texte brut.
- **4 signalements marqués « test présumé »** attendent une décision humaine
  (« essai » et « test » sont aussi le vocabulaire des exercices ISM
  obligatoires — la règle signale sans écarter). Travail QHSE ordinaire.
- **Dette documentaire corrigée au passage** : le runbook renvoyait à
  `./scripts/migrate-prod.sh`, **qui n'existe pas** dans le dépôt.
