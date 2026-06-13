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

Reste à câbler par le parent après collecte : appel `cascade_from_leg`
dans l'endpoint ETA shift du `captain_router` ; `claims_cost` et finalisation
du rollup ; escalade SLA tickets (FLX-08) ; contrôle ROB ±2 t.

## 4. Refontes profondes — séquencement (à venir)

Ces trois directions partagent une cible : **un seul modèle de vente, un seul
modèle cargo**, alimenté soit par l'opérateur (back-office), soit par le client
(front). Elles se font par étapes réversibles, schéma additif d'abord.

### 4.1 Fusion des rails A (commandes) et B (bookings)
**Cible** : un objet de réservation unique, deux canaux de saisie. Le canal
est une propriété (`channel ∈ {operator, client}`), pas un modèle séparé.
**Étapes** :
1. Pont de capacité (✅ déjà : `capacity.py` somme bookings + commandes).
2. **Booking → packing list + portail** (§4.2) : rail B atteint la chaîne cargo.
3. Vue unifiée du remplissage et du CA par leg (référentiel client commun
   `client_accounts ↔ commercial_clients`, ✅ lien déjà posé en cycle 2).
4. Convergence des statuts (commande/booking) sur une seule machine d'états.
5. Bascule des écrans : un back-office « réservations » unique (filtre canal).

### 4.2 Booking → packing list + portail `/p/{token}`
**Schéma** : `packing_lists.booking_id` (nullable, à côté de `order_id`),
migration `0028`. **Service** : sur `booking → confirmed`
(`booking_lifecycle.on_status_change`), créer la PL + token portail (réutilise
`services/packing_list`), notifier le client. **Effet** : le client booking
remplit sa packing list comme un chargeur du rail A — première brique tangible
de la fusion.

### 4.3 Stowage 18 zones ↔ Escale (opérations + dockers)
**Cible** : le plan d'arrimage et les vacations dockers parlent le même langage
de cale. **Étapes** : exposer l'occupation par cale (`hold`) du stowage aux
docker shifts (cadence par cale) ; à l'auto-assignation, refléter l'ordre de
chargement dans la planification des shifts ; remonter la position cale d'un
batch sinistré au claim (auto-zone, prescrit).

## 5. Actions d'exploitation (hors code)

1. `alembic upgrade head` (migrations 0023→0027 ; 0028 à venir avec §4.2).
2. Variables d'env : `MAPTILER_TOKEN` (ou `MAPBOX_TOKEN`),
   `COMMERCIAL_INBOX_EMAIL`, `PIPEDRIVE_API_TOKEN` ; puis
   `docker compose up -d --force-recreate app`.
3. Commercial : générer les grilles par défaut, **relire les taux** (formule
   OPEX), relier les comptes plateforme aux clients négociés.
4. Lancer `python -m scripts.renumber_legs` (dry-run) puis `--yes` après revue.

---

*Retour au [cadre & synthèse](README.md). Registre cycle 2 au §7 du README.*
