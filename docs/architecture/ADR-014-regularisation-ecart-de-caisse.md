# ADR-014 — La régularisation d'un écart de caisse est un geste du siège

- **Date** : 2026-08-30
- **Statut** : **accepté** — arbitré le 2026-08-30
- **Décideur** : Julien Gondé
- **Origine** : premier retour d'usage réel du contrôle de caisse (Cdt de
  l'ANEMOS, courriel du 2026-08-29), remarque n° 4 : *« J'ai actuellement une
  caisse théorique à 1 676,89 € et une caisse réelle à 1 988,35. Comment faire
  pour corriger ? Autre, régularisation caisse ? »*

---

## Contexte

Le contrôle de caisse (`cash_counts`, livré le 2026-08-27) fait exactement ce
qu'on lui demande : le commandant déclare sa caisse coupure par coupure, le
total est recalculé côté serveur, et l'écart avec le solde théorique est **figé**
avec ce solde. Un mouvement saisi après coup ne réécrit jamais un contrôle rendu.

Ce qui n'était pas encadré, c'est **la suite donnée à l'écart**.

Le premier usage réel l'a montré sans détour : un excédent de 311,46 € constaté,
et la question « comment corriger ? ». La seule réponse que l'outil offrait était
un mouvement de caisse ordinaire, rangé dans « Autre encaissement ».

Conséquence, et c'est le vrai sujet : **le commandant qui répond de la caisse
pouvait solder lui-même l'écart qu'on venait de constater**, par une écriture
que rien ne distinguait d'une autre au journal. Un manquant disparaissait en une
saisie. Le contrôle de caisse devenait alors une formalité : il constate un
écart que le contrôlé peut effacer.

C'est exactement le couple que l'**ADR-013** avait fermé pour le remboursement —
un rôle qui peut encaisser *et* défaire un encaissement n'offre aucun contrôle.
Le même raisonnement s'applique ici, un cran plus loin : un rôle qui peut
**détenir** la caisse, **déclarer** son état *et* **effacer** l'écart entre les
deux ne se contrôle pas du tout.

## Décision

**La régularisation d'un écart de caisse est un geste du siège. Le bord ne
régularise pas.**

Le bord détient, compte, déclare et **signale**. Le siège décide de la suite.

## Ce que cela implique

- **Deux catégories dédiées** — `regularisation_excedent` et
  `regularisation_manquant` — qui nomment l'écriture pour ce qu'elle est. Elles
  sont **délibérément absentes** de `INCOME_CATEGORIES` et `EXPENSE_CATEGORIES` :
  ces deux tuples alimentent à la fois la liste déroulante du bord *et* la
  validation de la route générique de mouvement (`categories_for`). Les en tenir
  à l'écart ferme la voie du bord en un seul point, sans garde séparée à oublier.
  Le `CHECK` en base porte, lui, sur l'union — la catégorie doit rester
  écrivable par la route du siège.
- **La route de régularisation relève de `finance:M`**, jamais de `ventes:M` :
  cette dernière est celle qui tient la caisse et qui déclare le comptage. Comme
  pour le remboursement, qui détient `finance:M` reste réglable dans
  `/admin/permissions`.
- **Pas de cloisonnement par navire** sur ce geste : le siège régularise pour
  toute la flotte, comme il rembourse pour toute la flotte (ADR-012 borne le
  bord, pas le siège).
- **L'écriture est adossée à un écart déclaré** (`settles_cash_count_id`) :
  pas de régularisation flottante. La contrepartie est un contrôle nommé, daté,
  et signé d'un commandant. Cette colonne est **distincte** de `cash_count_id`,
  qui dit « gelé **par** ce contrôle » : les confondre ferait passer une
  régularisation pour un mouvement verrouillé.
- **Elle ne peut ni dépasser l'écart ni en inverser le sens**, et le restant se
  déduit des régularisations déjà passées sur le même contrôle. Sans cette
  borne, la catégorie ne serait qu'un « Autre encaissement » relabellé : aucun
  contrôle gagné. Le sens suit celui de l'écart constaté, jamais celui d'une
  saisie — un excédent se régularise par une entrée, un manquant par une sortie.
- **Elle est datée du jour de la décision**, jamais antidatée dans la période
  contrôlée : une écriture qui remonterait avant le comptage réécrirait par la
  bande un contrôle déjà rendu.
- **Le motif est obligatoire.** Une régularisation sans cause écrite est
  précisément ce que cette décision cherche à empêcher.
- **Le grand livre reste append-only.** Rectifier une régularisation se fait par
  contre-écriture, comme tout mouvement (`reverse_movement`).

## Ce que la décision ne dit pas

**Régulariser n'est pas la première réponse à un écart, c'est la dernière.** Un
excédent signifie que de l'argent est entré sans être saisi : vente à bord réglée
en espèces non enregistrée, dépôt du siège, avance équipage rendue. La marche à
suivre est de **retrouver et saisir les écritures manquantes**, chacune à sa
vraie date et dans sa vraie catégorie ; le solde théorique rejoint alors la
caisse réelle, écriture par écriture, et l'écart s'explique au lieu d'être
soldé. Seul le reliquat **vraiment inexplicable** relève de la régularisation.
C'est écrit dans la notice commandant (§7 bis) parce que l'outil, seul, ne peut
pas l'imposer.

## Alternatives écartées

- **Laisser le bord régulariser dans une fenêtre courte** (le jour du comptage).
  Rejetée pour la même raison qu'en ADR-013 : elle rouvre exactement le couple
  que la décision ferme, pour un gain de confort qu'un appel au siège couvre.
- **Une catégorie unique « Régularisation »**, sens libre. Rejetée : le sens
  doit être **dérivé** de l'écart constaté. Le laisser au choix de l'opérateur
  autorise une régularisation qui aggrave l'écart au lieu de le solder.
- **Montant libre.** Rejetée : sans borne adossée à l'écart déclaré, la
  fonctionnalité n'aurait apporté qu'un libellé.
- **Recalculer l'écart après régularisation.** Rejetée : l'écart d'un contrôle
  est **figé**, c'est sa raison d'être. Le restant se calcule à côté, à partir
  des régularisations rattachées — le contrôle rendu, lui, ne bouge pas.

## Conséquences

- Migration `20260830_0136` : colonne `settles_cash_count_id` + élargissement de
  `ck_cashbox_mov_category`. Le retour arrière **refuse** de s'appliquer s'il
  reste des régularisations en base, plutôt que d'échouer à mi-parcours ou de
  supprimer des écritures d'un registre append-only.
- L'écran d'un contrôle de caisse affiche désormais la suite donnée à l'écart :
  régularisations passées, restant à régulariser, et — pour le siège seulement —
  le formulaire. Le bord y lit la consigne : signaler, pas régulariser.
- Un écart qui revient d'un contrôle à l'autre n'est pas un problème de saisie
  mais de procédure. La décision rend ce motif **visible** au siège, ce qui était
  impossible tant que le bord soldait lui-même.

## Reste ouvert

`cash_count.review_count()` — la suite formelle donnée par le siège à un contrôle
(*validé* / *contesté*) — existe et est testée depuis le 2026-08-27 mais **n'est
exposée par aucune route**. Une déclaration partie par erreur reste « DÉCLARÉE »
indéfiniment. Même sujet, mêmes acteurs que cet ADR ; non tranché ici.
