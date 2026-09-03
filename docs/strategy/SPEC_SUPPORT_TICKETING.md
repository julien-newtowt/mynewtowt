# Spécification — Support applicatif (« Assistance »)

> **Objet** : permettre à tout collaborateur de signaler une difficulté ou un
> dysfonctionnement **du logiciel MyTOWT lui-même**, et de suivre le traitement de
> sa demande d'assistance.
>
> **Statut** : spécification — **aucun code écrit**. À valider avant implémentation.
> **Rédigé le** 2026-08-21. **Branche** : `feature/support-ticketing` (depuis `main`).
> **Demandé par** : Yasmin, le 2026-08-21.

---

## 1. ⚠️ Ce module n'est PAS le module `tickets` — différenciation stricte

Le dépôt contient déjà un module nommé `tickets` (`/tickets`). **Il traite un tout
autre sujet** : les incidents d'exploitation portuaire pendant une escale (avarie,
avitaillement urgent, formalité douanière, urgence médicale…).

Confondre les deux serait coûteux : ils n'ont ni le même public, ni les mêmes
priorités, ni les mêmes droits. **Toute la nomenclature de ce nouveau module est
donc choisie pour rendre la confusion impossible**, au niveau du code comme de
l'interface.

| | Module **existant** — incidents d'escale | Module **nouveau** — support applicatif |
|---|---|---|
| **Sujet** | Un problème survenu **dans le monde réel**, pendant une escale | Un problème survenu **dans le logiciel** |
| **Nom de module (permissions)** | `tickets` | **`support`** |
| **Racine d'URL** | `/tickets` | **`/support`** |
| **Libellé interface (FR)** | « Tickets escale » | **« Assistance »** — action : « Signaler un problème » |
| **Vocabulaire utilisateur** | un **ticket** | une **demande d'assistance** (« une demande ») |
| **Préfixe de référence** | `TKT-2026-A3F1` | **`SUP-2026-0001`** |
| **Tables** | `tickets`, `ticket_comments` | **`support_tickets`**, `support_ticket_comments`, `support_ticket_attachments` |
| **Classes** | `Ticket`, `TicketComment` | **`SupportTicket`**, `SupportTicketComment`, `SupportTicketAttachment` |
| **Service** | `app/services/tickets.py` | **`app/services/support.py`** |
| **Routeur** | `app/routers/tickets_router.py` | **`app/routers/support_router.py`** |
| **Gabarits** | `templates/staff/tickets/` | **`templates/staff/support/`** |
| **Icône Lucide** | `ticket` | **`life-buoy`** |
| **Qui peut ouvrir** | 6 rôles sur 9 | **les 9 rôles** |
| **Priorités / SLA** | P1/P2/P3, SLA 2 h / 8 h / 72 h, escalade manager | **gravité déclarée, AUCUN SLA** |
| **Emplacement dans la navigation** | groupe « Exploitation » | **hors groupe métier** — accès permanent, cf. §9 |

### Règles de nomenclature à ne pas enfreindre

1. **Aucun symbole de ce module ne s'appelle `Ticket` tout court.** Le préfixe
   `Support` est obligatoire sur les classes, les tables et les fichiers.
2. **Le mot « ticket » n'apparaît pas dans l'interface française** de ce module. La
   rubrique s'appelle **« Assistance »**, l'objet est une **demande d'assistance**,
   et l'action d'entrée est **« Signaler un problème »**. Cela évite qu'un
   utilisateur cherche ses demandes dans « Tickets escale ».
   *(Libellé arbitré par Yasmin le 2026-08-21 : « Assistance » retenu contre
   « Signalements ».)*
3. **Aucun import croisé** entre `services/support.py` et `services/tickets.py`. Les
   deux modules ne partagent rien d'autre que les briques transverses
   (`notifications`, `activity`, `safe_files`).
4. Un test de non-régression vérifie les points 1 et 3 (cf. §12).

---

## 2. Le constat qui motive la conception

