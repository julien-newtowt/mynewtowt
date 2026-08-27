# ADR-011 — Séparer la caisse d'espèces des encaissements par carte

- **Date** : 2026-08-27
- **Statut** : **accepté** — arbitré le 2026-08-27
- **Décideur** : Julien Gondé
- **Rédaction** : audit multi-agents du module « Vente à bord » + « Caisse de bord »
  (cf. `docs/audit/2026-08-27-audit-vente-a-bord-caisse.md`, constat V-02)
- **Décision** : **option B** (colonne `medium`), et le rapprochement bancaire
  des règlements CB **reste hors application** — il se fait dans le logiciel
  comptable, à partir de l'export mensuel de l'application et de l'extrait
  bancaire.

---

## Contexte

`services/onboard_sales.settle_sale` est le chemin unique de règlement d'une
vente à bord. Il crée, **quel que soit le moyen de paiement**, un
`CashboxMovement` de catégorie `vente_a_bord` d'un montant positif égal au total
de la vente (`app/services/onboard_sales.py:259-270` — aucune branche sur
`payment_method`).

Or `OnboardCashbox` décrit explicitement de l'argent **physique** :
« *Tracks day-to-day cash movements made by the captain or crew* »
(`app/models/onboard_cashbox.py:4-5`). Et la clôture mensuelle
(`services/cashbox.close_month`) calcule :

```
variance = counted_balance − computed_balance
```

où `counted_balance` est le **comptage physique des billets** saisi par le
commandant à l'écran (`templates/staff/cashbox/detail.html:110-115`) et
`computed_balance` la somme des mouvements de la période.

Le comportement actuel est **documenté et assumé** : la notice commandant
énonce « *Chaque vente réglée (espèces ou carte) crée un mouvement dans Caisse
de bord* » (§7). Ce n'est donc pas une régression, mais un choix de conception
dont l'audit établit qu'il produit un effet non voulu.

## Constat

Sur un mois type — 400 € de ventes espèces, 1 200 € de ventes carte, 300 € de
décaissements :

| Grandeur | Valeur | Commentaire |
|---|---|---|
| `computed_balance` | 1 300 € | inclut les 1 200 € encaissés **chez Stripe** |
| Espèces réellement en caisse | 100 € | ce que le commandant compte |
| `variance` archivée | **−1 200 €** | écart structurel, reproduit chaque mois |

Trois conséquences, par ordre de gravité :

1. **Le seul contrôle de caisse du module devient inopérant.** Un écart
   « normal » de plusieurs centaines d'euros s'installe, derrière lequel une
   perte d'espèces réelle (200 € manquants) est indétectable.
2. **Le grand livre affiche une trésorerie de bord qui n'existe pas.** Les
   1 200 € sont sur le compte Stripe, puis en banque — jamais dans le coffre.
3. **Les encaissements carte ne sont rapprochables de rien.** Il n'existe ni
   compte de transit, ni écran de rapprochement « ventes CB payées ↔
   `payment_intent` Stripe ».

Facteur aggravant : les deux natures partagent la **même catégorie**
`vente_a_bord`. L'écart n'est donc même pas rattrapable a posteriori par
filtrage, sans jointure sur `onboard_sales.payment_method`.

---

## Options

### Option A — Statu quo, avec avertissement à l'écran

Conserver l'écriture unique et afficher, au moment de la clôture, le montant des
ventes carte de la période pour que le commandant retranche mentalement.

- **Pour** : aucun changement de schéma, aucune migration, aucun risque.
- **Contre** : demande une correction mentale à chaque clôture, sur un écran
  utilisé par des non-comptables, dans un contexte de relève. La variance
  archivée en base reste fausse — donc inexploitable pour tout contrôle
  ultérieur ou tout audit.
- **Verdict** : rejetée. Un contrôle qui exige d'être corrigé de tête n'est pas
  un contrôle.

### Option B — Colonne `medium` sur le mouvement de caisse *(recommandée)*

Ajouter `CashboxMovement.medium` (`cash` | `card`, défaut `cash`), rétro-remplie
depuis `OnboardSale.payment_method`. `close_month` exclut `medium='card'` de
`computed_balance` et donc de `variance`, tout en conservant ces mouvements au
journal sous un total distinct « CB à rapprocher Stripe ».

