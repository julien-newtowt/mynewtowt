# Intégration Marad → mynewtowt (crew) — note de cadrage (LECTURE SEULE)

> **Statut : crew members + plannings IMPLÉMENTÉS (read-only côté Marad).** Le
> client HTTP (`app/utils/marad.py`), le service de sync
> (`app/services/marad_sync.py` : `sync_crew`, `sync_schedules`, `sync_all`), le
> cron (`POST /api/marad/refresh`) et le **bouton « Synchroniser Marad »** sur
> `/crew` sont en place. Le schéma de `GET /api/Crewing` est **confirmé** (§3.1).
> Les plannings (`GET /api/CrewingSchedule`, **schéma confirmé** 2026-06-18) sont
> importés dans une **table miroir** `marad_crew_schedules` (§3.3), avec
> réconciliation **« voyage » Marad ↔ `leg`** par navire + fenêtre de dates.
> **Restent à brancher** : documents/certificats (`GetCrewMembersDocuments`) et
> la confirmation du **nom du header d'auth** (`MARAD_API_KEY_HEADER`, défaut
> `X-Api-Key` — la doc Swagger `external.marad.ms` renvoie 403 hors réseau
> autorisé). **mynewtowt n'écrit jamais dans Marad.**
>
> Date : 2026-06-17 · Auteur : cadrage + implémentation.

---

## 0. TL;DR

- Marad **expose bien une API REST** (« Marasoft Generic API ») hébergée sur
  `https://external.marad.ms`, avec une **doc Swagger/portail** et des
  **release notes** publiquement listées par les moteurs de recherche.
- **Auth = clé d'API dans un header** (le passage par query string est
  *déprécié* : supprimé à partir de la v5.5.24). Le **nom exact du header**
  et le **format de la clé** ne sont **pas confirmés** (page bloquée en accès
  direct → à valider sur la doc / auprès du support).
- Endpoints crew confirmés par les release notes : `GET /api/Crewing`,
  `GET /api/Crewing/CrewMember`, `GET /api/CrewingSchedule` (+ POST/PUT),
  `GET /api/CrewingRestHours`, `POST /api/CrewingDocuments/GetPassportDetails`,
  `POST /api/CrewingDocuments/GetCrewMembersDocuments`, plus
  `GET /api/ranks/getranks`, `GET /api/vessels/getVessels`,
  `GET /api/Synchronization/getSyncDetails`.
- **Rate limit connu** : `GET /api/Crewing` et `GET /api/CrewingSchedule` sont
  classés *HighProcessing* → **1 requête / minute** ; les autres méthodes
  **15 req/min**. C'est dimensionnant : impose un **pull périodique batch**,
  pas du temps réel.
- **Recommandation : option (a) — pull périodique** via un endpoint interne
  `POST /api/marad/sync` protégé par `X-API-Token` (cron Power Automate),
  qui appelle un service de sync **upsert** préservant les champs saisis à la
  main (modèle calqué sur `services/pipedrive_sync.py`).
- **Inconnues bloquantes** avant implémentation : nom du header d'auth +
  procédure d'obtention de clé ; schéma JSON exact des réponses (noms de
  champs) ; modèle de filtrage incrémental (`createdDate`/`modifiedDate`,
  format) ; pagination ; existence de webhooks. → **questions à l'éditeur**
  en §9.

---

## 1. Découverte de l'API Marad

### 1.1 Niveau de certitude — légende

| Marqueur | Sens |
|---|---|
| ✅ **Confirmé** | Lu dans une source publique (URL citée). |
| 🟡 **Indice** | Évoqué par un résultat de recherche, non vérifié sur la doc primaire. |
| ❓ **Inconnu** | Non trouvé — à confirmer auprès de l'éditeur / sur la doc authentifiée. |

> ⚠️ **Limite d'environnement.** Les hôtes `marad.com`, `marad.cloud` et
> surtout `external.marad.ms` (le portail API) **renvoient HTTP 403** à toute
> requête sortante directe depuis cet environnement (WAF / anti-bot, et/ou
> auth requise). Les faits ci-dessous proviennent donc d'**extraits indexés
> par les moteurs de recherche**, pas d'une lecture intégrale de la doc. Tout
> ce qui est marqué ✅ doit malgré tout être **re-vérifié sur la doc primaire**
> une fois l'accès navigateur / la clé d'API obtenus.

### 1.2 Éditeur & produit

