# Spécification — workflow du Bill of Lading

> Demande métier de Yasmin, 2026-07-29. Remplace l'émission de BL actuelle
> (un `GET` non tracé produisant un document mutable) par un cycle explicite
> à quatre étapes, avec journalisation de toute modification.
>
> Documents liés : `PROJECT_CONTEXT.md` §14.2 (audit du registre BL),
> `docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md` (plan, RAF),
> `docs/operations/07-ordre-pr-et-merge.md` (ordre de sortie).

---

## 1. Workflow demandé

```
1. Draft BL généré
2. Validation du draft par le client
3. Signature du commandant
4. BL final remis au client
```

Le client peut **mettre à jour la packing list pour modifier le draft**. Toute
modification doit être suivie **via la journalisation**.

---

## 2. Ce que cette demande change par rapport au plan initial

⚠️ **Elle invalide une des « micro-gardes BL » prévues au J2.** J'avais prévu de
**geler le batch dès l'émission du BL** — pour empêcher qu'un document émis
reste modifiable sous le même numéro. **C'est incompatible** avec ce workflow :
le client doit précisément pouvoir modifier la packing list au stade draft.

**Le point de gel se déplace : ce n'est plus l'émission, c'est la signature du
commandant.** C'est d'ailleurs plus juste métier — avant signature, un
connaissement n'engage personne ; après, il engage le transporteur.

**Elle résout aussi la tension centrale de l'audit §14.2.** Le problème n'était
pas la mutabilité en soi : c'était qu'il n'existait **aucune distinction
draft/final**, et qu'un document mutable était présenté comme définitif avec la
mention « Trois originaux signés (3 OBL) ». Le draft explicite lève ce défaut.

---

## 3. État actuel

> ⚠️ Ce tableau était l'**état de départ** relevé dans le code le 2026-08-03. La
> colonne « depuis » dit ce qui a changé depuis — le tableau n'est pas réécrit,
> pour que la trajectoire reste lisible en revue.

| Élément | État au 2026-08-03 | Depuis |
|---|---|---|
| Numéro de BL | `bl_number` (unique, indexé) + `bl_issued_at` sur `PackingListBatch` | inchangé — toujours attribué une seule fois, au draft |
| Émission | **`GET`** `.../bl.pdf` en permission **`cargo:C`** (consultation !), qui **écrit en base** via `assign_bl_number` | ✅ scindée : `POST .../bl/draft` en `cargo:M` écrit, `GET .../bl.pdf` en `cargo:C` ne fait que rendre |
| Traçabilité de l'émission | ❌ **aucune** — pas d'`activity_record`, pas d'acteur. Seul vestige : un horodatage anonyme | ✅ `bl_issued_by_id`/`_name` + double trace (`activity_logs` + `PackingListAudit`) |
| Mutabilité | `can_modify(pl)` ne teste que `pl.status != "locked"`, **ignore `bl_number`** | ✅ gel indépendant porté par le **lot** (`bl_workflow.is_frozen`), câblé sur les 4 chemins d'écriture |
| Statut de la PL | `status` (défaut `"draft"`), passe à `"locked"` au verrouillage | inchangé — les deux verrous restent **indépendants**, et c'est voulu |
| Import Excel | `delete` de tous les batches puis recréation ⇒ **recycle les numéros de BL** | ⚠️ **bloqué** (409) si un BL est signé ; le recyclage au stade `draft` reste à corriger par *upsert* |
| Journalisation côté portail client | ❌ **aucune** — 8 routes mutantes, zéro `activity.record()` | ✅ les 8 routes tracées (acteur `portal:PL{id}`, jamais le token) |
| Draft / final | ❌ inexistant | ✅ machine à états `draft → client_validated → master_signed → final` |
| Validation client | ❌ inexistante | ✅ en service (client XOR staff pour son compte) — **écran restant** |
| Signature commandant sur le BL | ❌ inexistante | ✅ en service (hash SHA-256, point de gel) — **écran restant** |

