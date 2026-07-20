# Runbook — Génération automatique du trombinoscope (Armement)

> Cf. `docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md` pour la conception.
> Le cron Power Automate déclenche `POST /api/trombinoscope/generate` le
> dernier jour de chaque mois. Génération manuelle disponible à tout moment
> via `GET /crew/trombinoscope.pdf` (bouton staff, permission `crew:C`).

## 1. Vue d'ensemble

| Élément | Détail |
|---|---|
| Endpoint cron | `POST /api/trombinoscope/generate` (header `X-API-Token: <TROMBINOSCOPE_API_TOKEN>`) |
| Action | Regroupe les marins actifs (fonction ou agence de sous-traitance), génère le PDF, l'archive |
| Cible interne | `generated_reports` (`type="trombinoscope"`) + fichier sous `var/uploads/generated_reports/` |
| Génération manuelle | `GET /crew/trombinoscope.pdf` (streaming direct, archive aussi une ligne) |

## 2. Variable d'environnement (`.env`)

| Variable | Rôle | Sans elle |
|---|---|---|
| `TROMBINOSCOPE_API_TOKEN` | Secret du header `X-API-Token` du cron interne | endpoint → **503** |

## 3. Cron Power Automate — dernier jour du mois

Créer un flux *planifié* (Scheduled cloud flow), récurrence mensuelle, déclenché
le dernier jour du mois (Power Automate : *Recurrence* avec expression
« dernier jour du mois », ou déclenchement quotidien + condition sur le jour) :

1. **HTTP** :
   - Méthode `POST`, URI `https://my.towt.eu/api/trombinoscope/generate`
   - En-tête `X-API-Token: <TROMBINOSCOPE_API_TOKEN>` (brut) — Corps vide.

Réponse attendue (`200`) :

```json
{"report_id": 12, "period": "2026-07", "member_count": 28}
```

> **Test manuel** :
> ```bash
> TOK=$(grep '^TROMBINOSCOPE_API_TOKEN=' .env | cut -d= -f2-)
> curl -X POST "https://my.towt.eu/api/trombinoscope/generate" -H "X-API-Token: $TOK"
> ```

## 4. Exploitation

- Génération manuelle à tout moment : bouton staff sur `/crew` → `GET /crew/trombinoscope.pdf`
  (données les plus récentes, aucune attente du cron).
- Chaque génération (auto ou manuelle) crée une ligne `generated_reports` —
  `generated_by` NULL pour le cron, sinon l'utilisateur à l'origine.
- Pas encore d'écran de consultation de l'historique des archives (backlog,
  cf. cahier des charges §13 — non bloquant pour la v1).

## 5. Dépannage

### `POST /api/trombinoscope/generate` → 503
`TROMBINOSCOPE_API_TOKEN` non configuré dans le `.env` du conteneur.

### `POST /api/trombinoscope/generate` → 403
Header `X-API-Token` absent ou différent de `TROMBINOSCOPE_API_TOKEN`.

### `200` mais `member_count: 0`
Aucun `CrewMember` actif trouvé (`is_active = true`) — vérifier la
synchronisation Marad (`docs/operations/04-marad-crew-sync-runbook.md`) et le
statut actif/inactif dans `/crew`.

## 6. Sécurité & confidentialité

- Endpoint protégé par `X-API-Token` (comparaison à temps constant), sur le
  même patron que `/api/marad/refresh`.
- CSRF : `/api/trombinoscope/` est exempté (auth par token, pas de cookie).
- Le PDF archivé ne contient que Fonction/Photo/Nom/Prénom des marins actifs —
  aucune autre donnée sensible du dossier marin (passeport, Schengen, visas).