- ✅ **Marad** est édité par **MaraSoft B.V.** (NL). Système de ship/fleet
  management utilisé par **4 200+ navires**, modules : maintenance,
  procurement, QHSE, safety, inventory, **certificates**, **crewing**, flgo…
  Sources : marad.com, marasoft.nl/modules/crewing.
- ✅ Domaines : vitrine `marad.com` et `marad.cloud` ; **API = `external.marad.ms`**.

### 1.3 Existence & nature de l'API — ✅ Confirmé

- ✅ Marad annonce une **API REST** : *« The Marad REST API enables you to
  interact with Marad programmatically […] build apps, script interactions
  with Marad, or develop any other type of integration »* — page
  `marad.com/developer/`.
- ✅ L'API a un nom : **« Marasoft Generic API »**, avec :
  - une page **Release Notes** : `https://external.marad.ms/api/releasenotes`
  - un **core / portail** (probable Swagger UI) : `https://external.marad.ms/index.html`
    (titre indexé : « Marasoft Generic API Core »).
- 🟡 Style : **REST / JSON** sur ASP.NET (le versionnage `v5.5.x`, le style
  des chemins `api/Pascal/Case`, et la mention « HighProcessing/Static data »
  pointent fortement vers une stack .NET / ASP.NET Web API). Format JSON =
  hypothèse forte mais **non lue noir sur blanc** → ❓ à confirmer.
- ❓ **OpenAPI/Swagger** : très probable (`/index.html` = Swagger UI typique ;
  tenter aussi `/swagger`, `/swagger/v1/swagger.json`) mais **non confirmé**.
- ❓ **SOAP / GraphQL** : aucun indice → considérer comme REST-only.

### 1.4 Authentification — 🟡 / ❓

- ✅ **Clé d'API** (API Key). Mécanisme confirmé par les release notes :
  *« In v5.5.23, the API Key via Query String Parameters remains temporarily
  supported for backward compatibility. Starting with v5.5.24 this will be
  removed; the API Key must be provided exclusively in the request headers. »*
  → **Conclusion : header obligatoire à terme.**
- ❓ **Nom exact du header** : NON confirmé. Candidats fréquents pour une API
  .NET : `X-Api-Key`, `ApiKey`, `Authorization: ApiKey <clé>`, ou
  `Ocp-Apim-Subscription-Key` (si Azure API Management). **À confirmer.**
- ❓ **OAuth2 / Basic** : aucun indice d'OAuth2. On part sur **clé statique par
  société/tenant**. À confirmer (notamment : 1 clé = 1 « company », scope par
  navire ?).
- ❓ **Obtention de la clé** : *« contact the support department »*
  (`support@marasoft.nl`). Pas de self-service apparent → **démarche
  commerciale / contractuelle requise** (typique des logiciels maritimes
  propriétaires).

### 1.5 Endpoints pertinents (crew) — ✅ noms confirmés par les release notes

Les **chemins** ci-dessous sont cités dans les release notes indexées ; les
**paramètres et schémas de réponse** ne le sont pas (❓).

