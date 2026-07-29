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

## 3. État actuel (vérifié dans le code)

| Élément | État | Fichier |
|---|---|---|
| Numéro de BL | `bl_number` (unique, indexé) + `bl_issued_at` sur `PackingListBatch` | `models/packing_list.py:205-206` |
| Émission | **`GET`** `/{pl_id}/batches/{batch_id}/bl.pdf` en permission **`cargo:C`** (consultation !), qui **écrit en base** via `assign_bl_number` | `routers/cargo_packing_router.py:361-374` |
| Traçabilité de l'émission | ❌ **aucune** — pas d'`activity_record`, pas d'acteur. Seul vestige : un horodatage anonyme | idem |
| Mutabilité | `can_modify(pl)` ne teste que `pl.status != "locked"`, **ignore `bl_number`** | `services/packing_list.py:230-231` |
| Statut de la PL | `status` (défaut `"draft"`), passe à `"locked"` au verrouillage, revient à `"submitted"` au déverrouillage | `models/packing_list.py:93` |
| Import Excel | `delete` de tous les batches puis recréation ⇒ **recycle les numéros de BL** (la séquence est un comptage) | `routers/cargo_packing_router.py:535-540` |
| Journalisation côté portail client | ❌ **aucune** — 8 routes mutantes (dont suppression de batch et de document), zéro `activity.record()` | `routers/cargo_portal_router.py` |
| Draft / final | ❌ inexistant | — |
| Validation client | ❌ inexistante | — |
| Signature commandant sur le BL | ❌ inexistante | — |

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
**`client_validated`** | **Le client titulaire du booking**, depuis l'espace authentifié **`/me`** — *pas* le portail expéditeur `/p/{token}`. Repli : le staff valide **pour son compte**, tracé comme tel | ⚠️ Oui, mais toute modification **repasse l'état à `draft`** et invalide la validation | PDF draft, mention « validé par le consignee le … » |
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

⚠️ **Cette migration dépend du RAF R1** : avec deux `head` Alembic divergents,
`alembic revision` exige de préciser la cible et `upgrade head` échoue. **La
fusion Alembic doit être faite avant ce lot.**

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

- **Émission en `POST`, pas en `GET`** — un lien de consultation ne doit pas
  écrire en base. Aujourd'hui un préchargement de lien ou un scan de sécurité
  émet des BL en série.
- **Permission `cargo:M` minimum** pour générer un draft (aujourd'hui `cargo:C`,
  ce qui autorise `technique`, `data_analyst` et **`marins`** à émettre un
  connaissement). Signature commandant en `captain:M`.
- **Refuser l'import Excel** si un batch de la PL est en `master_signed` ou
  `final` (409). Au stade `draft`, l'import reste autorisé — c'est le
  comportement voulu — mais il doit **préserver les numéros** : passer en
  *upsert* par `batch_number` au lieu de *delete-all/recreate*.
- **Séquence de numéros non recyclable** : remplacer le comptage par une
  séquence append-only, pour qu'un numéro consommé ne puisse **jamais** être
  réattribué même après suppression d'une ligne.

---

## 5. Points à trancher avant implémentation

1. **Le rail booking.** Décision D2 acte que les documents sont générés depuis
   les packing lists. Le rail booking (`/cargo/booking/{ref}/bl.pdf`) produit un
   BL **sans consignataire ni notify party** : à retirer dans le lot J2, avant
   ce lot-ci. À confirmer.
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

3. **Nombre d'originaux.** Aujourd'hui `3` est codé en dur
   (`services/pdf_generator.py:99`) et imprimé « 3 OBL signés » même sur un
   document non signé. Doit-il être paramétrable par BL ? Et faut-il un registre
   de remise des originaux (*surrender*) — sans lui, la livraison sans
   présentation d'original (*misdelivery*) est **exclue de la couverture P&I** ?
4. **Le commandant signe-t-il par batch ou par escale ?** Un navire chargeant
   20 lots implique 20 signatures. Un écran de signature groupée est peut-être
   nécessaire.
5. **Date « shipped on board ».** Aujourd'hui la date d'émission est l'instant
   du clic. Un BL antidaté est une fraude documentaire et une exclusion de
   garantie. La date doit venir du chargement réel (`Booking.loaded_at`, qui
   existe et n'est jamais utilisé) ou de l'ATD.

---

## 6. Estimation et séquencement

| Étape | Charge | Dépendance |
|---|---|---|
Journalisation du portail client (volet 1 du §4.3) | 0,5 j | aucune — **livrable dès le J2** |
Fusion Alembic (RAF R1) | 0,5 j | validation manager |
Migration + machine à états + transitions tracées | 2 j | fusion Alembic |
Écrans (génération draft, validation client, signature commandant) | 2 j | ci-dessus |
Filigrane draft / BL final / révisions | 1 j | ci-dessus |
Séquence non recyclable + upsert de l'import Excel | 1 j | ci-dessus |

**Total ≈ 6,5 jours** hors décisions du §5. C'est un **lot structurant**, pas un
quick win : il touche un document juridiquement opposable et mérite la revue du
manager.

**Ce qui est livrable immédiatement au J2, sans migration ni décision** : la
journalisation des mutations du portail client (elle est de toute façon requise
par la demande, et comble un trou d'audit existant), et le retrait du rail
booking dégradé.
