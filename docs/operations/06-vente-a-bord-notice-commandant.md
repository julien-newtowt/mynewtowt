# Notice — Vente à bord (à l'usage des commandants)

> Comment enregistrer et encaisser une vente de biens ou services à bord
> (boutique, avitaillement franchisé), en espèces ou par carte bancaire.
> Écran : **Opérations → Vente à bord** (`/captain/ventes`). Accès réservé
> aux profils disposant du droit **captain** (commandants et marins habilités).

---

## En bref

1. **Ouvrir** Vente à bord → choisir **votre navire**.
2. **Avitailler** le stock une fois par embarquement (entrées de marchandises).
3. **Nouvelle vente** → ajouter les articles → **encaisser** (espèces ou carte).
4. L'encaissement alimente automatiquement la **caisse du bord** et le
   **registre douanier**.

Toutes les ventes à bord sont en **franchise de taxe** (régime
*avitaillement / franchise*), tracées au registre — c'est automatique, vous
n'avez rien à cocher.

---

## 1. Ouvrir le module et choisir le navire

Menu latéral **Opérations → « Vente à bord »**. La page d'accueil liste les
navires ; **cliquez sur le vôtre** pour ouvrir son espace (écran
*« Inventaire du bord »* + *« Ventes récentes »* + *« Nouvelle vente »*).

## 2. Le catalogue (ce qui est vendable)

Le **Catalogue des biens & services** (bouton *« Gérer le catalogue »*)
regroupe les articles vendables. Chaque produit a un **libellé**, un **prix
unitaire**, une **devise**, une **unité** (pièce, paquet…) et le **type**
*Bien* ou *Service* ; il peut suivre ou non son **stock**. Sa **référence
(SKU)** est **attribuée automatiquement** à la création — vous n'avez pas à la
saisir.

> Ajoutez un produit avec **« Nouveau produit »**. Un produit désactivé
> n'apparaît plus à la vente mais reste dans l'historique.

## 3. Avitailler : entrer le stock à bord

> 🔔 **Seuil d'alerte** — dans le catalogue, chaque article peut porter un
> seuil. Dès que le stock du navire passe dessous, l'inventaire l'affiche
> *« à réapprovisionner »*. Un stock négatif est toujours signalé, seuil ou
> non : c'est un écart d'inventaire à régulariser.

Avant les premières ventes d'une rotation, saisissez les quantités embarquées.
Dans l'espace du navire, section **Inventaire du bord**, ajoutez un mouvement
de stock avec le motif :

| Motif | Sens | Quand l'utiliser |
|---|---|---|
| **Avitaillement (entrée)** | + | Embarquement de marchandises en franchise |
| **Retour / reprise** | + | Reprise d'un article (annulation) |
| **Inventaire** | +/− | Recalage sur un comptage physique |
| **Ajustement** | +/− | Correction manuelle (casse, perte…) |
| **Vente (sortie)** | − | *Automatique* : posé à chaque vente réglée |

La sortie de stock d'une vente est **automatique** au règlement — vous n'avez
jamais à la saisir à la main.

## 4. Enregistrer une vente

1. Espace du navire → **« Nouvelle vente »**. (Vous pouvez indiquer le nom de
   l'acheteur ; ce n'est pas obligatoire.)
2. La vente s'ouvre au statut **Brouillon**.
3. Section **Articles** → **« Ajouter un article »** : choisissez le **produit**
   et la **quantité**, puis **« Ajouter »**. Répétez pour chaque article.
4. Le **Total** se met à jour automatiquement.

> Tant qu'aucun article n'est ajouté (**Total 0,00**), les boutons
> d'encaissement restent **grisés** : c'est normal, ajoutez d'abord une ligne.

## 4 bis. Remise, gratuité, article hors catalogue

**Remise** — au moment d'ajouter un article, un champ *Remise (%)* applique une
réduction sur cette ligne. **100 % vaut gratuité** : c'est ainsi qu'on offre un
article à l'équipage ou qu'on fait un geste commercial. Le prix du catalogue
reste affiché et conservé ; seul le total de ligne change.

