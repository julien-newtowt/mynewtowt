# Cycle 3 — mise en œuvre « en profondeur des métiers »

> **Objet** : registre d'exécution des directions données après le cycle 2,
> branche `claude/adoring-johnson-szpgln` (PR #13). Chaque ligne = une
> décision de la direction, son interprétation, et son statut vérifié.
> **Ajustement de doctrine acté** : la **capacité disponible** et le **prix
> public** ne sont plus exposés ; le prix passe par l'outil de **devis sur
> grille tarifaire**. La **facturation n'est pas gérée par cet ERP**
> (booking note + comptabilité externe). Le **transport de passagers** est
> arrêté (mai 2026) — tout code/mention retiré.

## 1. Registre des directions (cette itération)

Statuts : ✅ livré & poussé · 🔁 en cours (agent) · 🟩 approuvé, à livrer ·
🧱 refonte profonde (planifiée §4) · ☑️ déjà satisfait (cycle 2) · 📝 doc/ops.

| # | Direction | Lecture | Statut | Preuve / commit |
|---|---|---|---|---|
| Planning | Retirer la météo prévisionnelle | route + template leg_detail | ✅ | `dc29b63` |
| Planning | Script de renumérotation des routes | resequencing chronologique 2 phases | ✅ | `scripts/renumber_legs.py` `dc29b63` |
| Planning | Token MapTiler/MapBox absent | code OK (alias `map_token`) ; action serveur | 📝 | `.env.example` `dc29b63` + `docker compose up -d --force-recreate app` |
| Planning | Page `/planning/share` lisible = prospectus + export A4 | tableau POL/POD + ETD/ETA + bouton impression A4 | ✅ | `planning_share.html` + `print-page.js` `dc29b63` |
| Planning | Conflits de quai à remettre | vue `port_conflicts` | 🔁 | agent planning |
| Planning | **Challenger toutes les dates à chaque décalage ETA** (UC-03) | cascade legs→escale→dockers→PL→bookings | 🔁 | agent planning (`date_cascade.py`) ; hook captain à câbler |
| Commercial | Devis sans identification (COM-01/02) | outil public `/devis` sur grille | ✅ (cycle 2) | `devis_router`, `quoting` |
| Commercial | Grille par route + grille par défaut + options | palette/tonne/réservation/booking note | ✅ (cycle 2) | `quoting`, `commercial_router` |
| Commercial | Synchroniser les leads Pipedrive (COM-04) | best-effort + notif rôle + email | ✅ (cycle 2) | `services/leads.py` |
| Commercial | Renforcer le multilingue FR-EN-ES-PT(BR) (COM-07) | parité catalogues + repli FR | ✅ | `7011d08` |
| Finance | Facturation hors ERP (COM-05) ; facture auto retirée | booking note PDF, pas de facture | ✅ | `co2`/booking note ; `invoicing` dormant |
| Finance | **TVA 0 %** transport maritime international | art. 262 II CGI | ✅ | `invoicing`/`pdf_generator` + mentions `dc29b63` |
| Finance | LegFinance = KPI du leg auto à la clôture (FLX-05) | revenus + dockers + opérations + OPEX | ☑️/🔁 | `finance_rollup` (cycle 2) ; +opérations/quai : agent escale |
| Finance | Claims → `LegFinance.claims_cost` (FLX-09) | Σ company_charge du leg | 🟩 | à ajouter au rollup |
| Escale | Relier escale ↔ Onboard (FLX-04) | actions escale → événements SOF | 🔁 | agent escale |
| Escale | Coûts dockers/opérations → LegFinance | Σ opérations + Σ dockers + quai×j | 🔁 | agent escale |
| Escale | Verrouillage d'escale à la clôture | champ lock + garde | 🔁 | agent escale (migration 0027) |
| Bord | Check-lists ISM/ISPS + registre visiteurs (FLX-11) | routes + UI | 🔁 | agent onboard |
| Bord | Tracking satcom → pré-remplir noon report (FLX-07) | dernière position en préremplissage | 🔁 | agent onboard |
| MRV | Noon signé⇒MRV ; SOF EOSP/SOSP⇒MRV ; ROB ±2 t warning (FLX-03) | référence n°1 = noon | ☑️/🟩 | `mrv_sync` (cycle 2) ; contrôle ROB ±2 t à confirmer |
| Tickets | Escalade SLA (FLX-08) | notif manager au dépassement | 🟩 | à implémenter |
| Crew | Schengen persisté + barrières (FLX-06) | snapshot + blocage affectation | ☑️ | cycle 2 |
| Client | Alertes proactives (retard/ETA) sur extranet | notif + bannière à la connexion | ✅ | `dc29b63` |
| Client | Rapport RSE annuel par client (ENV-06) | PDF + CSV agrégés | ✅ | `06733a5` |
| Env. | Retirer les taux de décarbonation (ENV-01) | plus de −95/−89 % | ✅ | `dc29b63` |
| Env. | Variables CO₂ paramétrables/admin (ENV-02) | table versionnée + `/admin/co2` | ☑️ | cycle 2 ; consommé KPI+certificats |
| Env. | Distance forfaitaire 3000 NM jamais silencieuse (ENV-03) | source + mention « non vérifiée » | ✅ | `dc29b63` |
| Corporate | Transport de passagers arrêté — retirer tout | route, page, PAX, rôle, i18n | ✅ | `dc29b63` |
| B2B | API documentée + webhooks | pas d'actualité — dev différé | 📝 | différé (backlog) |
| Refonte | Rail B booking → packing list + portail `/p/token` | 1er pas de la fusion des rails | 🧱 | §4 |
| Refonte | Plan d'arrimage 18 zones ↔ Escale (opérations + dockers) | lier stowage et escale | 🧱 | §4 |
| Refonte | **Fusionner le traitement des rails A et B** | un canal, deux modes de remplissage (back-office opérateur / front client) | 🧱 | §4 |

## 2. Livré et poussé cette itération

| Commit | Contenu |
|---|---|
| `dc29b63` | Planning (météo retirée, prospectus A4, renumber, map env), passagers retirés, TVA 0 %, facturation hors ERP, alertes ETA client + bannière login, distance certificat tracée |
| `06733a5` | Rapport CO₂ annuel par client (PDF+CSV) — ENV-06 |
| `7011d08` | Parité i18n FR-EN-ES-PT(BR) + repli FR — COM-07 |

Vérifié à chaque commit : `ruff` propre, app importée, **270 tests verts**.

## 3. En cours — vagues d'agents (cette itération)

Trois chantiers parallèles sur domaines disjoints (un seul ajout de schéma,
migration `0027`, pour éviter toute collision de chaîne Alembic) :

1. **Escale** : actions escale → SOF (FLX-04) ; coûts opérations/quai →
   LegFinance ; verrouillage d'escale (lock + garde + migration 0027).
2. **Bord** : check-lists ISM/ISPS + registre visiteurs (FLX-11) ;
   pré-remplissage du noon report depuis la dernière position satcom.
3. **Planning** : cascade complète des dates à chaque décalage (UC-03,
   `services/date_cascade.py`) ; vue conflits de quai.

**Livrés et collectés** (les 3 agents + câblage parent) :
- Escale : actions → SOF auto (FLX-04), coûts opérations+quai → LegFinance,
  verrouillage d'escale + garde (migration 0027).
- Bord : check-lists ISM/ISPS + registre visiteurs (FLX-11) ; noon report
  pré-rempli depuis la position satcom (FLX-07, déjà câblé, confirmé).
- Planning : cascade complète des dates (UC-03, `date_cascade.py`) — legs
  aval, opérations d'escale, docker shifts, + notification clients ; vue
  conflits de quai `/planning/conflicts`.
- Captain : cascade câblée dans l'endpoint ETA shift (remplace l'ancienne
  notification directe — plus de double envoi).
