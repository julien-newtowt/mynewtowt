# Audit — Module « Vente à bord » (`/captain/ventes`) et « Caisse de bord » (`/cashbox`)

| | |
|---|---|
| **Date** | 2026-08-27 |
| **Branche** | `claude/audit-ventes-onboard-3uwjd7` |
| **Demandeur** | Yasmin Ponce |
| **Déclencheur** | Test réel à bord non concluant |
| **Nature** | Audit technique, fonctionnel et expérience utilisateur |
| **Périmètre** | `onboard_sales_router.py` (847 l.), `cashbox_router.py` (351 l.), `services/onboard_sales.py` (428 l.), `services/cashbox.py` (322 l.), `services/stripe_checkout.py` (187 l.), `models/onboard_sales.py`, `models/onboard_cashbox.py`, 8 gabarits, 4 fichiers de tests, migrations `0005` / `0035` / `0096` |
| **Méthode** | 6 auditeurs indépendants (architecture, sécurité & intégrité financière, couverture fonctionnelle, UX terrain, QA, reconstitution des conditions réelles), puis consolidation et re-vérification directe des constats porteurs |
| **Modifications de code** | **Aucune.** Conformément au workflow projet, les constats sont remontés pour arbitrage. |

> **Note de contexte** — le régime d'instructions temporaires de `CLAUDE.md` (manager en congé) courait jusqu'au **2026-08-17**, désormais dépassé. Les points marqués « arbitrage » dans ce rapport peuvent donc être portés directement au manager plutôt que gelés.

---

## 1. Résumé exécutif

**Réponse à la question posée** — les fonctionnalités ne sont effectivement pas au rendez-vous, mais le diagnostic est plus précis que « il manque des fonctions » :

> Le module est un **MVP de faisabilité technique promu au rang de module livré**. Sa qualité de surface est réelle (patterns projet respectés, `ruff` vert, 25 tests verts, docstrings soignées). Sa **qualité de fond ne tient pas les invariants qu'exigent de la monnaie et un registre douanier** : sérialisation des écritures, contraintes d'intégrité, rapprochement avec le prestataire de paiement, cloisonnement par navire, et surtout **boucle de correction** (annuler, rembourser, rectifier). Un point de vente sans boucle de correction est démontrable, pas exploitable.

**Trois conclusions, dans l'ordre d'urgence :**

1. **Le test à bord a très probablement échoué sur un mur de permissions, pas sur une fonctionnalité manquante.** Le rôle `marins` est en consultation seule sur le module `captain` ; l'override qui devait le passer en modification n'est posé nulle part dans le dépôt. Le menu reste visible, les écrans s'ouvrent — le 403 ne tombe qu'au premier clic sur un bouton. *(§3)*
2. **Trois défauts font perdre de l'argent réel ou détruisent des registres, sans voie de réparation applicative.** Double débit du marin via un lien Stripe jamais expiré, caisse espèces polluée par les encaissements carte, valeurs `NaN` acceptées dans des tables append-only. *(§4)*
3. **La promesse « utilisable en mer » n'est pas tenue.** Le module est hors du périmètre du service worker et n'utilise nulle part la file d'attente hors-ligne pourtant déjà construite et active ailleurs dans la même application. La notice commandant affirme pourtant le contraire. *(§4, V-04)*

**Verdict de déployabilité :**

| Configuration | Verdict |
|---|---|
| Voie **carte** (Stripe provisionné) | ❌ **Non déployable.** V-01, V-02, V-05, V-06, V-07 non traités. |
| Voie **espèces seule** (`STRIPE_SECRET_KEY` vide) | ⚠️ **Déployable pour un pilote encadré** après le lot 1 (V-03, V-08), et sous réserve d'accepter V-09 et V-12 comme risques **documentés** le temps du pilote. |

**Notes de maturité** — technique **5,5/10** · UX **4/10** · couverture fonctionnelle métier **~45 %** du périmètre attendu d'un point de vente + caisse d'agence.

---

## 2. Ce qui est solide (à préserver en corrigeant)

Constats factuels, vérifiés fichier par fichier. Ce module n'est pas bâclé, et les corrections ne doivent pas dégrader ces acquis :

- **Conformité formelle aux patterns `CLAUDE.md` quasi totale** : aucun `db.commit()` en route ni en service, aucune f-string SQL, `require_permission()` sur 17/17 endpoints, `flush + RedirectResponse(303)` systématique, `_csrf` présent dans les 6 gabarits porteurs de formulaires.
- **Montants non falsifiables côté client** : `add_line` (`services/onboard_sales.py:186`) fige `unit_price` et `label` depuis `OnboardProduct` ; `recompute_total` recalcule par somme ; `stripe_checkout.create_session` reconstruit les `line_items` depuis les lignes serveur.
- **Signature webhook correcte** : `stripe.Webhook.construct_event` → comparaison à temps constant, tolérance anti-rejeu de 300 s. Exemption CSRF du préfixe `/webhooks/` justifiée et bornée (`csrf.py:57`).
- **Secure-by-default effectif sur l'essentiel** : sans `STRIPE_SECRET_KEY`, la création de session renvoie 503, le webhook renvoie 503, la réconciliation devient no-op et le bouton CB est grisé.
- **Aucun secret ni donnée de carte journalisée** ; CSP inchangée (Checkout hébergé, QR en SVG `segno` généré côté serveur).
- **Grand livre de stock append-only signé**, justificatifs photo mobile (`capture="environment"`), verrouillage des mouvements à la clôture, traçabilité `activity.record()` sur la majorité des mutations.
- **Un filet de réconciliation existe** (`_reconcile_pending_card_payment`) — bon réflexe opérationnel, mal placé (V-10).
- **Les bons problèmes ont été identifiés** par l'auteur : idempotence, snapshot des prix, registre append-only, repli sans webhook. C'est l'implémentation de ces intentions qui échoue, pas leur conception.

---

## 3. Pourquoi le test à bord a échoué — diagnostic prioritaire

### 3.1 Hypothèse principale : le rôle `marins` ne peut rien écrire

**Faits vérifiés directement :**

- `app/permissions.py:131` → `("marins", "captain"): "C"` — **consultation seule**.
- Les 13 mutations des deux modules exigent `captain:M` (`onboard_sales_router.py:133,178,206,280,322,413,431,449,476,580` ; `cashbox_router.py:128,191,269`).
- La table `role_permissions` est créée **vide** (`migrations/versions/20260612_0026_...py:47-61`, `create_table` seul) et **aucune migration ni aucun script du dépôt n'y insère de ligne** (recherche exhaustive : aucun résultat). L'override `marins → CM` documenté dans `CLAUDE.md` et dans l'en-tête du routeur **n'existe donc que dans la documentation**.
- La barre latérale reste visible : `_layout.html:29` appelle `can_access` → `has_any_access`, qui se contente du niveau `C` et ne lit que `_MATRIX` (`templating.py:211-213`, `permissions.py:313-314`).

**Symptôme qu'aurait vu le testeur :** le menu « Vente à bord » et « Caisse de bord » s'affiche, les écrans s'ouvrent et se lisent normalement. Au **premier clic sur un bouton** — Nouveau produit, Ouvrir la vente, Ajouter un article, Encaisser, Mouvement de caisse — page pleine **« 403 — Accès refusé »**, sans mention du module ni de la permission en cause. Rien n'est enregistré.

**Le dépôt connaissait déjà le piège** : `scripts/check_user.py:131-135` imprime, pour tout compte `marins`, *« l'espace commandant est en CONSULTATION seule par défaut — pour agir (vente à bord, SOF…), activez « Modifier » sur (marins × captain) dans /admin/permissions »*. Ce diagnostic n'a pas été exécuté avant le test.

**Pourquoi les tests ne l'ont pas vu** : `tests/integration/conftest.py:69-71` fabrique un `SimpleNamespace(role="administrateur")` et les tests appellent les coroutines de route **en direct**, hors ASGI — donc sans jamais exécuter `require_permission`. Aucun test n'exerce le chemin `marins`. Le défaut était structurellement invisible avant la mise en situation réelle.

**Correctif immédiat** : `/admin/permissions` → cellule `(marins × captain)` = **CM**. Réservé à `administrateur` (`admin:M`) — `manager_maritime` n'a que `admin:C` et **ne peut pas** le faire. Prise d'effet ≤ 60 s (cache). **Recommandation** : poser cet override par migration ou par seed, pour qu'il ne dépende plus d'une manipulation manuelle non documentée.

### 3.2 Murs secondaires, réels et démontrés

Même si le 403 explique l'échec à lui seul, ces quatre défauts ont au minimum dégradé l'expérience du testeur et brouillé son diagnostic :

