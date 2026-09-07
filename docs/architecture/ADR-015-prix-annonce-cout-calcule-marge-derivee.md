# ADR-015 — Prix annoncé, coût calculé, marge dérivée

- **Statut** : accepté
- **Date** : 2026-09-04
- **Portée** : module commercial — grilles tarifaires, offres, commandes, clients
- **Décideurs** : Julien (direction), Yasmin (développement)
- **Remplace partiellement** : ADR-010 (refonte du module commercial, 2026-08-26)

---

## Contexte

Le module commercial calculait le tarif d'une route à partir du coût
d'exploitation :

```
base_rate = OPEX jour × jours de mer / capacité navire
```

Le commercial pouvait « surcharger » cette valeur à la main (`is_manual`), ce qui
la gelait contre les recalculs. Une seule colonne, `RateGridLine.base_rate`,
portait donc **deux notions incompatibles** :

- un **coût**, quand `is_manual` était faux ;
- un **prix**, quand il était vrai.

Trois conséquences, toutes constatées à l'usage :

1. **La marge n'était jamais lisible.** Sur une route automatique elle valait
   zéro par construction — l'application vendait à prix coûtant sans le dire.
   Sur une route manuelle, le coût avait disparu de la base : il n'existait plus
   aucun terme auquel comparer le prix.
2. **Le logiciel décidait du prix.** L'écran présentait le résultat de la formule
   comme le tarif, et la saisie du commercial comme une exception (« surcharge
   manuelle »). C'est l'inverse du métier : un armateur annonce un prix de marché
   et vérifie ensuite qu'il couvre son coût.
3. **L'unité était implicite.** Tout était en €/palette, alors que le café vert
   et le cacao — les deux verticales B2B2C de la vitrine — se négocient
   couramment **à la tonne**.

Un quatrième point s'y ajoutait : la grille portait un **navire de référence**
dont dépendaient l'OPEX et la capacité. La flotte étant composée de sisterships
TSC 80 partageant le même OPEX jour, ce choix laissait croire à une politique
tarifaire par coque qui n'existe pas, et donnait deux prix différents pour la
même route selon le navire coché.

## Options considérées

**A — Ne rien changer, documenter la lecture.** Écrire dans l'écran que
`base_rate` est un coût quand la source est « OPEX » et un prix quand elle est
« manuel ». Rejetée : une colonne qui change de sens selon la valeur d'une autre
colonne se relit mal, et aucune documentation ne rend la marge calculable pour
les routes manuelles — l'information n'est pas là.

**B — Ajouter une colonne « marge cible » et continuer à calculer le prix.**
`prix = coût × (1 + marge)`. Rejetée : c'est toujours le logiciel qui fixe le
prix, à un paramètre près. Le besoin exprimé est que le commercial annonce un
prix de marché, qui ne dérive pas mécaniquement du coût.

**C — Séparer les deux notions, dériver la marge.** Retenue.

## Décision

### 1. Deux colonnes, une dérivée

- `RateGridLine.base_rate` — le **prix annoncé**, dans l'unité de la route.
- `RateGridLine.cost_rate` — le **coût de revient** calculé. Jamais saisi.
- `margin_eur` / `margin_pct` / `is_below_cost` — **propriétés dérivées**, jamais
  stockées. Le taux de marge est calculé **sur le prix de vente**
  (`marge / prix`) : c'est la lecture commerciale (« ce que je garde sur 100 €
  vendus »), et elle reste finie quand le coût tend vers zéro.

`is_manual` change de sens et devient explicite dans l'écran : **« prix
confirmé »** contre **« prix proposé »**. Le logiciel propose
(`suggested_price` : coût rapporté à une marge cible de 25 %), l'opérateur
confirme — un bouton **Confirmer** existe pour valider une proposition sans la
modifier. Un recalcul de coût ne déplace **jamais** un prix confirmé.

### 2. `cost_rate` est nullable, et c'est le point important

`NULL` ne veut pas dire « zéro » : il veut dire **« coût non calculable »** —
concrètement, une route tarifée à la tonne pour laquelle aucun port en lourd
n'est renseigné au référentiel flotte. L'écran affiche « — » et renvoie vers
Admin → Flotte ; la marge n'est pas affichée.