- Finance : claims → `LegFinance.claims_cost_eur` (FLX-09, migration 0028 ;
  Σ règlement sinon provision des sinistres provisioned/settled du leg).

Vérifié : `ruff` propre, 318 routes, chaîne Alembic 0023→0028 linéaire,
**270 tests verts**.

**Restent ouverts** : escalade SLA tickets (FLX-08, nécessite un champ
`escalated_at` → migration dédiée, non fait pour éviter une migration
concurrente ce cycle) ; contrôle ROB ±2 t en warning qualité (raffinement
du recalcul MRV) ; surfaçage du coût sinistres dans l'écran finance (la
valeur alimente déjà la marge). Packing list : pas de champ `loading_date`
ni de `leg_id` sur `PackingList` (FK commande) → cascade PL sans objet
tant que la fusion des rails (§4.2) n'a pas ajouté `booking_id`.

## 4. Blocs d'évolution — prêts à lancer

> Cible commune des blocs B1→B3 : **un seul modèle de vente, un seul modèle
> cargo**, alimenté soit par l'opérateur (back-office), soit par le client
> (front). Chaque bloc est une fiche de lancement autonome (objectif,
> périmètre, schéma, étapes, acceptance, dépendances). Migrations additives,
> étapes réversibles. **Prochaine révision Alembic libre : `0029`.**
> Ordre recommandé : **B1 → B2 → B3** (B4/B5 indépendants, lançables en parallèle).

