# Runbook — Tracking trajets & Météo historisée

> Activation, exploitation et dépannage du suivi de **trajet réel** et de la
> **météo historisée**. Audience : staff NEWTOWT / ops déploiement.
>
> Couvre : la page **Tracking** (`/tracking`, historique des trajets), la page
> **Performance › Navigation** (`/navigation`) et le **snapshot météo** Windy
> (`POST /api/weather/refresh`, cron Power Automate toutes les 30 min).

## 1. Vue d'ensemble

| Élément | Détail |
|---|---|
| Page tracking | `/tracking` — positions live + toggle « historique des trajets » (filtre navire × leg × période, trait reliant les points) |
| Page navigation | `/navigation` — par leg : carte points GPS + route théorique, distance réelle vs théorique, durée, distance restante, météo le long du trajet |
| Ingestion positions | `POST /api/tracking/upload` (header `X-API-Token`, cf. runbook tracking satcom) → table `vessel_positions` |
| Endpoint cron météo | `POST /api/weather/refresh` (header `X-API-Token: <WEATHER_API_TOKEN>`) |
| Snapshot manuel | bouton « Snapshot » sur `/navigation` (permission `planning` M) |
| Module RBAC | `planning` — Consult pour voir, Modify pour déclencher un snapshot manuel |
| Fournisseur météo | [Windy Point Forecast](https://api.windy.com/) (si `WINDY_API_KEY`) ; **repli Open-Meteo** sinon (gratuit) |
| Tables | `vessel_positions` (positions satcom) · `vessel_weather` (relevés météo historisés) |
| Migration | `migrations/versions/20260617_0040_vessel_weather.py` (`vessel_weather`) |

**Principe de la météo.** Windy est un service de **prévision** : il ne fournit
pas d'archive profonde. On constitue donc notre propre historique en
**snapshotant toutes les 30 min** la météo (vent · courant · vague · température)
au **dernier point GPS connu de chaque navire**. Ces relevés (`vessel_weather`)
restent disponibles **après coup**, y compris pour les legs déjà réalisés : la
page Navigation lit l'historique, elle n'appelle pas Windy en direct.

## 2. Variables d'environnement (`.env`)

| Clé | Rôle | Sans valeur |
|---|---|---|
| `WEATHER_API_TOKEN` | Secret du header `X-API-Token` pour le cron météo | `POST /api/weather/refresh` → `503` |
| `WINDY_API_KEY` | Clé API Windy (météo) | repli automatique sur **Open-Meteo** (les 4 paramètres restent couverts) |
| `MAPTILER_TOKEN` (ou `MAPBOX_TOKEN`) | Tuiles MapLibre (cartes tracking/navigation) | carte raster OSM lente |
| `TRACKING_API_TOKEN` | Header `X-API-Token` de l'ingestion des positions | `POST /api/tracking/upload` → `503` |

> ⚠️ Le `.env` est lu **au démarrage du conteneur**. Toute modification nécessite
> `docker compose up -d --no-deps --force-recreate app`.

Vérifier l'état des clés (lecture seule, valeurs masquées) :

```bash
./scripts/check_api_keys.sh                 # lit .env
./scripts/check_api_keys.sh --container     # + détecte une dérive host ↔ conteneur
```

`scripts/deploy.sh` exécute automatiquement cette vérification en pré-flight
(bloque si une clé **obligatoire** manque ; avertit pour les optionnelles).

## 3. Activation (première mise en service)

```bash
cd /opt/mynewtowt

# 1) Être sur main et à jour
git checkout main && git pull --ff-only origin main

# 2) Générer le secret du cron météo (hex = pas de caractères ambigus)
NEW="$(openssl rand -hex 32)"
grep -q '^WEATHER_API_TOKEN=' .env \
  && sed -i "s|^WEATHER_API_TOKEN=.*|WEATHER_API_TOKEN=${NEW}|" .env \
  || echo "WEATHER_API_TOKEN=${NEW}" >> .env
#   (ajouter WINDY_API_KEY=... à la main si compte Windy ; sinon repli Open-Meteo)

# 3) Contrôler les clés
./scripts/check_api_keys.sh

# 4) Déployer : build + alembic upgrade head + recreate + smoke /health
./scripts/deploy.sh
```

Vérifications post-déploiement :

```bash
docker compose run --rm app alembic current        # → 20260617_0040
docker compose exec db psql -U towt -d towt -c "\dt vessel_weather"
docker compose exec -T app printenv WEATHER_API_TOKEN
```

## 4. Cron Power Automate — snapshot météo (toutes les 30 min)

Créer un flux *planifié* (Scheduled cloud flow) :

- **Déclencheur** : *Récurrence* — toutes les **30 minutes**.
- **Action** : *HTTP*
  - **Méthode** : `POST`
  - **URI** : `https://my.towt.eu/api/weather/refresh`
    *(remplacer par le domaine public réel de l'instance)*
  - **En-têtes** :
    | Clé | Valeur |
    |---|---|
    | `X-API-Token` | `<valeur de WEATHER_API_TOKEN>` (brute, sans espace) |
  - **Corps** : *(vide)*

Réponse attendue (`200`) — exemple :

```json
{ "saved": 2, "skipped": 1, "errors": 0, "provider": "windy", "details": [] }
```

- `saved` : nouveaux relevés historisés (1 par navire dont le dernier point GPS
  n'était pas encore couvert).
- `skipped` : navires sans nouvelle position depuis le dernier passage
  (**idempotent** : un même point GPS n'est historisé qu'une fois) ou sans
  position du tout.
- `provider` : `windy` si `WINDY_API_KEY` est configurée, sinon `open-meteo`.

> 💡 La granularité de l'historique suit celle des positions satcom : le cron
> 30 min capture la météo du point GPS le plus récent à chaque passage. Si les
> positions arrivent moins souvent, les passages sans nouveau point sont
> simplement `skipped`.

**Optionnel — rejeu de cohérence.** Le même appel peut être déclenché juste
après l'ingestion satcom (`POST /api/tracking/upload`) pour historiser la météo
du point fraîchement reçu sans attendre le prochain tick de 30 min.

## 5. Exploitation

- **Tracking** (`/tracking`) : cliquer « Afficher l'historique des trajets »,
  choisir un navire/leg (filtre de référence) **ou** une période (dates du/au).
  La carte trace tous les points reliés par un trait = parcours réellement
  réalisé. Clic sur un point → date/SOG/COG.
- **Navigation** (`/navigation`) : sélectionner un leg (actif ou historique).
  La page affiche distance réelle vs théorique, durée depuis le départ, distance
  restante, et les relevés météo historisés le long de la trace (clic = détail
  vent/courant/vague/température). Bouton « Snapshot » pour forcer un relevé.

## 6. Dépannage

### `POST /api/weather/refresh` → 503
`WEATHER_API_TOKEN` absent du conteneur. Renseigner le `.env` puis recréer le
conteneur (`docker compose up -d --no-deps --force-recreate app`). Vérifier la
dérive host ↔ conteneur : `./scripts/check_api_keys.sh --container`.

### `POST /api/weather/refresh` → 403
Le token reçu ≠ `WEATHER_API_TOKEN`. Auto-test depuis le conteneur (envoie le
token réellement chargé) :

```bash
docker compose exec -T app python -c "
import urllib.request, urllib.error, os
tok = os.environ.get('WEATHER_API_TOKEN','')
print('token conteneur:', (repr(tok[:6])+'…') if tok else 'VIDE')
req = urllib.request.Request('http://127.0.0.1:8000/api/weather/refresh', method='POST', headers={'X-API-Token': tok})
try:
    r = urllib.request.urlopen(req, timeout=60); print('STATUS', r.status, r.read()[:200])
except urllib.error.HTTPError as e: print('HTTP', e.code)
"
```

Si l'auto-test renvoie `200` mais Power Automate `403` → corriger l'en-tête côté
flux (clé exacte `X-API-Token`, valeur brute sans espace).

### Refresh `200` mais `saved: 0`
- Aucun navire n'a de **nouvelle** position depuis le dernier passage
  (`skipped` > 0) : comportement normal (idempotent).
- Aucun navire n'a de position du tout : alimenter `vessel_positions` via
  `POST /api/tracking/upload` (cf. runbook tracking satcom).
- `errors` > 0 : accès réseau sortant vers `api.windy.com` / `*.open-meteo.com`
  bloqué par le firewall → à autoriser. Le repli Open-Meteo nécessite aussi
  l'accès réseau sortant.

### La page Navigation n'affiche pas de météo
- Vérifier que des relevés existent pour la fenêtre du leg :
  ```bash
  docker compose exec db psql -U towt -d towt -c \
    "SELECT vessel_id, count(*) FROM vessel_weather GROUP BY vessel_id;"
  ```
- Pour un **leg ancien**, la météo n'existe que si le cron tournait pendant le
  voyage (pas d'archive Windy rétroactive). C'est attendu.

### Carte vide / lente
`MAPTILER_TOKEN` (ou `MAPBOX_TOKEN`) absent → tuiles raster OSM par défaut.
Renseigner un token et recréer le conteneur.

`curl 127.0.0.1:8000` **ne fonctionne jamais depuis l'hôte** : l'app n'a aucun
port public, elle n'est joignable que via Caddy (80/443 → `app:8000`). Tester
via le domaine public ou `docker compose exec app …`.

## 7. Sécurité & confidentialité

- Le `.env` n'est **jamais** commité (gitignore) ; `check_api_keys.sh` ne révèle
  jamais les valeurs (longueur uniquement).
- Windy / Open-Meteo sont appelés **côté serveur** (la clé Windy ne fuite pas au
  navigateur) — aucune modification CSP nécessaire.
- L'endpoint cron est protégé par `X-API-Token` (comparaison à temps constant).