**Patron de signature déjà éprouvé dans le dépôt, à réutiliser** — `SofEvent`
(`models/sof_event.py:107-111`) : `signed_at` / `signed_by_id` /
`signed_by_name` / `signature_hash` (SHA-256 du tuple des champs signés,
détecte toute altération) / `is_locked` (le backend rejette tout UPDATE après
signature). Mon audit a vérifié que ce mécanisme est réellement immuable. **Ne
pas réinventer : le décalquer.**

---

## 4. Cible

### 4.1 Machine à états du BL

| État | Qui déclenche | Contenu modifiable ? | Document produit |
|---|---|---|---|
`aucun` | — | Packing list librement éditable | — |
**`draft`** | Cargo/Opérations (`cargo:M`) | ✅ **Oui** — par le staff **et** par l'**expéditeur** via le portail `/p/{token}` (c'est lui qui remplit la packing list dont le draft est issu) | PDF **filigrané `DRAFT — NOT NEGOTIABLE`**, sans mention d'originaux |
**`client_validated`** | **Le client titulaire du booking**, depuis l'espace authentifié **`/me`** — *pas* le portail expéditeur `/p/{token}`. Repli : le staff valide **pour son compte**, tracé comme tel | ⚠️ Oui, mais toute modification **repasse l'état à `draft`** et invalide la validation | PDF draft, mention « validé par le client le … » (ou « validé par NEWTOWT pour le compte du client le … » en cas de repli) |
**`master_signed`** | Le commandant (`captain:M`) | ❌ **Non** — gel effectif | PDF signé, hash calculé |
**`final`** | Émission au client (automatique à la signature, ou action explicite `cargo:M`) | ❌ Non | **BL final** avec numéro définitif et mentions d'originaux |

**Règle de régression** : toute modification de la packing list au stade
`client_validated` **annule la validation** et ramène à `draft`. Le client
revalide. C'est le comportement attendu — une validation porte sur un contenu
précis, pas sur un dossier ouvert.

**Règle de gel** : à partir de `master_signed`, la correction ne passe plus par
l'édition mais par une **révision numérotée** (`TUAW_…_R2`) qui annule
explicitement la précédente, les deux restant tracées.

### 4.2 Modèle de données (migration requise)

Sur `PackingListBatch` :