### Bloc B1 — Booking → packing list + portail `/p/{token}` 🟢 GO
**Pourquoi** (direction) : « Rail B booking doit emmener vers Packing lists +
portail /p/token ». Première brique tangible de la fusion des rails.
**Périmètre** : `app/models/packing_list.py`, `app/services/packing_list.py`,
`app/services/booking_lifecycle.py`, migration `0029`, e-mail/notif client.
**Schéma** : `packing_lists.booking_id` (FK bookings, nullable, à côté de
`order_id` qui devient nullable) + contrainte « order_id XOR booking_id ».
**Étapes** :
1. Migration `0029` : `booking_id` nullable + index ; `order_id` nullable.
2. `packing_list.create_for_booking(db, booking)` (jumeau de la création
   rail A) : génère le token 24 hex / 90 j, pré-remplit les champs TOWT
   depuis le booking (leg, POL/POD, références).
3. Hook : dans `booking_lifecycle.on_status_change(..., "confirmed")`, appeler
   `create_for_booking` (idempotent) + notifier le client (lien portail).
4. Portail `/p/{token}` : déjà agnostique du parent — vérifier l'affichage
   quand la PL vient d'un booking (pas d'un order).
**Acceptance** : confirmer un booking crée une PL + token ; le client la
remplit via `/p/{token}` ; verrouillage et audit identiques au rail A.
**Dépendances** : aucune (le lien `client_accounts ↔ commercial_clients` est
déjà posé). **Débloque** : cascade dates → PL (champ `loading_date` à ajouter
ici), et la vue cargo unifiée de B2.

### Bloc B2 — Fusion du traitement des rails A (commandes) et B (bookings) 🟢 GO
**Pourquoi** (direction) : « RAIL A et B doivent fusionner — le canal de vente
est soit le backend (remplissage opérateur) soit le front (remplissage client) ».
**Périmètre** : `app/models/booking.py` (+ `channel`), back-office réservations,
`app/services/capacity.py` (✅ pont fait), vues commerciales.
**Étapes** :
1. Ajouter `channel ∈ {operator, client}` sur le booking (migration) ;
   les commandes opérateur deviennent des bookings `channel=operator`.
2. Machine d'états unique (réconcilier `ORDER_STATUSES` et booking statuses).
3. Back-office « Réservations » unique avec filtre par canal ; vue de
   remplissage et CA par leg consolidée (bookings tous canaux).