**Aujourd'hui, quand un utilisateur rencontre un bug, il ne reste aucune trace dans
le système.** Vérifié le 2026-08-21 :

| Brique annoncée dans `CLAUDE.md` | Réglage présent | Dépendance installée | Code d'initialisation |
|---|---|---|---|
| Sentry | `settings.sentry_dsn`, `SENTRY_DSN` dans `.env.example` | `sentry-sdk[fastapi]==2.20.0` | **aucun** — `sentry_sdk.init()` n'est appelé nulle part |
| OpenTelemetry | `settings.otel_exporter_otlp_endpoint` | `opentelemetry-*` | **aucun** |
| Prometheus | `settings.prometheus_metrics` | `prometheus-fastapi-instrumentator` | **aucun** |

S'y ajoute qu'`app/main.py` déclare des gestionnaires pour l'authentification, le 404
et le 403, **mais pas pour le 500** : une exception non rattrapée tombe dans le
défaut de Starlette — réponse nue, trace sur stderr, rien de durable. Et
`activity_logs` ne journalise que les écritures **réussies**.

### Conséquence directe sur la conception

Ce module n'est pas un complément à une capture automatique d'erreurs : il en est
**le seul et unique canal**. D'où une exigence non négociable :

> **La demande doit porter elle-même son contexte technique**, capturé
> automatiquement. Sans cela on récolte des « ça ne marche pas sur la page des
> escales » inexploitables.

Le câblage de Sentry / OTel / Prometheus et un gestionnaire 500 forment un **lot
d'observabilité distinct**, délibérément hors périmètre (cf. §13). C'est le
complément naturel : la demande dit **qu'**il y a un problème, Sentry dirait
**lequel**.

---

## 3. Périmètre de la v1

### Dans le périmètre

- Ouverture d'une demande par **tout collaborateur** (les 9 rôles staff).
- **Capture automatique du contexte technique** (§5).
- **Pièces jointes**, captures d'écran incluses (§8).
- Consultation : chacun voit **ses** demandes ; `administrateur` voit **toutes**.
- **Archivage à 3 mois + écran d'archives** (§7.1) — les deux ensemble, jamais l'un
  sans l'autre.
- Fil de commentaires entre le demandeur et celui qui traite.
- Tri par `administrateur` : changement d'état, assignation.
- Notification à l'ouverture (vers `administrateur`) et à chaque changement d'état
  (vers le demandeur).
- Journalisation d'audit sur toute écriture.
- i18n dans les **5 catalogues**.

### Hors périmètre v1 — décidé, pas oublié

| Écarté | Motif |
|---|---|
| Ouverture par les **clients** (`/me`) | Décision Yasmin 2026-08-21 : « commençons par interne ». Le modèle prévoit la place (§4) sans l'exposer |
| Ouverture depuis le **portail expéditeur** (`/p/{token}`) | Idem. Canal non authentifié ⇒ surface d'abus à traiter séparément (rate limiting, anti-spam) |
| **SLA, priorités P1/P2/P3, escalade** | Un SLA n'a de sens que si quelqu'un s'engage dessus. Personne ne s'y engage aujourd'hui — un SLA affiché et non tenu est un faux vert |
| **Kanban** | La v1 est une liste filtrable. Le kanban se justifie à volume, pas avant |
| **Câblage Sentry / OTel / 500 handler** | Lot d'observabilité distinct — le mélanger rendrait ce lot non révocable indépendamment (`07-ordre-pr-et-merge.md` §1) |

---

## 4. Modèle de données

### `support_tickets`

