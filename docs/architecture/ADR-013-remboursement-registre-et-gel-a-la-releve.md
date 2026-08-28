# ADR-013 — Remboursement, valeur du registre de vente, et gel de la caisse à la relève

- **Date** : 2026-08-27
- **Statut** : **accepté** — arbitré le 2026-08-27
- **Décideur** : Julien Gondé
- **Rédaction** : suites de l'audit du module « Vente à bord » + « Caisse de bord »
  (`docs/audit/2026-08-27-audit-vente-a-bord-caisse.md`)

Cet ADR regroupe quatre arbitrages tranchés le même jour. Ils sont réunis ici
parce qu'ils portent tous sur la **boucle de correction** du module — ce qui se
passe quand quelque chose doit être repris, prouvé, ou arrêté.

---

## Décision 1 — Seul le siège peut rembourser

**Contexte.** Le statut `refunded` est déclaré dans le modèle, lu par la garde
de `settle_sale`, promis aux Opérations par la notice commandant… et **écrit par
aucun chemin de code**. Une vente encaissée par erreur est aujourd'hui
définitive : la seule voie de correction est un accès SSH à la production
(l'existence du script `scripts/purge_onboard_sales_today.py` en atteste). C'est
le manque P0 le plus structurant du module.

**Décision.** Le remboursement est un **geste du siège**. Le commandant ne
rembourse pas.

**Ce que cela implique.**

- La route de remboursement ne relève **pas** de `captain:M`, qui est la
  permission d'exploitation du bord — celle-là même qui encaisse. Un rôle qui
  peut encaisser *et* défaire un encaissement n'offre aucun contrôle.
- Le remboursement se fait par **contre-passation**, jamais par suppression :
  un mouvement de caisse négatif, des mouvements de stock `retour`, et la vente
  passée à `refunded`. Les deux registres restent append-only — c'est ce qui
  fait leur valeur.
- Le bord doit pouvoir **signaler** une vente à rembourser sans pouvoir
  l'exécuter : sinon la décision se contourne par téléphone et la trace se perd.

*Alternative écartée* : autoriser le commandant à rembourser dans une fenêtre
courte (le jour même). Rejetée — elle rouvre exactement le couple
encaisser/défaire que la décision ferme, pour un gain de confort qu'un appel au
siège couvre déjà.

---

## Décision 2 — Le registre de vente détaxée n'est pas un registre de BL

**Contexte.** L'audit relevait que le registre des mouvements de stock (ventes
en franchise) n'a aucun des garde-fous appliqués au registre BL : pas de
chaînage SHA-256, pas de PDF opposable, pas de montants, pas de visa. La
question posée était : faut-il le porter au même niveau ?

**Décision. Non — ce sont deux objets de nature différente.**

Le connaissement est un **document signé par le transporteur et le chargeur** ;
l'obligation de l'armateur porte sur la **tenue du registre des BL** (intangible)
et sur la **conservation des BL originaux**. Le registre de vente à bord est un
**journal d'exploitation interne** : il trace des sorties de marchandises
avitaillées en franchise, il n'engage pas deux parties l'une envers l'autre.

**Ce que cela implique.**

- Les travaux « chaînage SHA-256 », « export PDF opposable », « mentions légales
  IMO/MMSI + visa du commandant » sont **retirés du backlog** du module. Ils
  répondaient à une exigence qui n'existe pas ici.
- Le registre reste **append-only de fait** (aucune route d'écriture autre que
  l'insertion) : c'est une propriété d'hygiène, pas une exigence de preuve.
- Rien ne change côté BL, dont le registre et les originaux conservent leurs
  exigences propres (cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`).

*Ce que la décision ne dit pas* : elle ne se prononce pas sur l'ajout de
**montants** au registre de vente, qui reste un simple confort d'exploitation
(valoriser les sorties) et non une exigence probante. À traiter comme du P2.

---

## Décision 3 — Service passagers : en suspens

**Contexte.** `regime="franchise"` est écrit en dur : toute vente à bord est
traitée comme un avitaillement d'équipage. Une vente à un passager n'en est pas
un, ce qui aurait posé problème à l'ouverture du service 2027.

**Décision.** **L'activité passagers est suspendue.** La question ne se pose
plus dans l'immédiat.

**Ce que cela implique.** Le `regime` reste mono-valeur. Une réserve
technique subsiste et est consignée ici pour ne pas être redécouverte : le
registre écrit aujourd'hui la constante `REGIME_FRANCHISE` **en dur**, au lieu
de lire `sale.regime`. Tant qu'il n'existe qu'un régime, l'écart est invisible ;
le jour où un second apparaît, le registre mentirait sans que rien n'échoue.
Corriger cette ligne coûte une minute et évite un défaut silencieux — à faire,
même avec l'activité suspendue.

---

## Décision 4 — La déclaration de fin d'embarquement fige la comptabilité du débarquant

**Contexte.** Le contrôle de caisse introduit le 2026-08-27 constate et fige
l'écart, mais ne gèle rien : un mouvement daté d'avant la déclaration pouvait
encore être saisi après coup. La question était de savoir si une relève devait
arrêter les comptes.

**Décision. Oui.** À la déclaration d'un état de caisse de motif **fin
d'embarquement**, la comptabilité du commandant débarquant est **figée** : les
mouvements antérieurs ou égaux à la date du comptage passent en lecture seule et
aucun mouvement ne peut plus être ajouté sur cette période.

**Pourquoi c'est cohérent.** Une relève est une **décharge**. Le commandant
sortant remet une caisse dont le contenu a été compté contradictoirement ; si
l'entrant — ou le sortant — peut encore écrire dans la période remise, la
décharge ne vaut rien et l'écart redevient inimputable. C'est précisément le
manque que le contrôle de caisse corrige.

**Le point délicat, et sa résolution.** Un gel naïf ferait perdre de l'argent
réellement encaissé : un paiement carte confirmé par Stripe après la
déclaration, mais daté dans la fenêtre gelée, serait refusé — et le webhook
finirait par abandonner. Un principe déjà posé dans ce module l'interdit :
**on ne perd jamais l'écriture d'un paiement encaissé.**

La règle retenue distingue donc les deux natures d'écriture :

| Écriture | Comportement sur période gelée |
|---|---|
| **Saisie manuelle** (mouvement de caisse) | **Refusée**, avec un message nommant le gel et sa date |
| **Règlement d'une vente** (espèces confirmées ou webhook Stripe) | **Reportée** au premier jour non gelé, avec mention explicite dans le libellé |

Le report est visible, daté et explicable — contrairement à une perte, qui ne
laisse rien. L'écart du contrôle rendu, lui, n'est jamais réécrit : le paiement
reporté apparaît dans le contrôle suivant, à sa vraie place.

**Portée.** Le gel couvre **toute la caisse** du navire, pas seulement les
devises déclarées : une relève arrête la comptabilité du débarquant, pas une
partie de celle-ci. Corollaire opérationnel, déjà énoncé dans la notice : le
commandant doit déclarer **toutes** les devises qu'il détient.

**Coexistence avec la clôture mensuelle.** Les deux mécanismes verrouillent le
même champ (`locked_at`) mais répondent à deux besoins distincts — la clôture
arrête un **mois comptable**, la relève arrête la **responsabilité d'une
personne**. Chaque mouvement verrouillé porte la référence de ce qui l'a gelé,
pour que l'on sache toujours au titre de quoi.
