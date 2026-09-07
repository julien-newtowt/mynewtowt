# Reprise du module commercial — 2026-09-04

**Branche** : `claude/commercial-module-improvements-9u3ndb`
**Origine** : huit retours d'usage de Julien sur les écrans commerciaux.
**ADR** : [ADR-015](../architecture/ADR-015-prix-annonce-cout-calcule-marge-derivee.md) (accepté).
**Migration** : `20260904_0141_rate_grid_line_cost_and_unit`.

---

## Situation

Huit points signalés, du bug de production à la remise en cause d'un modèle
métier. Ils sont traités ici dans l'ordre où ils se répondent, pas dans l'ordre
où ils ont été posés.

## 1. `/commercial/offers/new` répond 422 — déjà corrigé, pas déployé

**Fait.** Le code de `main` **ne porte plus ce défaut**. Il a été corrigé le
2026-09-04 à 06:05 UTC par le commit `465252e` (« deux routes inatteignables… »),
qui a remis les chemins littéraux devant `/offers/{offer_id}` et posé la
sentinelle générale `tests/regression/test_literal_routes_not_shadowed.py`.

Vérifié sur cette branche : `/commercial/offers/new` est déclaré **avant**
`/commercial/offers/{offer_id}` dans la table de routage, et la sentinelle passe.

**Conclusion.** `my.towt.eu` sert une image antérieure à ce commit. C'est un
**écart de déploiement**, pas un défaut de code — le correctif part avec la
prochaine mise en production.

## 2. Inversion prix ↔ coût (COM-12)

Le fond du sujet. `RateGridLine.base_rate` portait **deux notions à la fois** :
un coût quand `is_manual` était faux, un prix quand il était vrai. La marge
n'était donc jamais lisible — nulle par construction dans le premier cas, non
calculable dans le second, faute d'avoir conservé le coût.

Séparé en deux colonnes, avec la marge **dérivée** :

| | avant | après |
|---|---|---|
| `base_rate` | coût **ou** prix, selon `is_manual` | **prix annoncé** par le commercial |
| `cost_rate` | — | **coût de revient** calculé, jamais saisi |
| marge | non calculable | `margin_eur` / `margin_pct`, dérivées |
| `is_manual` | « surcharge manuelle » | « **prix confirmé** » |

Le logiciel **propose** (`suggested_price` : coût rapporté à 25 % de marge sur
prix de vente), l'opérateur **confirme** — un bouton *Confirmer* valide une
proposition sans la modifier. Un recalcul de coût ne déplace **jamais** un prix
confirmé, alors qu'auparavant l'icône ↻ effaçait le tarif du commercial.

Le recalcul global porte désormais sur **toutes** les routes : il sautait les
routes manuelles, dont le coût restait figé et la marge donc fausse.

**Unité de vente par route** (`rate_unit` : `palette` | `tonne`) — le café vert
et le cacao se négocient au poids. Une cotation sur une route au poids **refuse**
de coter sans tonnage déclaré au lieu d'inventer une équivalence ; le palier de
volume et la cale restent comptés en emplacements.

**`cost_rate` est nullable à dessein** : `None` = coût non calculable (aucun port
en lourd au référentiel flotte). L'écran affiche « — » et renvoie vers
Admin → Flotte. Même patron que la distance théorique d'un leg sans coordonnées
de port. Substituer un tonnage par défaut ferait passer une marge inventée pour
un fait.

**Ce que la reprise de données peut et ne peut pas faire.** Les routes **non
manuelles** voient leur `base_rate` recopié dans `cost_rate` : c'était bien le
coût OPEX. Elles afficheront donc **0 % de marge** — ce n'est pas un défaut
d'affichage, elles se vendaient réellement à prix coûtant, et c'est le premier
constat utile du lot. Les routes **manuelles** n'ont jamais eu de coût
enregistré : leur `cost_rate` reste `NULL` et leur marge « — » jusqu'au prochain
recalcul depuis l'écran, qui pose le coût **sans toucher au prix**.

## 3. Page « nouvelle grille tarifaire »

- **Client** : liste déroulante **filtrable** (`searchable-select.js`, amélioration
  progressive — le `<select>` natif reste le contrôle posté et fonctionne sans
  JavaScript). La recherche ignore accents et casse : « senegal » remonte
  « Sénégal ».
- **Navire de référence retiré.** L'OPEX jour est celui de la flotte
  (sisterships TSC 80). `RateGrid.vessel_id` reste en base pour les grilles
  antérieures (aucune donnée détruite) mais n'est plus exposé ; toute édition
  d'en-tête le remet à `NULL` — explicitement, parce que la valeur n'était plus
  effaçable depuis l'écran une fois le sélecteur retiré.

## 4. Boutons d'action et formule affichée

Les fiches alignaient des boutons de tailles et de styles différents — un
`btn-primary` pleine taille à côté d'un `btn-sm` outillé, à côté d'un lien nu,
et « Modifier l'en-tête » repassait à la ligne au milieu du mot faute de largeur.

Une classe Kairos unique, `.page-actions`, impose la même grammaire partout :
états (badges, pills) puis séparateur puis actions, même hauteur, même taille de
police, **jamais de césure**, un seul `btn-primary`, les gestes destructeurs en
`btn-danger`, le retour en `btn-ghost`. Appliquée aux fiches grille, client,
offre, commande, estimation et aux en-têtes de listes.

La phrase expliquant la formule `base_rate = OPEX/j × …` est retirée : elle
décrivait le calcul que ce lot cesse de faire faire au logiciel. Le tableau des
routes affiche maintenant, colonne par colonne, ce qu'elle tentait d'expliquer :
unité, coût de revient, prix annoncé, marge, et si le prix est proposé ou
confirmé.