| Colonne | Type | Notes |
|---|---|---|
| `id` | PK | |
| `reference` | `String(20)`, unique | `SUP-2026-0001` — **séquentiel**, cf. §6 |
| `reporter_id` | FK `users.id`, **non nul** | l'auteur de la demande |
| `reporter_role` | `String(30)`, non nul | **rôle figé à la création** — le rôle d'un utilisateur peut changer ensuite, le contexte de la demande ne doit pas bouger avec |
| `kind` | `String(20)` | `bug` \| `question` \| `amelioration` |
| `severity` | `String(20)` | `bloquant` \| `genant` \| `mineur` — **déclarée par l'utilisateur**, sans SLA attaché |
| `title` | `String(200)`, non nul | |
| `description` | `Text`, non nul | |
| `status` | `String(20)`, non nul | défaut `nouveau`, cf. §7 |
| `assigned_to_id` | FK `users.id`, nullable | rempli au tri |
| `resolution` | `Text`, nullable | ce qui a été fait ou pourquoi c'est rejeté |
| `client_id` | FK, nullable | **réservé** : place laissée pour l'ouverture client (hors v1), jamais renseigné en v1 |
| **Contexte technique** | | |
| `page_url` | `String(500)`, nullable | écran où le problème est survenu |
| `http_referer` | `String(500)`, nullable | |
| `user_agent` | `String(400)`, nullable | navigateur / OS |
| `app_version` | `String(20)`, nullable | `settings.app_version` au moment de la demande |
| `occurred_at` | `DateTime(tz)`, nullable | quand **l'utilisateur** dit que c'est arrivé |
| **Horodatages** | | |
| `created_at` / `updated_at` | `DateTime(tz)` | serveur (`server_default=now()`) |
| `triaged_at`, `resolved_at`, `closed_at` | `DateTime(tz)`, nullable | |

**Index** : `status`, `reporter_id`, `assigned_to_id`, `(status, severity)`.

### `support_ticket_comments`

`id`, `support_ticket_id` (FK **CASCADE**), `author_id`, `author_name`,
`body` (`Text`), `is_internal` (`Boolean`, défaut `False`), `created_at`.

> `is_internal` : une note visible du seul `administrateur`. Utile pour consigner un
> diagnostic sans le servir au demandeur. **Le gabarit doit filtrer** — un
> commentaire interne affiché au demandeur serait une fuite. Test exigé (§12).

### `support_ticket_attachments`

`id`, `support_ticket_id` (FK **CASCADE**), `file_path` (`String(300)`, chemin
relatif retourné par `safe_files.save_upload`), `original_name` (`String(255)`),
`file_mime` (`String(100)`), `size_bytes` (`Integer`), `uploaded_by_id`,
`created_at`.

---

## 5. Contexte technique — capture automatique

Renseigné **côté serveur**, jamais depuis un champ de formulaire modifiable :

| Champ | Source |
|---|---|
| `page_url` | champ caché pré-rempli par le lien « Signaler un problème » (§9), **validé** côté serveur : doit être un chemin relatif du site, sinon ignoré |
| `http_referer` | en-tête `Referer` de la requête |
| `user_agent` | en-tête `User-Agent`, tronqué à 400 caractères |
| `app_version` | `settings.app_version` |
| `reporter_id`, `reporter_role` | utilisateur authentifié — **jamais** lus du payload |