| # | Défaut | Preuve | Ce que voit le testeur |
|---|---|---|---|
| a | **Le pavé du navire n'est pas cliquable** — `onclick` inline bloqué par la CSP stricte, alors que la carte a `cursor:pointer` et un effet de survol | `security_headers.py:24` (pas de `'unsafe-inline'`) vs `hub.html:16`, `cashbox/index.html:12` ; `kairos.css:231-239` | **Tout premier écran** : il tape le pavé du navire, rien ne se passe. Seul le petit lien « Ouvrir → » marche. Impression immédiate d'appli cassée. |
| b | **Toute erreur métier s'affiche en JSON brut** — seuls 404 et 403 ont un gabarit HTML | `main.py:203-223` : aucun handler pour 400/503, alors que le module en lève sur ~25 chemins | Écran blanc avec `{"detail":"Vente sans montant : ajoutez au moins une ligne."}`, sans mise en page ni bouton retour. Rapporté comme « ça affiche du code ». |
| c | **Appels Stripe bloquants sur le chemin d'affichage** — timeout SDK 80 s, `max_network_retries=2`, aucun timeout surchargé | `onboard_sales_router.py:363` (GET `sale_detail` → `retrieve_session`), `:540` ; `stripe_checkout.py:88,168` | Sur lien dégradé, la page d'une vente **tourne plusieurs minutes** puis affiche… toujours « En attente de paiement ». Lu comme « c'est figé ». |
| d | **Icônes et HTMX servis par CDN** (`unpkg`), non précachés | `base.html:13-14` ; `sw.js:26-44` (same-origin seulement) | Sans internet, toutes les icônes disparaissent, les polices basculent en repli. Les formulaires fonctionnent, mais l'écran « a l'air cassé ». |

### 3.3 Trancher en cinq minutes — requêtes SQL

Plus fiable que le souvenir du testeur :

```sql
-- 1. Si aucune ligne (marins, captain, CM) → hypothèse principale démontrée.
SELECT role, module, level FROM role_permissions;

-- 2. Si rien n'est journalisé pendant la fenêtre du test alors que le testeur
--    affirme avoir cliqué → il n'a jamais franchi require_permission.
SELECT * FROM activity_logs WHERE module='captain' ORDER BY id DESC LIMIT 20;

-- 3. Des lignes pending_payment sans cashbox_movement_id pointent vers
--    le couple webhook / réconciliation plutôt que vers les permissions.
SELECT reference, status, payment_method, stripe_checkout_session_id,
       cashbox_movement_id FROM onboard_sales ORDER BY id DESC;
```

### 3.4 Questions à poser au testeur

**Bloc 1 — discrimine tout le reste :**
1. Avec quel compte et quel rôle le test a-t-il été fait (`marins`, `operation`, `administrateur`) ?
2. Voyiez-vous « Vente à bord » dans le menu, et la page s'ouvrait-elle avec la liste des navires ? *(Si oui + échec au clic → hypothèse principale confirmée.)*
3. Avez-vous vu une page **« 403 — Accès refusé »** ? À quel moment — en ouvrant un écran, ou en cliquant sur un bouton ?
4. Quel est le **tout premier bouton** qui n'a pas fonctionné ?
5. Quelqu'un a-t-il ouvert `/admin/permissions` avant le test pour activer « Modifier » sur (marins × captain) ?

**Bloc 2 — affichage :**
6. En arrivant sur `/captain/ventes`, avez-vous tapé le pavé du navire sans effet, ou utilisé le lien « Ouvrir → » ?
7. Les icônes du menu étaient-elles visibles, ou l'interface paraissait-elle « nue » ?
8. Avez-vous vu un écran blanc avec du texte entre accolades (`{"detail":"…"}`) ? Si oui, **quel texte exact** ?

**Bloc 3 — réseau et Stripe :**
9. Test à quai (wifi/4G port) ou en mer sous satellite ? Le reste de l'ERP répondait-il normalement au même moment ?
10. Des pages ont-elles tourné plus d'une minute ? Sur quel écran ?
11. Le bouton carte était-il actif (« 💳 Générer un lien CB ») ou grisé (« 💳 CB indisponible ») ?
12. Si un paiement CB a été tenté : le client a-t-il réussi à payer, et **qu'a affiché son téléphone juste après** ?
13. La vente est-elle restée « En attente de paiement » ? Est-elle passée « Payée » plus tard en rechargeant ?

**Bloc 4 — état résiduel :**
14. Une vente a-t-elle été créée (référence `VB-2026-00xx`) ou l'échec est-il survenu avant toute création ?
15. Y a-t-il eu un **encaissement réel** — de l'argent a-t-il changé de mains ?
16. Le catalogue contenait-il des produits avant le test, et dans quelle devise ?

---

## 4. Constats consolidés

Numérotation unifiée. Chaque constat a été relevé indépendamment par au moins un auditeur et re-vérifié dans le code.

### 🔴 Critiques — perte d'argent ou corruption irréversible

#### V-01 — Le lien Stripe survit à l'annulation et à la bascule en espèces → double débit du marin

**Faits.** Aucun appel à `stripe.checkout.Session.expire` n'existe dans le dépôt (recherche exhaustive : zéro occurrence). Or :
- `confirm_cash` (`onboard_sales_router.py:446-469`) règle en espèces **sans contrôle d'état ni expiration de la session** ;
- `cancel_sale` (`services/onboard_sales.py:309-315`) passe à `cancelled` sans expirer la session, et l'annulation est **explicitement autorisée** en `pending_payment` ;
- `create_checkout` (`:487,510`) autorise la régénération et **écrase** l'ancien identifiant de session, qui reste ouvert et payable.

**Scénario.** Vente `VB-2026-0007`, 60 EUR, lien CB généré et QR scanné. Le marin dit qu'il paiera en espèces → « Basculer en espèces » → caisse `+60 EUR`, vente `paid`. Trois minutes plus tard il finalise le paiement déjà engagé : **Stripe débite 60 EUR de sa carte**. Le webhook arrive, `settle_sale` voit `cashbox_movement_id` posé et retourne `False` **en silence** (`services/onboard_sales.py:248-252` : aucun log, aucun `activity_log`, aucune notification). **Le marin a payé 120 EUR pour 60 EUR d'achats**, la plateforme n'a aucune trace des 60 EUR chez Stripe, et V-06 garantit qu'aucun remboursement n'est possible dans l'application.

**Aggravant : la notice commandant prescrit ces gestes.** `docs/operations/06-vente-a-bord-notice-commandant.md` §6 recommande « Basculer en espèces » quand un lien CB est en attente, et §9 affirme qu'à l'annulation « **rien n'est encaissé** » — ce qui est faux. Ce n'est pas un cas limite : c'est le chemin nominal dès qu'un marin change d'avis.

**Correctif.** (1) Ajouter `expire_session()` à `stripe_checkout.py` et l'appeler dans `cancel_sale`, `confirm_cash` et en tête de `create_checkout` ; refuser le geste si l'expiration échoue et que la session est encore ouverte. (2) Poser `expires_at` à 30 min à la création plutôt que les 24 h par défaut. (3) Faire de la branche « déjà réglée » un **incident visible** : `activity_record` + notification siège avec le `payment_intent_id` de la charge orpheline — seul moyen de rembourser à froid.

---

#### V-02 — Les ventes carte sont écrites dans la caisse espèces → la variance de clôture est fausse tous les mois

**Fait vérifié directement.** `settle_sale` (`services/onboard_sales.py:259-270`) crée **inconditionnellement** un `CashboxMovement` `category="vente_a_bord"` de `+sale.total`, **sans aucune branche sur `payment_method`**. Or `OnboardCashbox` décrit de l'argent physique (« *Tracks day-to-day cash movements made by the captain or crew* », `models/onboard_cashbox.py:4-5`) et `close_month` (`services/cashbox.py:279-296`) calcule `variance = counted_balance − computed_balance`, où `counted_balance` est le **comptage physique des billets** saisi à l'écran (`cashbox/detail.html:110-115`).

**Scénario.** Mois M : 400 EUR de ventes espèces, 1 200 EUR de ventes CB, 300 EUR de décaissements. `computed_balance = 1 300 EUR`. Le commandant compte physiquement 100 EUR et saisit 100 → **`variance = −1 200 EUR`**. La clôture est archivée avec un écart structurellement inexplicable, **chaque mois**. Conséquence directe : le seul contrôle de caisse du module devient inexploitable, et **un vol d'espèces de 200 EUR est invisible** — il se noie dans un écart « normal ». Symétriquement, le livre affiche 1 300 EUR de trésorerie de bord qui n'existent pas, et les 1 200 EUR chez Stripe ne sont rapprochables d'aucune écriture.

