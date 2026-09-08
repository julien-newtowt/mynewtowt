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

**Correctif permanent** : PR #197 — migration `20260907_0143` (rattrapage
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
séduisante, pas une validation. Cf. **ADR-016**.

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

## 2026-09-04 (suite) — MRV devient un module de navigation à part

**Objectif** (demande de Yasmin) : sortir le MRV du groupe « Performance ».
Justification métier retenue telle quelle : *« MRV étant une obligation
réglementaire mérite un suivi particulier. Ce n'est pas juste de la
performance. Les chiffres obtenus par MRV pourront plutôt être insérés dans le
suivi générique de la performance de la société, mais pour moi il vaut le coup
de lui dédier un module et de libérer un peu de place pour le reste. »*

**Livrables** :
- Groupe de navigation `MRV — réglementaire` dédié, avec trois sections
  (Événements / Émissions port & voyages / Suivi & référentiels). Les 7 entrées
  MRV quittent « Performance », qui retrouve 6 entrées lisibles.
- Sous-titres de section (`.nav-subhead`) plutôt qu'un second niveau repliable :
  `sidebar.js` n'ouvre que le groupe **le plus proche** du lien actif
  (`closest`), donc un groupe imbriqué serait resté fermé.
- `datasets` scindé en deux vues dédiées (`/mrv/datasets/ovdla`,
  `/mrv/datasets/ovdbr`), chacune ne proposant que **ses** exports. La vue
  combinée est conservée : c'est la cible de redirection de la génération.
- Deux vues de restitution : `/mrv/emissions/voyages` (trajet Departure →
  Arrival) et `/mrv/emissions/port` (séjour au port suivant l'arrivée).

**Deux pièges traités, tous deux invisibles en test** :
- `max-height: 720px` sur les groupes de navigation (600 avant) : le groupe MRV
  (10 liens + 3 sous-titres) se faisait **silencieusement tronquer** par
  `overflow: hidden`, rendant les dernières entrées inatteignables.
- Le sous-titre est un `li` de texte nu, pas un `span` dans un `a` : les règles
  de repli à 64 px ne l'auraient pas masqué et il aurait débordé. Trois règles
  CSS ajoutées (desktop replié, tablette, tablette dépliée).

**Constat de fond remonté en cours de route, et tranché.** L'assiette des
émissions du grand livre était la consommation **hors mouillage**
(`emission_ledger`, `do_consumed = conso_hors`) : la consommation d'escale était
calculée et stockée, mais **aucune émission n'en était dérivée**. « Port
Emissions » n'avait donc aucun chiffre de CO₂ à afficher, et la règle d'or
interdit de le calculer ailleurs que dans le grand livre.

Trois options présentées à Yasmin (laisser le trou visible / calculer dans le
grand livre / renommer l'écran en « consommation d'escale »). **Décision :
« port emissions = émissions d'escale »** — l'écran porte bien des émissions.

Implémentation : `emissions_breakdown(conso_escale, factor)` ajouté **dans**
`emission_ledger` (seul endroit légal), matérialisé par la migration
`20260907_0144` (`co2_escale_t`, `co2eq_escale_t`). Même facteur, même primitive
que le trajet. Le résumé étant un cache recalculable, les colonnes se remplissent
au prochain recalcul — aucun backfill dans la migration, qui ne doit pas dépendre
du code de calcul du moment. ⚠️ Les voyages **antérieurs au déploiement** ne
sont donc jamais remplis par ce chemin : ils exigent la reprise à froid
`scripts.backfill_voyage_emission_summaries` (ajoutée le 2026-09-07 après le
3ᵉ tour de revue, et inscrite au runbook §6.1 bis).

**Invariant à respecter désormais** : les deux assiettes sont **disjointes** et
ne doivent jamais être additionnées en silence — l'escale d'un voyage peut
s'étendre sur la fenêtre du voyage suivant. Verrouillé par un test du grand
livre et un test de vue.

**Le cas du mouillage, tranché dans le même mouvement.** Le constat remonté
(« la conso au mouillage ne reçoit non plus aucune émission ») a reçu une
réponse d'une nature différente de celle de l'escale : *« co2 mouillage est
bien en dehors du scope MRV, peut-être il vaut le coup d'ajouter un sélecteur
pour les ajouter avec une note du scope. »*

Ce n'était donc pas un défaut : l'exclusion du mouillage de l'assiette du
trajet est **correcte au regard du règlement**. Mais elle laissait du carburant
réellement brûlé sans émission connue, même pour l'analyse interne.

Livré : `co2_mouillage_t`/`co2eq_mouillage_t` calculés dans le grand livre
(migration `20260907_0145`), et un **sélecteur de périmètre** sur
`/mrv/emissions/voyages` — « Périmètre MRV » par défaut, « + mouillage (hors
MRV) » en opt-in, avec un bandeau d'avertissement au lieu de l'informatif.

**L'invariant qui justifie le sélecteur** : le chiffre MRV est *identique* dans
les deux positions ; seul le total élargi apparaît. Un chiffre réglementaire ne
doit jamais grossir parce qu'on a ajouté un indicateur interne à côté. Vérifié
sur le HTML produit — le total élargi est **absent** du HTML en mode MRV, pas
seulement masqué.

Deux décisions de détail : le sélecteur n'apparaît pas sur l'écran d'escale (au
port vs en mer — le mélange n'a pas de sens, et une demande explicite y est
ignorée) ; le total élargi vaut `None` dès que le CO₂ du trajet manque, alors
qu'un mouillage absent vaut zéro (le navire n'a pas mouillé, ce n'est pas une
donnée manquante).

