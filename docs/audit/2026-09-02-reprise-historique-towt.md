# Reprise d'historique TOWT — audit des sources et plan d'action

> Rédigé le 2026-09-02 (branche `claude/newtowt-historical-data-f3mdsw`) à la
> demande de Julien Gondé. Décision d'architecture associée : `ADR-014`.
> Méthode : audit multi-agents (3 agents d'exploration du code, 1 agent de
> revue critique sur un modèle distinct), conception et coordination
> centralisées. Légende de certitude : ✅ fait mesuré · 🟡 hypothèse
> raisonnable · ❓ à confirmer par Julien.

## 1. Situation

NEWTOWT est la reprise d'une compagnie antérieure (TOWT). Ses deux navires
(ANEMOS, code `1` ; ARTEMIS, code `2`) naviguent depuis 08/2024 et l'ERP
`mynewtowt` n'a démarré qu'en 2026, **à vide** (décision Q1 du chantier MRV v2 :
aucune donnée historique en production). Quatre gisements d'historique existent
hors ERP :

| Source | Où | Contenu | Volume ✅ |
|---|---|---|---|
| Classeur des traversées | `Historique_Traversées_V2.xlsx` (fourni) | 36 voyages 2024-08 → 2026-01, 2 onglets visibles (ANEMOS 19, ARTEMIS 17) + 2 masqués (PORTS 226 lignes, ITINERAIRES 40 routes) | 120 Ko |
| Relevés GPS satcom | SharePoint « Service Technique / 12 - Tracking » | 1 CSV par navire et par heure, ~12 points au pas de 5 min | ≈ 32 000 fichiers, ≈ 400 000 points 🟡 |
| Noon reports | SharePoint « 10 - Data reporting Noon reports » | 1 classeur Excel par jour et par navire (Noon / Departure / Arrival), 2 générations de formulaire | ≈ 1 300 classeurs 🟡 |
| Ancien tableau de bord | `20260127_Tableau_exp.pbix` (fourni) | Power BI, 10 pages, 12 tables | 1,6 Mo |

Objectif exprimé : reprendre cet historique dans l'ERP, **non modifiable**,
**filtrable avec la mention TOWT**, en créant les legs à partir des informations
exploitables aujourd'hui, et en tirer une évolution de la culture data.

## 2. Analyse des sources — faits mesurés

### 2.1 Classeur des traversées (legs)

**Structure.** Colonnes : TRIP CODE, POL ID, POD ID, POL/POD NAME (vides), ETD/ETA
(vides), ETO/ETC (8-9 legs 2024 seulement), ATD/ATA (« DEPARTURE/ARRIVAL » côté
ARTEMIS), OPERATIONS/COMPLETION (8 legs 2024), FILLING RATE et REMARKS (vides).
Seules les **dates réelles au jour** sont fiables et complètes (36/36). Il n'y a
**aucun prévisionnel** exploitable.

**Codage TOWT des voyages.** Deux générations ✅ :

- 2024 : `{navire}{POL char}{POD char}{rang}{année}` — ex. `1YMB4` = navire 1,
  Y (New York) → M (Santa Marta), 2ᵉ voyage, 2024. Cas atypiques : `1LY1A4`,
  `2VH0A4`, `2ZL54`.
- 2025+ : `{navire}{rang}{POL char}{POD char}{année}` — ex. `1HYF5` = navire 1,
  8ᵉ voyage 2025, Y (New York) → F (Fécamp). Sous-legs suffixés `2LQF5-B`, `-C`.

Le caractère de port (`CHAR TOWT`, onglet PORTS) n'est défini que pour 16 ports
et **entre en collision** (`Z` = BRSSO et FRCOC ; `V` = BRVIX, USPCA, VNSGN) ;
`F` (Fécamp) et `B` (Puerto Barrios) sont utilisés sans être déclarés. C'est
exactement la faiblesse que le format ERP (`1CFRBR6`, pays ISO à 2 lettres)
corrige. **Conséquence** : le code TOWT n'est pas reconstructible par règle — il
doit être **conservé tel quel** comme identifiant d'archive.

**Anomalies relevées** (28, détail dans `scripts/data/towt_legs_history.csv`, colonne `notes`) :