Aggravant : les deux natures partagent la **même catégorie** `vente_a_bord`, donc l'écart n'est même pas rattrapable par filtrage sans jointure sur `onboard_sales.payment_method`.

**Nuance importante — c'est un choix documenté, pas une régression** : la notice §7 énonce « *Chaque vente réglée (espèces ou carte) crée un mouvement dans Caisse de bord* ». C'est donc un **défaut de conception assumé**, et sa correction relève de l'arbitrage (§7).

**Correctif proposé.** Colonne `CashboxMovement.medium` (`cash` / `card`, défaut `cash`, migration + backfill depuis `OnboardSale.payment_method`), exclusion de `medium='card'` du `computed_balance` et de la `variance`, conservation au journal sous un total « CB à rapprocher Stripe ». ⚠️ **Dépendance** : le verrou d'idempotence repose aujourd'hui sur `sale.cashbox_movement_id` ; si la voie carte cesse de créer un mouvement de caisse, **remplacer d'abord le verrou** (V-05) sous peine de casser l'idempotence en corrigeant la comptabilité.

---

#### V-03 — `NaN` et `Infinity` traversent les parseurs et se figent dans des registres sans route de suppression

**Fait vérifié directement.** `Decimal("nan")` et `Decimal("Infinity")` sont des littéraux **valides** : aucune `InvalidOperation`. Et `Decimal("nan") == 0` vaut **`False`**. Les deux parseurs ne testent jamais `is_finite()` :
- `onboard_sales_router.py:57-61` (`_parse_decimal`) ;
- `cashbox_router.py:138-140` (`abs(Decimal(amount.replace(",", ".")))`, sans quantification).

Conséquences par endpoint :

| Endpoint | Entrée | Effet |
|---|---|---|
| `POST /cashbox/{id}/movement` | `amount=nan` | La garde `if amount == 0` (`services/cashbox.py:82`) est franchie → `NaN` inséré. `balances()` et `close_month()` reposent sur `SUM(amount)` → **le solde de caisse du navire devient `NaN` définitivement**. |
| `POST /captain/ventes/{id}/stock` | `qty=nan` | Ligne `NaN` dans `onboard_stock_movements` (**registre douanier append-only**) → `stock_on_hand` et l'inventaire à `NaN` pour toujours. |
| `POST .../vente/{ref}/line` | `qty=nan` ou `Infinity` | `decimal.InvalidOperation` non attrapée (le routeur ne capture que `OnboardSalesError`, `:421`) → **HTTP 500**. |
| `POST .../catalogue/products` | `unit_price=Infinity` | Produit persisté avec un prix infini ; toute vente le référençant part en 500. |

PostgreSQL `numeric` accepte `NaN` et (depuis PG 14) `Infinity`. **Aucune route de suppression n'existe** sur `CashboxMovement` ni sur `OnboardStockMovement` : la correction ne peut se faire qu'en SQL direct en production.

*Réserve honnête : le round-trip `asyncpg` d'un `Decimal("NaN")` vers `numeric` n'a pas pu être exécuté (pas de Postgres dans l'environnement d'audit). Le comportement PostgreSQL est documenté ; un test `test_cashbox_rejects_nan_amount` doit le confirmer.*

**Correctif.** Un helper de parsing unique, appliqué **dans les services** et pas seulement dans les routeurs :
```python
def parse_amount(raw: str, *, min_value: Decimal | None = None) -> Decimal:
    try:
        v = Decimal((raw or "").strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(400, "Valeur numérique invalide") from None
    if not v.is_finite() or abs(v) >= Decimal("1e10"):
        raise HTTPException(400, "Valeur numérique invalide")
    if min_value is not None and v < min_value:
        raise HTTPException(400, "Valeur hors bornes")
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```
Doublé de `CHECK` en base (V-08) : un garde-fou applicatif seul ne protège pas un registre à valeur probante.

---

#### V-04 — Aucun mode hors-ligne, contrairement à ce qu'affirme la notice

**Fait vérifié directement.** `app/static/sw.js:80-89` limite `shouldHandle` à `/onboard`, `/onboard/*` et `/static/*` — et exclut d'emblée toute méthode autre que `GET`. **`/captain/ventes` et `/cashbox` sont hors périmètre**, et aucun de leurs 8 gabarits ne charge `pwa-onboard.js`. La file IndexedDB + Background Sync (`onboard-offline.js`) ne s'active que sur `<form data-offline-queue>` — attribut présent uniquement dans `staff/onboard/event_form.html` et `navigation.html`. **Aucun formulaire de vente ou de caisse ne l'utilise.**

Or la notice §5 affirme que l'espèce est « **le mode toujours disponible, même sans connexion** ». C'est faux : il n'existe **qu'un seul déploiement, à terre** (`docker-compose.yml:18-53` — le service `app` n'a aucun port publié, il n'est joignable que via Caddy). Le navire attaque un serveur à terre par son lien satellite ; sans lien, ni la vente espèces ni la caisse ne fonctionnent.

**Ce que voit l'utilisateur quand le POST d'encaissement échoue** — trois cas distincts, aucun n'étant applicatif :
1. **Lien coupé** → page d'erreur réseau **du navigateur** (« Vous n'êtes pas connecté »). Le SW n'intercepte pas : ni `offline.html`, ni rejeu. La vente n'a pas bougé et l'utilisateur ne sait pas si elle est passée.
2. **Serveur joignable, permission manquante** → page **403** sans explication (§3.1).
3. **Serveur joignable, refus métier ou Stripe injoignable** → **JSON brut** (§3.2 b).

