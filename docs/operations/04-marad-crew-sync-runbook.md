# Runbook — Synchronisation Marad (crew + plannings)

> Intégration **LECTURE SEULE** : mynewtowt lit les marins et les plannings
> d'embarquement depuis Marad et ne réécrit **jamais** dans Marad. Le cron
> Power Automate déclenche périodiquement `POST /api/marad/refresh`.
> Cf. `docs/integrations/marad-crew-readonly.md` pour la conception.

## 1. Vue d'ensemble

| Élément | Détail |
|---|---|
| Endpoint cron | `POST /api/marad/refresh` (header `X-API-Token: <MARAD_SYNC_TOKEN>`) |
| Action | `sync_all` = marins (`/api/Crewing`) **+** plannings (`/api/CrewingSchedule`) |
| Cibles internes | `crew_members` (upsert via `marad_id`) + `marad_crew_schedules` (miroir) |
| Page | `/crew` (bouton « Synchroniser Marad » = même action, manuelle) |

## 2. Variables d'environnement (`.env`)

| Variable | Rôle | Sans elle |
|---|---|---|
| `MARAD_API_TOKEN` | Clé d'API Marad (fournie par l'éditeur) | sync = **no-op** (`configured:false`) |
| `MARAD_BASE_URL` | Hôte du **serveur de votre tenant** (⚠ voir ci-dessous) | défaut `https://external.marad.ms` |
| `MARAD_SYNC_TOKEN` | Secret du header `X-API-Token` du cron interne | endpoint → **503** |
| `MARAD_API_KEY_HEADER` | force le header d'auth Marad (recommandé : `ApiKey`) | auto-détecté : `ApiKey`/`ApiToken`/`X-Api-Key` (la cascade peut déclencher des 429) |
| `MARAD_VESSEL_MAP` | (optionnel) repli `marad_number_ou_nom=vessel_id,…` | résolution navire par nom/code uniquement |

