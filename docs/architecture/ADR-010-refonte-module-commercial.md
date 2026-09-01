# ADR-010 — Refonte du module commercial (grilles, estimations, offres, booking note)

- **Date** : 2026-08-26
- **Statut** : accepté, implémenté sur `claude/commercial-module-multi-agent-fe0jhc`
- **Décideur** : Julien Gondé (arbitrages Q1–Q6 du 2026-08-26)
- **Rédaction** : session de refonte multi-agents (audit comportement, cohérence
  inter-modules, sécurité, UX, veille marché)

---

## Contexte

Le module commercial portait déjà l'essentiel d'un moteur tarifaire — grilles
par client, calcul du taux de base sur l'OPEX, paliers de volume, options — mais
éclaté sous des noms proches (`Quote`, `RateOffer`, « devis », « offre ») et
troué à plusieurs endroits structurants :

- aucun **commercial attitré** en base : « notifier le commercial du client » ne
  pouvait viser que le rôle entier ;
- aucune **condition de règlement** ;
- des **statuts d'offre inatteignables** (`declined`, `expired` n'étaient posés
  par aucune route) : une offre restait « envoyée » indéfiniment, y compris
  après le départ du navire ;
- aucune **réservation de volume** par l'offre dans le chargement prévisionnel ;
- aucune **historisation** exploitable des changements de prix ;
- une **fuite de prix inter-clients exploitable** (cf. §Décision 1).

Un audit conduit par cinq agents spécialisés a établi ce constat avant toute
écriture de code. La refonte est donc une mise en cohérence doublée d'une
complétion, pas une reconstruction.

---

## Décision 1 — Le tarif négocié ne sort jamais vers une identité non établie

**C'est la décision structurante ; les autres en découlent.**

L'inscription client forçait `is_verified=True` (aucun flux de vérification
n'existe) puis rattachait le compte au client commercial partageant le **domaine
e-mail**. Un concurrent s'inscrivant en `x@client.fr` lisait la grille négociée
de ce client : taux, remises de volume, prix des options, référence de grille.

Le rattachement d'un compte plateforme à un client commercial — qui **est** la
clé d'accès aux prix négociés — devient un **acte explicite d'un opérateur**
`commercial:M`, audité. Le rapprochement automatique par e-mail est supprimé ;
il ne subsiste qu'une **suggestion** (match e-mail exact) affichée à l'opérateur,
qui n'écrit rien.

Conséquence directe sur le parcours public : le formulaire de la vitrine ne
chiffre plus. Il déposait un prix devant qui n'avait pas été identifié — et sur
la grille négociée du client si le visiteur se trouvait connecté. Il crée
désormais une **fiche prospect** et notifie le commercial ; le tarif viendra
d'une offre qualifiée.

*Alternative écartée* : garder le chiffrage public sur la grille standard
seulement. Rejetée — la grille standard reste une information commerciale, et la
distinction « standard / négociée » aurait reposé sur un test facile à casser
lors d'une évolution ultérieure. Ne rien afficher est une règle qu'on ne casse
pas par inadvertance.

---

## Décision 2 — Grilles tarifaires : en-tête multi-routes conservé (Q2)

La référence codifiée `P-[MMAA]-[MMAA]-[XX]-[YY]` encode **une** paire POL/POD,
alors qu'une grille en couvre plusieurs. Deux lectures possibles : passer à
« une grille = une route », ou garder l'en-tête multi-routes et porter la
référence sur la ligne-route.

**Retenu : l'en-tête multi-routes**, la référence étant générée **par
ligne-route**. Le modèle existant est préservé (aucune migration de découpage
des grilles en production), et la codification garde son sens.

Le code pays vient du référentiel `ports` en **ISO alpha-2** (Q1), pas des deux
premières lettres du LOCODE : s'y fier ferait porter la référence par une
convention de nommage plutôt que par la donnée, et un port mal saisi passerait
inaperçu. Une validité ouverte porte `----`, un pays inconnu `??` — visible,
plutôt que silencieusement faux.

**Plusieurs grilles actives par client** sont désormais possibles : activer
l'une ne périme plus les autres. Quand plusieurs couvrent la même route à la
date d'ETD, `RateGridLine.is_route_default` tranche. Seule la grille par défaut
anonyme reste unique — deux replis concurrents rendraient le tarif public non
déterministe.

---

## Décision 3 — Conditions de règlement déclaratives (Q6.2)

`RateGridPaymentTerm` : 1 à 3 échéances, déclencheur parmi « avant chargement »,
« avant déchargement », « X jours avant le départ ». La somme doit faire
**exactement 100 %** — un échéancier partiel laisserait une part du fret sans
date d'exigibilité.