4. Migration de reprise : convertir les `orders` existants en bookings
   `operator` (script idempotent, dry-run d'abord).
**Acceptance** : un opérateur et un client créent la même entité ; capacité,
CA et remplissage par leg sont lus en un seul endroit ; aucun double comptage.
**Dépendances** : B1 (le rail B doit déjà atteindre la chaîne cargo).
**Risque** : Med — reprise de données ; livrer derrière un feature flag
(`feature_flags` existe, inutilisé).

### Bloc B3 — Stowage 18 zones ↔ Escale (opérations + dockers) 🟡 GO conditionnel
**Pourquoi** (direction) : « Le plan d'arrimage 18 zones doit connecter avec
Escale : opérations + dockers ».
**Périmètre** : `app/services/stowage.py`, `app/models/stowage.py`,
`app/routers/escale_router.py`, `app/models/escale.py` (lien cale↔shift).
**Étapes** :
1. Exposer l'occupation par cale (`hold`) du plan d'arrimage.
2. Lier `DockerShift.hold` aux zones du stowage (cadence palettes/heure par
   cale) ; à l'auto-assignation, refléter l'ordre de chargement dans la
   planification des vacations.
3. Auto-zone des claims cargo : remonter la position cale d'un batch sinistré
   (prescrit, FLX-10).
**Acceptance** : ouvrir une vacation docker affiche la cale et son
remplissage ; un claim cargo connaît sa zone d'arrimage.
**Dépendances** : aucune dure ; bénéficie de B1/B2 (batches issus des deux
canaux). **Statut** : GO dès qu'une ressource se libère après B1.

### Bloc B4 — Escalade SLA des tickets (FLX-08) 🟢 GO
**Périmètre** : `app/models/ticket.py` (+ `escalated_at`), `app/services/tickets.py`,
`app/services/notifications.py`, tâche de scan, migration.
**Étapes** : champ `escalated_at` ; au dépassement de `sla_target_at` (P1 2 h /
P2 8 h / P3 72 h), notifier le rôle `manager_maritime` une seule fois
(dédup via `escalated_at`) ; déclenché par un scan (cron Power Automate
existant, ou au chargement du kanban). **Acceptance** : un ticket P1 dépassé
notifie le manager une fois et apparaît « en retard ».

### Bloc B5 — Contrôle qualité MRV ROB ±2 t 🟢 GO
**Périmètre** : `app/services/mrv_sync.py` / recalcul MRV, `app/models/mrv.py`
(`quality_status`, `quality_notes`). **Étape** : à la génération MRV depuis
noon report, comparer ROB déclaré vs ROB calculé (précédent + bunkering −
consommation) ; si |écart| > 2 t → `quality_status="warning"` + note.
**Acceptance** : un écart > 2 t lève un warning visible à l'export DNV.

## 5. Actions d'exploitation (hors code) — pré-déploiement

1. **Migrations** : `alembic upgrade head` (chaîne `0023 → 0028` appliquée
   cette itération ; `0029+` viendront avec B1).
2. **Variables d'env** : lancer **`./scripts/setup_env.sh`** (idempotent ;
   complète `MAPTILER_TOKEN`/`MAPBOX_TOKEN`, `COMMERCIAL_INBOX_EMAIL`,
   `PIPEDRIVE_API_TOKEN`, `SMTP_HOST`, et vérifie `SECRET_KEY`/`DATABASE_URL`),
   puis `docker compose up -d --force-recreate app`.
3. **Commercial** : générer les grilles par défaut, **relire les taux**
   (formule OPEX), relier les comptes plateforme aux clients négociés.
4. **Planning** : `python -m scripts.renumber_legs` (dry-run) puis `--yes`
   après revue.

---

*Retour au [cadre & synthèse](README.md). Registre cycle 2 au §7 du README.*