**Article hors catalogue** — le lien *« + Ligne hors catalogue »* permet de
vendre quelque chose qui n'est pas au référentiel (dépannage, service
ponctuel) : désignation, prix, quantité. **N'inventez plus de faux produit**
pour cela — il polluerait le catalogue et l'inventaire durablement.

Une ligne hors catalogue **ne bouge pas le stock** au règlement : l'article
n'est pas suivi, c'est cohérent.

## 5. Encaisser en espèces

Section **Encaissement** → **« Encaisser en espèces »** → confirmez le montant.
La vente passe **Payée**, et un mouvement est créé dans la **caisse du bord**
(catégorie *vente à bord*). C'est le mode le plus simple : il ne dépend
d'aucun prestataire extérieur.

Le champ **« Espèces reçues »** est facultatif mais utile : il calcule et
conserve la **monnaie rendue**. Sans cette trace, un écart au comptage de
caisse reste inexplicable. La caisse est créditée du **montant de la vente**,
jamais de la somme remise par l'acheteur.

Une fois la vente réglée, **« Reçu de l'acheteur (PDF) »** produit le
justificatif à remettre. Ce n'est pas une facture : les ventes à bord sont en
franchise de taxe et n'ouvrent pas droit à déduction — le document le précise.

> 📶 **Sans réseau, utilisez « Vente rapide — espèces »** (en haut de l'écran
> du navire). Cette voie construit le panier sur votre appareil et n'envoie
> qu'une seule fois, à la validation : si le lien est coupé, la vente est
> **conservée localement** et part automatiquement au retour du réseau. Vous
> pouvez encaisser normalement.
>
> Les autres écrans (« Nouvelle vente » pas à pas, saisie de caisse, carte
> bancaire) **exigent une connexion** : ils enchaînent plusieurs échanges avec
> le serveur, ou dépendent du prestataire de paiement. En cas de coupure sur
> ces voies-là, **ne ressaisissez rien** : attendez le réseau, rouvrez la vente
> et vérifiez son statut avant toute nouvelle action.

## 5 bis. Vente rapide — la voie à utiliser sans réseau

En haut de l'écran du navire, **« Vente rapide — espèces »** encaisse en une
seule opération : choisissez l'article, la quantité, **« Ajouter au panier »**,
répétez, puis **« Encaisser en espèces »**.

C'est la seule voie utilisable **hors connexion**. La différence n'est pas
cosmétique : le parcours pas à pas envoie une requête à chaque étape et a besoin
du serveur entre chacune, alors que la vente rapide n'envoie **qu'une fois**, à
la fin. Si le réseau manque à cet instant, la vente est conservée sur l'appareil
et repart toute seule au retour du lien — un message vous le confirme.

**Ne ressaisissez pas une vente qui semble « ne pas être passée »** : elle est
en file d'attente. Un doublon serait un second encaissement. Le système refuse
d'ailleurs de l'enregistrer deux fois, mais autant ne pas s'y fier.

Limites à connaître : espèces et euros uniquement, et les articles doivent déjà
être au catalogue (préparez-le à quai). La carte bancaire exige une connexion
par nature — le paiement passe par un prestataire extérieur.

## 6. Encaisser par carte bancaire (CB)

1. Section **Encaissement** → **« Générer un lien CB (Stripe) »**.
2. La vente passe **En attente de paiement** et l'écran **« Lien de paiement
   CB »** s'affiche :
   - **« Scanner pour payer »** : le client scanne le **QR code** avec son
     téléphone ;
   - **« Ou ouvrir le lien »** : bouton *« Ouvrir la page de paiement »* /
     copie de l'URL, si vous préférez présenter la page vous‑même.