Elles sont **déclaratives** : reprises sur la booking note, elles ne déclenchent
aucune facturation. La facturation du fret reste hors plateforme (virement,
comptabilité Pennylane), conformément à l'arbitrage A5 déjà acté.

*Conséquence assumée* : le logiciel ne sait pas si une échéance a été honorée. Le
verrou anti-impayé identifié par la veille marché (« pas de connaissement sans
règlement ») reste donc à la main du commercial. Le construire supposerait de
faire entrer le suivi d'encaissement dans l'outil — décision qui dépasse ce lot.

---

## Décision 4 — Cycle de vie de l'offre : quatre états dont un calculé

`en_cours` → `valide` / `echue` / `annule`.

`echue` est le seul état calculé : validité dépassée **ou** navire parti (ATD),
deux conditions indépendantes (Q6.4). Il est **matérialisé** par un balayage,
pas seulement affiché : le volume réservé doit se libérer même si personne
n'ouvre l'écran, et l'historique doit porter la trace du passage à l'échéance.

`draft` et `sent` fusionnent en `en_cours` : la distinction n'était pas exploitée
(aucun envoi réel n'existait, seul le statut changeait) et la règle métier ne
prévoit pas d'état intermédiaire.

**Une grille et un voyage sont obligatoires à la création**, mais la contrainte
n'est **pas** posée en base : des offres antérieures existent sans l'un ni
l'autre, et les rendre `NOT NULL` exigerait de leur inventer une grille ou un
voyage. `RateOffer.is_legacy` les marque pour qu'on ne les prenne pas pour une
saisie bâclée.

---

## Décision 5 — Réservation de volume et anti-double-comptage

Une offre `en_cours` ou `valide` réserve son volume dans le chargement
prévisionnel du leg ; l'annulation et l'échéance le libèrent.

Le risque réel n'est pas la réservation, c'est le **triple comptage** : une offre
convertie en commande, elle-même reprise en booking, réserverait la même cale
trois fois. Les offres portant une commande sont donc exclues du comptage,
exactement comme les commandes reprises en booking le sont déjà (invariant
B2.2). Une régression y est détectable : le test dédié échoue si l'exclusion
disparaît.

---

## Décision 6 — Historisation : table dédiée, chaînée, non purgeable

`activity_logs` ne suffisait pas : il consigne qu'une action a eu lieu, sans
ancienne ni nouvelle valeur, et il est purgeable par ancienneté.

`rate_offer_revisions` conserve, par révision : le **diff champ par champ**,
l'**état complet** de l'offre, et un **hachage chaîné** sur la révision
précédente. Le chaînage n'empêche pas d'écrire en base — rien ne le peut — mais
il rend la falsification **détectable** : retirer ou réécrire une révision casse
la chaîne des suivantes, et l'écran l'affiche.

Le hachage porte sur une sérialisation canonique (clés triées, `Decimal` en
chaîne et jamais en flottant) : sans cela, deux sérialisations équivalentes du
même état donneraient des empreintes différentes et la vérification échouerait à
tort.

La table est hors périmètre de purge, et une assertion au chargement empêche de
l'y ajouter par analogie avec les autres journaux. Au passage, le **vidage
intégral d'`activity_logs`** est refusé (la purge par rétention reste ouverte) :
il portait déjà la seule trace des modifications de grilles.

---

## Décision 7 — « Booking note » désigne le contrat (Q4)

Le nom était pris par le PDF de confirmation de réservation de l'espace client,
installé dans les cinq catalogues de traduction. Ce n'est pas le même document.

Le nom revient au **contrat de réservation d'espace en cale** (trame de type
BIMCO CONLINEBOOKING, signé par le chargeur et le transporteur), établi
automatiquement à la validation d'une offre. La confirmation client devient
« confirmation de réservation ».

Ses champs sont **stockés**, pas rendus à la volée : le commercial les corrige
avant diffusion, et ce qui a été envoyé au client reste consultable tel quel même
si l'offre ou le référentiel évoluent ensuite. La diffusion gèle le document et
consigne l'empreinte SHA-256 de ce qui est parti.

Les conditions générales (22 clauses, clause ANEMOS, clause US COGSA) vivent
**verbatim** dans `app/services/booking_note_terms.py`, comme donnée versionnée :
une clause se relit, se date et se compare d'une version à l'autre. Toute
correction de fond engage le transporteur et relève de la direction.

*Règle tenue* : ne jamais inventer. L'agent au port de déchargement n'est pas une
donnée du système — il est désigné escale par escale — donc la case reste vide
avec une aide qui le dit. Un contrat qui affirme une information fausse est pire
qu'un contrat visiblement à compléter.

