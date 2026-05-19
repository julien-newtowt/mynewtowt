# Debugging playbook

> Méthode `/systematic-debugging`. Référence à appliquer avant chaque
> mise en production et à chaque incident.

## 1. Posture

- **Pas de fix à l'aveugle.** On ne touche pas au code tant qu'on n'a
  pas une hypothèse étayée par une observation.
- **Reproduction d'abord.** Si on ne peut pas reproduire, on documente
  ce qu'on a tenté et on demande des éléments supplémentaires.
- **Une cause racine, pas un symptôme.** Tant qu'on n'a pas relié l'erreur
  à la cause technique précise, le fix sera fragile.

## 2. Étapes

### Étape 1 — Cartographier les chemins critiques

Avant de toucher quoi que ce soit, lister :

- Quel est le chemin utilisateur impacté ?
- Quels modules sont en interaction (auth, DB, cache, externe) ?
- Quelle est la matrice persona × chemin documentée dans
  `tests/e2e/golden_paths.csv` ?

### Étape 2 — Forcer la condition de défaut

Reproduire en isolant :

- Variables réseau (proxy, satellite, mobile).
- Variables navigateur (private, cookies vides).
- Variables data (cas limites : 0 palettes, 1 jour, 365 jours).
- Variables permission (rôle minimal vs admin).

Documenter le **scénario reproductible exact** (curl, capture
clavier, requête SQL).

### Étape 3 — Capturer dans le defect board

`docs/operations/defect-board.md` tient la liste :

```
| ID | Date | Reporter | Persona | Sévérité | Module | Reproductible | Owner | Status | ETA |
```

ID format : `DFT-YYYYMMDD-XXX` (XXX séquence du jour).

### Étape 4 — Identifier la cause racine

Pour chaque défaut, écrire :

```markdown
## DFT-20260518-001

**Symptôme observé** : ...

**Reproduction** :
```sh
curl ... | jq .
```

**Diff comportement** :
- Attendu : ...
- Observé : ...

**3 hypothèses** (du plus probable au moins probable) :
1. <hypothèse> — validation : <expérience>
2. ...
3. ...

**Validation expérimentale** :
- ...

**Cause racine identifiée** : ...

**Fix proposé** : ...

**Tests de non-régression à ajouter** :
- ...
```

### Étape 5 — Tester le fix

Avant de pousser un fix :

- Lancer le test E2E du golden path concerné.
- Lancer les tests E2E des 3 chemins adjacents (amont, aval, contournement).
- Lancer la suite unitaire complète.
- Vérifier qu'aucun nouveau test ne tombe en flaky.

Si tout passe, ouvrir une PR avec :

- Lien defect board → DFT-...
- Tests de non-régression visibles dans le diff.
- Migration / flag si applicable.
- Screenshots avant/après si UI.

## 3. Anti-patterns à éviter

| Anti-pattern | Pourquoi c'est mauvais |
|--------------|------------------------|
| "Ça marche chez moi" | L'environnement utilisateur n'est pas reproductible |
| Retry loop avec `time.sleep` | Masque le bug temporel, ne le résout pas |
| `try/except: pass` autour de l'erreur | Cache la cause, complique le diagnostic |
| Fix de typo sans test | Le typo reviendra à la prochaine refacto |
| "Je rebase puis je vois si ça passe" | La CI n'est pas un debugger, c'est une garantie |
| Ajout d'un test après le fix | Le test doit échouer avant, passer après |

## 4. Outils

| Besoin | Outil |
|--------|-------|
| Capture HTTP | mitmproxy local, browser devtools |
| Profile SQL | `pg_stat_statements`, EXPLAIN ANALYZE |
| Tracing | OTLP → Tempo (Grafana) |
| Logs corrélés | Loki + Grafana |
| Tests interactifs | `pytest --pdb` |
| Reproduire prod localement | snapshot anonymisé via `scripts/anonymize-snapshot.sh` |

## 5. Defect board (modèle)

`docs/operations/defect-board.md` :

```
# Defect Board

## Open

### DFT-20260518-001 — Booking refuse à plus de 50 palettes
- Persona: client B2B
- Sévérité: majeure
- Owner: @booking-team
- ...

## Resolved (last 30 days)

### DFT-20260510-014 — Login fail sur Safari iOS
- Status: resolved 2026-05-12
- Root cause: cookie SameSite=Lax incompatible avec redirect cross-domain
- Fix: PR #142
```

## 6. Post-mortem template

Pour les incidents SEV-1 et SEV-2, dans les 48 h :

```markdown
# Post-mortem — INC-20260518-001

## Résumé
1 ligne.

## Impact
- Durée : XX minutes.
- Utilisateurs touchés : X.
- Données touchées : oui/non.

## Timeline
- T+0 : symptôme détecté.
- T+5 : équipe alertée.
- T+15 : confirmation.
- T+30 : containment.
- T+45 : fix déployé.
- T+60 : monitoring renforcé.

## Cause racine
...

## Ce qui a bien fonctionné
...

## Ce qu'on doit améliorer
...

## Actions
- [ ] Action 1 (owner, deadline)
- [ ] Action 2 ...
```

Publié dans `docs/operations/post-mortems/INC-YYYYMMDD-XXX.md`.
Aucun blâme personnel ; focus sur les systèmes.
