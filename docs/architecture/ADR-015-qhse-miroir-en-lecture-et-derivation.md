# ADR-015 — QHSE : miroir en lecture, et dérivation plutôt que colonne

- **Date** : 2026-09-04
- **Statut** : **accepté** — décisions 1 à 3 tranchées par Yasmin Ponce le 2026-09-04 ; décision 4 constatée en incident de production le même jour
- **Décideur** : Yasmin Ponce (D10 : arbitrage antérieur de Julien Gondé)
- **Rédaction** : suites de l'analyse des exports FMS réels (2026-09-04) et de l'incident `DFT-20260904-001`

Le module QHSE a reçu ses premières données réelles les 2026-09-03/04 : deux
formats d'export du FMS, 188 lignes sur la flotte. L'arbitrage **D10**
(`docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md`) avait déjà tranché la
répartition des rôles — *le FMS est la source de vérité QHSE, il reste l'outil
de saisie ; MyTOWT analyse, aide à la décision et pilote*. Ces quatre décisions
fixent ce que cela implique concrètement, une fois confronté à la donnée.

---

## Décision 1 — Aucune colonne pour une donnée que le FMS possède

**Contexte.** L'analyse du second export a fait apparaître trois colonnes sans
équivalent dans le modèle (`Checklist`, `Limit Date`, `Closed by`), et le
tableau de bord réclamait une classification opérationnel/audit absente du
schéma. La tentation naturelle était d'ajouter des colonnes.

**Décision.** MyTOWT n'ajoute **aucune colonne** pour capter une donnée dont le
FMS restera l'unique outil de saisie. Les colonnes non aliasées le restent, et
un indicateur qui manque se **dérive** de ce que l'export porte déjà, ou ne
s'affiche pas.

**Ce que cela implique.**
- Un besoin d'analyse ne justifie jamais une migration de schéma : il se traduit
  en calcul de lecture, ou en constat documenté si la donnée n'existe nulle part.
- Le corollaire est une contrainte réelle : certains indicateurs du cahier des
  charges resteront hors de portée tant que le FMS ne les portera pas. C'est
  assumé et doit être **dit à l'écran**, pas comblé par une saisie parallèle.

*Alternative écartée* : ouvrir une saisie complémentaire dans MyTOWT pour les
champs manquants. Rejetée — deux sources d'écriture sur le même registre
QHSE produisent des divergences qu'aucun rapprochement ne rattrape, et D10
attribue l'écriture au FMS.

---

## Décision 2 — On encode le fait, jamais son interprétation

**Contexte.** Le cahier des charges (§4.1) demande une répartition
« opérationnel / audit », chiffrée à ~33 % d'audit sur l'échantillon analysé.
`report_source` ne peut pas la porter : l'ingestion n'y écrit que `operational`
ou `suspected_test` — cette dernière valeur n'appartenant même pas à
l'énumération déclarée `QHSE_REPORT_SOURCES`, le champ étant détourné pour la
détection de motif de test (RQ02).

La donnée réelle porte en revanche l'**émetteur**, et lui distingue nettement
trois origines (relevé sur 188 lignes) : autorités externes (Centre de Sécurité
des Navires, Transport Canada, USCG — 24), siège (`TOWT COMPANY` — 46), bord
(commandant, chef mécanicien, second — 118).

Classer le siège en « audit interne » aurait produit ~37 % d'audit, très proche
du chiffre attendu. C'était pourtant une **lecture**, pas un fait : un
signalement émis à terre n'est pas nécessairement un audit.

**Décision.** L'indicateur affiche **bord / siège / autorité externe** —
vérifiable ligne à ligne — et non « opérationnel / audit ». Regrouper les
origines autrement reste un choix d'affichage, sans reprise de données.

**Ce que cela implique.**
- Un indicateur ne doit pas s'aligner sur un chiffre attendu au prix d'une
  hypothèse invérifiable. Le rapprochement des ~33 % était une coïncidence
  séduisante, pas une validation.
- Si le métier tranche plus tard que le siège émet des audits internes, le
  regroupement se fait à l'affichage, en une ligne.

---

## Décision 3 — `indetermine` est une valeur de premier rang

**Contexte.** L'heuristique de classification laisse 3 lignes réelles
inattribuables (émetteur `TOWT` seul). Les ranger dans « bord » par défaut
aurait donné un graphe d'apparence exhaustive.

**Décision.** `indetermine` est l'une des quatre origines, affichée même à
zéro, et sa part est signalée à l'écran comme limite de fiabilité du graphe.

**Ce que cela implique.** Même patron que `schengen_status = 'indetermine'`
(équipage) et que les dénominateurs explicites de C1/C2 : l'absence
d'information se montre, elle ne se range pas dans la catégorie majoritaire.

**Corollaire — distinguer l'anomalie de la limite de format.** L'écran
`/qhse/qualite` listait initialement 90 signalements sur 90, parce que
« responsable non identifié » valait 90. Ce n'était pas 90 oublis : l'export
« historique par navire » ne porte **aucune** colonne de responsable. Un
constat structurel est donc compté et expliqué à part, jamais mêlé aux
anomalies corrigeables ligne par ligne — la liste est passée à 36/90, et elle
est devenue un plan de travail.

---

## Décision 4 — Une migration est un instantané, pas un appel au code vivant

**Contexte.** L'incident `DFT-20260904-001` : tout import QHSE échouait en 500
en production. `quality_check_results.rule_id` porte une FK vers
`validation_rules.rule_id`, et la production ne contenait pas les règles QHSE.
Cause : la migration `20260709_0097` **importe la constante `RULE_SEED` du code
applicatif** au moment de son exécution. Les règles ajoutées au catalogue après
son passage en production n'y sont jamais arrivées.

Le défaut était **structurellement invisible aux tests** : le seed au boot
peuple tout en développement, et une base reconstruite depuis la chaîne
complète de migrations est correcte elle aussi, puisque `0097` relit le
`RULE_SEED` *courant*. Seule une base migrée avant les ajouts était incomplète
— il n'en existait qu'une, la production.

**Décision.** Une migration écrit ses valeurs **en dur**. Son effet ne doit
jamais dépendre de la date à laquelle elle s'exécute. Toute nouvelle entrée
d'un référentiel semé en base exige une migration additive idempotente.

**Ce que cela implique.**
- Trois sentinelles (`tests/regression/test_validation_rules_seeded.py`) :
  l'instantané figé doit rester aligné sur le catalogue, aucune migration ne
  doit importer une constante de seed (`0097` explicitement grand-pérée —
  réécrire une migration déjà passée en production serait pire), et l'empreinte
  du catalogue est épinglée pour qu'un ajout sans rattrapage échoue.
- Un écran de réparation doit rester **atteignable** : `seeded = bool(rules)`
  masquait le bouton d'init dès qu'une seule règle existait, rendant un
  référentiel *partiel* irréparable depuis l'interface. Un état dégradé doit se
  voir et se corriger sans déploiement.

*Alternative laissée en arbitrage* : appeler `seed_reference_data` au boot dans
tous les environnements (il est idempotent et purement additif) supprimerait la
classe entière de défaut, au prix d'une écriture en base au démarrage de la
production. Non tranché — relève de Julien.