3. Le client règle sur la page sécurisée hébergée par Stripe (le lien est
   valable **~24 h** ; bouton **« Régénérer un lien »** s'il a expiré).
4. Une fois le paiement effectué, la vente bascule **Payée** et alimente la
   caisse — **comme pour l'espèce**.

**Le paiement n'apparaît pas tout de suite ?** Revenez sur la vente (ou
**rechargez** la page) : son statut se met à jour à l'ouverture dès que la
banque a confirmé. Inutile de refaire un lien.

**Bouton « CB indisponible » (grisé) ?** Le paiement carte n'est pas activé
sur cette installation. Encaissez **en espèces** ; signalez‑le au siège si la
CB devait être disponible.

**Le client règle finalement en espèces** alors qu'un lien CB est en attente ?
Sur la vente, utilisez **« Basculer en espèces »**. Le lien de paiement est
alors **fermé côté Stripe** : le client ne peut plus payer par carte une vente
déjà réglée en liquide. Si un message vous signale que le lien n'a pas pu être
fermé (Stripe injoignable), **n'encaissez pas en espèces** — réessayez une fois
le réseau revenu, sinon le client risque de payer deux fois.

## 7. La caisse du bord

Chaque vente réglée (espèces **ou** carte) crée un mouvement dans **Caisse de
bord** (`/cashbox`), en positif, dans la devise de la vente. Vous y suivez le
solde en temps réel et la **clôture mensuelle**. Rien à ressaisir : la vente et
la caisse sont liées.

## 7 bis. Déclarer l'état de sa caisse (contrôle)

**Quand :** à chaque **fin d'embarquement** (avant de débarquer) et à chaque
**fin de mois**. Un contrôle ponctuel est possible à tout moment.

**Où :** Caisse de bord → **« Déclarer l'état de caisse »**.

**Comment :**

1. Choisissez le **motif** (relève / fin de mois / contrôle) et la **date** du
   comptage.
2. Indiquez votre nom, et — s'il s'agit d'une relève — celui du commandant
   **entrant**. C'est ce qui rend la responsabilité du cash traçable : jusqu'ici
   la caisse n'avait pas de détenteur identifié.
3. **Cochez uniquement les devises que la caisse contient réellement.** Une
   devise cochée mais non comptée produirait un faux écart.
4. Comptez physiquement et saisissez le **nombre de coupures** pour chaque
   valeur, billets puis pièces. Si vous ne triez pas la petite monnaie, portez
   son montant global sur la ligne **« Pièces non détaillées »**.
5. Validez. Le total est **recalculé par le système** à partir de vos
   quantités : il n'est jamais repris tel quel d'un champ « total ».

**L'écart n'a pas à être nul.** Il est calculé, affiché, et **conservé** avec le
solde théorique du jour. Expliquez-le dans le champ prévu (avance non saisie,
erreur de rendu de monnaie, dépense sans justificatif…). Un écart expliqué vaut
mieux qu'un comptage ajusté pour tomber juste.

**Un mouvement saisi après coup ne modifie pas un état déjà déclaré** : il
apparaîtra dans le contrôle suivant. C'est voulu — un contrôle qui se réécrirait
tout seul ne contrôlerait rien.

## 7 ter. Corriger un mouvement de caisse mal saisi

Sur la ligne du mouvement, **« ✏️ Rectifier »** : indiquez le motif, et le
montant correct si vous en avez un (laissez vide pour une simple annulation).

**Rien n'est effacé.** Le mouvement d'origine reste au journal, et le système
ajoute une écriture opposée **datée du jour**, puis le montant corrigé. C'est la
manière comptable de rectifier : on ne réécrit pas le passé, on l'équilibre.

Deux conséquences à connaître :

- la correction n'apparaît **pas** dans la période d'origine. Si un contrôle de
  caisse a déjà été rendu sur cette période, il reste ce qu'il était — la
  rectification se lira dans le suivant ;
- un mouvement ne se rectifie **qu'une fois**. Si vous vous êtes trompé dans la
  correction elle-même, rectifiez l'écriture de correction.

Les mouvements **verrouillés** (clôture mensuelle passée, ou relève déclarée) ne
proposent pas ce bouton : leur période est arrêtée.