C'est le même patron que la distance théorique d'un leg dont un port n'a pas de
coordonnées (`Leg.distance_nm = None`, marqué `*` à l'affichage, corrigeable
dans Admin → Ports). Substituer un tonnage par défaut ferait passer une marge
inventée pour un fait — exactement le « vert par défaut » que l'audit du
2026-07-28 identifie comme le motif de défaut dominant de l'application.

### 3. L'unité de vente est portée par la route

`RateGridLine.rate_unit` ∈ {`palette`, `tonne`}. Par la **route** et non par la
grille : un même client peut acheter au poids sur une ligne (cacao en vrac) et à
l'emplacement sur une autre (marchandise palettisée).

Un devis sur une route au poids **refuse de coter** sans tonnage déclaré
(`QuotingError`) au lieu de dériver un poids d'un nombre de palettes. Le palier
de volume et la réservation de cale restent comptés en **emplacements** : c'est
bien de la cale qui est occupée, quelle que soit l'unité de facturation.

Conséquence assumée : `GridQuote` porte désormais son `rate_unit`, et tout écran
qui rend un taux de base doit rendre l'unité avec. Un montant sans unité laisse
un €/tonne se lire comme un €/palette.

### 4. Une grille ne porte plus de navire de référence

L'OPEX jour est celui de la flotte. `RateGrid.vessel_id` reste en base pour les
grilles antérieures (aucune donnée détruite) mais n'est plus exposé ; toute
édition d'en-tête le remet à `NULL` — explicitement, parce que la valeur
n'était plus effaçable depuis l'écran une fois le sélecteur retiré.

Pour le coût **à la tonne**, la capacité de référence est le port en lourd
moyen des navires actifs qui en déclarent un (`_fleet_deadweight_t`). La flotte
étant composée de sisterships, la moyenne est exacte ; sans aucun port en lourd
renseigné, elle vaut `None` — cf. point 2.

## Conséquences

**Positives.**

- La marge est lisible route par route, avec le coût en regard, et une vente à
  perte est signalée (`is_below_cost`).
- Le prix redevient une décision commerciale, tracée : « proposé » tant qu'il ne
  l'est pas, « confirmé » ensuite, avec l'auteur au journal d'activité.
- Le recalcul global porte désormais sur **toutes** les routes (il sautait les
  routes manuelles) : leur coût était figé, donc leur marge fausse dès que
  l'OPEX bougeait.
- Le fret au poids devient exprimable sans détour ni équivalence inventée.

**Négatives, assumées.**

- Les routes **manuelles** existantes n'ont pas de coût historisé : la migration
  laisse leur `cost_rate` à `NULL` et leur marge s'affiche « — » jusqu'au
  prochain recalcul depuis l'écran (qui pose le coût **sans toucher au prix**).
  Reprendre un coût qui n'a jamais été enregistré aurait été une invention.
- Les routes **automatiques** existantes affichent une marge de 0 %. Ce n'est
  pas un défaut d'affichage : elles se vendaient réellement à prix coûtant. Le
  chiffre est le premier constat utile de ce lot.
- L'édition d'une grille héritée déplace son OPEX du navire vers la flotte. Si
  un navire portait un OPEX propre, son prix proposé change au recalcul suivant.
  Les prix **confirmés** ne bougent pas.

## Points ouverts

- **Marge cible paramétrable.** Elle est aujourd'hui une constante
  (`DEFAULT_TARGET_MARGIN_PCT = 25 %`) servant uniquement à *proposer*. La poser
  par client ou par route relève d'une décision de direction, pas d'un réglage
  technique.
- **Port en lourd au référentiel flotte.** Tant qu'il n'est pas renseigné
  (Admin → Flotte), aucune route à la tonne n'a de coût, donc de marge. C'est un
  prérequis de donnée, pas un développement.
- **Consolidation du coût réel.** `cost_rate` reste un coût *théorique* (OPEX ×
  durée / capacité). Le rapprochement avec l'OPEX réellement constaté par voyage
  (`LegFinance`) n'est pas fait, et ne doit pas l'être en écrivant d'office dans
  une saisie d'opérateur — même arbitrage que pour le CA de la vente à bord
  (ADR-013).
