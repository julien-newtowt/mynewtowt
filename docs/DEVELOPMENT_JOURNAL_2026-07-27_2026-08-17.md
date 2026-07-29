# Journal de développement — 2026-07-27 → 2026-08-17

> Rapport de passation destiné au manager, tenu pendant son absence.
> Une entrée par journée de travail. Voir `PROJECT_CONTEXT.md` pour l'état
> du projet et les audits, `CLAUDE.md` pour les consignes opérationnelles.

---

## 2026-07-27 → 2026-07-29 — Phase de découverte

**Objectif** : comprendre l'architecture, les processus métier et les
interactions entre modules avant tout développement.

**Livrables** :
- `PROJECT_CONTEXT.md` §1-12 — architecture, workflows, rôles/permissions,
  flux portail client ↔ ERP, APIs et intégrations, procédure réelle de
  lancement local (le README était incomplet), dette technique priorisée.
- `PROJECT_CONTEXT.md` §13 — audit de cohérence métier (1re passe).
- `PROJECT_CONTEXT.md` §14 — audit approfondi 6 domaines, regard expert
  maritime (~60 constats, organisés par motif de risque transverse).
- `docs/user-guide/roles-processus-integrations.md` — guide fonctionnel de
  découverte (4 publics, fil rouge d'une expédition, 5W+1H par service).

**Branches** : `docs/decouverte-fonctionnelle` (depuis `main`).
**Commits** : `633d73f` (fondations), `98d9e25` (§13), `3b2a54e` (§14).

**Décisions actées avec Yasmin** :
- Marad porte les certificats navire/ISM et les décisions d'embarquement ;
  Pennylane porte la comptabilité réelle. Ces « trous » de mynewtowt sont
  intentionnels, pas des oublis.
- Le DPA (Code ISM) est le rôle `manager_maritime`, supérieur hiérarchique
  d'Armement et de Technique. Commercial et Opérations sont deux filières
  autonomes sans chef commun.
- **Correction d'une hypothèse erronée** : le MRV UE s'applique bien à cette
  flotte (Règl. UE 2023/957, extension aux 400-5000 GT depuis le 01/01/2025) ;
  les émissions sont déjà vérifiées par un organisme accrédité (THETIS-MRV,
  datasets déposés chez DNV). Le module MRV v2 n'est pas de la
  sur-ingénierie réglementaire.

**Risques identifiés** : voir `PROJECT_CONTEXT.md` §14. Les plus critiques :
faux vert Schengen, absence de registre de connaissements, horodatage ATD/ATA
= heure de saisie, heures voile du carnet de bord surévaluées ×6.

---

## 2026-07-29 — Phase 2, J1 : filet de sécurité CI

**Objectif** : établir l'état réel de la suite de tests avant de modifier du
code sensible (capacité, horodatage, émission de BL). Prérequis à tout
développement.

**Contexte de la décision** : le 2026-07-27, la question « faut-il ajouter
`tests/integration` + `tests/regression` à la CI ? » avait été tranchée par
« pas d'action immédiate ». Décision **rouverte** le 2026-07-29 avec l'accord
de Yasmin : elle était raisonnable tant qu'on ne développait pas, elle ne
l'est plus dès lors qu'on touche à la capacité de cale, à l'horodatage des
faits et à l'émission de connaissements.

**Constat de départ (vérifié)** : `.github/workflows/ci.yml:65` ne lance que
`pytest tests/unit` (84 fichiers). `tests/integration/` (110 fichiers) et
`tests/regression/` (4 fichiers, dont la sentinelle des facteurs d'émission
et le filet de parité V2↔V3) **ne tournent jamais en CI**. Le « 710 passed »
des rapports de déploiement provenait d'exécutions locales.

**Travaux réalisés** :
1. Environnement de test local remis en état — `docker-compose.override.yml`
   (non versionné) étendu pour publier Postgres sur `localhost:5432`,
   base `towt_test` créée. Miroir de la configuration du job CI.
2. Passe de collecte : **811 tests collectés, zéro erreur d'import** — la
   suite est structurellement saine.
3. Exécution complète de `tests/integration` + `tests/regression`.

**Résultat** : **781 passés · 29 échecs · 1 skip** (96 % de vert).
Trois catégories d'échecs, distinction essentielle :

| Cat. | Nb | Nature | Traitement |
|---|---|---|---|
| ① | ~15 | **Environnement** — rendu PDF, `WeasyPrint could not import some external libraries` (GTK/Pango absents sur l'hôte Windows) | À valider sur le runner Ubuntu de la CI, pas à corriger |
| ② | ~13 | **Tests périmés** — route déplacée sous préfixe (`/offers/…` → `/commercial/offers/…`) ; paramètres de route ajoutés (ex. `incident_location`, ONB-08) et tests appelant la fonction en direct recevant l'objet `Form` (`AttributeError: 'Form' object has no attribute 'strip'`). **Pas des bugs de production** | Corrections d'une ligne |
| ③ | 1 | **À investiguer** — `test_eta_extension_cascades_downstream` : attendu `BASE + 25 j`, obtenu `BASE + 26 j` sur la cascade de dates | Investigation dédiée, à ne pas « corriger » en ajustant l'attente |

**Risque identifié (important)** : l'échec ③ tombe exactement dans une zone
signalée par l'audit (§14.4 — le résolveur de cascade n'ajoute pas la durée
d'escale entre deux legs aval). Un jour d'écart. Soit l'attente du test est
périmée après un changement délibéré non documenté, soit c'est un off-by-one
réel. **Quelqu'un a modifié la cascade et personne ne l'a su, parce que ce
test ne tourne jamais en CI.** C'est la justification empirique du J1.

**Décision technique prise** : ne pas ajuster l'attente du test ③ à la valeur
observée pour obtenir du vert — ce serait entériner un comportement sans
l'avoir compris.

**En attente d'arbitrage Yasmin** : option de mise en place du filet —
(A) corriger les tests périmés puis activer en CI (~2-3 h, recommandé),
(B) activer avec 29 `xfail` documentés (~30 min, risque de noyer ③),
(C) activer tel quel (écarté : CI rouge permanente = filet ignoré).

**Prochaines étapes recommandées** :
1. Trancher l'option A/B/C, puis brancher integration+regression en CI.
2. Investiguer ③ (cascade) — à rapprocher du lot horodatage.
3. Enchaîner sur le J2 (quick wins) : alerte ETA en mer, nom client +
   `leg_code` sur la liste bookings, heures voile ×6, redirection du bouton
   BL vers le rail packing list, 2 micro-gardes BL.

### J1 — suite : option A retenue, travaux réalisés

**Branche** : `chore/ci-integration-tests` (depuis `docs/decouverte-fonctionnelle`).
**Tag de référence** : `pre-upgrade-2026-08`.

**Commits** :

| Commit | Nature |
|---|---|
| `d702678` | `fix(tracking)` — normalisation naïf/aware dans `leg_window` |
| `93d1fda` | `fix(tracking)` — extension de la normalisation à `compute_metrics` |
| `d309497` | `test` — correction de 4 tests périmés |

**Un vrai bug applicatif trouvé** (pas un test périmé) :
`voyage_track.leg_window` comparait `end < start` **sans normaliser** les
bornes, alors que la convention du projet est explicite — la docstring de
`planning.ensure_utc` dit : *« les saisies `datetime-local` sont naïves.
Toute arithmétique de planification passe par ce helper »*. Le cas n'est donc
**pas limité à SQLite** : il peut survenir en production sur une saisie
naïve. `leg_window` alimente le tracking, la navigation et l'association des
positions GPS à un leg.

**Leçon méthodologique — le filet a immédiatement démontré sa valeur** : le
premier correctif (`d702678`) rendait `leg_window` aware mais laissait
`compute_metrics` soustraire ces bornes à des valeurs naïves (`leg.ata`,
`recorded_at`). Le TypeError était **déplacé, pas résolu**, et **2 tests de
KPI navigation qui passaient ont régressé**. Détecté au re-run complet,
corrigé en `93d1fda` (normalisation de bout en bout, `ensure_utc` remonté en
import module — pas de cycle, `planning` n'importe pas `voyage_track`).
Sans ce filet, cette régression partait en production.

**Tests périmés corrigés (aucun n'était un bug de production)** :
- `test_docx_generator` — l'assertion omettait le préfixe du router
  (`APIRouter(prefix="/commercial")`) ; la route existe bien.
- `test_packing_list_delete` — fixture antérieure à la contrainte
  `ck_packing_lists_order_xor_booking`, FK réellement appliquée : ajout de la
  chaîne minimale navire → ports → leg → booking.
- `test_attachments_autofill_reprise` — 2 tests CREW-04 exerçaient
  `crew_router.crew_assign`, **supprimée délibérément** par `9b752bd`
  (« retirer créations manuelles », doctrine Marad = source de vérité).

**Divergence doc/code relevée au passage (à arbitrer)** : l'arbitrage **A4
« embarquement hors leg autorisé »** figure toujours dans `CLAUDE.md` et
`CrewAssignment.leg_id` est toujours nullable, mais le **seul** producteur de
`CrewAssignment` est désormais `services/escale_crew.py:52`, appelé avec un
leg. **Aucun chemin applicatif ne crée plus d'affectation hors leg** : la
capacité existe en base, plus dans l'UI. Soit réactiver un chemin, soit
retirer A4 des décisions actées.

**État de la suite** : **784 passés · 24 échecs · 1 skip** (contre 781/29/1 au
départ). Ventilation des 24 restants :

| Cat. | Nb | Nature | Reste à faire |
|---|---|---|---|
| ① | ~13 | Rendu PDF / WeasyPrint (GTK/Pango absents sur l'hôte Windows) | À valider sur runner Ubuntu — probablement verts en CI |
| ② | ~6 | Fuite d'objet `Form` : tests appelant les fonctions de route **en direct** sans passer tous les paramètres (`admin_activity_logs`, `admin_data_reprise`, `claim_incident_fields`, `claims_onb06` ×3) | Passer les paramètres explicitement |
| ③ | ~2 | Fixtures antérieures à `ck_packing_lists_order_xor_booking` (`escale_leg_overview`, `portal_messages_read`) | Même patron que `test_packing_list_delete` |
| ④ | 1 | `trombinoscope_notification` | À diagnostiquer |
| ⑤ | 1 | **`test_eta_extension_cascades_downstream`** — attendu `BASE + 25 j`, obtenu `BASE + 26 j` | **Investigation dédiée** (cf. `PROJECT_CONTEXT.md` §14.4) |

**Décision : la CI n'est PAS encore activée sur integration+regression.** Elle
serait rouge, ce qui transformerait le filet en bruit ignoré (le motif même
que l'option C voulait éviter). Séquence retenue : **corriger d'abord (② ③ ④),
activer ensuite, valider ① sur le runner Ubuntu.**

**Risques identifiés ce jour** :
- Le correctif partiel `d702678` montre qu'une normalisation de fuseau
  appliquée à un seul point d'un module **déplace** le problème. Toute
  correction de ce type doit être vérifiée par un re-run **complet**, pas
  ciblé.
- L'écart cascade (⑤) tombe dans une zone déjà signalée par l'audit. **Ne pas
  ajuster l'attente du test à la valeur observée** pour obtenir du vert.

**Prochaines étapes** :
1. Corriger ② ③ ④ (~1-2 h), puis activer integration+regression en CI.
2. Investiguer ⑤ (cascade) — à rapprocher du lot horodatage (#1).
3. Enchaîner sur le J2 (quick wins).

**Fichiers applicatifs modifiés** : `app/services/voyage_track.py` uniquement.
`docker-compose.override.yml` étendu (local, non versionné) pour publier
Postgres sur `localhost:5432` et créer la base `towt_test`.