## 7 quater. Savoir ce que la boutique a vendu

**Vente à bord → « Chiffre d'affaires »** (ou depuis l'écran du navire).
Filtrable par navire et par période, exportable en CSV pour la comptabilité.

Trois ventilations : par navire, par article (classé par chiffre d'affaires) et
par voyage. Seules les ventes **réglées** comptent — un brouillon n'est pas du
chiffre d'affaires — et le net est déduit des remboursements.

Deux limites à connaître :

- les montants restent **séparés par devise**. L'application ne tient aucun
  taux de change : additionner des euros et des đồng donnerait un total faux
  d'apparence juste ;
- la **marge n'est pas calculable** : le catalogue ne porte pas de prix de
  revient. Le chiffre affiché est un chiffre d'affaires, pas un résultat.

## 8. Le registre douanier (vente détaxée)

Le **Registre des mouvements — vente détaxée** (bouton *« Registre »* dans
l'espace du navire) liste tous les mouvements de marchandises en franchise :
avitaillements, ventes, ajustements, inventaires, retours. Il constitue la
**preuve douanière** des ventes en franchise à bord.

- **Filtrez** par période (date de début / de fin).
- **Exportez** en **CSV** pour transmission ou archivage.

## 9. Annuler ou corriger une vente

- **Avant règlement** (Brouillon ou En attente de paiement) :
  **« Annuler la vente »**. Rien n'est encaissé, aucun stock n'est décrémenté,
  et le lien de paiement CB éventuellement généré est **fermé côté Stripe** —
  le client ne peut plus payer une vente annulée. Si l'écran signale que le
  lien n'a pas pu être fermé, **n'annulez pas** : réessayez avec du réseau.
- **Après règlement** : une vente **Payée** ne s'annule pas d'un clic (un
  encaissement a eu lieu). Sur la vente, utilisez **« Demander un remboursement
  au siège »** en indiquant le motif. Le siège est alerté et traite le
  remboursement ; **le bord n'y touche pas** — on encaisse à bord, on ne défait
  pas un encaissement à bord.
- Une fois traité, la vente passe **Remboursée**. Rien n'est supprimé : un
  mouvement de caisse **négatif** vient équilibrer l'encaissement d'origine, et
  les articles suivis en stock **reviennent en stock** automatiquement.

---

## Aide‑mémoire — statuts d'une vente

| Statut | Signification |
|---|---|
| **Brouillon** | Articles en cours de saisie, rien n'est encaissé |
| **En attente de paiement** | Lien CB généré, en attente du règlement du client |
| **Payée** | Réglée (espèces confirmées ou carte confirmée) — caisse alimentée |
| **Annulée** | Abandonnée avant tout règlement |
| **Remboursée** | Remboursée après règlement (opération siège) |

## Questions fréquentes

- **Je ne vois pas « Vente à bord » dans le menu.** Votre profil n'a pas le
  droit *captain* — demandez au siège de l'activer.
- **Je vois les écrans mais chaque bouton renvoie « Accès refusé » (403).**
  Votre profil est en **consultation seule** sur le module. Le siège doit
  activer « Modifier » sur la cellule *(votre rôle × captain)* dans
  `/admin/permissions` ; la prise d'effet demande jusqu'à une minute.
- **Le QR est trop petit / illisible.** Utilisez **« Ouvrir la page de
  paiement »** ou copiez l'URL affichée : c'est le même paiement.
- **Puis‑je vendre un article dont le stock est à zéro ?** Oui, la vente n'est
  jamais bloquée par le stock (le paiement prime) ; le registre reflétera un
  stock négatif à régulariser par un **Inventaire**.
- **Faut‑il choisir un régime de taxe ?** Non : toutes les ventes à bord sont
  en **franchise** (avitaillement), appliqué automatiquement.

---

*Écrans concernés : `/captain/ventes` (accueil), espace navire, catalogue,
vente, lien de paiement CB, registre. Pour la mise en service du paiement carte
(clés Stripe), voir le référent système au siège.*
