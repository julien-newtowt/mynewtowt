# Runbook — Génération automatique du trombinoscope (Armement)

> Cf. `docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md` pour la conception.
> Déclenchement **automatique principal** : scheduler interne (APScheduler,
> `app/services/trombinoscope_scheduler.py`), le dernier jour de chaque mois
> à 23:55 (Europe/Paris) — **aucun flux Power Automate à configurer**. Un
> endpoint manuel token-protégé reste disponible en secours. Génération
> manuelle à tout moment via `GET /crew/trombinoscope.pdf` (bouton staff,
> permission `crew:C`).

## 1. Vue d'ensemble

| Élément | Détail |
|---|---|
| Déclencheur automatique | Scheduler interne APScheduler, `CronTrigger(day="last", hour=23, minute=55)`, timezone Europe/Paris |
| Endpoint manuel de secours | `POST /api/trombinoscope/generate` (header `X-API-Token: <TROMBINOSCOPE_API_TOKEN>`) |
| Action | Regroupe les marins actifs (fonction ou agence de sous-traitance), génère le PDF, l'archive, notifie le rôle `armement` |
| Cible interne | `generated_reports` (`type="trombinoscope"`) + fichier sous `var/uploads/generated_reports/` |
| Génération manuelle staff | `GET /crew/trombinoscope.pdf` (streaming direct, archive aussi une ligne) |

## 2. Pourquoi un scheduler interne (et pas un cron externe comme les autres intégrations) ?

Toutes les autres automatisations du projet (météo, Marad, veille, tickets,
devis, MRV) passent par un cron **externe** (Power Automate) qui appelle un
endpoint token-protégé — c'est délibérément différent ici : le trombinoscope
n'a pas besoin d'appeler un service tiers (contrairement à Marad/météo/
NewsData), donc un scheduler **in-process** évite d'avoir un flux Power
Automate supplémentaire à configurer et maintenir côté IT.

**Garde-fou multi-workers** : l'app tourne avec 2 workers uvicorn (cf.
`Dockerfile`), qui démarreraient chacun leur propre scheduler. Le job utilise
un verrou consultatif **transactionnel** Postgres
(`pg_try_advisory_xact_lock`, libéré automatiquement au commit/rollback) :
seul le worker qui l'obtient exécute la génération. Une vérification
supplémentaire sur `generated_reports` protège contre un redémarrage de
l'app le même jour (pas de doublon même si le scheduler est relancé après le
premier déclenchement du mois).

## 3. Variables d'environnement (`.env`)

| Variable | Rôle | Sans elle |
|---|---|---|
| `TROMBINOSCOPE_SCHEDULER_ENABLED` | `false` désactive le scheduler interne (utile en dev local) | par défaut `true` (actif) |
| `TROMBINOSCOPE_API_TOKEN` | Secret du header `X-API-Token` de l'endpoint manuel de secours | endpoint → **503** (le scheduler automatique n'est pas affecté) |

## 4. Exploitation

- **Rien à configurer côté Power Automate/IT** pour l'automatique — le
  scheduler démarre avec l'application (`@app.on_event("startup")`) et
  s'arrête proprement à l'arrêt (`@app.on_event("shutdown")`).
- Génération manuelle à tout moment : bouton staff sur `/crew` → `GET
  /crew/trombinoscope.pdf` (données les plus récentes, aucune attente du
  scheduler).
- Forcer une génération automatique sans attendre le dernier jour du mois
  (tests, incident) : endpoint de secours —
  ```bash
  TOK=$(grep '^TROMBINOSCOPE_API_TOKEN=' .env | cut -d= -f2-)
  curl -X POST "https://my.towt.eu/api/trombinoscope/generate" -H "X-API-Token: $TOK"
  ```
  Réponse (`200`) : `{"report_id": 12, "period": "2026-07", "member_count": 28}`
- Chaque génération (scheduler, endpoint de secours, ou manuelle) crée une
  ligne `generated_reports` — `generated_by` NULL pour l'automatique, sinon
  l'utilisateur à l'origine.
- Logs applicatifs : `trombinoscope-scheduler: …` (démarrage, exécution,
  verrou déjà détenu par un autre worker, génération déjà faite pour la
  période, erreurs).
- Pas encore d'écran de consultation de l'historique des archives (backlog,
  cf. cahier des charges §13 — non bloquant pour la v1).

## 5. Dépannage

### Le trombinoscope ne s'est pas généré le dernier jour du mois
- Vérifier `TROMBINOSCOPE_SCHEDULER_ENABLED` (absent ou `true` = actif).
- Vérifier les logs au démarrage : `Scheduler trombinoscope démarré (dernier
  jour du mois, 23:55 Europe/Paris).` doit apparaître à chaque redémarrage
  de l'app.
- L'app doit être **en cours d'exécution** à 23:55 le dernier jour du mois
  (heure Europe/Paris) — un redémarrage/déploiement à ce moment précis peut
  faire manquer le déclenchement ce mois-là (`misfire_grace_time=3600`
  couvre un décalage de moins d'1h). Rattraper via l'endpoint manuel de
  secours si besoin.

### `POST /api/trombinoscope/generate` (secours) → 503
`TROMBINOSCOPE_API_TOKEN` non configuré dans le `.env` du conteneur —
n'affecte pas le scheduler automatique.

### `POST /api/trombinoscope/generate` (secours) → 403
Header `X-API-Token` absent ou différent de `TROMBINOSCOPE_API_TOKEN`.

### `200`/génération OK mais `member_count: 0`
Aucun `CrewMember` actif trouvé (`is_active = true`) — vérifier la
synchronisation Marad (`docs/operations/04-marad-crew-sync-runbook.md`) et le
statut actif/inactif dans `/crew`.

## 6. Sécurité & confidentialité

- Endpoint de secours protégé par `X-API-Token` (comparaison à temps
  constant), sur le même patron que `/api/marad/refresh`.
- CSRF : `/api/trombinoscope/` est exempté (auth par token, pas de cookie).
- Le PDF archivé ne contient que Fonction/Photo/Nom/Prénom des marins actifs —
  aucune autre donnée sensible du dossier marin (passeport, Schengen, visas).