⚠️ **`page_url` est la seule valeur qui transite par le client.** Elle doit être
traitée comme une donnée non fiable : validation en chemin relatif (`/…`, pas de
schéma, pas d'hôte), sinon on l'écarte. Sans quoi elle devient un vecteur de
redirection ouverte ou d'injection dans le gabarit.

---

## 6. Référence — séquentielle, et pourquoi pas aléatoire

Format : **`SUP-{année}-{séquence 4 chiffres}`** → `SUP-2026-0001`.

### Le contre-exemple est dans le dépôt

Le module `tickets` génère `TKT-{année}-{secrets.token_hex(2)}`, soit **4 caractères
hexadécimaux = 65 536 valeurs par an**, sur une colonne `unique=True`, **sans aucune
gestion de collision** (aucun `try`, aucun `IntegrityError` dans tout
`services/tickets.py`) :

| Tickets par an | Probabilité de collision |
|---|---|
| 100 | 7,3 % |
| 200 | 26,2 % |
| **300** | **49,6 %** |
| 500 | 85,1 % |

Une collision lève une `IntegrityError` non rattrapée ⇒ **500 à la création**. Ce
n'est pas théorique à quelques centaines de demandes par an.

### Ce qu'on fait à la place

Numérotation **séquentielle par année**, avec la boucle de reprise **qui existe déjà
dans le dépôt** : `packing_list.assign_bl_number` fait `for _attempt in range(5)`,
et — c'est le point essentiel — enveloppe chaque tentative dans un **savepoint**
(`async with db.begin_nested(): await db.flush()`).

Le savepoint n'est pas un détail de style : sans lui, l'`IntegrityError` du premier
essai laisse la transaction **entière** en échec, et il n'y a plus rien à reprendre.
C'est exactement le défaut corrigé dans l'ingestion QHSE (`188be0e`), où un
`rollback()` global annulait tout l'import. **Même patron, pas d'invention.**

Une référence séquentielle a en outre l'avantage d'être **citable à l'oral** (« ma
demande 42 »), ce qu'un hexadécimal aléatoire n'est pas — et pour un outil dont
on parle en réunion, ça compte.

> Ce défaut du module `tickets` est **signalé, non corrigé ici** : il appartient à ce
> module, et le corriger depuis ce lot violerait « un lot = un objectif ». À porter
> dans un lot `fix/tickets-reference-collision`.

---

## 7. Machine à états

```
nouveau ──→ en_cours ──→ resolu ──→ clos
   │            │           │
   │            └──→ en_attente_utilisateur ──→ en_cours
   │
   └──→ rejete   (depuis nouveau ou en_cours, motif OBLIGATOIRE)
```

| État | Sens |
|---|---|
| `nouveau` | déposé, pas encore regardé |
| `en_cours` | pris en charge |
| `en_attente_utilisateur` | information manquante — la balle est chez le demandeur |
| `resolu` | corrigé ; le demandeur peut confirmer ou relancer |
| `clos` | terminé, état final |
| `rejete` | ne sera pas traité (doublon, hors périmètre, non reproductible) — **`resolution` obligatoire** |