---

## Décision 8 — Signature électronique : niveau avancé, webhook autoritaire (Q5)

Yousign, en **signature avancée** au sens eIDAS (identification du signataire +
scellement), pas simple : la booking note engage un montant commercial.

L'intégration est calquée sur `services.stripe_checkout`, seule intégration
signée déjà en production. Quatre propriétés :

1. secure-by-default — sans clé, 503, et la signature manuscrite du document
   Word reste le circuit actif ;
2. HMAC-SHA256 sur le **corps brut**, comparé en temps constant ;
3. le payload n'est **jamais** cru sur l'état métier : toute transition est
   précédée d'une relecture serveur-à-serveur chez Yousign ;
4. idempotence, et non-régression d'état (une note signée n'est jamais
   rétrogradée par un événement tardif).

**Signature ≠ règlement** : les deux états sont indépendants et aucun ne pilote
l'autre. Les lier ferait, à la première évolution, considérer un contrat signé
comme encaissé.

---

## Décision 9 — Calcul du taux de base sur les valeurs réelles (Q6.3)

`nav_days` lit la vitesse du leg, `base_rate` la capacité du navire, quand elles
sont connues — au lieu des constantes 8 nœuds / 978 palettes. Le prix se
calculait sur une capacité fictive alors que la disponibilité affichée utilisait
la capacité réelle : les deux divergeaient dès qu'un navire s'écartait de 978.
Les constantes restent en repli, et une vitesse ou capacité nulle y retombe
plutôt que de diviser par zéro.

Les paliers par défaut suivent le barème métier (`< 50` / `50-100` / `100-300` /
`300-500` / `500-800` / navire complet), **bornes inclusives des deux côtés**
(Q6.1). Le palier « navire complet » devient non borné : il était plafonné à
850, si bien qu'au-delà le calcul retombait sur un repli silencieux.

---

## Décision 10 — Renommage « Devis » → « Estimation tarifaire » (Q3)

Les **libellés visibles** sont réécrits phrase par phrase dans les cinq langues.
Les **clés i18n** sont conservées : elles sont référencées dans des dizaines de
templates, et les renommer aurait multiplié le risque pour un gain nul.

Les **URL `/devis` sont conservées** — elles circulent en liens internes, dans
des PDF et des e-mails déjà envoyés — et `/commercial/estimations` existe
désormais en parallèle.

Deux faux amis relevés à l'audit sont épargnés : « quote-part » (prorata
financier, sans rapport avec `Quote`) et « devise » (monnaie, qui contient la
sous-chaîne « devis »). Le pro forma de réservation, dont le libellé bascule
entre « Devis » et « Facture » selon le statut du booking, est hors périmètre :
ce n'est pas un chiffrage préalable à la vente mais une facture pas-encore-
définitive.

---

## Conséquences

**Positives**

- Le tarif négocié n'est plus accessible sans identification établie par un
  opérateur.
- Le chargement prévisionnel d'un leg reflète enfin les offres en cours.
- Une contestation de prix se documente : l'historique est complet et vérifiable.
- La booking note est établie sans ressaisie et signable électroniquement.
- Le vocabulaire est unifié et sans homonymie.

**Coûts et limites assumés**

- Le rattachement manuel ajoute une étape à l'ouverture d'un extranet client.
  C'est le prix de la confidentialité tarifaire, et l'écran propose la
  correspondance pour éviter la saisie.
- Le logiciel ne suit toujours pas le règlement : le verrou anti-impayé reste
  humain.
- Les offres antérieures n'ont pas d'historique et n'en recevront pas
  rétroactivement — en fabriquer un reviendrait à inventer des révisions qui
  n'ont jamais eu lieu.
- `grid_id` / `leg_id` restent nullables en base ; la règle est appliquée à la
  création, pas par contrainte.
- La descente de la migration `0122` ne peut pas restaurer la distinction
  `draft` / `sent` (perdue à la montée, délibérément).

**À trancher plus tard**

- Unité de surcharge « fret payant (W/M) » — norme breakbulk relevée par la
  veille, non retenue faute de besoin exprimé.
- Statut « option posée » avec libération automatique de capacité, équivalent
  fonctionnel du dead freight sans le risque de la survente.
- Suivi des échéances de règlement et verrou sur la remise du connaissement.

---

## Références

- Migrations : `20260826_0120` à `20260826_0124`
- Services : `commercial`, `quoting`, `estimation`, `offer_history`,
  `offer_lifecycle`, `booking_note`, `booking_note_signature`, `yousign`
- Journal de développement : `docs/DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`
- Gabarit contractuel : `app/services/booking_note_terms.py`
