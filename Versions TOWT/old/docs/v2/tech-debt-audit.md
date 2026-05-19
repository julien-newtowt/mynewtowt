# Audit Dette Technique — my_newtowt v3.0.0

> **Date** : 2026-05-07
> **Branche d'audit** : `claude/init-app-restructure-F0DKN`
> **Périmètre** : code applicatif post-rebranding NEWTOWT et post-suppression module Passagers.
> **Méthode** : revue statique du dépôt (15 303 lignes Python, 87 templates Jinja2, 1 069 lignes CSS) + recherche orientée par 12 axes.
> **Statut** : Audit pur. Aucune modification de code dans cette PR — décisions humaines requises avant action.

---

## Synthèse

| Sévérité | Nombre | Effort total estimé |
|---------:|-------:|---------------------|
| 🔴 Critique | 3 | ~5 j |
| 🟠 Élevé | 5 | ~12 j |
| 🟡 Moyen | 4 | ~8 j |
| 🟢 Faible / différé | 5 | ~6 j |
| **Total** | **17** | **~31 j-h** |

Le projet a une dette technique **modérée mais concentrée** : trois points critiques (tests absents, conflit Alembic / `create_all`, supply-chain CDN sans SRI) demandent un sprint dédié avant tout chantier d'évolution majeur. Les autres points sont absorbables au fil de l'eau.

---

## 🔴 Critique — à traiter avant V3.x

### 1. Aucune couverture de tests automatisés

- **Constat** : pas de dossier `tests/`, pas de `pytest.ini`/`conftest.py`, pas de pipeline CI (`.github/workflows/` absent). Zéro test unitaire ou d'intégration sur 15 k lignes Python qui pilotent voyage planning, BL, finance, MRV.
- **Risque** : toute évolution casse silencieusement. Le rebrand + suppression passagers vient de toucher 60+ fichiers ; rien ne garantit qu'un endpoint critique (création leg, génération BL, RGPD export cargo) ne soit régressé.
- **Effort** : 2 j pour bootstrap (pytest-asyncio, fixtures DB SQLite/Postgres test, factory_boy) + 1 j pour 10 tests critiques (auth, leg CRUD, order assignment, BL PDF, finance compute) + GitHub Actions workflow.
- **Recommandation** : commencer par tests **smoke** (chaque router répond 200/302/404 selon attendu) avant tests métier.

### 2. Conflit `Base.metadata.create_all` ↔ Alembic

