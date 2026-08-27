# ADR-012 — Cloisonner « Vente à bord » et « Caisse de bord » par navire

- **Date** : 2026-08-27
- **Statut** : **accepté** — arbitré le 2026-08-27
- **Décideur** : Julien Gondé
- **Rédaction** : audit multi-agents du module « Vente à bord » + « Caisse de bord »
  (cf. `docs/audit/2026-08-27-audit-vente-a-bord-caisse.md`, constat V-09)
- **Décision** : le **personnel maritime est borné à son navire d'affectation**.
  Les seules consultations ouvertes sur la flotte entière sont le **planning de
  navigation** et la **position des navires**.

---

## Contexte

`User.assigned_vessel_id` existe et sert de critère de cloisonnement dans
plusieurs modules du bord : `onboard_router.py:343-344,712-715,1099`,
`captain_router.py:776-778`, `services/cutoff_reminders.py:131`.

Dans `onboard_sales_router.py`, ce champ ne sert **qu'à une redirection de
confort** (`:89-92`) : si l'utilisateur est rattaché à un navire, le hub le
renvoie directement sur son espace. **Aucune route ne le contrôle.**
`cashbox_router.py` ne le lit jamais.

## Constat

Portée réelle de l'accès aujourd'hui, telle qu'établie par lecture de
`app/permissions.py` :

| Niveau `captain` | Rôles concernés | Ce qu'ils peuvent faire, sur **tous** les navires |
|---|---|---|
| `C` (lecture) | `armement`, `data_analyst`, `commercial`, `marins`* | Lire les ventes, l'inventaire, le **registre douanier** et le **solde de caisse** de toute la flotte. `/cashbox` liste explicitement tous les navires. |
| `CM` (écriture) | `operation`, `technique`, `marins`* | Créer des ventes, encaisser, écrire au registre, mouvementer la caisse de **n'importe quel** navire. |
| `CMS` | `manager_maritime`, `administrateur` | idem + suppression |

\* `marins` passe de `C` à `CM` par l'override posé par la migration 0125.

Conséquence concrète : le commandant du navire 1 peut **clôturer et verrouiller
la caisse du navire 2**, opération irréversible qui gèle des mouvements — sans
que rien, dans l'écran du navire 2, ne signale qui l'a fait au-delà du journal
d'audit.

Ce défaut se combine à une seconde faiblesse (traitée séparément, lot 7) : la
route `GET /captain/ventes/vente/{ref}` déclenche une **écriture financière**
(réconciliation Stripe) sous une simple permission de **consultation**, en
attribuant le mouvement de caisse au lecteur de passage. Un analyste ouvrant une
vente par curiosité devient, dans l'audit, le caissier d'un navire dont il n'a
pas la charge.

---

## Décision

La question n'était pas « faut-il cloisonner ? » — l'écriture croisée entre
navires n'est défendable par personne — mais **jusqu'où** et **pour qui**.

> **Le personnel maritime est borné à son navire d'affectation.** Les seules
> autorisations de visualisation portant sur la flotte entière sont le
> **planning de navigation** et la **position des navires**.

Cette règle est **plus stricte que la recommandation initiale** (qui n'aurait
borné que la caisse, en laissant ventes et inventaire visibles sur la flotte).
Elle est aussi plus simple à tenir : une règle unique, deux exceptions nommées,
là où la posture « ouverte sauf la caisse » demandait de trancher module par
module ce qui est sensible — un test qu'une évolution ultérieure casse sans
qu'on s'en aperçoive.

### Ce que la décision implique, par question posée

| Question | Réponse |
|---|---|
| **Écriture** — un marin écrit-il sur un autre navire ? | **Non**, jamais. |
| **Lecture** — un marin lit-il la caisse ou les ventes d'un autre navire ? | **Non.** Seuls le planning de navigation et les positions restent ouverts sur la flotte. |
| **Rôles siège** | Non bornés : ils doivent pouvoir corriger et administrer à distance. |

### Portée de l'application immédiate

La décision énonce un **principe général**, mais n'est appliquée pour l'instant
qu'aux deux modules audités — **Vente à bord** et **Caisse de bord**. L'étendre
aux autres modules du bord (`escale`, `cargo`, `crew`, `mrv`, `qhse`,
`tickets`…) demande un passage dédié, module par module : chacun a ses écrans de
liste, ses exports et ses cas de bord, et un cloisonnement posé à l'aveugle
produirait exactement le blocage terrain que cette remédiation corrige.

**Ce chantier reste donc ouvert** et doit être planifié explicitement — il n'est
pas soldé par le présent ADR.

### Point resté ouvert : qui clôture la caisse ?