**Correctif.** Court terme : corriger la notice et annoncer explicitement que le module exige le lien. Moyen terme : `data_offline-queue` sur le formulaire `confirm-cash` en premier (l'encaissement espèces n'appelle aucune API tierce — c'est le candidat le plus simple et le plus rentable), élargissement de `shouldHandle` à `/captain/ventes` et `/cashbox` en lecture, chargement de `pwa-onboard.js` sur les gabarits concernés.

---

### 🟠 Élevés

#### V-05 — L'idempotence n'est pas un verrou : deux règlements simultanés créent deux mouvements de caisse

`services/onboard_sales.py:247-273` — la garde `if sale.cashbox_movement_id is not None` lit un attribut d'un objet **déjà chargé en session**, sans `SELECT … FOR UPDATE` et sans re-lecture. En `READ COMMITTED`, deux transactions lisent toutes deux `NULL`, insèrent chacune un `CashboxMovement` et un jeu de sorties de stock, puis sérialisent sur un `UPDATE` **sans clause `WHERE cashbox_movement_id IS NULL`** : le second écrase l'id du premier mouvement, qui devient orphelin et indétraçable.

Le scénario n'est pas théorique : Stripe redélivre les webhooks, et `_reconcile_pending_card_payment` s'exécute **à chaque affichage** de la vente (V-10). Le commandant qui rafraîchit sa page pendant que le webhook arrive déclenche exactement ce cas. Aucun filet en base : `onboard_sales.cashbox_movement_id` n'a **pas** de contrainte `UNIQUE` (migration `0096:67,75-77` — FK seule).

**Le codebase sait déjà faire.** `services/packing_list.py:465` : `await db.get(BlNumberSequence, leg.id, with_for_update=True)`, avec le commentaire *« sérialise deux émissions simultanées […] c'est en production que cela compte »*. Idem `services/capacity.py:126`. Le module vente à bord n'a pas repris le pattern.

**Correctif.** (1) `with_for_update=True` en tête de `settle_sale`, avant la garde. (2) `UNIQUE (cashbox_movement_id)` en base comme filet. (3) Table `stripe_webhook_events(event_id PK)` : insertion en tête du webhook, `IntegrityError` → 200 immédiat — seule idempotence réellement au niveau événement.

Défaut jumeau : `next_reference` (`services/onboard_sales.py:55-67`) fait `SELECT max()` puis `INSERT` **sans verrou** → deux ventes simultanées produisent la même référence → `IntegrityError` sur `uq_onboard_sale_reference` → **500** pour l'un des deux commandants.

---

#### V-06 — Le statut « Remboursée » est documenté, promis aux Opérations, et n'existe dans aucun chemin de code

**Fait vérifié directement.** `"refunded"` est déclaré (`models/onboard_sales.py:80`) et **lu** par la garde de `settle_sale` (`:253`). Aucune ligne du dépôt ne l'**écrit** : aucune route, aucune fonction de service (recherche exhaustive sur `app/routers/` et `app/services/` : une seule occurrence, celle de la garde).

`cancel_sale` renvoie l'utilisateur vers « utilisez le remboursement » — fonctionnalité inexistante. La notice §9 renvoie au siège, qui **n'a pas d'écran non plus**. Le webhook ne traite ni `charge.refunded`, ni `charge.dispute.created` (`onboard_sales_router.py:783-787`) : un chargeback retire l'argent du compte Stripe sans qu'aucune écriture ne bouge.

L'existence du script `scripts/purge_onboard_sales_today.py` prouve le manque : la seule voie de correction est un accès SSH à la production.

**Conséquence.** Toutes les issues de V-01, V-07 et des chargebacks sont **irréparables dans l'application**. Le remboursement se fera dans le tableau de bord Stripe et le livre de la plateforme divergera définitivement du compte bancaire.

**Correctif.** Rail « remboursement » en miroir strict de `settle_sale` : `stripe.Refund.create` si CB, `CashboxMovement` **négatif** de contre-passation, mouvements de stock `retour`, passage à `refunded` + `refunded_at` + `refund_id`, `activity_record`. **Jamais par suppression** — cohérent avec l'append-only du registre. Permission : `finance:M` ou `captain:S`, pas `captain:M` (V-12). Puis traiter `charge.refunded` et `charge.dispute.created` côté webhook.

---

#### V-07 — Le webhook répond 200 quand le règlement échoue : Stripe ne réessaiera jamais

`onboard_sales_router.py:825-831` attrape `(OnboardSalesError, CashboxError)` — donc aussi `PeriodClosed` — journalise en `error` et **retourne**, puis `stripe_webhook` répond `{"received": True}` en 200 (`:788`). Stripe retire l'événement de sa file de retry.

**Scénario.** 31/07 à 23 h 50, un marin paie 80 EUR par carte. Le webhook arrive à 23 h 58 mais le commandant a clôturé juillet à 23 h 30 : `PeriodClosed`, log dans un fichier que personne ne lit, **200 OK**. La vente reste `pending_payment` pour toujours — la réconciliation à l'affichage rejouera le même `PeriodClosed`. **80 EUR encaissés chez Stripe, zéro écriture, zéro sortie de stock, zéro alerte**, et le registre douanier ne mentionne pas la marchandise sortie.

À noter : `get_db` commite sur succès. Un webhook qui « échoue » proprement commite tout de même les effets de bord déjà produits (typiquement l'`OnboardCashbox` créé par `get_or_create` juste avant l'échec).

**Correctif.** Distinguer l'échec **définitif** (vente annulée, total nul → 200 + notification siège) de l'échec **transitoire** (`PeriodClosed`, erreur DB → **500**, pour que Stripe réessaie selon sa politique de 3 jours). Et autoriser explicitement le règlement d'un paiement tombant dans une période clôturée en le datant du jour de réception, pour ne jamais perdre l'écriture.

---

#### V-08 — Zéro contrainte d'intégrité en base sur deux registres à valeur probante

Les vocabulaires sont déclarés en Python (`SALE_STATUSES`, `PAYMENT_METHODS`, `STOCK_REASONS`, `SUPPORTED_CURRENCIES`, `INCOME_CATEGORIES` / `EXPENSE_CATEGORIES`) et **aucun n'est adossé à un `CHECK`**.

| Manque | Référence | Conséquence |
|---|---|---|
| `CHECK status IN SALE_STATUSES` | `models/onboard_sales.py:177` | Statut arbitraire persistable |
| `CHECK payment_method IN ('cash','card')` | `:178` | idem |
| `CHECK currency IN (...)` (3 tables) | `:110,:179`, `onboard_cashbox.py:137` | Devise hors périmètre |
| `CHECK unit_price >= 0` | `:109` | **Prix négatif** (voir ci-dessous) |
| `CHECK line_total >= 0`, `qty > 0` | `:238-239` | Ligne négative |
| `CHECK qty <> 0` / `amount <> 0` | `:142`, `onboard_cashbox.py:136` | Mouvement neutre |
| **`UNIQUE (cashbox_movement_id)`** | `:191-193` | **Filet manquant contre V-05** |
| `CHECK category IN (...)` | `:139` | Catégorie libre en base |

**Ce n'est pas un standard absent du projet** : `models/packing_list.py` porte **12 `CheckConstraint`** (`:290 ck_bl_revision_positive`, `:548 ck_bl_sequence_non_negative`…) précisément parce que le registre BL a valeur probante. Le registre douanier de vente détaxée et la caisse de bord sont dans **exactement la même catégorie** et n'ont rien.

**Défaut lié — prix produit sans validation** (`onboard_sales_router.py:146,184`) : aucun contrôle de signe ni de borne. Un prix **négatif** est accepté et se propage en `line_total` négatif ; la garde `if sale.total <= 0` ne protège que le cas total, un panier mixte reste encaissable à un montant minoré arbitrairement.

**Divergences modèle ↔ migration** (mineures mais réelles) : `onboard_cashboxes.opened_at`, `cashbox_movements.recorded_at` et `cashbox_closures.closed_at` sont `nullable=False` dans les modèles mais **sans `nullable=False`** dans `20260518_0005_big_drop.py:28,41,57`.

**Correctif.** Migration additive posant ces `CHECK` / `UNIQUE`, **précédée d'une requête de contrôle des données existantes**. Priorité : `UNIQUE(cashbox_movement_id)` et les bornes de signe et de finitude, qui adressent V-05 et V-03 au niveau où ça compte.

---

#### V-09 — Aucun cloisonnement par navire, alors que le pattern existe dans le module voisin

`User.assigned_vessel_id` n'est utilisé, dans `onboard_sales_router.py:89-92`, que pour **une redirection de confort**. Aucune des routes `/{vessel_id}/…` (`:219,272,316,604,633`) ne vérifie que le navire demandé est celui de l'utilisateur. `cashbox_router.py` ne lit **jamais** ce champ.

C'est pourtant un critère de filtrage établi ailleurs : `onboard_router.py:343-344,712-715,1099`, `captain_router.py:776-778`, `services/cutoff_reminders.py:131`.

**Portée réelle.** Tout titulaire de `captain:C` — soit `operation`, `technique`, `armement`, `data_analyst`, `commercial`, `marins`, `manager_maritime`, `administrateur` — **lit les ventes, l'inventaire, le registre douanier et le solde de caisse de tous les navires**. `/cashbox` liste explicitement tous les navires. Tout titulaire de `captain:M` y **écrit** : le commandant du navire 1 peut clôturer et **verrouiller** la caisse du navire 2.

**Correctif.** Un `Depends` factorisé `require_vessel_access(vessel_id, user)` appliqué aux deux routeurs, laissant passer les rôles siège (`administrateur`, `armement`) et bornant les autres à `assigned_vessel_id`. `_get_sale_or_404` doit porter le contrôle (aujourd'hui il ne filtre que sur la référence, `:71-77`).

---

#### V-10 — Un règlement financier déclenché par un `GET`, sous permission de lecture seule

`onboard_sales_router.py:355-363` — `sale_detail` est un **`GET`** protégé par `require_permission("captain", "C")` (**Consult**) et appelle `_reconcile_pending_card_payment(db, sale, recorded_by_id=user.id)`, qui crée un `CashboxMovement`, bascule la vente en `paid` et écrit des sorties dans le registre douanier. `get_db` commite en fin de requête.

Trois problèmes distincts : (a) mutation sous permission de consultation ; (b) `GET` non idempotent, donc déclenchable par un prefetch navigateur ou un scanner de liens ; (c) c'est la moitié de la course de V-05.

**Scénario.** Un `data_analyst` (captain:C) ouvre une vente par curiosité. Un mouvement de caisse de 400 EUR est créé sur un navire dont il n'a pas la charge, **attribué à son `user.id`** — l'audit le désigne comme caissier.

Le mécanisme de réconciliation est **utile** ; c'est son emplacement qui est mauvais.

**Correctif.** Le sortir du `GET` : soit `POST /vente/{ref}/reconcile` en `captain:M` avec bouton explicite, soit une tâche cron protégée par token balayant les ventes `pending_payment` — le codebase a déjà ce pattern pour 7 crons. Et attribuer `recorded_by_id = None` plutôt que le lecteur de passage.

---

#### V-11 — Le webhook ne vérifie ni le montant, ni la devise, ni l'environnement

`_settle_from_session` (`:812-841`) lit `payment_status` et `payment_intent` ; **`amount_total`, `currency` et `livemode` ne sont lus nulle part dans le dépôt**. `_find_sale_from_session` (`:791-809`) résout par `metadata.sale_id` en priorité et **ne vérifie jamais** que `obj["id"] == sale.stripe_checkout_session_id`. La métadonnée posée (`stripe_checkout.py:141`) ne contient pas d'`env`. Le montant porté en caisse est `sale.total`, **jamais** le montant réellement collecté.

**Scénario (mésconfiguration, pas exploit).** Un même compte Stripe sert staging et production, les deux endpoints webhook sont enregistrés — cas classique. Un compte Stripe diffuse chaque événement à *tous* ses endpoints du même mode. Un test staging solde `sale_id = 42` pour 1,00 EUR ; l'événement, **signé validement**, arrive aussi en production ; la **vente 42 de production** (400 EUR, en attente) est soldée : `+400 EUR` en caisse, stock décrémenté, alors qu'aucun euro n'a été collecté pour elle.

Défaut connexe : `is_configured()` ne teste que `stripe_secret_key` (`config.py:169-171`). Avec la clé posée et `STRIPE_WEBHOOK_SECRET` **absent**, l'UI propose « Générer un lien CB », les cartes sont réellement débitées, et le webhook répond 503 à chaque livraison. Aucun contrôle non plus qu'une clé `sk_test_…` ne tourne pas en production.

**Correctif.** Avant `settle_sale`, exiger : `obj["id"] == sale.stripe_checkout_session_id`, devise identique, `amount_total == amount_to_minor(sale.total, sale.currency)`, `livemode` cohérent avec `app_env`, `metadata["env"]` cohérent. Sur écart : ne pas régler, `logger.error` + `activity_record` + notification siège. Faire de `stripe_enabled` un `bool(secret) and bool(webhook_secret)`. Ajouter un validateur pydantic refusant `sk_test_` en production.

---

### 🟡 Moyens

| # | Constat | Référence | Impact |
|---|---|---|---|
| V-12 | **Aucune séparation des tâches** : `close_period` exige `captain:M`, **la même cellule** que `add_mov` et `confirm_cash`. Le même compte encaisse, compte, clôture, verrouille et produit l'export. Le module Caisse est rattaché à `captain`, pas à `finance`. | `cashbox_router.py:262-269,128` | Contrôle interne minimal absent ; combiné à V-02 l'écart est illisible |
| V-13 | **Fusion de ligne re-tarife rétroactivement** : `existing.unit_price = unit_price` réécrit le prix depuis le catalogue courant. 2 unités à 10 € puis +1 après passage à 15 € donne **45 €** au lieu de 35 €, vidant de son sens le « snapshot » revendiqué par le docstring | `services/onboard_sales.py:197-201` | Montant facturé faux, silencieux |
| V-14 | **`activity.record()` absent sur 4 mutations**, dont `add_sale_line` et `delete_sale_line` — l'audit enregistre la création de la vente et son encaissement, **mais pas la composition du panier entre les deux**. Sur un registre de vente détaxée, c'est la maille qu'un contrôle douanier demande | `onboard_sales_router.py:202,407,426,844` | Trou d'audit sur le registre probant |
| V-15 | **Dates invalides silencieusement ignorées** : `except ValueError: pass` en caisse (mouvement daté du jour sans signal), et `_parse_date` renvoyant `None` désactive le filtre de période du **registre douanier et de son export** — l'utilisateur croit exporter un mois, il exporte tout | `cashbox_router.py:143-150`, `onboard_sales_router.py:660-666` | Antidatage impossible ; export trompeur |
| V-16 | **500 sur entrées triviales** : `month=13` → `IllegalMonthError` ; `year=0` → `ValueError` ; deux clôtures concurrentes → `IntegrityError` sur `uq_closure_period` | `cashbox_router.py:238,266` ; `services/cashbox.py:47,259-269` | Robustesse |
| V-17 | **Solde de caisse négatif possible** (aucun contrôle de disponibilité dans `add_movement`) et **antidatage avant une période jamais clôturée** accepté, décalant le cumul reporté | `services/cashbox.py:65-122` | Intégrité comptable |
| V-18 | **Appels Stripe sans timeout ni version d'API épinglée** : SDK 11.4.1 par défaut à ~80 s × 2 retries. L'affichage du détail d'une vente — écran courant — se bloque jusqu'au timeout sur liaison dégradée | `stripe_checkout.py:160-173` | Cause probable du ressenti « c'est figé » (§3.2 c) |
| V-19 | **Le stock n'est jamais consulté au moment de vendre** : `add_line` ne lit pas `stock_on_hand` et l'écran ne l'affiche pas. Le solde négatif n'apparaît qu'après coup. Le projet a arbitré ce cas plus prudemment ailleurs (A3 stowage : « avertir par défaut + blocage dur configurable ») | `services/onboard_sales.py:165`, `sale.html:62` | Stock part en négatif sans alerte |
| V-20 | **Signe du mouvement non contrôlé contre son motif** : un `avitaillement` (entrée, positive par définition) avec `qty=-5` est accepté et s'affiche en colonne **Sortie** avec le libellé « Avitaillement (entrée) » — le registre s'auto-contredit | `services/onboard_sales.py:115-117` | Registre incohérent |
| V-21 | **Arrondi VND** : `total` et `line_total` sont en `Numeric(12,2)` alors que `amount_to_minor` traite le VND comme zéro-décimale. Une ligne à 100 000,50 VND est **facturée 100 001** et **comptabilisée 100 000,50**. Écart cumulatif ligne par ligne | `models/onboard_sales.py:180,239` vs `stripe_checkout.py:73-74` | Divergence Stripe ↔ caisse |
| V-22 | **`checkout.session.expired` ressuscite une vente annulée** : `revert_to_draft` ne sort tôt que si `is_settled or status == "paid"` ; une vente `cancelled` repasse `draft`, redevient encaissable, avec un `cancelled_at` incohérent | `services/onboard_sales.py:318-324` | Encaissement sans contrepartie |
| V-23 | **Métier fuité dans les routeurs** : transition d'état écrite dans `create_checkout` (`:510-513`) alors que toutes les autres vivent dans le service ; ~145 lignes de logique Stripe dans le routeur (`:701-847`) ; `close_period` écrit le CSV **avant** la clôture — si `close_month` échoue, le fichier reste orphelin | `onboard_sales_router.py:510,701-847` ; `cashbox_router.py:276-307` | Testabilité, cohérence |
| V-24 | **Piège de devise** : le formulaire « Nouvelle vente » propose EUR/USD/VND sans filtre, mais `add_line` refuse tout produit d'une autre devise. Une vente ouverte en USD sans produit USD est un **cul-de-sac** : annuler et recommencer | `vessel.html:24-29` vs `onboard_sales_router.py:377-390` | Blocage terrain |
| V-25 | **Un lien CB non payé fige la vente** : aucune sortie opérateur vers le brouillon (`revert_to_draft` n'est appelé **que** par le webhook `expired`). Sans webhook, la vente n'est plus jamais modifiable | `onboard_sales_router.py:785-786,844-847` | Blocage terrain |
| V-26 | **`_default_leg_id` prend le dernier leg par `id`**, pas le leg courant à la date de la vente. Le `leg_id` des ventes et des mouvements de caisse est **structurellement peu fiable** — à corriger **avant** de construire tout reporting dessus | `onboard_sales_router.py:64-68` | Donnée d'imputation déjà corrompue |
| V-27 | **N+1 et écriture sur `GET`** : `cashbox_index` boucle sur tous les navires en appelant `get_or_create` (INSERT) puis `balances` — 2 requêtes par navire et une création de caisse sur simple consultation | `cashbox_router.py:62-65` | Performance, effet de bord |
| V-28 | **`SITE_URL` par défaut `http://localhost:8000`** : le QR pointe bien vers `checkout.stripe.com` (le paiement aboutit), mais `success_url` / `cancel_url` renvoient le téléphone du client vers `localhost` → « site inaccessible » après un paiement **réussi**. Tout le monde conclut à un échec | `.env.example:9`, `config.py:41`, `onboard_sales_router.py:497-504` | Diagnostic faussé en test CB |
| V-29 | **`/webhooks/` non exempté du mode maintenance** : pendant un déploiement, les événements Stripe reçoivent une page HTML 503. Récupérable par retry, sauf combiné à V-07 | `middlewares/maintenance.py:22,44` | Perte d'événement |
| V-30 | **Exports CSV** : ni BOM UTF-8 (accents cassés dans Excel FR), ni neutralisation d'injection de formule (`=`, `+`, `-`, `@` en tête de cellule) sur `note` / `description` / `label`. Écart codebase-wide, pas propre au module | `services/onboard_sales.py:392`, `services/cashbox.py:199` | Qualité d'export |

**Constats mineurs 🟢** *(non détaillés)* : `onclick` inline bloqué par la CSP (10 occurrences dans `app/templates/`, endémique) ; filtre `|money` non utilisé au profit de `"%.2f"|format` (19 occurrences, affichage VND faux) ; paramètre mort `currency` dans `_create_session_sync` ; relation `OnboardStockMovement.product` inutilisée (piège `MissingGreenlet` en async) ; constantes mortes `PRODUCT_KINDS`, `MOVEMENT_CATEGORIES` ; `register_rows` sans `LIMIT` ; `revert_to_draft` efface la trace de session ; `update_product` ne peut corriger ni `kind`, ni `currency`, ni `tracks_stock` ; ordre de routes fragile (`/catalogue` avant `/{vessel_id}`) non verrouillé par un test ; quantités affichées en `%.3f` (« 1.000 pièce ») alors que `checkout.html:60` fait déjà `.normalize()` ; cibles tactiles `.btn-sm` ≈ 24 px sur des actions critiques.

---

## 5. Couverture fonctionnelle métier

### 5.1 Constat de méthode

**Il n'existe aucun cahier des charges pour ce module.** Recherche exhaustive : `docs/legacy/captain/onboard-v2-spec.md` et `audit.md` ne contiennent **aucune** occurrence de « vente », « caisse » ou « boutique » ; `docs/strategy/` n'a pas de CDC dédié. La seule documentation, `docs/operations/06-vente-a-bord-notice-commandant.md`, a été rédigée **après** le code. Le module a été **spécifié par son implémentation**, en un lot de 4 commits.

**Conséquence** : l'écart ressenti n'est pas une régression, c'est la distance entre l'attente implicite du métier (un point de vente + une caisse d'agence) et un MVP jamais cadré.

### 5.2 Matrice de couverture

Légende : ✅ complet · 🟡 partiel · ❌ absent

| Domaine | Besoin | État | Preuve | Priorité |
|---|---|---|---|---|
| **Catalogue** | Créer / activer / désactiver un article | ✅ | `router:123,202` | — |
| | **Modifier le prix ou le libellé** | ❌ | Route `:170` **orpheline** — aucun formulaire de `catalogue.html:57-90` ne la cible | **P0** |
| | Corriger devise / type / suivi de stock | ❌ | `:170-186` ne touche que 4 champs | P1 |
| | Catégories, TVA, photos, codes-barres, prix par navire | ❌ | Aucun champ (`models:100-116`) | P1–P2 |
| | Prix multi-devises d'un même article | ❌ | 1 produit = 1 devise ; vendre un café en EUR **et** VND = 2 articles, 2 stocks, inventaire faux | P1 |
| **Inventaire** | Grand livre append-only signé | ✅ | `models:131-160` | — |
| | Entrée d'avitaillement | ✅ | `router:272` | — |
| | Écran de comptage (saisir le physique, écart calculé) | 🟡 | Motif `inventaire` accepté mais **le commandant fait l'arithmétique à la main** (`vessel.html:66-69`) | P1 |
| | **Antidater un mouvement** | ❌ | `occurred_at` non exposé ; forcé à `now()` (`services:124`) → registre chronologiquement faux | P1 |
| | Seuil d'alerte, valorisation, vue flotte | ❌ | Aucun champ, aucune agrégation | P1–P2 |
| | Stock négatif | 🟡 | Jamais bloqué (décision assumée), affiché en rouge — **mais aucune alerte, aucune relance** | P1 |
| **Vente** | Panier multi-lignes, prix figé serveur | ✅ | `services:165,186` | — |
| | **Remise / gratuité / prix libre / ligne hors catalogue** | ❌ | Aucun champ ; `product_id` est nullable mais **aucune route ne crée de ligne libre**. Contournement : créer un faux produit | **P0** |
| | **Ticket ou reçu remis à l'acheteur** | ❌ | Aucune route, aucun gabarit PDF. Le marin paie et **ne reçoit rien** | **P0** |
| | **Remboursement / avoir** | ❌ | V-06 | **P0** |
| | Annulation avant règlement | ✅ (mais V-01) | `router:576` | — |
| | Historique / recherche | 🟡 | 50 dernières, **sans filtre ni pagination** | P1 |
| | Typologie d'acheteur (équipage / passager / client) | ❌ | `buyer_name` texte libre, non lié au RH ni au CRM → franchise non justifiable par la qualité de l'acheteur | P1 |
| | Numérotation non recyclable | ❌ | `MAX(reference)+1` : après purge, le numéro **est réattribué**. Contraire à l'exigence appliquée au BL cargo | P1 |
| **Encaissement** | Espèces, CB Stripe + QR | ✅ | — | — |
| | **Rendu de monnaie** (montant remis / rendu) | ❌ | Aucun champ → aucune trace du liquide reçu, écart de caisse inexplicable | P1 |
| | Paiement mixte, pourboires | ❌ | `payment_method` scalaire | P2 |
| **Caisse** | Mouvements signés, soldes par devise, 14 catégories | ✅ | `services/cashbox.py:65,125` | — |
| | Justificatif photo mobile | ✅ | `detail.html:66-70` | — |
| | **Corriger un mouvement erroné** | ❌ | **Aucune route** UPDATE/DELETE. Contournement : contre-écriture dans une catégorie fourre-tout | **P0** |
| | **Fond de caisse / ouverture** | ❌ | Pas de solde initial ; toute dotation doit être maquillée en encaissement | P1 |
| | **Clôture journalière / par escale / à la relève** | ❌ | Seul `close_month`, bornes strictement mensuelles. Le rythme comptable est mensuel, le rythme opérationnel est l'escale | **P0** |
| | **Passation de caisse au changement d'équipage** | ❌ | Aucun hook sur la clôture d'escale ni sur `crew_assignments`. **La caisse n'a pas de détenteur** — personne n'est responsable d'un écart | **P0** |
| | Comptage physique + écart | 🟡 | Un champ global par devise à la clôture ; pas de détail par coupure, pas de motif, pas de validation siège | P1 |
| | Remise en banque, transfert entre caisses, taux de change | ❌ | Absents des catégories ; 3 soldes juxtaposés, jamais de contre-valeur EUR | P1–P2 |
| | Rapprochement ventes ↔ caisse | 🟡 | Le lien existe **en base** (`cashbox_movement_id`) mais **aucun écran ne le montre** ; pas de deep-link, pas de rapport | P1 |
| **Registre douanier** | Append-only, filtre période, export CSV | ✅ (de fait) | `services:347-389` | — |
| | Garde-fous d'un registre probant | ❌ | Pas de chaînage SHA-256, **pas dans `NEVER_PURGE_TABLES`**, pas de trigger anti-UPDATE — à comparer à `rate_offer_revisions` | P1 |
| | **Valeurs / montants** dans le registre | ❌ | Quantités seules ; un registre de franchise **sans valeur** est difficilement opposable | P1 |
| | Mentions légales, PDF opposable, IMO/MMSI, visa commandant | ❌ | CSV brut. À comparer au soin apporté au BL | P1 |
| | `regime` écrit **en dur** `"franchise"` au lieu de `sale.regime` | ❌ | `services:385` — si un second régime apparaît, le registre mentira sans rien casser | P1 |
| **Reporting** | **CA par navire / leg / période** | ❌ | **Aucune agrégation `SUM(OnboardSale.total)` dans tout `app/`**. Le siège ne peut pas répondre à « combien la boutique a vendu ce mois-ci ? » | **P0** |
| | Marge, produits les plus vendus | ❌ | Pas de prix de revient | P1 |
| | **Consolidation Finance / KPI** | ❌ | Zéro référence à `OnboardSale` dans `finance_router.py` et `kpi_router.py` — revenu réel hors périmètre de pilotage | P1 |
| **Accès** | Le commandant peut vendre out-of-the-box | ❌ | §3.1 | **P0** |
| | Cloisonnement par navire | ❌ | V-09 | **P0** |
| | Fonctionnement hors connexion | ❌ | V-04 | **P0** |
| | Entrée depuis la page d'accueil du bord | ❌ | `staff/onboard/landing.html` ne propose que `/cashbox` — `/captain/ventes` est **absent** de l'écran que le commandant utilise réellement | P1 |

### 5.3 Verdict fonctionnel

Le périmètre couvert est **une tranche verticale minimale** qui fonctionne de bout en bout : créer un article → entrer du stock → ouvrir une vente → ajouter des lignes → encaisser → alimenter la caisse → tracer au registre → exporter.

**Ce qui manque n'est pas de la périphérie : c'est la boucle du jour 2** — corriger, rembourser, prouver (reçu), clôturer au bon rythme, transmettre la responsabilité, rendre compte. Un point de vente et une caisse d'agence sans ces cinq fonctions sont exploitables **en démonstration**, pas en production.

**Point de gouvernance** : le statut « ✅ » de ce module dans `CLAUDE.md` et dans la référence stratégique devrait être requalifié en « **MVP — non exploitable en production** ». Laisser un ✅ sur un module dont la boucle de correction est absente produit exactement la surprise exprimée par le commanditaire.

---

## 6. Expérience utilisateur — note 4/10

### 6.1 Parcours reconstitués

**Vente espèces, 1 article** — 3 taps + 1 confirmation = **4 interactions, 3 rechargements de page complets**, aucune donnée conservée côté client. Un TPE standard fait la même chose en **2 interactions, 1 écran, 0 rechargement**. Ajouter un 2ᵉ article coûte +2 interactions et +1 rechargement : il n'y a pas de panier au sens POS, chaque ligne est un POST suivi d'un rendu complet de page (sidebar, topbar et historique retransmis à chaque fois).

**Vente carte** — 3 taps, **dépendance réseau à 2 moments critiques** (génération du lien, paiement du client) et **0 feedback temps réel** : `checkout.html:33-37` demande explicitement à l'utilisateur de recharger la page pour savoir si le client a payé. Aucun polling, aucun `meta refresh` — alors que le backend fait l'effort de réconcilier à l'affichage.

### 6.2 Frictions principales

| Sév. | Friction | Référence |
|---|---|---|
| 🔴 | Aucune capacité hors-ligne (V-04) — le défaut le plus disqualifiant pour un outil dont la promesse est l'usage en mer | `sw.js:80-89` |
| 🔴 | Erreurs en JSON brut, sans page ni bouton retour (§3.2 b) | `main.py:203-223` |
| 🔴 | Encaissement CB doublement dépendant d'un réseau vivant, sans état « lien expiré / génération échouée » | `onboard_sales_router.py:499-509` |
| 🟠 | Pas de recherche ni de grille produit — un `<select>` natif, scroll interminable au-delà de ~15 articles, aucun raccourci « articles fréquents » | `sale.html:60-64` |
| 🟠 | Rechargement complet à chaque ligne — **zéro `hx-` dans les 8 gabarits** alors qu'HTMX est la norme du projet | `sale.html:56` |
| 🟠 | **9 tableaux sur 9 sans `.table-scroll` ni `.data-table`** — le composant existe, est documenté (`kairos.css:1602-1606`) et est utilisé dans `commercial/`, `cargo/`, `escale/`. Ici les tableaux (jusqu'à 9 colonnes au registre) font défiler **toute la page** horizontalement sur mobile | `catalogue.html:60`, `registre.html:37`, `cashbox/detail.html:124,146`… |
| 🟠 | Aucune actualisation du statut CB, malgré le texte qui l'annonce | `checkout.html:33-37` |
| 🟠 | Édition de produit orpheline : endpoint existant, aucune UI | `router:170` vs `catalogue.html` |
| 🟠 | Page navire = 3 tâches empilées sans onglets ni ancres — après une vente, il faut re-scroller tout en haut | `vessel.html:17-117` |
| 🟡 | Cibles tactiles ≈ 24 px (`.btn-sm`) sur la suppression de ligne, ≈ 35 px sur `.btn` — sous les 44 px recommandés pour un usage debout, une main prise | `kairos.css:1763`, `sale.html:41` |
| 🟡 | Cartes cliquables sans support clavier (`onclick` sur `<article>`, pas de `tabindex`) | `hub.html:16` |
| 🟡 | Textes 100 % français en dur, `t()` jamais appelé — **dette commune à tout le back-office**, pas propre à ce module, mais l'équipage est explicitement international | 8 gabarits |

### 6.3 Quick wins UX (< 1 jour chacun)

1. Confiner les 9 tableaux dans `<div class="table-scroll">` + `class="data-table"` — mécanique, < 1 h.
2. Handler générique `StarletteHTTPException` rendant une page Kairos avec message et bouton retour — **gain transverse à toute l'app** pour un coût faible.
3. Formulaire d'édition produit en modal (`loadModal()` déjà disponible) branché sur la route existante.
4. Polling léger (5–10 s) sur `checkout.html` avec redirection auto dès `paid`.
5. `data-offline-queue` sur le formulaire `confirm-cash` — première brique hors-ligne, sans dépendance tierce.
6. `min-height: 44px` sur `.btn` pour les actions d'encaissement.
7. Remplacer les pavés `onclick` par des `<a>` englobants (corrige aussi le blocage CSP).
8. `.normalize()` sur les quantités (pattern déjà présent dans `checkout.html:60`).

---

## 7. Tests — couverture réelle

**Exécution réelle** (dépendances installées par l'auditeur) :
```
python3 -m pytest tests/unit/test_cashbox_service.py tests/unit/test_onboard_sales_service.py \
  tests/unit/test_onboard_sales_templates_compile.py tests/integration/test_onboard_sales.py -q
→ 25 passed in 4.55s
```

**Couverture mesurée :**

| Fichier | Couverture |
|---|---|
| `services/onboard_sales.py` | 72 % |
| `services/stripe_checkout.py` | 63 % |
| `services/cashbox.py` | 53 % |
| `routers/onboard_sales_router.py` | 31 % |
| **`routers/cashbox_router.py`** | **0 % — jamais importé par la suite** |

**Constat structurel** : **aucun test du périmètre ne passe par la couche HTTP.** Les tests appellent directement les coroutines de route en Python, court-circuitant `require_permission`, le middleware CSRF, le parsing FastAPI et les codes de statut réels. Le pattern `TestClient` existe pourtant ailleurs dans le dépôt (`tests/unit/test_csrf.py`, `tests/integration/test_marad_flgo.py`).

Conséquence directe : **aucun test ne vérifie qu'un rôle non autorisé reçoit 403** — c'est précisément le défaut qui a fait échouer le test à bord.

**Trous critiques** : `construct_event` (vérification de signature webhook — **la seule barrière d'authentification de cet endpoint public**) totalement non couvert ; la route `POST /webhooks/stripe` jamais invoquée ; `close_month` / `close_period` (opération irréversible qui verrouille des mouvements) **zéro test** ; `balances`, `recent_movements`, `period_movements`, `revert_to_draft`, `current_inventory` : zéro test ; permissions, CSRF, 503 sans clé Stripe, montant divergent, concurrence, VND de bout en bout, arrondis aux limites : zéro test.

**Faiblesses des tests existants** : chemin nominal quasi exclusif ; `test_cancel_only_when_unsettled` ne teste que l'échec, **le succès de `cancel_sale` n'est couvert nulle part** ; les mocks Stripe (`_create_session_sync`, `retrieve_session` monkeypatchés intégralement) masquent le comportement réel ; la fixture `_setup_sale` est toujours mono-produit, EUR, stock suffisant — aucune variation. Point positif : les assertions présentes sont fortes (valeurs précises, pas de `assert 200` isolé), aucun test tautologique repéré.

**Backlog P0** (avant toute mise en production) : webhook via vraie route avec signature valide / invalide / rejeu ; 503 sans clé et sans secret webhook ; 403 pour un rôle sans `captain:M` ; **`marins` bloqué sans override puis autorisé avec** ; rejet CSRF ; prix ≤ 0 refusé ; `NaN` refusé (caisse et stock) ; quantité ≤ 0 refusée ; `settle_sale` sur vente annulée ou total nul ; `close_month` (verrouillage, variance, période clôturée).

---

## 8. Plan de remédiation

Ordre imposé par les dépendances techniques, pas par la sévérité seule.

| Lot | Branche proposée | Contenu | Effort | Risque | Bénéfice |
|---|---|---|---|---|---|
| **0 — Déblocage immédiat** | *(configuration, pas de code)* | Override `(marins × captain) = CM` posé **et** documenté (idéalement par migration/seed) ; `SITE_URL` correct ; `/captain/ventes` ajouté à `staff/onboard/landing.html` ; correction de la notice sur le hors-connexion et sur « rien n'est encaissé » | ½ j | Nul | **Sans lui, aucun test à bord ne peut avoir lieu** |
| **1 — Anti-corruption** | `fix/vente-bord-parsing-montants` | V-03 (parsing durci `is_finite`) + V-08 partiel (`CHECK` finitude et signe) + V-16 (bornes `year`/`month`) + V-15 (dates invalides refusées) | ~1 j | Faible, localisé | Supprime les chemins de corruption irréversible. **Indépendant de Stripe** — s'applique aussi à un pilote espèces seul |
| **2 — Intégrité des sessions Stripe** | `fix/vente-bord-sessions-stripe` | V-01 (`Session.expire` + notification d'incident) + V-22 + V-11 partiel (`stripe_enabled` complet, garde clé test) + V-18 (timeouts) | ~1,5 j | Moyen | Ferme la double-charge **avant** toute mise en service de la CB |
| **3 — Durcissement du webhook** | `fix/vente-bord-webhook` | V-11 (montant / devise / session / `livemode` / `env`) + V-05 (`with_for_update` + `UNIQUE` + table d'événements) + V-07 (500 sur échec transitoire) + V-29 | ~1,5 j | Moyen | Dépend du lot 2 pour la sémantique « session attendue » |
| **4 — Socle données** | `fix/vente-bord-contraintes-db` | V-08 complet + alignement modèle ↔ migration + V-20 (signe vs motif) + prix ≥ 0 | ~1 j | Faible, **mais exige un contrôle des données existantes avant pose** | Verrouille les registres au bon niveau |
| **5 — Confiance opérationnelle** | `feat/vente-bord-recu-remboursement` | Reçu PDF WeasyPrint (socle `templates/pdf/` existant) + V-06 (remboursement par contre-passation) + correction de prix produit en UI + ligne libre / remise | ~2,5 j | Moyen — **cadrage métier requis** | Rend le module réellement exploitable |
| **6 — Gouvernance du cash** | `feat/caisse-gouvernance` | V-02 (séparation espèces/CB) + détenteur de caisse + clôture ponctuelle à la relève + contre-écriture de correction + V-17 | ~3 j | **Élevé — arbitrage requis (§9)** | Rend le contrôle de caisse opérant |
| **7 — Cloisonnement & audit** | `fix/vente-bord-cloisonnement` | V-09 + V-10 (réconciliation hors `GET`) + V-12 (clôture sous `finance:M`) + V-14 (traces manquantes) | ~1 j | Faible techniquement, **arbitrage sur la visibilité des Ops à terre** | Aligne le module sur le reste de l'ERP |
| **8 — Pilotage** | `feat/vente-bord-reporting` | V-26 (`_default_leg_id` — **à corriger avant** tout reporting, sinon la donnée continue de se corrompre) + CA par navire / période / article + consolidation Finance/KPI | ~2 j | Faible | Rend la boutique visible du siège |
| **9 — UX & dette** | `fix/vente-bord-ux` | Les 8 quick wins UX + V-13, V-19, V-21, V-23, V-24, V-25, V-27, V-30 + constats 🟢 | ~2,5 j | Faible | Ramène le module au niveau `cargo` / `commercial` |
| **10 — Tests** | `test/vente-bord-couverture` | Backlog P0 du §7, avec passage par `TestClient` | ~2 j | Faible | Ferme la faille qui a laissé passer le défaut de permission |

**Séquencement recommandé pour un pilote à bord rapide** : lot 0 → lot 1 → lot 10 (P0 permissions et `NaN`) → pilote **espèces seules** avec `STRIPE_SECRET_KEY` vide → lots 2-3 → réouverture de la voie CB.

---

## 9. Points d'arbitrage — à ne pas trancher seul

Trois décisions engagent l'armateur ou la direction financière, pas l'équipe technique :

1. **Séparation caisse espèces / encaissements CB (V-02).** La correction change la **définition comptable** de la caisse de bord et le sens de la variance de clôture. Le comportement actuel est documenté dans la notice : le corriger contredit une procédure déjà diffusée. → **Direction financière**, avec un ADR à l'appui.
2. **Cloisonnement par navire (V-09).** Aujourd'hui les Opérations à terre voient et modifient tous les navires. Le corriger change leur visibilité quotidienne. → **Opérations + Armement**.
3. **Délégation du remboursement (V-06).** Qui a le droit de rembourser — le commandant à bord, ou le siège seul ? Quelle trace douanière pour une marchandise revenue en stock ? → **Siège + Opérations**.

Deux questions de fond restent ouvertes et méritent d'être posées avant de développer davantage :

- **Valeur probante du registre douanier** : faut-il y porter les montants, produire un PDF opposable avec IMO/MMSI et visa du commandant, et le chaîner en SHA-256 comme `rate_offer_revisions` ? C'est un engagement du transporteur.
- **Régime de taxe** : `regime="franchise"` en dur ne tiendra pas à l'ouverture du service passagers 2027 (`/passagers`, `Vessel.capacity_pax`) — une vente à un passager n'est pas un avitaillement d'équipage.

**Recommandation de méthode** : une réunion de 45 minutes avec les Opérations et l'Armement pour cadrer le besoin réel vaut plus que n'importe quel développement d'ici la prochaine relève. Le module n'a jamais eu de cahier des charges (§5.1) ; c'est la cause racine de l'écart ressenti.

---

## 10. Checklist de mise en service — avant le prochain test à bord

À exécuter **à terre**, dans cet ordre.

**Phase 1 — Serveur**
1. `/tmp/.maintenance` absent → `curl -s https://<domaine>/health` renvoie `{"status":"ok"}`.
2. `.env` : **`SITE_URL=https://<domaine public>`** (jamais `localhost`). Redémarrer l'app.
3. Migrations Alembic à jour (`20260706_0096_onboard_sales` appliquée).
4. *(CB uniquement)* `STRIPE_SECRET_KEY` **et** `STRIPE_WEBHOOK_SECRET` renseignés ; endpoint `https://<domaine>/webhooks/stripe` déclaré chez Stripe avec `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired` ; « Send test webhook » → 200.

**Phase 2 — Données de référence (rôle `administrateur` obligatoire)**

5. `/admin/vessels` : le navire de test existe et est actif.
6. **`/admin/permissions` : cellule `(marins × captain)` = `CM`. ← l'étape qui manquait.** Attendre 60 s (cache).
7. `/admin/users` : compte du commandant `is_active`, rôle `marins`, `assigned_vessel_id` renseigné.
8. Mot de passe déjà changé (`must_change_password = False`). MFA sans objet pour `marins`.

**Phase 3 — Vérification par le code (30 secondes)**

9. `docker compose exec app python scripts/check_user.py <username>` — **aucune ligne d'avertissement ne doit subsister.**

**Phase 4 — Répétition à quai, bon réseau, sur le compte du commandant**

10. Le menu Opérations → Vente à bord est visible.
11. `/captain/ventes/catalogue` → créer **un** produit en **EUR**, « Suivre le stock » coché. *(403 ici ⇒ l'étape 6 n'a pas pris.)*
12. Espace navire → mouvement de stock `+10`, motif *avitaillement*.
13. Nouvelle vente **en EUR** → ajouter le produit → **Encaisser en espèces**.
14. Vérifier la chaîne complète : statut **Payée** · mouvement `+` dans `/cashbox/{id}` catégorie « Vente à bord » · ligne de sortie au **Registre douanier** · export CSV téléchargeable.
15. *(CB)* Payer avec une carte de test → vérifier que **le téléphone atterrit sur une page NEWTOWT accessible** (contrôle de l'étape 2) et que la vente bascule **Payée** sans intervention (contrôle de l'étape 4).

**Phase 5 — Cadrage du test en mer**

16. Annoncer explicitement : **le module exige le lien satellite**, il n'existe **aucun mode hors-ligne**. En cas de coupure, ne rien ressaisir avant retour du réseau et vérification de l'état de la vente.
17. Fournir un canal de remontée (photo d'écran) : une erreur en **JSON brut** est un défaut connu, pas une fausse manœuvre du commandant.
18. **Ne pas utiliser « Basculer en espèces » ni « Annuler » sur une vente dont le lien CB a été affiché** tant que V-01 n'est pas corrigé — risque de double débit réel.

---

## 11. Synthèse chiffrée

| | Nombre |
|---|---|
| 🔴 Critiques | 4 |
| 🟠 Élevés | 7 |
| 🟡 Moyens | 19 |
| 🟢 Mineurs | ~14 |
| Besoins métier P0 non couverts | 10 |
| Effort de remédiation estimé | ~18 jours-homme (lots 0 à 10) |
| Effort minimal pour un pilote espèces encadré | ~2 jours (lots 0, 1 et P0 du lot 10) |

---

*Audit conduit en multi-agents (6 auditeurs indépendants : architecture, sécurité & intégrité financière, couverture fonctionnelle, UX terrain, QA, conditions réelles), puis consolidé et re-vérifié. Aucun code n'a été modifié. Les constats portant les conclusions les plus lourdes — permission `marins`, absence de seed `role_permissions`, acceptation de `NaN`, périmètre du service worker, handlers d'erreur, absence de `Session.expire`, absence de route de remboursement, écriture de caisse inconditionnelle — ont été re-vérifiés directement dans le code avant rédaction.*