- Transitions **explicites** dans un dictionnaire, comme `tickets._TRANSITIONS`.
- `resolu → en_cours` autorisé : une correction incomplète se rouvre.
- `clos` et `rejete` sont **terminaux**.
- Le **demandeur** peut passer `en_attente_utilisateur → en_cours` (en répondant) et
  `resolu → en_cours` (« ce n'est pas corrigé »). Tout le reste est réservé à
  `administrateur`.

### 7.1 Archivage à 3 mois — et pourquoi ce n'est PAS une purge

**Décision Yasmin (2026-08-21) : « à archiver après 3 mois ».**

⚠️ **Archiver n'est pas purger, et la question initiale confondait les deux.** Elle
proposait d'inscrire les 3 tables dans `ALLOWED_PURGE_TABLES` — or cette mécanique
**supprime** les lignes. Ce n'est pas ce qui est demandé, et ce ne serait pas
souhaitable : l'historique des demandes est la mémoire des défauts du logiciel. Un
bug réapparu deux ans plus tard se diagnostique en retrouvant l'ancienne demande.

**Retenu : archivage = sortie de la vue courante, la donnée reste.**

#### Un état dérivé, pas une colonne ni un cron

Une demande est **archivée** si — et seulement si :

```
status ∈ {clos, rejete}  ET  (maintenant − horodatage terminal) > 90 jours
```

Aucune colonne `archived_at`, **aucune tâche de fond**. C'est un prédicat de
requête, évalué à la lecture.

| Option écartée | Motif |
|---|---|
| Colonne `archived_at` + cron | Introduit un état à maintenir, donc une dérive possible (cron non exécuté ⇒ rien n'est archivé, et personne ne le voit). Le module `tickets` a déjà ce genre de dépendance |
| Purge par rétention | Détruit la mémoire des défauts. Et une suppression ne se rattrape pas |

Le seuil vit dans une constante nommée du service (`ARCHIVE_AFTER_DAYS = 90`). S'il
doit devenir réglable, il rejoindra la configuration — mais pas avant qu'on en ait
le besoin réel.

#### 🔴 L'écran d'archives part avec, pas après

**C'est une exigence, pas une option.** Le module `tickets` dit textuellement dans
son code : *« closed/cancelled are off-board, accessed via "Archives" »*. J'ai
énuméré ses 8 routes : **cet écran n'existe pas.** Conséquence réelle aujourd'hui —
un ticket d'escale clos **disparaît de l'interface**, atteignable seulement par URL
directe, alors que ses références sont aléatoires donc non devinables.

⇒ Livrer l'archivage sans l'écran d'archives, ce serait reproduire ce défaut en
connaissance de cause. Les deux sont dans le même lot, et un test vérifie qu'une
demande archivée **reste atteignable** depuis l'écran d'archives.

| Écran | Contenu |
|---|---|
| `/support` | demandes **non archivées** |
| `/support/archives` | demandes **archivées**, mêmes filtres, même cloisonnement de lecture (chacun les siennes, l'admin toutes) |

Une demande archivée reste **consultable en détail** (`/support/{ref}`) et **en
lecture seule** : plus de commentaire, plus de changement d'état, pièces jointes
toujours téléchargeables selon la règle du §8.

---

## 8. Pièces jointes et captures d'écran

**Tout est réutilisé de `app/services/safe_files.py`** — rien de nouveau côté
sécurité :

- `save_upload(content, original_name, subdir="support")` : valide extension +
  taille + **magic number**, écrit sous `settings.upload_dir/support/` avec un **nom
  aléatoire** (`secrets.token_hex(16)`) — le nom fourni par l'utilisateur ne touche
  jamais le disque.
- `content_length_exceeds_max()` en pré-filtre **anti-OOM** : rejet 413 **avant** de
  charger le corps en mémoire.
- `resolve_path()` **anti-path-traversal** à la lecture.
- Whitelist existante (`utils/file_validation.py`) : `.png`, `.jpg`, `.jpeg`,
  `.webp`, `.pdf`, `.docx`, `.xlsx`, `.csv`, `.txt`, `.zip`… — les captures d'écran
  sont couvertes. **20 Mo** par fichier.

### Limites propres à ce module

| Règle | Valeur | Motif |
|---|---|---|
| Pièces jointes par demande | **5 maximum** | borne le stockage sans gêner l'usage réel |
| Ajout après création | autorisé, tant que la demande n'est pas `clos`/`rejete` | on pense souvent à la capture après coup |
| Suppression d'une pièce | son auteur, ou `administrateur` | |
| Téléchargement | le demandeur ou `administrateur` — **jamais** le simple porteur du droit `support:C` | une capture d'écran peut contenir des données d'un autre module (finance, RH) |

⚠️ **Le point de contrôle d'accès au téléchargement est le plus sensible de ce
lot.** Une capture d'écran est une **exfiltration potentielle** : un marin qui
photographie un écran RH et l'attache à une demande rendrait ces données
lisibles par quiconque peut ouvrir la pièce. D'où la règle « demandeur ou admin
seulement », et un test dédié (§12).

Suppression d'une demande (`support:S`) ⇒ `unlink` best-effort du fichier disque
puis suppression de la métadonnée, patron de `captain_router.delete_leg_attachment`.

---

## 9. Interface et point d'entrée

### Le point d'entrée décide de l'adoption

Un lien **« Signaler un problème »** présent en permanence dans le gabarit staff
(`templates/staff/_layout.html`), **hors groupe métier** — c'est un outil
transverse, pas une rubrique d'exploitation. Il **pré-remplit `page_url`** avec
l'écran courant.

> Un formulaire qu'il faut aller chercher dans un menu ne sera pas utilisé. C'est le
> pré-remplissage de l'URL qui fait la différence entre une demande exploitable
> et « ça bug quelque part ».

### Écrans

| Écran | Contenu |
|---|---|
| `/support` | **Mes demandes** (toutes, si `administrateur`) — **non archivées**, liste filtrable par état, type, gravité. Compteur des `nouveau` pour l'admin |
| `/support/nouveau` | Formulaire : type, gravité, titre, description, date de survenue, pièces jointes. Contexte technique en champs cachés / serveur |
| `/support/{ref}` | Fiche : contexte technique, pièces jointes, fil de commentaires, actions selon le rôle. **Lecture seule si archivée** (§7.1) |
| `/support/archives` | Demandes **archivées** (§7.1) — mêmes filtres, même cloisonnement |

Composants Kairos existants (`.card`, `.badge`, `.pill`, `.alert`, `.btn`) — **aucun
CSS nouveau**, aucun script inline (CSP stricte).

---

## 10. Permissions

### Cellules de la matrice — 18ᵉ module

| Rôle | `support` |
|---|---|
| `administrateur` | **CMS** |
| les 8 autres (`operation`, `armement`, `technique`, `data_analyst`, `marins`, `commercial`, `manager_maritime`, `rh`) | **CM** |

`C` = voir · `M` = créer une demande et commenter · `S` = supprimer
(`administrateur` seul).

> `armement`, `commercial` et `rh` n'ont **aucun** accès au module `tickets`. C'est
> précisément pourquoi ce besoin ne pouvait pas être servi en étendant ce module :
> il aurait fallu leur ouvrir le kanban d'escale.

### ⚠️ Ce que la matrice ne sait PAS exprimer

La matrice est `rôle × module × {C,M,S}`. Elle **ne peut pas** dire « voir les siens »
plutôt que « voir tous ». Or c'est le cœur du besoin.

**Deux règles vivent donc dans le routeur, pas dans la matrice** :

1. **Cloisonnement de lecture** : un non-`administrateur` ne voit que les
   demandes dont il est le demandeur. Sur `/support/{ref}`, un accès à celle d'un
   autre renvoie **404** (pas 403 — un 403 confirmerait l'existence de la
   ressource).
2. **Tri réservé** : changement d'état (hors les deux transitions du demandeur, §7)
   et assignation sont réservés à `administrateur`.

**Le niveau `S` ne sera pas détourné pour signifier « peut trier ».** `S` veut dire
Suppress ; lui faire dire autre chose serait un mensonge sémantique dans un fichier
de sécurité. Le contrôle de rôle est explicite et testé.

---

## 11. Notifications, audit, i18n

**Notifications** (`services/notifications.create`, qui sait déjà cibler un rôle) :

| Événement | Cible |
|---|---|
| Demande créée | `target_role="administrateur"` |
| Changement d'état | `target_user_id` = le demandeur |
| Commentaire **non interne** | l'autre partie (demandeur ↔ assigné) |

> **Pas d'e-mail à ce stade** — arbitré par Yasmin le 2026-08-21. La cloche de
> notification suffit pour la v1. `services/email.py` reste disponible si l'usage
> montre que l'administrateur passe à côté des demandes ; le brancher plus tard est
> un ajout de quelques lignes, sans reprise du modèle.

**Audit** : `services.activity.record()` sur création, changement d'état,
assignation, commentaire, ajout et suppression de pièce jointe. `module="support"`.

**i18n** : clés dans **les 5 catalogues** (`fr`, `en`, `es`, `pt_br`, `vi`) pour les
libellés d'états, types, gravités et l'interface.

> Le module `tickets` porte ses **19 libellés métier en français dur** dans son
> service, avec 3 clés i18n seulement. On ne reproduit pas ça : des marins
> vietnamiens doivent pouvoir signaler un problème logiciel dans leur langue.
>
> ⚠️ **Piège vérifié** : `i18n.t()` n'applique `.format()` que si on lui passe des
> kwargs, et il l'enveloppe dans un `contextlib.suppress(KeyError, IndexError)`. Un
> faux marqueur entre accolades dans un libellé ne lève donc pas — il renvoie
> silencieusement la chaîne non formatée. **Aucune accolade décorative** dans les
> nouvelles clés.

---

## 12. Tests exigés

Le module `tickets` a **11 tests, tous sur des fonctions pures** : aucun test sur
`create_ticket`, `change_status`, `assign_ticket`, `add_comment`, ni sur toute
l'escalade SLA. On ne reproduit pas ça.

### Unitaires — `tests/unit/test_support_service.py`

- Format de référence ; **séquence sans trou et sans doublon** ; comportement sous
  collision simulée (la boucle de reprise aboutit).
- Table de transitions : chaque transition valide, chaque transition invalide.
- Rejet sans motif ⇒ refus.
- Validation de `page_url` : chemin relatif accepté, URL absolue / `javascript:` /
  hôte externe **écartés**.

### Intégration — `tests/integration/test_support_screens.py`

- Les 3 écrans rendent.
- **Cloisonnement** : l'utilisateur A ne voit pas la demande de B (**404**), et
  ne l'a pas dans sa liste.
- **Tri réservé** : un non-admin qui tente un changement d'état non autorisé ⇒ 403.
- **Les deux transitions du demandeur** fonctionnent.
- **Commentaire interne invisible** du demandeur dans le gabarit rendu.
- **Pièce jointe** : upload accepté (png), refusé (extension hors whitelist),
  refusé (magic number incohérent) ; **téléchargement interdit à un tiers**.
- Notification créée à l'ouverture, ciblée `administrateur`.
- `activity_logs` écrit sur chaque mutation.

### Archivage — `tests/integration/test_support_archives.py`

Le défaut du module `tickets` étant précisément qu'une donnée devient inatteignable,
ces tests portent sur l'**atteignabilité**, pas sur le filtrage :

- Une demande `clos` depuis **89 jours** est dans `/support`, **absente** de
  `/support/archives`.
- À **91 jours** : l'inverse — et **toujours consultable** en détail.
- Une demande **non terminale** ancienne de 2 ans **n'est jamais archivée** (le
  critère est l'état terminal, pas l'âge seul).
- Une demande archivée est en **lecture seule** : commentaire et changement d'état
  refusés.
- Le **cloisonnement s'applique aussi aux archives** : A ne voit pas les archives de
  B.
- Les **pièces jointes d'une demande archivée restent téléchargeables** par le
  demandeur et l'admin — l'archivage ne doit pas devenir une perte d'accès
  silencieuse.

### Non-régression — différenciation (§1)

- Aucun symbole exporté par `services/support.py` ne s'appelle `Ticket*` sans
  préfixe `Support`.
- `services/support.py` **n'importe pas** `services/tickets.py`, et réciproquement.
- Les libellés français du module ne contiennent pas le mot « ticket ».

### Règle du Quality Gate à appliquer sans exception

> *Un contrôle qu'on ne fait pas échouer volontairement au moins une fois n'est pas
> un contrôle.* Chaque test ci-dessus doit être **vu échouer** sur le code d'avant,
> et aucun ne doit pouvoir passer **à vide**.

⚠️ **Piège connu, à ne pas rejouer** : SQLite (le moteur de la suite) **n'applique
pas** les longueurs `String(n)`. Un test qui prétend provoquer une erreur en
dépassant `String(200)` **réussit à vide**. Consigné lors du lot QHSE.

---

## 13. Migration et ordonnancement Alembic

**Une migration** : création des 3 tables. Aucune modification de table existante.

Au 2026-08-21, la tête de `main` est **`20260807_0113`**. Mais **deux autres lots
chaînent déjà dessus** : les 5 migrations du lot BL (`#158`, `20260814_0114` →
`20260817_0118`) et celle de QHSE (`#160`, `20260722_0106`).

⇒ **Trois frères sur le même parent.** `main` peut en absorber **un** sans rien
faire ; les suivants arriveront avec un parent qui n'est plus la tête et
**recréeront plusieurs têtes Alembic** — la panne qui a bloqué tout déploiement en
juillet.

**Ce lot étant le dernier arrivé et hors de la file de la phase 2, c'est à lui de se
rechaîner en dernier**, sur la tête réelle au moment de sa fusion. À faire juste
avant, pas à l'avance : la tête dépend de l'ordre de fusion effectif.

> Ne **pas** chaîner par avance sur les migrations de BL ou de QHSE : ce lot
> deviendrait infusionnable sans elles, en violation du principe « un lot =
> révocable indépendamment ».

---

## 14. Impact sur les fichiers existants

| Fichier | Nature de la modification | Risque |
|---|---|---|
| `app/permissions.py` | +1 module (`support`), +9 cellules | 🟠 fichier de sécurité — modification purement additive, aucune cellule existante touchée |
| `app/main.py` | +1 router | 🟢 |
| `app/models/__init__.py` | +3 modèles | 🟢 |
| `app/templates/staff/_layout.html` | +1 lien permanent | 🟢 |
| `app/i18n/*.py` (×5) | + clés du module | 🟢 additif — parité fr↔vi vérifiée par la suite de régression |
| `CLAUDE.md` | table des modules : 17 → 18 ; ligne du domaine | 🟢 doc |

**Aucun fichier partagé avec les six lots de la phase 2** — ce lot est indépendant
et peut être relu sans attendre leur fusion. Seul `app/permissions.py` est également
touché par QHSE (#160), en zone différente (module `qhse` vs `support`) : fusion
propre attendue, à revérifier le jour venu.

---

## 15. Estimation

| Bloc | Charge |
|---|---|
| Modèle + migration | 0,5 j |
| Service (états, référence séquentielle, requêtes, prédicat d'archivage) | 1 j |
| Routeur + cloisonnement de lecture + gardes de tri | 1 j |
| Pièces jointes (upload, téléchargement contrôlé, suppression) | 0,75 j |
| 4 gabarits (liste, formulaire, fiche, archives) + lien permanent | 1,25 j |
| i18n ×5 | 0,5 j |
| Tests (unit + intégration + archives + non-régression), avec sabotage de chaque garde | 1,5 j |
| **Total** | **≈ 6,5 jours** |

L'archivage coûte peu (+0,5 j) parce qu'il est **dérivé** : pas de colonne, pas de
cron, pas de migration supplémentaire. L'essentiel de la charge est l'écran
d'archives et ses tests d'atteignabilité.

---

## 16. Décisions — arbitrées le 2026-08-21

| # | Question | Décision |
|---|---|---|
| 1 | Libellé de la rubrique | ✅ **« Assistance »** (contre « Signalements »). Objet = « demande d'assistance », action d'entrée = « Signaler un problème » |
| 2 | Valeurs de `kind` | ✅ **`bug` / `question` / `amelioration`** — proposition retenue sans objection. Ajouter une valeur plus tard est une clé i18n et une entrée de tuple, pas une migration |
| 3 | Notification e-mail | ✅ **Non à ce stade.** Cloche seule. `services/email.py` reste branchable sans reprise du modèle |
| 4 | Rétention | ✅ **Archivage à 3 mois, PAS de purge.** État dérivé sans colonne ni cron (§7.1), **avec** l'écran d'archives dans le même lot |

**Plus aucune décision bloquante.** La spec est complète pour l'implémentation.

### Ce qui restera à décider plus tard, en connaissance de cause

- **Ouverture aux clients** (`/me`) puis au **portail expéditeur** (`/p/{token}`) —
  la colonne `client_id` réserve la place, le canal non authentifié demandera un
  traitement anti-abus.
- **Seuil d'archivage réglable** — constante nommée aujourd'hui, à déplacer en
  configuration seulement si le besoin apparaît.
- **Correction du module `tickets`** : collision de référence (§6) et écran
  d'archives manquant (§7.1). Deux défauts réels, à porter dans un lot dédié à ce
  module — pas ici.
