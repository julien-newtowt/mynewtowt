# Runbook — Veille d'actualité

> Activation, exploitation et dépannage du module **Veille d'actualité**
> (`/veille`). Audience : staff NEWTOWT. Phase 1 — flux brut via
> l'agrégateur NewsData.io, sans enrichissement IA (prévu en phase 2).

## 1. Vue d'ensemble

| Élément | Détail |
|---|---|
| Page staff | `/veille` (fil) · `/veille/sources` (gestion) |
| Endpoint cron | `POST /api/veille/refresh` (header `X-API-Token`) |
| Module RBAC | `veille` — Consult = tout le staff ; gestion des sources = rôles transverses |
| Agrégateur | [NewsData.io](https://newsdata.io) — endpoint `/api/1/latest` |
| Tables | `news_sources` (requêtes thématiques) · `news_items` (articles dédupliqués) |
| Migration | `migrations/versions/20260601_0020_veille_news.py` |

Une **source** = une requête sauvegardée envoyée à NewsData (mots-clés +
pays + langues + catégorie), ciblable par rôle. L'ingestion déduplique sur
l'`article_id` NewsData (à défaut un SHA-256 du lien).

## 2. Variables d'environnement (`.env`)

| Clé | Rôle | Sans valeur |
|---|---|---|
| `NEWSDATA_API_KEY` | Clé API NewsData.io | `/veille` affiche « non configuré », refresh → `503` |
| `VEILLE_API_TOKEN` | Secret du header `X-API-Token` pour le cron | `POST /api/veille/refresh` → `503` |
| `NEWSDATA_BASE_URL` | Optionnel (défaut `https://newsdata.io/api/1/latest`) | défaut utilisé |

> ⚠️ Le `.env` est lu **au démarrage du conteneur**. Toute modification
> nécessite `docker compose up -d --no-deps --force-recreate app`.

## 3. Activation (première mise en service)

```bash
cd /opt/mynewtowt

# 1) Être sur main et à jour
git checkout main && git pull --ff-only origin main

# 2) Renseigner les secrets veille dans .env (token hex = pas de caractères ambigus)
NEW="$(openssl rand -hex 32)"
grep -q '^VEILLE_API_TOKEN=' .env \
  && sed -i "s|^VEILLE_API_TOKEN=.*|VEILLE_API_TOKEN=${NEW}|" .env \
  || echo "VEILLE_API_TOKEN=${NEW}" >> .env
#   (ajouter NEWSDATA_API_KEY=pub_xxxx à la main ou via un éditeur)

# 3) Déployer : build + alembic upgrade head + recreate + smoke /health
./scripts/deploy.sh
```

Vérifications :

```bash
docker compose run --rm app alembic current        # → 20260601_0020
docker compose exec db psql -U towt -d towt -c "\dt news_*"
docker compose exec -T app printenv VEILLE_API_TOKEN
```

## 4. Configurer les sources

Dans l'ERP : **Pilotage → Veille d'actualité → Sources** (`/veille/sources`).
Exemples :

| Nom | `q` (mots-clés) | Pays | Langues | Catégorie |
|---|---|---|---|---|
| Transport maritime | `shipping OR "maritime transport" OR "cargo ship"` | — | `en,fr` | `business` |
| Voile & wind propulsion | `"wind propulsion" OR "sailing cargo" OR "voilier cargo"` | — | `en,fr` | — |
| Brésil (ports/commerce) | `porto OR exportação OR navio OR comércio` | `br` | `pt` | `business` |
| Décarbonation & réglementation | `"shipping emissions" OR decarbonization OR MRV OR FuelEU` | — | `en,fr` | `environment` |

Syntaxe NewsData : opérateurs `AND` / `OR` / `NOT`, guillemets pour une
expression exacte ; pays/langues en codes ISO séparés par virgules. Champ
**Rôles ciblés** vide = visible par tout le staff.

## 5. Rafraîchissement

- **Manuel** : bouton « Rafraîchir » sur `/veille` (permission M).
- **Automatique (cron Power Automate)** : action *HTTP*
  - Méthode : `POST`
  - URI : `https://my.newtowt.eu/api/veille/refresh`
  - Header : `X-API-Token: <VEILLE_API_TOKEN>`
  - Fréquence conseillée : 2 à 3×/jour (économie des crédits NewsData).

La déduplication garantit qu'un refresh répété n'insère pas de doublons.

## 6. Dépannage

### `/veille` renvoie 404
Le code n'est pas déployé sur l'environnement. Vérifier la branche
(`git branch --show-current` → doit être `main`) et que `origin/main`
contient le module (`git ls-tree -r --name-only origin/main | grep veille`),
puis redéployer.

### `POST /api/veille/refresh` → 403 (Forbidden)
Le token reçu ≠ `VEILLE_API_TOKEN`. Le reverse proxy Caddy ne filtre pas
`/api/` : le 403 vient de l'app.
1. Auto-test dans le conteneur (envoie le token réellement chargé) :
   ```bash
   docker compose exec -T app python -c "
   import urllib.request, urllib.error, os
   tok = os.environ.get('VEILLE_API_TOKEN','')
   print('token conteneur:', (repr(tok[:6])+'…') if tok else 'VIDE')
   req = urllib.request.Request('http://127.0.0.1:8000/api/veille/refresh', method='POST', headers={'X-API-Token': tok})
   try:
       r = urllib.request.urlopen(req, timeout=30); print('STATUS', r.status)
   except urllib.error.HTTPError as e: print('HTTP', e.code)
   "
   ```
2. Comparer hôte vs conteneur :
   ```bash
   echo "host : [$(grep '^VEILLE_API_TOKEN=' .env | cut -d= -f2-)]"
   docker compose exec -T app printenv VEILLE_API_TOKEN
   ```
   S'ils diffèrent → `docker compose up -d --no-deps --force-recreate app`.
3. Si l'auto-test renvoie `200` mais Power Automate `403` → corriger le
   header côté flux (clé exacte `X-API-Token`, valeur brute sans espace).

### `POST /api/veille/refresh` → 503
`VEILLE_API_TOKEN` ou `NEWSDATA_API_KEY` absent du conteneur → renseigner
le `.env` puis recréer le conteneur.

### Refresh `200` mais 0 article inséré
- Réponse `errors` non vide → souvent quota NewsData atteint ou requête
  trop restrictive (vérifier `q`/pays/langues).
- Accès réseau sortant vers `newsdata.io` bloqué par le firewall : à
  autoriser.

`curl 127.0.0.1:8000` **ne fonctionne jamais depuis l'hôte** : l'app n'a
aucun port public, elle n'est joignable que via Caddy (80/443 → `app:8000`).
Tester via le domaine public ou `docker compose exec app …`.

## 7. Roadmap phase 2

1. Scoring de pertinence « impact NEWTOWT » (0-100) par IA Claude.
2. Widget « Dernières actus » sur le dashboard staff.
3. Digest e-mail quotidien (`services/email.py`) ciblé par rôle, via
   `POST /api/veille/digest` (cron Power Automate).