| Champ | Type | Rôle |
|---|---|---|
`bl_state` | `String(20)`, défaut `NULL` | `draft` / `client_validated` / `master_signed` / `final` |
`bl_draft_at` | `DateTime(tz)` | Génération du draft |
`bl_issued_by_id` / `bl_issued_by_name` | FK users / `String(200)` | **Qui** a généré le draft (aujourd'hui inconnu) |
`bl_client_validated_at` | `DateTime(tz)` | Validation client |
`bl_client_validated_by_id` | FK `client_accounts`, nullable | Compte client ayant validé (cas normal) |
`bl_validated_on_behalf_by_id` | FK `users`, nullable | Membre du staff ayant validé **pour le compte** du client (repli, booking sans compte). **Exactement une des deux FK est renseignée** |
`bl_client_validated_by` | `String(200)` | Nom figé à la validation (instantané, survit à un renommage) |
`bl_signed_at` / `bl_signed_by_id` / `bl_signed_by_name` | idem `SofEvent` | Signature commandant |
`bl_signature_hash` | `String(64)` | SHA-256 du contenu signé — détecte l'altération |
`bl_revision` | `Integer`, défaut 1 | Numéro de révision |
`bl_superseded_by_id` | FK self, nullable | Révision qui annule celle-ci |

`bl_number` et `bl_issued_at` existants sont conservés (le numéro est attribué
à la génération du draft et **ne bouge plus**).

✅ **Le préalable Alembic est levé** (2026-08-10) : `main` porte
`20260807_0113_merge_heads_mrv_crewing` depuis le 2026-08-07, la tête est unique.
La migration de ce lot peut donc être créée directement — c'était le RAF R1, désormais
clos.

### 4.3 Journalisation — exigence explicite de la demande

Toute modification doit être tracée. Deux volets :

1. **Combler le trou actuel du portail client** (constat d'audit §14.2) : les
   8 routes mutantes de `cargo_portal_router.py` n'appellent **jamais**
   `activity.record()`. Ajouter la trace sur chacune, avec
   `user_name=f"portal:{shipper_name}"` et `entity_type="packing_batch"`, de
   sorte qu'on puisse reconstituer **qui** a changé **quoi** et **quand** —
   c'est ce que réclame un P&I club à l'ouverture d'un dossier.
2. **Tracer les transitions d'état du BL** : génération du draft, validation
   client, signature, émission finale, révision. Chaque transition dans
   `activity_logs` **et** dans `PackingListAudit` (qui existe déjà et trace
   champ par champ).

**Interdire la suppression physique** d'un batch portant un `bl_number` :
soft-delete avec motif obligatoire.

### 4.4 Corrections de sécurité à embarquer dans le même lot

- ✅ **FAIT** (2026-08-17) — **Émission en `POST`, pas en `GET`** : un lien de
  consultation ne doit pas écrire en base. Avant, un préchargement de lien ou un
  scan de sécurité émettait des BL en série.
  `POST /cargo/packing-lists/{pl_id}/batches/{batch_id}/bl/draft` génère ;
  `GET .../bl.pdf` rend et **n'écrit plus rien** (404 si aucun BL n'existe).
  Tests : `tests/integration/test_bl_emission_post_only.py`.
- ✅ **FAIT** (2026-08-17) — **Permission `cargo:M` minimum** pour générer un
  draft (c'était `cargo:C`, ce qui autorisait `technique`, `data_analyst` et
  **`marins`** à émettre un connaissement). La consultation reste en `cargo:C`.
  Signature commandant en `captain:M` : **reste à faire** avec l'écran.
- ✅ **FAIT** (2026-08-17, volet blocage) — **Refuser l'import Excel** si un
  batch de la PL est en `master_signed` ou `final` (409), des deux côtés (staff
  et portail). Au stade `draft` l'import reste autorisé — c'est le comportement
  voulu. **Reste** le volet préservation : passer en *upsert* par `batch_number`
  au lieu de *delete-all/recreate*, pour ne plus recycler les numéros.
- **Séquence de numéros non recyclable** : remplacer le comptage par une
  séquence append-only, pour qu'un numéro consommé ne puisse **jamais** être
  réattribué même après suppression d'une ligne.

---

## 5. Points tranchés — réponses de Yasmin (2026-08-03)

Les cinq points sont désormais tranchés. Ce qui suit remplace les questions.

### 5.0 Date « shipped on board » = dernier jour des opérations

> « Dans la section des opérations, on voit la ligne du temps montrant le flux
> daté. Le jour de *ship on board* devrait être le dernier jour des opérations.
> Avec la possibilité d'être modifié par l'équipe opérations (sous
> justification) et journal de modification en cas de contrôle. »

**Source de vérité** : la timeline d'escale (`EscaleOperation`), pas l'instant du
clic. La date retenue est celle de la **dernière opération** du leg — donc une
valeur **dérivée**, pas saisie.

**Motif à trois niveaux, identique à celui des durées de contrat du lot relèves**
(cf. `REFERENCE_METIER_RELEVES_EQUIPAGE.md` §3.2) :

| Niveau | Nature |
|---|---|
| 1. Dérivée | dernier jour des opérations d'escale |
| 2. Override | l'équipe Opérations (`escale:M` ou `cargo:M`) peut la corriger |
| 3. **Justification obligatoire** | **refuser l'enregistrement** d'un override sans motif |

⇒ **Un seul mécanisme à construire pour les deux lots.** À implémenter une fois,
réutilisable : *valeur dérivée + override tracé + justification exigée*.

Enjeu : un BL antidaté est une **fraude documentaire** et une exclusion de
garantie. Le journal est explicitement demandé « en cas de contrôle ».

### 5.1 Nombre d'originaux : toujours 3 — **et suivi de réception**

> « Toujours 3. Normalement, les BLs devraient être téléchargeables dans la
> plateforme client. L'idéal serait de tracker le timestamp de cette action ou
> ajouter une case de confirmation de réception côté client. Cette case devrait
> aussi apparaître pour l'équipe opérations, en mode backup. Si les BLs sont
> envoyés en papier par exemple, l'équipe opérations pourra confirmer la
> réception côté client en ajoutant la date et heure de confirmation et moyen
> (téléphone, mail, etc.) + PJ possible. »

`3` reste donc **constant** — pas de paramétrage à prévoir.

**Nouvelle exigence : un registre de remise.** C'est exactement le dispositif
dont l'absence exclut la *misdelivery* de la couverture P&I. Deux voies, la
seconde servant de repli :

| Voie | Déclencheur | Données |
|---|---|---|
| **Numérique** | le client télécharge le BL depuis `/me` | horodatage automatique du téléchargement · **et/ou** case « réception confirmée » cochée par le client |
| **Repli Opérations** | BL remis en papier / hors plateforme | date **et heure** de confirmation · **moyen** (téléphone, mail, courrier…) · **pièce jointe possible** · l'auteur côté staff |

⇒ Nouvelle table `BlDeliveryReceipt` (ou champs dédiés) : `batch_id`, `channel`
(`download` / `client_confirmed` / `ops_confirmed`), `confirmed_at`, `means`,
`confirmed_by_client_id` **xor** `confirmed_by_user_id`, `attachment_path`,
`notes`. Le repli staff est tracé **comme tel**, jamais présenté comme une
confirmation du client — même principe que
`bl_validated_on_behalf_by_id` au §4.2.

### 5.2 Signature du commandant : **au choix, unitaire ou groupée**

> « Donner le choix au commandant de tout signer ou signer un BL en
> particulier. »

Les deux modes, pas l'un ou l'autre : un écran listant les BL en attente sur son
navire, avec signature individuelle **et** action « tout signer ». Chaque BL reçoit
sa propre entrée de signature (`bl_signed_at`, `bl_signature_hash`) même en
signature groupée — le groupage est une commodité d'interface, pas une signature
unique portant sur un lot.

### 5.3 Données du BL : marchandises depuis la packing list, **parties depuis le portail**

> « Données marchandises pour BL générés à partir de packing list. Notify party
> & consignee à saisir depuis le portail expéditeur de MyTOWT. »

Confirme la décision D2 (rail packing list) **et** précise qui saisit les parties.

⚠️ **État vérifié dans le code (2026-08-03) — l'exigence est à moitié satisfaite :**

| Champ | Modèle | Formulaire portail | Formulaire staff |
|---|---|---|---|
| `shipper_*` | ✅ `PackingListBatch` | ✅ présent | ✅ |
| `consignee_*` | ✅ `PackingListBatch` | ✅ présent | ✅ |
| **`notify_*`** | ✅ `PackingListBatch` | ❌ **ABSENT** | ✅ |

Les cinq champs `notify_name` / `notify_address` / `notify_postal` /
`notify_city` / `notify_country` existent en base et figurent dans
`AUDITABLE_FIELDS` — ils sont donc déjà audités **dès qu'ils sont remplis**. Il
manque uniquement leur exposition dans `templates/portal/packing.html`.
**Correctif court, à embarquer dans ce lot.**

### 5.4 Le rail booking — ⛔ le retrait **ne peut pas** précéder ce lot

Décision D2 acte que les documents sont générés depuis les packing lists. Le rail
booking produit un BL **sans consignataire ni notify party** : il doit disparaître.
La réponse 5.3 le confirme — les parties se saisissent au portail, donc sur la
packing list, pas sur le booking.

**Mais l'inventaire des routes (fait le 2026-08-03) invalide le séquencement
initialement prévu** (« à retirer dans le lot J2, avant ce lot-ci ») :

| Route | Rail | Public | Remplacement disponible ? |
|---|---|---|---|
| `/cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.pdf` | **packing list** | staff | — *(c'est la cible)* |
| `/cargo/booking/{ref}/bl.pdf` | booking | staff | ✅ la route packing list |
| `/cargo/booking/{ref}/bl.docx` | booking | staff | ✅ idem |
| `/me/bookings/{ref}/bl.pdf` | booking | **client** | ❌ **AUCUN** |
| `/me/bookings/{ref}/bl.docx` | booking | **client** | ❌ **AUCUN** |

🔴 **Le rail packing list n'a aucune route côté client.** Et l'interface client
expose un bouton visible « 📄 Bill of Lading »
(`templates/client/booking_detail.html:199`) qui pointe vers la route booking.

⇒ **Retirer le rail booking maintenant supprimerait la seule façon pour un client
d'obtenir son connaissement**, sans remplacement, pour toute la durée d'attente
(retour de Julien + implémentation). C'est une régression fonctionnelle
visible — exactement ce que la méthode de développement prudent interdit.

**Séquencement corrigé.** Le retrait n'est pas un préalable, c'est une
**conséquence** : il se fait *dans* ce lot, une fois les routes client du rail
packing list créées, et dans cet ordre :

1. créer les routes client du rail packing list (draft filigrané / BL final selon
   `bl_state`) ;
2. **rebrancher** le bouton client `booking_detail.html:199` dessus ;
3. **alors** retirer les 4 routes du rail booking et leurs 3 entrées staff
   (`staff/cargo/booking_detail.html` ×2, `staff/cargo/index.html` ×1).

**Point de conception à trancher en route** : un booking peut porter **plusieurs
batches**, donc plusieurs BL, alors que l'URL client est au niveau du booking. Il
faut choisir entre lister les BL du booking ou produire un document par batch —
ce choix appartient à ce lot, pas à un correctif préalable.

**Ce qui a été fait sans risque en attendant** : l'ajout du notify party au
formulaire du portail (§5.3), pure addition sans retrait.
2. ~~Qui valide côté client ?~~ ✅ **TRANCHÉ (Yasmin, 2026-07-29)**

   **C'est le client titulaire du booking qui valide le draft**, depuis l'espace
   authentifié `/me`. La notion de **consignee reste séparée** : `consignee_name`
   et les champs d'adresse demeurent des données du connaissement (texte libre
   rempli par l'expéditeur), **sans lien avec le validateur**. On ne cherche donc
   pas à relier le consignee à un compte — ce serait de la complexité inutile.

   **Repli pour les bookings sans compte client** (`Booking.client_account_id`
   est nullable — cas d'une réservation saisie côté staff pour un client non
   inscrit) : **le staff valide pour le compte du client**, et la validation est
   tracée **explicitement comme telle** (`bl_validated_on_behalf_by_id` +
   `activity_logs`). Jamais de validation silencieuse présentée comme venant du
   client.

   **Implémentation** : le socle existe — `/me/bookings/{ref}/bl.pdf` est déjà
   une route *owner-only* (`routers/cargo_router.py:178`). Reprendre la
   vérification de propriété individuelle (`booking.client_account_id ==
   client.id`) : il n'y a pas de middleware central, chaque route `/me` la refait.

   Note : `3` reste **constant** (réponse 5.1), donc
   `services/pdf_generator.py:99` n'a pas besoin d'être paramétré — mais la
   mention « 3 OBL signés » doit disparaître du **draft**, qui n'est pas signé.

---

## 6. Estimation et séquencement

| Étape | Charge | État / dépendance |
|---|---|---|
| Journalisation des mutations du portail (volet 1 du §4.3) | 0,5 j | ✅ **FAIT** (2026-08-03, commit `1cb1d40`) — aucune migration |
| Fusion Alembic (RAF R1) | 0,5 j | ⏸️ **prête**, en attente de **Julien** (retour le 2026-08-17) |
| Notify party au formulaire du portail (§5.3) | 0,25 j | ✅ **FAIT** (2026-08-03) — champs déjà en base et audités, seule l'exposition manquait |
| Routes client du rail packing list, puis retrait du rail booking (§5.4) | 1 j | ⛔ **ne peut PAS précéder ce lot** — retirer maintenant priverait le client de tout BL |
| Migration + machine à états + transitions tracées | 2 j | ✅ **FAIT** (2026-08-14/17) — migration `20260814_0114`, `services/bl_workflow.py`, gel câblé sur les 4 chemins d'écriture, émission en `POST`/`cargo:M`. 45 tests |
| Écrans (génération draft, validation client, signature) | 2 j | ⚠️ **2 des 3 FAITS** (2026-08-17) — génération du draft (`POST .../bl/draft`) et **écran commandant** `/captain/bl` : signature **unitaire ET groupée** (§5.2), listes séparées « à signer » / « en attente de validation client » / « signés », compte rendu du mode groupé (signés **et** écartés). 16 tests. ⛔ **Validation client restante** — elle passe par `/me`, donc **liée au lot des routes client** ci-dessus (§5.4). Repli disponible dès maintenant en service (`validate_by_client(on_behalf_user=…)`), pas encore exposé à l'écran |
| Date *shipped on board* dérivée + override justifié (§5.0) | 1 j | ci-dessus. **Mécanisme partagé** avec le lot relèves |
| Registre de remise des originaux (§5.1) | 1,5 j | ci-dessus — table + écran client + repli Opérations avec PJ |
| Filigrane draft / BL final / révisions | 1 j | ✅ **filigrane FAIT** (2026-08-17) — filigrane `DRAFT` sur toutes les pages + mention opposable + bloc de signature conditionnel + suffixe `-DRAFT` au nom de fichier. 15 tests. ⚠️ **revue visuelle d'un PDF réel restant** (la CI prouve que le document se construit, pas qu'il s'affiche bien). Reste le volet **révisions numérotées** |
| Séquence non recyclable + upsert de l'import Excel | 1 j | volet **blocage** livré (409 si un BL est signé) ; reste l'*upsert* et la séquence append-only |

**Total ≈ 10,25 jours**, dont **≈ 5 livrés**. Révisé à la hausse depuis
l'estimation initiale de 6,5 j : les réponses du §5 ajoutent le **registre de
remise** (exigence nouvelle, non demandée initialement) et la **date dérivée avec
override justifié**. Les deux sont des ajouts de valeur, pas des dérives de
périmètre.

C'est un **lot structurant**, pas un quick win : il touche un document
juridiquement opposable (titre de propriété, prescription Hague-Visby d'un an) et
mérite la revue de Julien.

> 🔁 **Mutualisation à ne pas manquer** : le motif *valeur dérivée → override →
> justification obligatoire* est demandé **deux fois**, ici pour la date
> *shipped on board* (§5.0) et dans le lot relèves pour les durées de contrat
> (`REFERENCE_METIER_RELEVES_EQUIPAGE.md` §3.2). À construire **une seule fois**.
> Le dépôt porte déjà un précédent proche : `validation_engine.get_threshold`
> (MRV v2, « zéro seuil en dur », résolution fail-closed + snapshot d'audit).

**Ce qui est livrable immédiatement au J2, sans migration ni décision** : la
journalisation des mutations du portail client (elle est de toute façon requise
par la demande, et comble un trou d'audit existant), et le retrait du rail
booking dégradé.