- **Constat** : `app/database.py:42` appelle encore `Base.metadata.create_all()` au démarrage, alors qu'Alembic est configuré (`alembic.ini`, `migrations/env.py`, baseline `0001_baseline_post_sprint2.py`). Les deux mécanismes coexistent.
- **Risque** : drift de schéma entre dev (où `create_all` ajoute les colonnes ORM-only) et prod (où seul Alembic s'applique). Symptôme typique : fonctionne en local, casse en prod.
- **Bonus** : 4 fichiers SQL ad-hoc à la racine (`migration.sql`, `migration_co2.sql`, `migration_mrv.sql`, `migration_stowage.sql`) jamais convertis en migrations Alembic. Le `migrations/0003_drop_passengers.sql` créé dans cette PR perpétue ce pattern.
- **Effort** : 2 j — autogen Alembic du schéma actuel, suppression `create_all`, conversion des 4 SQL legacy en révisions Alembic + intégration `0003_drop_passengers` au workflow.
- **Recommandation** : interdire `Base.metadata.create_all` en prod via `if settings.APP_ENV != "production"`, puis le retirer totalement une fois confiance gagnée.

### 3. Supply-chain frontend sans intégrité (SRI)

- **Constat** : `app/templates/base.html:11,19` charge HTMX et Lucide depuis `unpkg.com` sans attribut `integrity="sha384-..."` ni `crossorigin`. Idem Leaflet sur 3 pages (dashboard, planning, cargo portal). La CSP autorise `unpkg.com` sans contrainte.
- **Risque** : compromission du CDN unpkg ou MITM = injection de JS arbitraire dans **toutes les pages authentifiées** (vol de session, exfiltration BL, manipulation tracking).
- **Effort** : 1 j — soit (A) télécharger les libs dans `app/static/lib/` et servir en self-host (recommandé), soit (B) ajouter les hashes SRI et figer les versions.
- **Recommandation** : option A. La CSP peut alors retirer `unpkg.com` complètement (`script-src 'self'`).

---

## 🟠 Élevé — à planifier sur le prochain sprint

### 4. SQL via f-string sur table dynamique

- **Fichier** : `app/routers/admin_router.py:761` — `text(f'SELECT * FROM "{table_name}" LIMIT 50000')` dans `export_global`.
- **Atténuation existante** : le nom de table est filtré contre `ALLOWED_TABLES` avant interpolation, donc l'injection est techniquement bloquée.
- **Mais** : viole explicitement la règle `CLAUDE.md` *« Never use f-strings to interpolate table/column names in SQL »*. Mauvais signal pour les contributeurs futurs.
- **Effort** : 0.25 j — utiliser `sa.table(table_name).select()` ou whitelisting avec `quote_identifier`.

### 5. Routers obèses

| Fichier | Lignes |
|---------|------:|
| `onboard_router.py` | 2 201 |
| `admin_router.py` | 1 796 (post-suppression passagers) |
| `cargo_router.py` | 1 751 |
| `pricing_router.py` | 1 326 |
| `planning_router.py` | 1 123 |

- **Risque** : difficile à reviewer, à tester unitairement, à découvrir. Conflits Git fréquents en équipe.
- **Effort** : 4 j — découpage de `onboard_router` (Cargo / Crew / SOF / Captain landing) et `admin_router` (Users / Vessels / Database / RGPD / Maintenance / Insurance).
- **Recommandation** : prioriser `onboard_router` — sera massivement remanié par le ticket V2 « Refonte Onboard 4 espaces ».

### 6. Inline styles massifs dans templates

- **Constat** : 1 274 occurrences de `style="..."` dans `app/templates/`. Top : `onboard/index.html` (1 138 — beaucoup de SVG/cabins), `admin/settings.html` (840), `kpi/index.html` (679), `escale/index.html` (668).
- **Conséquence** : la rebrand NEWTOWT (CSS variables) ne propage pas partout — beaucoup d'inline `color:#095561` (ancien bleu TOWT) hardcodés dans les exports PDF (`mrv_router`, `crew_router`, `claim_router`, `cargo_router`, `onboard_router`).
- **Effort** : 3 j (incrémental, par template) — extraire vers `.btn-x`, `.field-x`, etc. dans `app.css`. Remplacer `#095561` → `var(--newtowt-teal)` partout.
- **Recommandation** : faire tâche de fond, un template à la fois lors des évolutions fonctionnelles.

### 7. Stub `compute_pax_revenue_for_leg` à éliminer

- **Fichier** : `app/routers/finance_router.py:68-71` — fonction qui retourne désormais toujours 0.0 (passagers supprimés en v3.0).
- **Conséquence** : 6 callers (lignes 192, 195, 215, 228, 263, 272, 434) injectent une variable `pax_revenue=0` partout dans templates et calculs.
- **Effort** : 0.5 j — supprimer la fonction et toutes ses références (templates compris : `pax_revenue` à enlever des contextes).

### 8. Cookie / namespace `towt_*` non rebrandé

- **Constat** : `towt_csrf`, `towt_session`, `towt_lang`, `towt_sidebar_*` (localStorage). Après rebrand NEWTOWT, le namespace devrait être `newtowt_*` ou `mtw_*`.
- **Trade-off** : rename = invalidation des sessions actives + reset des préférences sidebar de tous les utilisateurs.
- **Effort** : 0.5 j (rename) + comm utilisateur.
- **Recommandation** : prévoir au prochain palier majeur (v3.1 ou v4) avec annonce préalable, pas en hotfix.

---

## 🟡 Moyen — fond de roulement

### 9. Fichiers orphelins à la racine

| Fichier | Statut | Recommandation |
|---------|--------|----------------|
| `import_planning.py` | non importé nulle part | déplacer dans `scripts/legacy/` ou supprimer |
| `planning_import.csv` | données d'import historique | archiver hors repo (S3, pgdump) |
| `import_tonnage.csv` | utilisé par `scripts/import_tonnage.py` | déplacer dans `scripts/data/` |
| `easy_chargement_navire_complet.xlsx` (1.4 MB) | référencé en commentaire dans `models/stowage.py` | extraire les données dans une seed script + supprimer du repo (pollue git) |
| `LogAbstract MRV Requirements.docx` (28 KB) | spec MRV | déplacer dans `docs/mrv/` |
| `migration*.sql` (4 fichiers) | voir point 2 | convertir en révisions Alembic |
| `presentation_mytowt.html` (44 KB) | slideshow séminaire 2025 | déplacer dans `docs/legacy/` ou archiver |

- **Effort** : 0.5 j de tri + commit "chore: archive legacy import artefacts".

### 10. `app/services/` quasiment vide

- **Constat** : seul `rate_limit.py` ; `__init__.py` vide. Logique métier reste dans les routers.
- **Effort** : variable selon ambition — 2-5 j pour extraire la logique métier critique (computation finance, génération BL, KPI) dans `app/services/`.
- **Recommandation** : extraire au cas par cas lors des refactors de routers (point 5).

### 11. Internationalisation incomplète

- **Constat** : `app/i18n/__init__.py` (947 lignes) couvre fr/en/es/pt-br/vi mais : (a) beaucoup de templates contiennent encore du français en dur (cf. `dashboard.html`, `admin/settings.html`) ; (b) plusieurs PDF (`bill_of_lading.html`, claims) sont 100 % FR.
- **Effort** : 4 j pour inventaire complet + extraction.
- **Recommandation** : différer tant que les utilisateurs sont 100 % FR (info à confirmer avec ops).

### 12. Modèle `Notification` partiellement nettoyé

- **Constat** : suppression OK des types `new_passenger_*` (ce PR), mais le code émetteur résiduel n'a pas été audité (recherche `Notification(type=...)` dans tous les routers à compléter).
- **Effort** : 0.5 j de recherche + cleanup.

---

## 🟢 Faible — différable

### 13. Logos PNG lourds dans `app/static/img/`

- 3 PNG NEWTOWT à ~135 KB chacun, sourcés depuis `Design/`. Pour le sidebar, version optimisée (resize 200×46 px) suffirait.
- **Effort** : 0.25 j (pillow/imagemagick).

### 14. Dépendances Python à jour

- `requirements.txt` figé avec versions exactes — bon pour reproductibilité, mais pas de Renovate / Dependabot configuré. `bcrypt==4.0.1` a 6 mois.
- **Effort** : 0.5 j (config Dependabot + premier run).

### 15. Permissions `gestionnaire_passagers` legacy

- `app/permissions.py:127` : mapping legacy `gestionnaire_passagers → commercial`. Plus de raison d'être. Vérifier qu'aucun user actif n'a encore ce rôle en DB avant suppression.
- **Effort** : 0.25 j + 1 query DB.

### 16. CSP unpkg.com ouvre script-src

- Voir point 3. Le `script-src 'self' 'unsafe-inline' https://unpkg.com` autorise tout JS inline + tout depuis unpkg. Une fois SRI/self-host fait (point 3), retirer `'unsafe-inline'` et `unpkg.com`. Bonus : retirer `'unsafe-inline'` impose extraire les `<script>` inline de `base.html` dans `app/static/js/` (env. 200 lignes JS clock/timezone à externaliser).
- **Effort** : 1 j (extraction JS inline + nonce-based CSP).

### 17. `import_planning.py` à la racine vs `scripts/`

- Cohérence : déjà couvert au point 9. Mineur.

---

## Recommandations stratégiques

1. **Avant tout chantier V3.x** : traiter points 1, 2, 3 (sprint dédié 5 j). Sans tests + Alembic propre + supply-chain self-host, toute évolution est risquée.
2. **Sprint 1 V3.x** : intégrer points 4 (SQL f-string), 7 (stub pax_revenue), 9 (orphans), 12 (notifs résiduelles) — quick wins ~2 j cumulés, gros effet « propreté ».
3. **Sprint 2 V3.x** : refactor `onboard_router.py` (point 5) en parallèle de la roadmap V2 « Refonte Onboard 4 espaces » qui le réécrira de toute façon. Mutualiser les efforts.
4. **Backlog continu** : inline styles (6), services layer (10), i18n (11) au fil des évolutions fonctionnelles.
5. **Différer à V4** : cookie namespace (8) avec annonce utilisateur. Pas de pression technique.

---

## Hors périmètre de cet audit

- **Sécurité applicative** : couvert par les rapports antérieurs (`docs/security/audit-v1.md`, `docs/security/action-plan.md`) — **purgés du repo lors de `Purge V2->V3`**, à recréer si nécessaire à partir des sauvegardes git (cf. `git show <sha>:docs/security/audit-v1.md`).
- **UX / accessibilité** : ancien audit purgé également (`docs/ux/audit.md`).
- **Performance / Lighthouse** : pas mesurée ici. La cible historique V2 (Lighthouse ≥ 90) reste pertinente.
- **Sauvegardes & DR** : `backup.sh` + `restore.sh` existent ; pas audités fonctionnellement (test de restauration recommandé en parallèle).