La recommandation initiale proposait de passer `close_period` sous `finance:M`,
au nom de la séparation des tâches (le même compte encaisse, compte, clôture et
verrouille). Cette question **n'a pas été tranchée séparément**, et la décision
prise sur le contrôle de caisse va dans le sens contraire : le commandant
sortant fige lui-même sa comptabilité à la relève (cf. §« Gel à la relève » du
rapport d'audit). La clôture reste donc sous `captain:M`.

La réserve de contrôle interne subsiste et reste **documentée** : elle sera à
reprendre si un écart significatif survient, ou si un commissaire aux comptes
la soulève.

---

## Éléments d'instruction (conservés pour mémoire)

### Question 1 — L'écriture

Proposition : **un utilisateur rattaché à un navire n'écrit que sur ce navire.**
Les rôles siège (`administrateur`, `armement`) restent non bornés, parce qu'ils
doivent pouvoir corriger et administrer à distance.

*Point à trancher* : `operation` et `technique` — aujourd'hui `captain:CM` —
doivent-ils écrire sur toute la flotte, ou seulement sur le navire dont ils ont
la charge ? Les agents d'escale à terre suivent-ils un navire, ou la flotte ?

### Question 2 — La lecture

Trois postures possibles :

| Posture | Description | Coût |
|---|---|---|
| **L1 — lecture ouverte** | Statu quo : tout `captain:C` lit tous les navires | Nul. Mais le solde de caisse et le registre douanier restent lisibles par 8 rôles |
| **L2 — lecture bornée au navire** | Symétrique de l'écriture | Les Opérations à terre perdent la vue flotte, qu'elles utilisent peut-être |
| **L3 — lecture ouverte, sauf caisse** *(recommandée)* | Ventes et inventaire visibles flotte ; **solde et mouvements de caisse** bornés au navire + rôles siège | Faible. Cible ce qui est réellement sensible |

*Point à trancher* : les Opérations à terre consultent-elles aujourd'hui la
caisse d'un navire dont elles n'ont pas la charge, et pour quel usage ?

### Question 3 — La clôture

La clôture de caisse est aujourd'hui sous `captain:M` — **la même cellule** que
« créer un encaissement ». Le même compte peut donc encaisser, compter, clôturer,
verrouiller et produire l'export comptable : aucune séparation des tâches.

Proposition : passer `close_period` sous `finance:M` (ou `captain:S`), avec
notification au siège à la clôture et refus au-delà d'un seuil d'écart sans
double validation.

*Point à trancher* : qui, nommément, clôture la caisse d'un navire — le
commandant à bord, ou le siège ? Cette réponse détermine aussi qui est
**responsable** d'un écart, ce qui n'a aujourd'hui **aucun titulaire**.

---

## Mise en œuvre retenue

- **Écriture et lecture** : bornées à `assigned_vessel_id` pour le personnel
  maritime, rôles siège exemptés.
- **Clôture** : reste `captain:M` (cf. §« Point resté ouvert » ci-dessus).
- **Détail technique** : une dépendance FastAPI factorisée
  `require_vessel_access(vessel_id, user)` appliquée aux deux routeurs, et
  `_get_sale_or_404` portant le contrôle (il ne filtre aujourd'hui que sur la
  référence, `onboard_sales_router.py:71-77`).
- **Effort** : ~1 j, faible risque technique.

## Conséquences

- Un utilisateur **sans** `assigned_vessel_id` reste non borné : c'est le
  comportement des rôles siège, et cela évite de bloquer les comptes existants
  au déploiement. **Corollaire à assumer** : le cloisonnement ne vaut que si les
  comptes du bord sont effectivement rattachés à un navire dans `/admin/users`.
  C'est une donnée à fiabiliser **avant** de déployer la règle, sinon elle ne
  protège rien.
- Les tests devront couvrir le 403 inter-navires — aujourd'hui **aucun test du
  module ne passe par la couche HTTP**, donc aucun ne vérifie une permission.
- Documentation à mettre à jour : `CLAUDE.md` (matrice), notice commandant,
  guide utilisateur.

## Risque résiduel assumé

Un compte de personnel maritime **sans navire d'affectation** perd l'accès au
module. C'est le comportement voulu — un cloisonnement qui laisse passer les
comptes mal renseignés ne cloisonne rien — mais il déplace la fiabilité de la
règle vers une donnée d'administration.

Deux mesures l'encadrent :

1. le refus est **explicite et actionnable** : il nomme la cause (compte non
   rattaché) et la correction (`/admin/users`), au lieu du « 403 » muet qui a
   fait échouer le premier test à bord ;
2. le rattachement figure à la **checklist de mise en service** du module
   (`docs/audit/2026-08-27-audit-vente-a-bord-caisse.md`, phase 2).

Sans ces deux garde-fous, cette décision recréerait exactement le blocage
terrain que la remédiation corrige par ailleurs.