| Type | Cas | Traitement |
|---|---|---|
| Faute de frappe bloquante | `2NZF5` ATA `2016-01-14` (ATD 2025-12-15) | Corrigée en `2026-01-14` 🟡 (durée cohérente avec `2MFZ5`), valeur brute conservée (`source_ata_raw`) |
| Ruptures de continuité (POD ≠ POL suivant) | `1CLA5`, `1DLG5`, `2DLY5` | Arrêts techniques / repositionnements non tracés ; **signalés, jamais inventés** |
| Dates ETO/ETC incohérentes | `1YLF4`, `2ZL54`, `2LYD4` (valeurs en 2025-04 pour des legs 2024) | Ignorées (colonnes non reprises) |
| OPERATIONS/COMPLETION postérieures à l'ATD | `2VH0A4`, `2RZB4` | Dates d'opérations à l'arrivée ; non reprises (pas de champ cible) |
| POL = POD | `2LQF5-B` (FRLEH → FRLEH) | Mouvement portuaire ; repris tel quel, sans distance |
| Ports absents de l'onglet PORTS | FRFEC (×11), REREU (×2), CAMAT (×2) | Catalogue embarqué dans le script (coordonnées approximatives, `source=user`) |
| Ports absents des catalogues du dépôt | COSTM, GTPBR, REREU, CAMAT, FRCOC | Idem ; un import UN/LOCODE ultérieur pourra les raffiner (précédence 15 > 10) |
| Code UN/LOCODE discutable | `REREU` (Réunion) pour Le Port (`RELPT`) | Conservé fidèlement, nom explicite « La Réunion (archive TOWT — Le Port) » |

**Onglet ITINERAIRES** : hypothèses de planification TOWT (vitesse 9,5 kn,
élongation 5 %, 4 j d'opérations, 40 routes avec distance orthodromique). C'est
un **référentiel de vitesse observée par route** (colonnes « Transit time
expected » / « Speed »), utile pour calibrer `Vessel.default_speed_kn` /
`default_elongation` — non repris en base, cité en §6.

### 2.2 Relevés GPS satcom

**Format** ✅ (`20241021100502-anemos-satcoms.csv`) : `;`-séparé, colonnes `Date`
(ISO local sans fuseau), `Timestamp` (epoch **UTC**, fait foi), `Latitude`,
`Longitude` (décimaux) + DMS, `SOG (knots)`, `COG (degree)`, `Active interface`
(`Starlink_…`), `Signal`, `Total distance (nm)` (cumul intra-fichier). Ordre
antéchronologique ; la première ligne (heure ronde) est souvent sans SOG/COG.

**Couverture** : premier fichier `2024-10-21 10:05` ✅. Les voyages
`1LY1A4`, `1YMB4`, `1MQC4`, le début de `1QLD4` et `2VH0A4` (08 → 10/2024) n'ont
donc **pas de trace GPS** dans ce dossier ❓ (à confirmer : autre dossier, autre
prestataire ?). Une copie du dossier existe dans « NAS DIRECTION FLOTTE » (même
contenu, figé au 2024-10-21).

**Dans l'ERP** : `vessel_positions` porte déjà la clé naturelle
`UNIQUE(vessel_id, recorded_at)`, une colonne `source` libre et `import_batch`
(TRK-05). Le rattachement à un leg est **temporel** (`voyage_track.leg_window`),
jamais par FK — d'où l'ordre impératif : **legs d'abord, positions ensuite**.
Deux points à corriger pour absorber 5 min de pas : la table était **purgeable**
(`admin_data.ALLOWED_PURGE_TABLES`) et `/tracking` sérialisait **tous** les
points d'une année dans la page (≈ 105 000 points/navire/an → plusieurs Mo).

### 2.3 Noon reports

**Formats** ✅ : onglet « Reporting form » = formulaire à libellés (colonne A),
onglet « Data » = listes de valeurs. Deux générations : « Version 3 » (09/2024,
8 colonnes) et « CFOTE_05 Noon Report Rev 2.1 » (dès le 20/09/2024, 14-16
colonnes : compteurs D / D-1 / départ en heures et **litres**, densité GO,
tirants d'eau, EU-MRV, cargo MT). Rubriques : identité (navire, **Voyage number =
TRIP CODE TOWT**, type), date/heure **locale + fuseau**, position DMS, ports,
condition, distances/vitesses depuis le dernier rapport et depuis le départ, ETA,
engins (heures, conso), ROB DO/urée/eau douce, météo ×6 tranches de 4 h, voiles
J0/J1/MS avant-arrière + boost + charge ME ×6 tranches, cales (T°/HR minuit et
midi, 9 zones), commentaires.

**Un pipeline existe déjà** ✅ : le dossier `Logs/` montre un script Python
« NEWTOWT — Extraction Noon Reports » lancé par tâche planifiée (15 h) depuis le
2026-06-26, qui consolide les rapports **2026** dans une « BDD » locale (132
fichiers au 2026-09-02) — avec des erreurs récurrentes en 07-08/2026
(`bat_errors.log`). Il ne couvre pas 2024-2025 et son code n'est pas dans le
dépôt ❓.

**Dans l'ERP** : le modèle legacy `noon_reports` (+ `_engines`, `_weather`,
`_sails`, `_holds`) couvre presque tout le formulaire mais est **gelé en écriture
depuis le LOT 14** ; le modèle cible `nav_events` est le périmètre réglementaire
MRV v2 (décision Q1 : démarrage à vide). Écrire l'archive dans l'un ou l'autre
contredit une décision actée — voir ADR-014, décision 6.