- **Pour** : un seul journal, donc une seule source de vérité et un historique
  continu ; rétro-compatible (le défaut `cash` préserve l'existant) ; l'export
  comptable gagne une colonne au lieu de changer de forme ; la clôture devient
  interprétable sans correction mentale.
- **Contre** : migration + backfill ; `close_month` doit être retesté (il ne
  l'est aujourd'hui par **aucun** test — cf. §Prérequis).
- **Effort** : ~1,5 j, dont la moitié en tests.

### Option C — Deux caisses distinctes par navire

Un `OnboardCashbox` par `medium`, la caisse « carte » servant de compte de
transit soldé au versement Stripe.

- **Pour** : modèle comptablement le plus juste ; ouvre la voie au rapprochement
  des versements Stripe (`payout`).
- **Contre** : double le nombre d'objets à manipuler pour le commandant, qui
  n'a rien à faire de la caisse « carte » ; impose de revoir tous les écrans, les
  exports et `get_or_create` ; complexité disproportionnée pour 2 navires.
- **Verdict** : à reconsidérer si et quand le rapprochement des versements
  Stripe devient un besoin réel. Pas maintenant.

---

## Décision

**Option B**, retenue le 2026-08-27. Elle corrige la cause — une écriture
d'espèces pour de l'argent qui n'est pas en espèces — sans changer le modèle
mental du commandant, qui continue de voir un journal unique et un solde
d'espèces, désormais juste.

**Le rapprochement bancaire ne vit pas dans l'application.** Il se fait dans le
logiciel comptable, en rapprochant l'export mensuel de l'application et
l'extrait bancaire. C'est cohérent avec l'arbitrage A5, qui a déjà sorti la
facturation fret de la plateforme : l'application produit la matière comptable,
elle ne tient pas la comptabilité.

*Conséquence directe sur l'export* : le CSV mensuel doit distinguer les
règlements carte des espèces et porter leur total séparément, sinon le
comptable ne peut pas faire le rapprochement que cette décision lui confie.
C'est la seule obligation que la décision crée côté application — et elle rend
l'option C (deux caisses distinctes) définitivement sans objet.

## Prérequis impératif à l'implémentation

`sale.cashbox_movement_id` **est** aujourd'hui le verrou d'idempotence du
règlement : c'est lui, et lui seul, qui empêche un webhook Stripe rejoué de
créer un second encaissement. Si la voie carte cesse de créer un mouvement de
caisse, ce verrou disparaît.

> **L'ordre est contraint** : le verrou d'idempotence doit être remplacé
> (colonne `settled_at` dédiée + `UNIQUE`, verrouillage `with_for_update`,
> table d'événements Stripe) **avant** de toucher à l'écriture comptable.
> Inverser les deux revient à casser l'idempotence en corrigeant la comptabilité.

Les lots 1 et 3 de la remédiation posent précisément ces garde-fous
(`UNIQUE (cashbox_movement_id)`, verrou de ligne, table
`stripe_webhook_events`). Cet ADR ne peut donc s'implémenter qu'après eux.

Second prérequis : `close_month` n'est couvert par **aucun test** aujourd'hui,
alors qu'il verrouille irréversiblement des mouvements. Écrire ces tests fait
partie du lot, pas de la suite.

## Conséquences si l'option B est retenue

- **Schéma** : `cashbox_movements.medium` (`String(4)`, `NOT NULL`, défaut
  `cash`, `CHECK medium IN ('cash','card')`), backfill par jointure sur
  `onboard_sales`.
- **Comptabilité** : la `variance` change de sens pour les périodes futures.
  Les clôtures **déjà archivées gardent leur valeur** (elles sont figées) : il
  faudra dire, dans la documentation, à partir de quelle date la variance est
  interprétable.
- **Documentation à mettre à jour** : notice commandant §7,
  `docs/operations/`, `CLAUDE.md` (section « Vente à bord »).
- **Écran** : un total « CB à rapprocher » distinct du solde d'espèces, et à
  terme un rapprochement Stripe (hors périmètre de cet ADR).

## Question tranchée

*Le rapprochement bancaire des encaissements carte doit-il vivre dans
l'application ?* → **Non.** Logiciel comptable, sur la base de l'export mensuel
et de l'extrait bancaire. Aucun écran de rapprochement Stripe n'est à
construire ; l'option C perd son seul argument.