Il y a donc désormais **trois assiettes** disjointes : trajet et escale dans le
périmètre MRV, mouillage à côté.

✅ **Migrations re-chaînées le 2026-09-07** (cf. l'entrée de ce jour) : la chaîne
est désormais linéaire par construction — `20260904_0142` (révision de fusion sur
`main`) → `20260907_0143` (#197) → `20260907_0144` (escale) → `20260907_0145`
(mouillage).

**Vérification** : les 11 écrans MRV rendus en HTTP authentifié contre l'app
complète (200 partout), absence de tout lien MRV résiduel dans le groupe
« Performance » vérifiée sur le HTML produit, 19 tests d'intégration.

---

## État à date (2026-09-04)

**Fusionné dans `main`** : PR #192 (assistance), #195 (QHSE lots 1-3).

**En attente de révision** : PR #197 (correctif du référentiel de validation —
production déjà réparée à chaud, la PR rend le correctif permanent).

**En attente de révision** : PR #198 (QHSE lot 4 — origine de l'émetteur et
écran qualité).

**En cours** : branche `feature/mrv-module-navigation` (module MRV dédié +
émissions d'escale). Elle part du sommet de la branche #198 — l'entrée de
journal MRV s'ajoute au fichier que cette branche crée.

**Ordre de fusion** : #197 → #198 → MRV. Les deux premières se recouvrent sur
`app/routers/qhse_router.py` et `tests/integration/test_qhse_screens.py`
(résolution triviale : les routes et les assertions coexistent) ; la troisième
demande de re-chaîner sa migration si #197 passe la première.

**Suite** : 3354 tests verts (conteneur Linux, PDF compris), `ruff`/`black`
verts, parité des 5 catalogues i18n.

### Ce qui reste ouvert, et pourquoi

- **Arbitrage à rendre par Julien** : semer le référentiel de validation au boot
  dans **tous** les environnements (et plus seulement en dev) supprimerait la
  classe entière de défaut de l'incident, au prix d'une écriture en base au
  démarrage de la production. Non tranché — cf. ADR-016, décision 4.
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

---

## 2026-09-08 — La pile a été fusionnée dans elle-même, pas dans `main`

**Branche** : `feature/qhse-origine-emetteur-et-qualite` (pointe de la pile,
elle porte désormais #198 **et** #200).

### Situation

Julien a fusionné les trois PRs le 2026-09-08, dans l'ordre annoncé et à
quelques minutes d'intervalle (10:12 → 10:14 → 10:16). GitHub les affiche
toutes les trois `MERGED`. **Une seule est arrivée dans `main`.**

| PR | Branche source | Base **déclarée** | Où le contenu a atterri |
|---|---|---|---|
| #197 | `hotfix/qhse-validation-rules-seed` | `main` | ✅ `main` |
| #198 | `feature/qhse-origine-emetteur-et-qualite` | `hotfix/qhse-validation-rules-seed` | ❌ la branche hotfix |
| #200 | `feature/mrv-module-navigation` | `feature/qhse-origine-emetteur-et-qualite` | ❌ la branche #198 |

C'est la conséquence directe de la **pile** que j'avais montée pour garantir
une chaîne Alembic linéaire : chaque PR déclarait la précédente comme base.
GitHub ne re-cible une PR enfant sur `main` que si la branche de base est
**supprimée** à la fusion. Les branches ayant été conservées, les fusions ont
été honorées telles que déclarées — donc dans la pile.

Aucun signal nulle part : la liste des PRs dit « merged », les branches disent
« merged », et la production n'a rien reçu. Vérification qui manquait, une
commande par PR :

```bash
git merge-base --is-ancestor \
  "$(gh pr view 198 --json mergeCommit --jq .mergeCommit.oid)" origin/main
```

**Rien n'est perdu** : la pointe `feature/qhse-origine-emetteur-et-qualite`
contient l'intégralité de #198 et #200. Il manque **une** fusion vers `main`.

### Ce qui a été fait

1. `main` intégré dans la pointe (vraie fusion, jamais de rebase sur une
   branche publiée) : 5 commits repris (#197, #199/#202 commercial, #201
   navigation). **Zéro conflit**, zéro marqueur dans le dépôt entier.
2. Les **deux constats du 7ᵉ tour de revue** corrigés — ils n'avaient jamais
   été fusionnés, donc ils sont encore devant nous plutôt que derrière :
   - **Un soutage déplacé laissait son tonnage sur l'ancien voyage.**
     `_refresh_affected_emission_summaries` était appelée *après* l'écrasement
     de `bunker.leg_id` : seul le voyage d'accueil était rafraîchi. Comme
     `build_bunker_lookup` sélectionne par `leg_id` et qu'aucun événement futur
     ne rafraîchit un voyage passé, le voyage quitté gardait un
     `conso_escale_t`/`co2_escale_t` **surestimés pour toujours**. L'appelant
     capture désormais `leg_id` et date de livraison **avant** mutation.
     Test de non-régression qui échoue sans le correctif.
   - **Le point de reprise ne couvrait pas la lecture.** Dans le hook
     d'événement comme dans le soutage, le `begin_nested()` n'entourait que
     l'écriture ; un échec de la requête « quels voyages sont touchés »
     empoisonnait la session **malgré** le `try`, et le `commit()` de
     `get_db()` annulait alors l'acte de bord. Exactement le contrat
     « jamais bloquant » que le 6ᵉ tour avait déjà corrigé — une occurrence de
     plus de la même classe, la troisième.
3. Deux détails : le chiffre de l'incident était faux dans les sentinelles
   (35 au lieu de **31** règles reçues en production — il aurait trompé le
   prochain qui lira l'incident), et la clé i18n
   `qhse_issue_closed_before_issued` était devenue inatteignable (le motif a
   été retiré de `QUALITY_ISSUES` au 4ᵉ tour) : retirée des 5 catalogues,
   parité revérifiée.
4. Le piège de la pile documenté dans `CLAUDE.md` (§ Git Workflow).

### Risques

🟡 **Modéré, et il est de présentation, pas de calcul.** Le contenu a déjà été
revu et fusionné une fois par Julien ; ce qui s'y ajoute est l'intégration de
`main` (sans conflit) et trois correctifs cernés. Mais les colonnes d'émission
touchent un **chiffre réglementaire** : la fusion vers `main` reste un acte de
gouvernance, et la revue de Julien s'impose indépendamment du vert de la CI.

### Reste à faire

- **Une fusion de la pointe vers `main`**, sur décision de Yasmin puis revue de
  Julien. Recommandation : une **PR unique** vers `main` — la chaîne Alembic est
  déjà linéaire (tête unique `20260907_0145`), la pile n'a donc plus de raison
  d'être, et c'était précisément son seul bénéfice.
- Supprimer les branches de la pile **après** cette fusion (avec accord), pour
  que le piège ne puisse pas se rejouer.

### 8ᵉ tour de revue — 3 constats, dont un qui cassait le script obligatoire

1. 🔴 **La reprise à froid s'arrêtait au premier voyage en échec.** Un
   `rollback()` **expire toutes les instances** de la session : lire
   `leg.leg_code` juste après déclenchait un rechargement synchrone, interdit
   sous session async (`MissingGreenlet`) — levée **dans le `except`**, donc
   non rattrapée. Le script promettait « rapport, pas d'arrêt » et faisait
   l'inverse ; tous les voyages suivants gardaient leurs colonnes à `NULL`.
   Or ce script est **obligatoire** après le déploiement des migrations `0144`
   et `0145` (colonnes créées à `NULL`, sans backfill). L'identité des voyages
   est désormais figée **avant** la boucle et le voyage rechargé à chaque tour :
   plus aucun accès ORM après un rollback.
2. **Le plafond de 40 lignes tronquait en silence.** Avec les archives TOWT,
   l'écran se lisait comme l'ensemble complet. Il annonce maintenant
   « 40 voyages affichés sur 137 » — même règle que « 10 sur 23 » sur la carte
   de navigation, et que l'écran qualité QHSE. Les critères de sélection sont
   **factorisés** (`_selection`) entre le listing et le comptage : un total
   calculé à part aurait fini par annoncer « 40 sur 12 ».
3. **Deux lectures d'un même état.** `co2_escale_t` affichait « non calculé »
   quand `co2eq_escale_t`, **nul en même temps** (même appel à
   `emissions_breakdown`), affichait un tiret — qui se lit comme un zéro. Même
   asymétrie sur l'assiette du trajet. Les quatre colonnes disent désormais la
   même chose du même état.

**Vérifications** : 2 tests neufs sur le plafond et la cohérence du comptage,
la chaîne de traduction rendue **pour de vrai** dans les 5 langues (le
`.format()` d'une chaîne traduite n'est pas exercé par `resp.context` — une
accolade fautive ne se serait vue qu'en production).

### Où s'arrête cette boucle de revue

Huit tours : 7 → 6 → 3 → 4 → 3 → 3 → 2 → 3 constats. Le compte ne descend pas,
mais **la nature des constats a changé** : plus aucun ne touche le calcul des
émissions (le grand livre, les trois assiettes, la règle d'or et les seuils ont
tenu les huit tours). Ce qui remonte désormais est périphérique — un script de
reprise, un plafond d'affichage, une asymétrie de rendu.

**Recommandation** : arrêter la boucle ici et passer la main à la revue de
Julien. Chaque tour supplémentaire coûte un cycle complet et produit surtout de
la retouche de surface ; le risque résiduel est mieux traité par un œil humain
sur la présentation d'un chiffre réglementaire que par un 9ᵉ tour.