| Méthode + chemin | Usage (d'après release notes) | Certitude |
|---|---|---|
| `GET /api/Crewing` | Liste des crew members. **Filtrable par date de création / modification** (idéal pour le delta sync). | ✅ chemin + filtre |
| `GET /api/Crewing/CrewMember` | Un crew member par ID. | ✅ |
| `GET /api/CrewingSchedule` | Plannings/rotations (embarquements). Renvoie l'**ID** de chaque schedule. | ✅ |
| `POST` / `PUT /api/CrewingSchedule` | **Écriture** (add/update schedule) — **À NE PAS UTILISER** (read-only). | ✅ (à exclure) |
| `GET /api/CrewingRestHours` | Heures de repos/travail (MLC). | ✅ |
| `POST /api/CrewingDocuments/GetPassportDetails` | Détails passeport de **plusieurs** crew members (POST mais c'est une **lecture** — batch). | ✅ |
| `POST /api/CrewingDocuments/GetCrewMembersDocuments` | Documents de **plusieurs** crew members (batch, lecture). | ✅ |
| `POST` / `DELETE /api/CrewingDocuments` | Ajout / suppression de documents — **À NE PAS UTILISER** (écriture). | 🟡 |
| `GET /api/CrewingDocuments/...` (group names) | Liste des noms de groupes de documents de la société. | 🟡 |
| `GET /api/ranks/getranks` | Référentiel des **rangs/postes** (mapping vers notre `role`). | ✅ |
| `GET /api/vessels/getVessels` | Référentiel **navires** (mapping vers nos `Vessel`). | ✅ |
| `GET /api/Synchronization/getSyncDetails` | Métadonnées de synchro (probable curseur/horodatage de delta). | ✅ chemin |

> ⚠️ **Attention sémantique** : certains `POST` Marad sont des **lectures**
> (ex. `GetPassportDetails`, `GetCrewMembersDocuments` — POST pour passer une
> liste d'IDs en body). « Read-only » côté mynewtowt = **ne jamais appeler les
> endpoints qui *mutent* l'état Marad** (`POST/PUT /api/CrewingSchedule`,
> `POST/DELETE /api/CrewingDocuments`, etc.), **pas** « n'émettre que des
> GET ». On maintiendra une **whitelist explicite d'endpoints autorisés**
> (cf. §6).

### 1.6 Pagination — ❓

Non documenté dans les extraits. À confirmer : `?page=&pageSize=`, curseur,
ou renvoi complet. Le `GetSyncDetails` suggère un mécanisme de **curseur de
synchro** (delta). **Bloquant léger** : à clarifier avant d'écrire la boucle
de pagination.

### 1.7 Rate limits — ✅ Confirmé (dimensionnant)

D'après les release notes :

- **1 requête / minute** (classées *HighProcessing/Static data*) :
  `GET /api/Crewing`, `GET /api/CrewingSchedule`, `GET /api/AddressBooks/GetSuppliers`,
  `GET /api/indicators`, `GET /api/ranks/getranks`,
  `GET /api/Synchronization/getSyncDetails`, `GET /api/users/getusers`,
  `GET /api/vessels/getVessels`, etc.
- **15 requêtes / minute** : toutes les autres méthodes.

**Implication directe** : la liste crew (`GET /api/Crewing`) est plafonnée à
**1 appel/min**. → On **récupère la liste en un seul appel** (paginé si
nécessaire, mais espacé d'≥ 60 s entre pages) puis on enrichit via les
endpoints 15/min. → **Cron toutes les 30–60 min** largement suffisant et
sûr ; **jamais d'appel à la volée** sur ces endpoints.

### 1.8 Webhooks — ❓

Aucun indice. Présence de `getSyncDetails` ⇒ modèle **pull / delta**, pas
push. À confirmer auprès de l'éditeur (si webhooks existaient, ils
simplifieraient l'incrémental).

### 1.9 Modèle de données crew exposé (côté Marad) — 🟡 (page produit, pas l'API)

D'après `marasoft.nl/modules/crewing` et `marad.com/features/crewing` :

- **Personne** : coordonnées (contact), **photo**, données personnelles,
  remarques.
- **Certificats & identités** : certificates of competency, examination
  papers, **passeport / IDs** ; Marad **alerte à l'expiration**.
- **Schedules** : planning d'embarquement / rotations.
- **Work & rest hours** (MLC) ; **crew lists** & **IMO forms** générés auto.
- **Documents** (+ templates), **non-conformity lists**, **safety drills**
  (lié au module Safety).

> Ce sont les **libellés produit**, pas les **champs JSON** de l'API. Les noms
> de champs exacts (camelCase ? PascalCase ?) sont ❓ → §9.

---

## 2. Notre modèle crew (cible de l'import)

Source lue : `app/models/crew.py`, `app/routers/crew_router.py`,
`app/services/crew_compliance.py`.

| Entité mynewtowt | Champs clés |
|---|---|
| `CrewMember` (`crew_members`) | `full_name`, `role`, `nationality` (CHAR2), `date_of_birth`, `passport_number`, `passport_expires_at`, `schengen_status` / `schengen_days_in_window` / `schengen_window_end` (**calculés en interne**), `visa_us_expires_at`, `visa_br_expires_at`, `seaman_book_number`, `seaman_book_expires_at`, `email`, `phone`, `is_active`, `notes`, `created_at` |
| `CrewCertification` (`crew_certifications`) | `crew_member_id`, `kind` (`stcw_basic`, `medical`, `gmdss`, `visa_us`…), `reference`, `issued_at`, `expires_at`, `document_url` |
| `CrewAssignment` (`crew_assignments`) | `crew_member_id`, `leg_id` (**FK leg interne**), `role_on_board`, `embark_at`, `disembark_at`, `embark_port_id`, `disembark_port_id`, `notes` |
| `CrewLeave` (`crew_leaves`) | congés — **100 % saisie ERP, hors périmètre Marad** |

**Particularités à respecter :**

- `role` est contraint par `CREW_ROLES` (FR : `capitaine`, `second`,
  `chef_mecanicien`, `cook`, `lieutenant`, `bosco`, `marin`,
  `eleve_officier`). Le référentiel Marad (`getranks`) **ne mappera pas 1:1**
  → table de correspondance obligatoire (rang Marad → rôle FR).
- Schengen (`schengen_*`) est **dérivé localement** (`crew_compliance.py`,
  FLX-06) à partir des assignments. **Ne jamais l'importer / l'écraser.**
- `CrewAssignment.leg_id` référence un **leg interne mynewtowt**. Les
  schedules Marad pointent un **navire Marad + dates**, pas nos legs → le
  rattachement à un leg est **non trivial** (voir §3 et §8, risque majeur).

---

## 3. Mapping de champs Marad → mynewtowt

> ⚠️ Colonne « Champ Marad » = **hypothèse** sur la base des libellés produit
> et des conventions REST .NET. **À remplacer par les noms réels** une fois le
> schéma JSON obtenu. Aucun de ces noms n'est confirmé par la doc.

### 3.1 `GET /api/Crewing` → `CrewMember` — ✅ schéma confirmé (échantillon éditeur 2026-06-17)

Schéma réel renvoyé par l'API (champs notables) : `id` (**GUID**, ex.
`3fa85f64-5717-4562-b3fc-2c963f66afa6`), `callName`, `firstName`, `lastName`,
`gender` (int), `birthDate` (ISO datetime), `countryOfBirthName`,
`cityOfBirthName`, `nationality`, `crewAgents[]`, `airportName`, `phone`,
`mobilePhone`, `email`, `noticeEmail`, `idNumber`, `bankAccount`, `ranks[]`
(liste de libellés), `livingAddress{}`, `postAddress{}`, `sizes{}`,
`vesselNames[]`.

Mapping **implémenté** dans `app/services/marad_sync.py` (additif, non
destructeur — un champ n'est écrasé que si Marad fournit une valeur exploitable,
les placeholders Swagger `"string"` sont ignorés) :

| Champ mynewtowt | Champ Marad (confirmé) | Notes |
|---|---|---|
| **`marad_id`** | `id` | **GUID** → colonne `String(36)` (clé de réconciliation, §4) |
| `full_name` | `firstName` + `lastName` (fallback `callName`) | concaténé, `(sans nom)` si vide |
| `role` | `ranks[0]` | 1er rang (libellé brut, tronqué 60). Mapping fin rang→rôle FR = TODO via `getranks` |
| `nationality` | `nationality` | conservé **uniquement** si code ISO-2 (colonne CHAR(2)) |
| `date_of_birth` | `birthDate` | parse ISO datetime → date |
| `email` | `email` | gardé si contient `@` |
| `phone` | `mobilePhone` (fallback `phone`) | tronqué 50 |
| `is_active` | — | défaut `True` à la création, **préservé** ensuite (Marad ne fournit pas de flag) |
| `passport_*`, `seaman_book_*` | via `GetPassportDetails` / documents | **non encore branché** |
| `visa_us_expires_at`, `visa_br_expires_at` | documents type visa | **non encore branché** |
| `schengen_*` | — | **JAMAIS importé** (calcul interne, §5.2) |
| `notes` | — | **JAMAIS écrasé** par la sync |

**Champs Marad volontairement NON importés** (sensibles ou hors modèle) :
`bankAccount` (**donnée bancaire — jamais stockée**), `idNumber`,
`livingAddress`/`postAddress`, `sizes`, `crewAgents`, `airportName`, `gender`,
`countryOfBirthName`/`cityOfBirthName`, `noticeEmail`, `vesselNames`
(le rattachement navire/leg reste manuel, §3.3 / §8).

### 3.2 `GetCrewMembersDocuments` → `CrewCertification`

| Champ mynewtowt | Champ Marad (hypothèse ❓) | Notes |
|---|---|---|
| `kind` | type/group de document | **mapping type Marad → `kind` interne** |
| `reference` | numéro de document | |
| `issued_at` | date d'émission | |
| `expires_at` | date d'expiration | Marad gère déjà l'alerte d'expiration |
| `document_url` | URL/blob du document | ❓ Marad renvoie-t-il une URL stable ou un binaire ? |
| `crew_member_id` | FK locale résolue via `marad_id` | |
| **`marad_document_id`** (à ajouter) | `id` du document | clé d'idempotence |

### 3.3 `GET /api/CrewingSchedule` → table miroir `marad_crew_schedules` — ✅ schéma confirmé (échantillon éditeur 2026-06-18)

Schéma réel (objets **imbriqués**) :

```jsonc
{
  "id": "<GUID schedule>",
  "crewMember": { "id": "<GUID marin>", "firstName": "…", "lastName": "…",
                  "crewAgents": ["…"], "employeeNumber": "…", "idNumber": "…" },
  "rank": "…", "status": "…", "vessel": "<NOM du navire>",
  "startInfo": { "dateTime": "<ISO>", "date": "…", "time": "…", "remarks": "…", "port": "…" },
  "endInfo":   { "dateTime": "<ISO>", "date": "…", "time": "…", "remarks": "…", "port": "…" }
}
```

> **Décision appliquée** : on **ne crée PAS** de `CrewAssignment` (dont `leg_id`
> est une FK obligatoire). Les schedules sont importés dans une **table miroir
> read-only** `marad_crew_schedules` (modèle `MaradCrewSchedule`, migration
> `0043`) et affichés sur la fiche marin (`/crew/members/{id}`, section
> « Planning Marad »).

> **Précision métier (confirmée)** : chez Marad, **un « voyage » = notre `leg`**.
> Le schéma CrewingSchedule **n'expose pas de code voyage** : la réconciliation
> au `leg` se fait donc par **navire (nom) + fenêtre de dates** — on retient le
> leg du navire dont l'intervalle `[atd|etd, ata|eta]` contient la date
> d'embarquement (`startInfo.dateTime`). `leg_id` NULL si aucun leg ne
> correspond (rattachement manuel possible). `marad_voyage_ref` stocke la route
> `port départ → port arrivée` à titre indicatif.

| Colonne miroir | Champ Marad confirmé | Notes |
|---|---|---|
| `marad_schedule_id` | `id` | clé d'idempotence (GUID) |
| `crew_member_id` | `crewMember.id` (imbriqué) | résolu via `CrewMember.marad_id` |
| `marad_crew_id` | `crewMember.id` (brut) | conservé pour re-résoudre après un sync crew |
| `vessel_id` | `vessel` (**nom**) | résolu par nom `Vessel.name` (repli `MARAD_VESSEL_MAP`) |
| `marad_vessel_name` | `vessel` | nom brut |
| `marad_voyage_ref` | `startInfo.port` → `endInfo.port` | route POL→POD (pas de code voyage) |
| `leg_id` | (réconcilié) | navire + fenêtre de dates (voyage = leg) |
| `rank_label` | `rank` | |
| `start_date` / `end_date` | `startInfo.dateTime` / `endInfo.dateTime` | parse ISO → date |
| `status` | `status` | |

**Champs Marad non importés** : `crewMember.idNumber`/`employeeNumber`/
`crewAgents` (sensibles/hors modèle), `startInfo`/`endInfo` `remarks`/`time`.

### 3.4 Référentiels

- `GET /api/ranks/getranks` → table de mapping `marad_rank → role` (config,
  pas un modèle métier).
- `GET /api/vessels/getVessels` → mapping `marad_vessel_id → Vessel.id`
  (analogue à `TRACKING_VESSEL_MAP` déjà utilisé pour le tracking satcom).

---

## 4. Réconciliation & idempotence

- Ajouter une **clé externe stable** sur les entités importées :
  `crew_members.marad_id` (nullable, unique), `crew_certifications.marad_document_id`,
  et la table miroir `marad_crew_schedules.marad_schedule_id`.
  → Exactement le pattern `Client.pipedrive_org_id` (cf.
  `services/pipedrive_sync.py`).
- **Upsert** : `marad_id` connu → UPDATE doux (champs Marad uniquement) ;
  inconnu → INSERT. Aucune suppression destructive : un marin disparu de
  Marad est marqué `is_active = False` (jamais hard-delete — cohérent avec
  l'append-only / audit du repo).
- **Delta** : utiliser le filtre `createdDate`/`modifiedDate` de
  `GET /api/Crewing` + `getSyncDetails` pour ne récupérer que les
  modifications depuis le dernier passage (stocker le curseur, ex. dans une
  table `integration_state` ou une colonne dédiée). Réduit la pression sur le
  rate limit 1/min.

---

## 5. Garantir le read-only & préserver le travail local

Deux exigences distinctes :

### 5.1 Ne jamais écrire dans Marad

- Le client HTTP Marad **n'expose que des fonctions de lecture** ; aucune
  fonction `create_*` / `update_*` / `delete_*` (contrairement à
  `utils/pipedrive.py` qui, lui, a des `create_organization`/`create_deal`).
- **Whitelist d'endpoints** dans le client (constante `READ_ENDPOINTS`).
  Toute requête hors whitelist lève une exception → garde-fou anti-régression.
- **Verbe non garant de l'innocuité** : `GetPassportDetails`/
  `GetCrewMembersDocuments` sont des POST de lecture → on les autorise
  explicitement ; on **interdit** `POST/PUT/DELETE /api/CrewingSchedule` et
  `POST/DELETE /api/CrewingDocuments`.

### 5.2 Séparer « importé de Marad » vs « saisi dans l'ERP »

Calqué sur le upsert Pipedrive (`existing.name`/`existing.address` mis à
jour, mais `client_type` et contacts manuels **préservés**) :

- **Champs « possédés par Marad »** (écrasables à chaque sync) :
  identité, passeport, certificats, rangs, dates d'expiration de documents.
- **Champs « possédés par l'ERP »** (jamais écrasés par la sync) : `notes`
  (sauf si vide), rattachement leg (`CrewAssignment`), `schengen_*`, congés
  (`CrewLeave`), et toute décision/override d'embarquement (FLX-06).
- En pratique : la sync ne touche **que** la liste blanche de colonnes
  « Marad-owned ». Documenter cette frontière dans le code et l'UI (badge
  « importé de Marad » sur les champs concernés).

---

## 6. Architecture recommandée

### 6.1 Comparatif des options

| Option | Description | Pour | Contre | Verdict |
|---|---|---|---|---|
| **(a) Pull périodique** (cron Power Automate → endpoint interne → service upsert) | `POST /api/marad/sync` (X-API-Token) déclenché toutes les 30–60 min ; service lit Marad et upsert en base. | Aligné sur l'existant (`/api/weather/refresh`, `/api/veille/refresh`, `/api/tickets/escalate-sla`) ; **respecte le rate limit 1/min** ; données dispo hors-ligne ; un seul point d'appel sortant ; idempotent. | Latence (fraîcheur ≤ intervalle de cron). | ✅ **Recommandé** |
| (b) À la volée + cache | Appel Marad au chargement de la page Crew, cache court. | Données fraîches à la demande. | **Incompatible avec 1 req/min** sur `GET /api/Crewing` ; couple l'UI à la dispo de Marad ; CSP/latence ; complexité cache. | ❌ |
| (c) Réplica / exports CSV-SFTP | Si pas d'API : exports plats déposés par Marad. | Robuste sans API. | **Inutile ici** : l'API existe. À garder comme **plan B** si l'accès API n'est pas accordé. | 🟡 fallback |

**Décision : (a) pull périodique.** C'est le seul compatible avec le rate
limit confirmé, et c'est exactement le pattern déjà éprouvé dans le repo.

### 6.2 Schéma de flux (option a)

```
Power Automate (cron 30–60 min)
        │  POST /api/marad/sync   header X-API-Token: <MARAD_SYNC_TOKEN>
        ▼
mynewtowt  app/routers/marad_router.py  (api_router)
        │  - vérifie X-API-Token (compare_digest)  → 503 si non configuré, 403 si invalide
        │  - 503 si MARAD_API_TOKEN absent
        ▼
app/services/marad_sync.py   sync_crew(db)
        │  upsert idempotent (marad_id), préserve champs ERP
        ▼
app/utils/marad.py   client HTTP read-only (httpx, whitelist d'endpoints)
        │  GET /api/Crewing (delta) → GetPassportDetails / GetCrewMembersDocuments / getranks / getVessels
        ▼
   https://external.marad.ms   (header API key)
```

Le déclencheur in-app (bouton « Synchroniser Marad » sur `/crew`, permission
`crew`/M) reste possible en **fallback manuel**, comme « Synchroniser
Pipedrive » sur `/commercial/clients`.

### 6.3 Esquisse de signatures (NON implémenté — illustratif)

```python
# app/utils/marad.py — client read-only
READ_ENDPOINTS: frozenset[str] = frozenset({
    "/api/Crewing",
    "/api/Crewing/CrewMember",
    "/api/CrewingDocuments/GetPassportDetails",      # POST mais lecture
    "/api/CrewingDocuments/GetCrewMembersDocuments", # POST mais lecture
    "/api/CrewingRestHours",
    "/api/CrewingSchedule",                          # GET only
    "/api/ranks/getranks",
    "/api/vessels/getVessels",
    "/api/Synchronization/getSyncDetails",
})

def enabled() -> bool: ...  # True si MARAD_API_TOKEN configuré

async def _get(path: str, *, params: dict | None = None) -> dict | None: ...
async def _post_read(path: str, *, json: dict) -> dict | None: ...
    # garde-fou : path ∈ READ_ENDPOINTS et explicitement marqué "lecture"

async def list_crew(modified_since: datetime | None = None) -> list[dict]: ...
async def get_passport_details(crew_ids: list[int]) -> list[dict]: ...
async def get_documents(crew_ids: list[int]) -> list[dict]: ...
async def list_ranks() -> list[dict]: ...
async def ping() -> bool: ...  # pour la page admin/settings

# app/services/marad_sync.py — upsert read-only
def is_configured() -> bool: ...
async def sync_crew(db: AsyncSession) -> dict:
    """Upsert crew Marad → crew_members (+ certifications).
    Préserve les champs ERP. Renvoie {configured, created, updated,
    deactivated, skipped, errors}. No-op propre si non configuré."""
```

Le service est **no-op** si `MARAD_API_TOKEN` est absent (comme
`pipedrive`/`newsdata`) → l'ERP tourne en local sans dépendance.

---

## 7. Variables d'environnement à prévoir

À ajouter dans `app/config.py` (section `# External`) et `.env.example` :

| Variable | Rôle | Défaut |
|---|---|---|
| `MARAD_BASE_URL` | Base de l'API Marasoft. | `https://external.marad.ms` |
| `MARAD_API_TOKEN` | **Clé d'API Marad** (header). Secret. | `None` → service no-op |
| `MARAD_API_KEY_HEADER` | Nom du header d'auth (❓ à confirmer). | `X-Api-Key` (provisoire) |
| `MARAD_SYNC_TOKEN` | Token `X-API-Token` du cron interne `POST /api/marad/sync`. | `None` → 503 |
| `MARAD_VESSEL_MAP` | Mapping `marad_vessel_id=vessel_id,...` (cf. `TRACKING_VESSEL_MAP`). | `""` |
| `MARAD_RANK_MAP` (option) | Mapping rang Marad → rôle FR. | défaut en dur |

**Sécurité** : secrets **hors repo** (`.env`, jamais commités) ; clé Marad =
**lecture seule côté Marad** (demander un compte/rôle API en lecture seule si
l'éditeur le permet — cf. §9) ; comparaison `X-API-Token` en temps constant
(`secrets.compare_digest`, comme tracking/veille/tickets).

---

## 8. Risques & inconnues

| # | Risque / inconnue | Impact | Mitigation |
|---|---|---|---|
| R1 | **Accès API non garanti** : clé sur demande éditeur, possible contrat/NDA. | Bloquant total. | Démarche commerciale en amont ; plan B = export CSV/SFTP (option c). |
| R2 | **Nom du header d'auth inconnu.** | Bloquant impl. | Rendre configurable (`MARAD_API_KEY_HEADER`) ; confirmer auprès du support. |
| R3 | **Schémas JSON inconnus** (noms de champs, structure nom, formats date/TZ). | Bloquant mapping. | Obtenir Swagger + 1 payload réel par endpoint avant d'écrire le mapping. |
| R4 | **Schedule Marad ↔ leg interne** non mappable. | Fonctionnel. | V1 : table miroir read-only, **pas** d'auto-création de `CrewAssignment`. |
| R5 | **Rate limit 1 req/min** sur `GET /api/Crewing`. | Perf/robustesse. | Pull batch + delta (`modifiedDate`/`getSyncDetails`) ; backoff sur 429. |
| R6 | **Référentiel rangs ≠** nos 8 rôles FR. | Données. | Table de mapping explicite + log des rangs non mappés. |
| R7 | **Pagination inconnue.** | Impl. | Confirmer ; prévoir boucle paginée espacée d'≥ 60 s. |
| R8 | **Documents : URL vs binaire.** | Stockage. | Si binaire, prévoir stockage local + `document_url` interne ; si URL Marad, vérifier qu'elle est accessible sans session interactive. |
| R9 | **Écrasement du travail ERP.** | Données. | Whitelist de colonnes « Marad-owned » (§5.2) + tests. |
| R10 | **403 / WAF** observé depuis cet env. | Vérif. | Confirmer la joignabilité depuis l'hôte de prod + IP allowlist éventuelle côté Marad. |

---

## 9. Questions à poser à l'éditeur Marad / MaraSoft (`support@marasoft.nl`)

1. Comment obtient-on une **clé d'API** ? Existe-t-il un **rôle/clé en lecture
   seule** (l'intégration n'écrira jamais dans Marad) ? Conditions
   contractuelles / NDA ?
2. **Nom exact du header** d'authentification et **format** de la clé
   (`X-Api-Key`, `Authorization`, `Ocp-Apim-Subscription-Key` … ?).
3. Pouvez-vous fournir l'**OpenAPI/Swagger** complet (ou l'accès à
   `external.marad.ms/index.html`) et un **exemple de payload réel** pour
   `GET /api/Crewing`, `GET /api/Crewing/CrewMember`,
   `GET /api/CrewingSchedule`, `GetPassportDetails`,
   `GetCrewMembersDocuments`, `getranks`, `getVessels` ?
4. **Pagination** : quels paramètres (`page`/`pageSize`, curseur) et quelle
   taille de page max ?
5. **Synchronisation incrémentale** : format/sémantique des filtres
   `createdDate`/`modifiedDate` ; rôle exact de
   `GET /api/Synchronization/getSyncDetails` (curseur de delta ?).
6. **Webhooks / push** disponibles pour les changements crew ? Sinon,
   intervalle de polling recommandé compte tenu du rate limit 1/min ?
7. **Documents/photos** : l'API renvoie-t-elle une URL stable, un base64, ou
   un binaire ? Authentification requise pour télécharger ?
8. **Référentiel des rangs** (`getranks`) : liste exhaustive et codes stables
   pour bâtir le mapping rang→rôle ?
9. **Champs MLC / rest hours** : structure de `GET /api/CrewingRestHours` ?
10. Y a-t-il une **IP allowlist** ou une restriction réseau côté Marad pour
    l'appelant (notre serveur de prod) ?
11. **Comportement en cas de dépassement** du rate limit (HTTP 429 ?
    `Retry-After` ?).

---

## 10. Plan d'implémentation par étapes (après obtention de l'accès)

> **Préalable bloquant : §9 (clé + Swagger + payloads réels).** Tant que ce
> n'est pas obtenu, l'implémentation reste suspendue.

1. **Migration Alembic** : ajouter `crew_members.marad_id`,
   `crew_certifications.marad_document_id`, table miroir
   `marad_crew_schedules`, (option) table `integration_state` pour le curseur.
2. **Config** : variables §7 dans `config.py` + `.env.example`.
3. **Client** `app/utils/marad.py` (httpx, header configurable, whitelist
   `READ_ENDPOINTS`, no-op si non configuré, gestion 429/backoff).
4. **Service** `app/services/marad_sync.py` (`sync_crew` upsert idempotent,
   préservation des champs ERP, mapping rangs/navires).
5. **Routes** `app/routers/marad_router.py` :
   `POST /api/marad/sync` (X-API-Token, cron) + bouton manuel
   `POST /crew/sync-marad` (permission `crew`/M, fallback in-app).
   `services.activity.record()` sur chaque sync.
6. **Admin** : intégrer `marad.ping()` dans la page admin/settings + doc
   runbook (`docs/operations/`), cron Power Automate.
7. **UI Crew** : badge « importé de Marad » sur les champs Marad-owned ;
   afficher schedules miroir sur la fiche marin (lecture).
8. **Tests** (`tests/`) : upsert (create/update/skip/deactivate), préservation
   des champs ERP, garde-fou whitelist (refus d'un endpoint d'écriture),
   no-op sans token, 503/403 sur l'endpoint cron.
9. **Sécurité** : `/security-review` avant merge (CLAUDE.md) ; vérifier
   qu'aucune fonction d'écriture Marad n'existe dans le client.

---

## Sources

- [Marad — Developer (REST API)](https://marad.com/developer/)
- [Marad — Crewing (features)](https://marad.com/features/crewing/)
- [Marasoft Generic API — Release Notes](https://external.marad.ms/api/releasenotes)
- [Marasoft Generic API Core (portail / Swagger probable)](https://external.marad.ms/index.html)
- [MaraSoft — Modules — Crewing](https://www.marasoft.nl/modules/crewing/)
- [Marad — Integrations](https://marad.com/integrations/)
- [Capterra — Marad](https://www.capterra.com/p/10024267/Marad/)

> ⚠️ Les pages `marad.com` / `external.marad.ms` ont renvoyé **HTTP 403** à la
> récupération directe depuis l'environnement de cadrage ; les faits ci-dessus
> proviennent d'extraits indexés et **doivent être re-vérifiés sur la doc
> primaire** une fois l'accès navigateur / la clé d'API obtenus.