### 2.4 Ancien tableau de bord Power BI

Modèle ✅ : 12 tables — `Sailing Schedules` (= le classeur des legs), `TraceGPS`
(Index, Trip Code, Latitude, Longitude, Timestamp, SOG), `Noon Reports Data`,
`NR Sails in use`, `NR Engine` / `NR Engine Pivot` (heures voile pure / voile
assistée / moteur), `Holds humidity` / `Holds temperature`, `CO2 Emissions`
(Consumption [l], CO2 Savings [tCO2s], Decarbonation Rate, UTT rate gCO2/t-km,
comparateurs avion `EF-PAX`, `DR-AIR`, `CDG-YUL Eq` et Ro-Pax `LEH-QUE Eq`),
`LOADING_DATA` (clients, produits, poids, palettes, taux de remplissage surface
et poids, `FREIGHT_RATE_PER_PALLET_EURO`), `Distance Ortho`, `Distance
Elongation` (réel/ortho, `EC Gap`). 10 pages : Trip Report (interne et
commercial), Nav Profile, Holds, GPS, PAX avion, PAX Ro-Pax, Freight Rates,
Emissions, Routes.

Le modèle de données compressé (VertiPaq) n'est pas lisible hors Power BI : les
**données** du PBIX ne sont pas une source de reprise, mais son **modèle** est
le cahier des charges implicite des indicateurs qui manquent à l'ERP (cf. §6).
Les émissions y sont calculées dans Power BI : elles ne doivent **pas** être
reprises telles quelles (règle d'or `emission_ledger`).

## 3. Ce que l'ERP sait faire aujourd'hui (constats code)

- ✅ Aucun marqueur d'origine ou d'archive sur `legs` ; `leg_code` unique,
  recalculé par `renumber_vessel_year` (rang = ordre des ETD de l'année civile,
  26 legs max par navire-année, un seul chiffre d'année). Insérer un leg
  d'archive 2026 sans précaution **renumérotait les legs NEWTOWT 2026**
  (`1AFRBR6` → `1BFRBR6`), casse la clé citée dans les noon reports en cours.
- ✅ `etd_ref/eta_ref/etd/eta` NOT NULL : un leg sans prévisionnel doit poser
  `etd = atd`, `eta = ata` (précédent `import_mrv_2025`).
- ✅ `voyage_transitions` est le chemin unique du réel (SOF, cascade,
  notifications) ; inadapté à une reprise de faits sans SOF ni leg suivant —
  le précédent `import_mrv_2025.ensure_legs` pose ATD/ATA directement.
- ✅ Immutabilité existante = **absence d'endpoints** (`/mrv/archive/events`)
  ou **garde d'exécution** (`escale_router._assert_escale_unlocked`).
- ✅ Filtre transverse `leg_filter` limité à `N-1 … N+2` : 2024 inaccessible.
- ✅ Météo historisée : seul le dernier point est snapshoté — l'archive n'aura
  pas de météo.
- ✅ MRV/OVDLA ne lit pas `vessel_positions` : la reprise GPS ne change aucun
  chiffre réglementaire.

## 4. Risques

- 🔴 **Renumérotation des codes 2026** par l'arrivée d'un leg d'archive dans
  l'année (voir §3). Traité : exclusion des archives de `renumber_vessel_year`,
  code TOWT conservé, test de non-régression.
- 🔴 **Purge de l'archive GPS** depuis `/admin/data` (table purgeable, aussi par
  rétention). Traité : lignes `source='towt_archive'` exclues de tout DELETE.
- 🟠 **Page `/tracking` illisible** (JSON de plusieurs Mo) avec un pas de 5 min.
  Traité : décimation d'affichage à 4 000 points, calcul de distance conservé sur
  la trace complète, mention à l'écran.
- 🔴 **Séquence vivante polluée** (revue critique) : sans exclusion, l'archive
  entrait dans la validation de chevauchement/continuité, la cascade, les
  voisins de séquence, l'audit `/planning`, le **taux de ponctualité publié**
  (100 % par construction), le compteur public de traversées, le contrôle
  qualité MRV nocturne et le filtre transverse de dix modules. Traité :
  exclusion systématique (ADR-014, décision 7), filtre transverse
  `include_archive=False` par défaut.
- 🔴 **Écrivains non gardés** (revue critique) : `scenario.apply` et le
  décalage d'ETA du bord modifiaient un leg d'archive. Traité : garde ORM
  `before_flush` + trigger PostgreSQL (ADR-014, décision 3) — l'immutabilité ne
  dépend plus d'un appel oublié.
- 🟡 **Rattachement temporel GPS ↔ leg** : un point entre deux legs (escale,
  arrêt technique) n'appartient à aucun voyage — c'est correct ; mais un leg
  d'archive sans ATA (aucun ici) avalerait tout le futur.
- 🟡 **Comparaisons KPI** : les indicateurs annuels (`/performance/navigation/
  kpis`, finance, Anemos) verront apparaître 2024-2025 sans données de cargo,
  OPEX ni MRV. Le badge « TOWT » et le filtre d'origine rendent la lecture
  explicite ; aucune consolidation financière ou carbone n'est alimentée.
- 🟡 **Ports approximatifs** créés par le script (`source=user`) : coordonnées
  au port, pas au quai. Raffinables par le chargeur UN/LOCODE sans blocage.
- 🟢 **Décision Q1 réouverte** (données historiques en prod) : c'est un choix
  de gestion, documenté par l'ADR-014, limité aux legs et aux positions.

## 5. Recommandation et périmètre livré

**Recommandation** : reprendre **maintenant** les legs et les positions GPS
(faits simples, valeur opérationnelle immédiate : cartes, distances réelles,
allongement, continuité de la flotte depuis la mise en service), et instruire
les **noon reports** dans un second lot après arbitrage du modèle cible.

Livré sur la branche (détail technique : ADR-014, journal du 2026-09-02) :

1. `legs.origin` (`newtowt` | `towt_archive`, migration 0138) + propriété
   `Leg.is_archive` ; garde `assert_leg_mutable` (édition, déplacement,
   suppression, déclarations départ/arrivée, escale) ; exclusion de la
   renumérotation ; filtre `origin` et badges « TOWT » dans `/planning` ; bandeau
   lecture seule sur la fiche.
2. `scripts/data/towt_legs_history.csv` (36 voyages, corrections tracées) +
   `scripts/import_towt_legs.py` (dry-run par défaut, idempotent, ports manquants
   créés, ruptures signalées).
3. `scripts/towt_gps_consolidate.py` (poste local, bibliothèque standard :
   consolide les ~32 000 CSV en un fichier par navire et par an + manifeste
   SHA-256) et `scripts/import_towt_positions.py` (serveur : insertion en lot,
   idempotente, `source='towt_archive'`, `import_batch` = fichier consolidé).
   Protection anti-purge et décimation d'affichage.
4. `scripts/towt_noon_extract.py` (prototype local : extraction des deux
   générations de formulaire vers NDJSON + CSV de synthèse, sans écriture en
   base) — pour instruire le lot 2 sur données réelles.
5. Tests : 27 nouveaux (gardes de service et garde ORM, renumérotation,
   séquence vivante, indicateurs publiés, filtre transverse, scénarios,
   contrôle MRV, purge, scripts sur le CSV réel, parseurs, décimation).
6. Revue critique par un second modèle (26 constats : 8 🔴, 8 🟡, 10 🟢) —
   tous les 🔴 et les 🟡 de code traités ; restent en lot 2 la sentinelle des
   sites `select(Leg)` et l'échantillonnage SQL des traces pour les KPI
   annuels.

## 6. Évolution de la pratique — culture data

Ce que l'ancien tableau de bord mesurait et que l'ERP ne mesure pas encore,
classé par valeur pour les Opérations :

| Indicateur TOWT (PBIX) | État ERP | Recommandation |
|---|---|---|
| Profil de propulsion par voyage (h voile pure / voile assistée / moteur, voiles J0/J1/MS par tranche de 4 h) | `nav_events` + `sail_readings` MRV v2 (capture bord) ; profil 4 h dans le dashboard env | P1 — exposer un « profil de navigation » par leg côté `/performance/navigation`, alimenté par les événements validés |
| Allongement réel vs ortho (`EC Gap`) et vitesse moyenne par route | `voyage_track.compute_metrics` par leg | P1 — vue **par route** (agrégat multi-legs), alimentée dès la reprise GPS ; remplace l'onglet ITINERAIRES |
| Cales : T°/HR minuit-midi par zone, écarts | `hold_readings` MRV v2 | P2 — graphe par leg dans la fiche cargo (valeur client : preuve de conservation) |
| Taux de remplissage surface / poids, clients et produits à bord, fret €/palette | Bookings/PL/offres (rail commercial) | P1 — KPI de remplissage **par leg** à partir des PL confirmées ; la donnée existe déjà |
| Comparateurs PAX avion / Ro-Pax, `CO2 Savings`, `Decarbonation Rate` | `co2.estimate` = comparateur officiel ; grand livre unique | P2 — ne pas réintroduire de calcul hors `emission_ledger` ; les comparateurs passagers relèvent de la vitrine 2027 |

Pratiques à faire évoluer (proposition) :

1. **Une clé de voyage, une seule** : le `leg_code` ERP devient la référence
   unique dans les noon reports (« Voyage number »), les fichiers, les mails.
   Les codes TOWT restent lisibles via le filtre « Archive TOWT ».
2. **Le noon report Excel est une saisie, pas un stockage** : la capture
   `/onboard/events` (MRV v2) remplace le formulaire ; le pipeline local
   « Extraction Noon Reports » (hors dépôt, en erreur depuis 07/2026) doit être
   soit rapatrié dans le dépôt (script versionné, testé), soit arrêté au profit
   de la capture bord. Décision Q6 déjà actée : la capture est le chemin.
3. **GPS en continu dans l'ERP** : le cron Power Automate alimente déjà
   `/api/tracking/upload` ; vérifier qu'il tourne au pas de 5 min et que la
   rétention ne purge plus que le live (l'archive est protégée). Le dossier
   SharePoint « 12 - Tracking » devient une sauvegarde, pas une source.
4. **Référentiel de ports partagé** : les 5 ports créés par la reprise sont à
   revoir dans Admin → Ports (coordonnées quai, `source=manual`).
5. **Calibrage des hypothèses de planification** : l'onglet ITINERAIRES donne
   des vitesses observées par route (7,1 kn USNYC→COSTM, 12,5 kn BRSSO→USBOS…) ;
   après reprise GPS, `Vessel.default_speed_kn` / `default_elongation` peuvent
   être ajustés sur le réel mesuré, route par route.

## 7. Prochaines étapes

| # | Action | Qui | Quand |
|---|---|---|---|
| 1 | ~~Valider l'ADR-014~~ — **accepté le 2026-09-02** (7 décisions, dont la table d'archive des noon reports) | Julien | fait |
| 2 | Lancer `python -m scripts.import_towt_legs` (dry-run puis `--yes`) en staging, contrôler `/planning?origin=towt` | Julien / Yasmin | J+1 |
| 3 | Exécuter `scripts/towt_gps_consolidate.py` sur le poste synchronisé, vérifier `manifest.json` (points, trous > 6 h, premier fichier 2024-10-21) | Julien | J+1 |
| 4 | Importer les CSV consolidés (`import_towt_positions`, dry-run puis `--yes`), contrôler `/tracking` (historique 2025, leg `1HYF5`) et `/performance/navigation/kpis` 2025 | Yasmin | J+2 |
| 5 | Exécuter `scripts/towt_noon_extract.py` sur 2024-2025, remonter les échecs de parsing ; arbitrer la table cible (ADR-014 D6) | Julien | J+7 |
| 6 | Confirmer la couverture GPS avant le 2024-10-21 (autre source ?) | Julien | J+7 |
| 7 | Lot 2 — archive des noon reports (table immuable + viewer lecture seule) | dev | après arbitrage |

Hors périmètre, dit sans détour : aucune donnée commerciale (clients, poids,
fret) ni financière (OPEX) de la période TOWT n'est reprise — les sources
fournies ne les contiennent pas de façon exploitable (`LOADING_DATA` vit dans le
PBIX compressé). Aucune émission historique n'est calculée.