> ⚠️ **`MARAD_BASE_URL` est par tenant.** Marasoft héberge chaque client sur un
> serveur numéroté (`external.marad.ms`, `external02.marad.ms`, …). Une clé
> valide peut **authentifier** sur un autre serveur mais y voir un **tenant
> vide** (`getVessels` → `[]`, 0 marin, 0 planning). Reprendre l'hôte utilisé
> par vos autres intégrations Marad (ex. requêtes Power BI). Tenant NEWTOWT :
> `https://external02.marad.ms` (header d'auth confirmé : `ApiKey`).

## 3. Activation (première mise en service)

```bash
# 1) Installer les deux clés (génère un MARAD_SYNC_TOKEN aléatoire) :
./scripts/set_marad_keys.sh --api-token "<clé Marad>" --gen-sync
#    → noter le MARAD_SYNC_TOKEN affiché (longueur seulement) ; sa valeur est
#      dans .env. C'est ce secret qu'on remettra dans Power Automate.

# 2) Contrôler les clés (doit afficher ✓ MARAD_API_TOKEN / ✓ MARAD_SYNC_TOKEN) :
./scripts/check_api_keys.sh

# 3) Déployer (build + alembic upgrade head [table marad_crew_schedules] + recreate) :
./scripts/deploy.sh
```

> Pour relire le `MARAD_SYNC_TOKEN` (à recopier dans Power Automate) :
> `grep '^MARAD_SYNC_TOKEN=' .env`

## 4. Cron Power Automate — sync Marad (toutes les 30–60 min)

⚠️ **`/api/Crewing` et `/api/CrewingSchedule` sont à 1 req/min avec une fenêtre
partagée** : les enchaîner dans un seul appel force un **429** sur les plannings,
et l'attendre dépasse le timeout du reverse-proxy (**Caddy coupe à ~60 s → 504**).
On **découple** donc en deux appels courts espacés, via le paramètre `?only=`.

Créer un flux *planifié* (Scheduled cloud flow), toutes les **30 minutes** :

1. **HTTP** — sync **crew** :
   - Méthode `POST`, URI `https://my.towt.eu/api/marad/refresh?only=crew`
   - En-tête `X-API-Token: <MARAD_SYNC_TOKEN>` (brut) — Corps vide.
2. **Delay** — **90 secondes** (action *Delay* Power Automate ; > 60 s pour
   libérer la fenêtre de quota du second endpoint).
3. **HTTP** — sync **plannings** :
   - Méthode `POST`, URI `https://my.towt.eu/api/marad/refresh?only=schedules`
   - Mêmes en-têtes — Corps vide.

> Chaque appel est **court** (un seul endpoint, pas d'attente) → jamais de 504.
> Réponses `200` : `{"part":"crew", "fetched":146, "created":…}` puis
> `{"part":"schedules", "fetched":…, "created":…}`.
>
> **Test manuel** (deux commandes espacées) :
> ```bash
> TOK=$(grep '^MARAD_SYNC_TOKEN=' .env | cut -d= -f2-)
> curl -X POST "https://my.towt.eu/api/marad/refresh?only=crew"      -H "X-API-Token: $TOK"
> sleep 90
> curl -X POST "https://my.towt.eu/api/marad/refresh?only=schedules" -H "X-API-Token: $TOK"
> ```

### Variante (un seul appel) — uniquement si le proxy autorise les réponses > 60 s
`POST /api/marad/refresh` (sans `?only=`) tente les deux et, si les plannings
prennent un 429, patiente `MARAD_SCHEDULE_RETRY_WAIT` s (défaut 65) puis retente.
Sur un proxy plafonné à 60 s (cas NEWTOWT/Caddy) **cet appel renvoie 504** :
préférer le découplage ci-dessus, ou poser `MARAD_SCHEDULE_RETRY_WAIT=0` pour
que l'appel unique réponde vite (crew OK, plannings éventuellement en 429 remonté).

- **Ancienne configuration à un seul appel** (référence) :
  - **Action** : *HTTP*
  - **Méthode** : `POST`
  - **URI** : `https://my.towt.eu/api/marad/refresh`
    *(remplacer par le domaine public réel de l'instance)*
  - **En-têtes** :
    | Clé | Valeur |
    |---|---|
    | `X-API-Token` | `<valeur de MARAD_SYNC_TOKEN>` (brute, sans espace) |
  - **Corps** : *(vide)*

Réponse attendue (`200`) — exemple :

```json
{
  "configured": true,
  "crew_created": 3, "crew_updated": 12, "crew_fetched": 15,
  "sched_created": 5, "sched_updated": 40, "sched_fetched": 45,
  "errors": 0
}
```

- `crew_*` : marins créés / mis à jour / lus dans `/api/Crewing`.
- `sched_*` : plannings créés / mis à jour / lus dans `/api/CrewingSchedule`.
- `configured:false` ⇒ `MARAD_API_TOKEN` absent (rien n'a été lu).
- `errors` > 0 ⇒ enregistrements fautifs ignorés (le batch continue) — voir logs.

> 💡 **Idempotent** : un même `marad_id` (marin) ou `marad_schedule_id` (planning)
> met à jour la ligne existante — aucun doublon. Les champs saisis dans l'ERP
> (Schengen, visas, notes, rattachement leg manuel) ne sont **jamais** écrasés.

## 5. Exploitation

- Vérifier le résultat dans l'UI : `/crew` (liste marins) et la fiche marin
  (`/crew/members/{id}`, section « Planning Marad »).
- Forcer une synchro à la demande : bouton **« Synchroniser Marad »** sur `/crew`.
- Logs applicatifs : lignes `marad: …` (header retenu, comptes, erreurs).

## 6. Dépannage

### `POST /api/marad/refresh` → 503
`MARAD_SYNC_TOKEN` n'est pas configuré dans le `.env` **du conteneur**.
→ `./scripts/set_marad_keys.sh …` puis `docker compose up -d --force-recreate app`.

### `POST /api/marad/refresh` → 403
Le header côté flux ne correspond pas. Vérifier :
- clé d'en-tête exactement `X-API-Token` ;
- valeur = `MARAD_SYNC_TOKEN` **brute** (sans espace, sans guillemets) ;
- pas de dérive host↔conteneur : `./scripts/check_api_keys.sh --container`.

### `200` mais `configured:false`
`MARAD_API_TOKEN` (la clé Marad, ≠ du sync token) manque → l'intégration est
inactive. L'installer avec `set_marad_keys.sh`.

### `200` mais `crew_fetched:0` / `sched_fetched:0`
Marad n'a rien renvoyé. Causes possibles (dans l'ordre de probabilité) :
- **mauvais hôte tenant** : l'auth passe mais `getVessels` renvoie `[]` →
  `MARAD_BASE_URL` pointe sur un serveur qui ne porte pas votre tenant
  (cf. encadré §2). Vérifier avec
  `docker compose exec app python -m scripts.marad_probe vessels` ;
- compte Marad sans marin/planning ;
- **mauvais header d'auth** : le client essaie `ApiKey`/`ApiToken`/`X-Api-Key`
  et journalise celui retenu (`marad: header d'auth retenu = '…'`). S'il n'en
  trouve aucun, fixer `MARAD_API_KEY_HEADER` dans `.env`.

### `429` dès le premier appel (sans Retry-After)
Le quota est déjà consommé par un **autre consommateur de la même clé** —
typiquement un refresh Power BI (chaque refresh du rapport Marad = ~125
requêtes, mêmes endpoints, même clé). Espacer les tests, éviter que le cron
de sync coïncide avec le refresh planifié du rapport, et demander à l'éditeur
une **clé dédiée** à mynewtowt. La cascade de sondage d'auth peut aussi
s'auto-infliger des 429 : épingler `MARAD_API_KEY_HEADER=ApiKey` (un seul
appel par requête).

### Crew OK mais `sched_fetched:0` (« N marins, 0 planning »)
`/api/Crewing` **et** `/api/CrewingSchedule` sont chacun à **1 req/min**, mais la
fenêtre est **partagée en pratique** : appelés coup sur coup dans un même
`sync_all`, le second (plannings) prend un **429**. C'est le cas le plus
fréquent une fois le crew fonctionnel. Trois remèdes, déjà en place :
- **Cron** (`POST /api/marad/refresh`) : `sync_all` **patiente `MARAD_SCHEDULE_RETRY_WAIT`
  secondes (défaut 65) puis retente les plannings une fois** — le crew n'est pas
  rappelé. En pratique le cron remonte donc crew **et** plannings en un passage
  (le call dure ~1 min, normal). Mettre `MARAD_SCHEDULE_RETRY_WAIT=0` pour
  désactiver ce retry.
- **Bouton `/crew`** : réponse immédiate (pas d'attente), mais le 429 est
  désormais **affiché** (bandeau « Synchronisation partielle »). Les plannings
  remonteront au prochain passage du cron, ou reprobez à la main ≥ 1 min après.
- **Test manuel de bout en bout** : `curl -X POST https://<host>/api/marad/refresh
  -H "X-API-Token: <MARAD_SYNC_TOKEN>"` — fait le retry, renvoie crew + plannings.

### Plannings non reliés à un leg (`leg_id` vide)
Le « voyage » Marad est réconcilié au `leg` par **navire + fenêtre de dates**.
Vérifier que le navire (nom/code) et les dates `leg` (`etd`/`eta`) couvrent la
période d'embarquement, ou rattacher manuellement côté ERP.

## 7. Sécurité & confidentialité

- Endpoint protégé par `X-API-Token` (comparaison à temps constant).
- CSRF : `/api/marad/` est exempté (auth par token, pas de cookie).
- Lecture seule côté Marad (whitelist d'endpoints, aucune fonction d'écriture).
- Données sensibles **non importées** : coordonnées bancaires, n° d'identité,
  adresses, tailles (cf. `docs/integrations/marad-crew-readonly.md` §3).