## 5. Une commande naît d'un engagement (COM-13)

`POST /commercial/orders` est **supprimé**, ainsi que son formulaire libre.
`GET /commercial/orders/new` liste désormais les **offres à confirmer** et les
**estimations à accepter** ; la création passe par
`POST /commercial/offers/{id}/convert`.

Une commande saisie hors de toute offre portait un tarif que rien ne rattachait à
une grille : ni sa marge, ni la conversion par grille du tableau de pilotage
n'étaient calculables.

⚠️ **Point de vigilance traité** : l'ancien formulaire portait des garanties de
saisie (LOCODE normalisé et validé, fenêtre de livraison validée, total dérivé du
tarif × volume) que la conversion n'avait pas. Elles ont été **portées dans la
conversion** avant de supprimer le formulaire, sans quoi la suppression aurait
perdu des règles au passage. Trois tests le verrouillent.

## 6. Suppression / modification par l'administrateur

La matrice de permissions ne sait pas exprimer « le commercial modifie, seul
l'administrateur supprime » (elle ne connaît que C/M/S par module). La règle vit
donc dans le routeur — même choix que le cloisonnement de `support_router`, et
pour la même raison.

Quatre routes, toutes réservées à l'administrateur :

| Route | Bloqueurs (le refus les nomme) |
|---|---|
| `POST /commercial/grids/{id}/delete` | offres, commandes référençant la grille |
| `POST /commercial/offers/{id}/edit` | — (correction, inscrite dans l'historique chaîné) |
| `POST /commercial/offers/{id}/delete` | commande issue, booking note **diffusée**, estimation convertie |
| `POST /commercial/devis/{ref}/delete` | estimation déjà convertie en offre |

Chaque suppression est journalisée (`activity_logs`). La correction d'une offre
émise **n'échappe pas** à l'historique chaîné SHA-256 : elle y entre sous
l'action `corrected`, avec l'auteur et le motif.

**Arbitrage à valider par Julien.** Supprimer une offre emporte ses révisions
(`ondelete=CASCADE`). C'est défendable — cet historique documente une offre qui
n'existe plus, et le journal d'activité conserve la trace de la suppression et de
son auteur — mais c'est une entorse à « `rate_offer_revisions` est ni exportable
ni purgeable ». Les bloqueurs sont là pour que le cas ne se présente que sur des
offres sans effet contractuel. Si la règle doit être absolue, l'alternative est
un statut « supprimée » masquant l'offre sans l'effacer : dites-le et je bascule.

## 7. Fiche client

- **Contenu remonté de Pipedrive** : `sync_clients` alimente maintenant le
  **contact** (nom / e-mail / téléphone, via un appel groupé `list_persons`,
  pas un appel par organisation) et le **pays** (`address_country`, résolu en
  ISO 2 quand le libellé est reconnu — sinon rien, un mauvais code afficherait un
  faux drapeau). Le bloc « Contact » de la fiche restait vide alors que le CRM
  porte l'information. Une valeur **absente** du CRM n'écrase jamais une saisie
  faite dans l'ERP : le silence de l'API n'est pas une valeur vide. Un
  horodatage `pipedrive_synced_at` dit de quand date ce qui est affiché.
- **Création réservée à l'administrateur** : une fiche créée en parallèle du CRM
  n'a pas de `pipedrive_org_id` et devient un doublon que la synchronisation ne
  peut plus rapprocher. Le formulaire n'est plus rendu pour les autres rôles, et
  la route refuse en 403 — pas seulement l'écran.
- **« Compte-ancre » → case « client stratégique »** : les trois attributs qui
  l'accompagnaient (`annual_volume_commitment`, `capacity_priority`,
  `co_branding_status`) n'étaient consommés par **aucune** règle — ni allocation
  de cale, ni facturation, ni tri ; seuls deux badges les affichaient. Trois
  champs saisis que rien n'applique finissent par être crus. Les colonnes restent
  en base, sans être exposées.

## 8. Le bouton en haut à droite

C'est le **basculement de densité d'affichage des tableaux** (`rows-3` de Lucide,
`#density-toggle`, `app/static/js/density.js`). Il alterne entre l'affichage
compact par défaut et un affichage « confortable » (lignes plus aérées), via la
classe `density-cosy` sur `<body>`.

La préférence est **par navigateur** (`localStorage`), volontairement pas
persistée en base : c'est un confort local, il ne suit pas l'utilisateur d'un
poste à l'autre. Il vient de la reprise UX Phase 3
(`docs/design/03-reprise-ux-legacy.md` §4).

---

## Qualité

- `pytest` : suite complète au vert (voir le récapitulatif de la PR/branche).
- `ruff` + `black` sur `app` et `tests` : propres.
- Migration `0141` : additive (deux colonnes sur `rate_grid_lines`, une sur
  `commercial_clients`), tête Alembic unique, `downgrade` symétrique.
- Aucun secret, aucun fichier temporaire.

## Ce qui reste ouvert

- **Port en lourd au référentiel flotte** (Admin → Flotte) : prérequis de
  **donnée**, pas de développement. Tant qu'il manque, aucune route tarifée à la
  tonne n'a de coût, donc de marge.
- **Marge cible paramétrable** : constante à 25 %, servant uniquement à
  *proposer*. La poser par client ou par route est une décision de direction.
- **Coût réel vs coût théorique** : `cost_rate` reste théorique (OPEX × durée /
  capacité). Le rapprocher de l'OPEX constaté par voyage (`LegFinance`) ne doit
  pas se faire en écrivant d'office dans une saisie d'opérateur — même arbitrage
  que pour le CA de la vente à bord (ADR-013).
- **Suppression d'une offre et historique chaîné** : arbitrage à confirmer (§6).
