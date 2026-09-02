# PLN-SEQ — Séquence de planification déclarative (départ / arrivée)

> Livré le 2026-09-01 (branche `claude/practical-mayer-rphihj`). Reprise de la
> logique de gestion du planning : la vie d'un leg suit désormais une séquence
> déclarative unique, sans chevauchement, avec recalculs automatisés et
> historisation de **tous** les mouvements de dates.

## 1. Le modèle en une phrase

Un leg vit la séquence **planifié → en mer → à quai → terminé** ; les deux
transitions du milieu sont **déclarées** (par l'opérateur d'escale ou par le
SOF du bord), tout le reste (SOF, statut, recalculs, historique, finance,
notifications, activation du leg suivant) en découle automatiquement.

## 2. Vocabulaire des dates

| Champ | Sens | Qui l'écrit |
|---|---|---|
| `etd_ref` / `eta_ref` | Référence **figée** à la création — base de la dérive | `create_leg` uniquement |
| `etd` / `eta` | Prévisionnel **courant** (saisi à la **journée**) | Planning (fiche, Gantt), ETA-shift bord, cascade, re-ancrage au départ déclaré |
| `atd` / `ata` | **Réel** déclaré (heure précise) | `voyage_transitions.declare_departure` / `declare_arrival` — chemin unique |

Doctrine transverse : tout calcul « où en est le voyage » lit le réel dès
qu'il existe, le prévisionnel sinon — helpers canoniques
`planning.effective_etd(leg)` / `planning.effective_eta(leg)`.

## 3. Chaîne de séquence fonctionnelle

```mermaid
flowchart TD
    subgraph PLANIF["Planification (échelle : la journée)"]
        A[Création du leg<br/>ETD/ETA saisis en jours<br/>etd_ref/eta_ref figés] --> B[Validations dures :<br/>chevauchement navire · continuité POL/POD<br/>vitesse plausible · clôture booking ETD−48h]
        B --> C[Statut : Planifié]
        C -->|édition / drag-drop / ETA-shift| C2[Recalcul simulé puis appliqué<br/>cascade aval + historisation<br/>schedule_revisions]
        C2 --> C
    end

    C --> D{{"🚢 Déclarer le départ<br/>du port de (POL)<br/>— escale ou SOF bord (SOSP)"}}

    subgraph DEP["Effets du départ déclaré"]
        D1[Inscription SOF : SOSP] --> D2[ATD posé<br/>statut « en mer »<br/>navigation OUVERTE<br/>tracking GPS, MRV, météo]
        D2 --> D3[ETA re-ancrée sur l'ATD<br/>durée de transit conservée]
        D3 --> D4[Recalcul de TOUS les legs suivants<br/>+ opérations escale + dockers<br/>+ clôtures booking + clients notifiés]
        D4 --> D5[Historisation :<br/>schedule_revisions<br/>source=departure_declared + cascade]
        D5 --> D6[Rollup OPEX réel<br/>+ notification SOSP → Opérations]
    end

    D --> D1
    D6 --> E[Statut : En mer]

    E --> F{{"⚓ Déclarer l'arrivée<br/>au port de (POD)<br/>— escale ou SOF bord (EOSP)"}}

    subgraph ARR["Effets de l'arrivée déclarée"]
        F1[Garde de séquence :<br/>ATD requis · ATA ≥ ATD] --> F2[Inscription SOF : EOSP]
        F2 --> F3[ATA posée<br/>statut « à quai »<br/>navigation FERMÉE]
        F3 --> F4[Legs suivants re-ancrés sur<br/>ATA + durée d'escale planifiée]
        F4 --> F5[ACTIVATION du leg suivant<br/>notification → Opérations]
        F5 --> F6[Historisation<br/>source=arrival_declared<br/>+ rollup + notification EOSP]
    end

    F --> F1
    F6 --> G[Statut : À quai]

    G --> H[Opérations d'escale au POD<br/>pointage · SOF terre · verrou escale]
    H --> I[Clôture de voyage<br/>submit → review → approve]
    I --> J[Statut : Terminé]

    F5 -.->|le leg suivant devient<br/>le voyage courant| C

    style D fill:#0D5966,color:#fff
    style F fill:#0D5966,color:#fff
    style E fill:#87BD29,color:#0b2e00
    style G fill:#B47148,color:#fff
```

**Espacement par l'escale** : la résolution des chevauchements recale un leg
au plus tôt à la **disponibilité** du précédent = ETA + escale planifiée
(`port_stay_planned_hours`, défaut 24 h) — jamais à son ETA brute (constat prod
du 2026-09-02 : quatre legs d'Artemis enchaînés le même jour). Même règle dans le
moteur scénario. Pour remettre d'aplomb une planification héritée :
`python -m scripts.respace_downstream_legs` (dry-run par défaut, `--yes`).

**Incident de reprogrammation** : si le recalcul aval devrait déplacer un leg
déjà appareillé, rien n'est écrasé (règle d'or « on ne touche jamais un fait
réalisé »), l'incident est tracé (`summary.skipped`) **et notifié** aux
Opérations (`cascade_blocked`), en plus des alertes dashboard existantes
(retard d'arrivée, ETA dépassée, conflit de port) et du bandeau d'audit du
Gantt (chevauchements, ruptures de continuité, dérives ≥ 4 h).

## 4. Statut stocké vs phase affichée

La machine à états **stockée** ne change pas
(`planned / in_progress / completed / cancelled`,
`services.planning.refresh_leg_status`, seule autorisée à écrire
`leg.status`). La **phase** opérationnelle est dérivée et affichée
(`Leg.phase`) : `planifie → en_mer (ATD) → a_quai (ATA) → termine (clôture
approuvée)`. Décision : ne pas élargir l'enum stockée — trop de consommateurs
de `status` ; la phase répond au besoin d'affichage « en mer / à quai » sans
migration de données ni risque de régression.

## 5. Les deux canaux de déclaration

| Canal | Écran | SOF | Horodatage |
|---|---|---|---|
| Escale (terre) | `/escale` → « Déclarer le départ du port de {POL} » / « Déclarer l'arrivée au port de {POD} » | inscrit automatiquement (SOSP/EOSP) s'il n'existe pas | saisi par l'opérateur (heure précise) |
| Bord | `/captain` → saisie SOF `SOSP` / `EOSP` (+ backstop à la signature) | le SOF **est** le déclencheur | `occurred_at` du SOF (plus jamais « maintenant ») |

Les deux canaux convergent sur `services.voyage_transitions` : mêmes gardes,
mêmes recalculs, même historisation. Re-déclarer au même horodatage est sans
effet (idempotence) ; à un horodatage différent, c'est une **correction
tracée** (ancienne → nouvelle valeur dans l'historique).

### Un seul leg actif par navire

Le départ d'un leg **exige** que le leg précédent du navire soit arrivé (ATA
déclarée) et lui soit postérieur ; il **termine** ce leg précédent dans le même
geste (`legs.voyage_completed_at`, migration `0137` → statut `completed`,
phase « terminé »). À tout instant un navire n'a donc qu'un leg « en mer » ou
« à quai ». La clôture administrative (`closure_*`, workflow captain) reste
indépendante : un leg terminé opérationnellement peut avoir une clôture en
attente (mention affichée sur la fiche).

La liste `/planning` affiche le réel dès qu'il existe (ATD/ATA, pastille
« réel », prévisionnel et écart en jours en dessous) et la phase en statut.

### Reprise des dates réelles

`python -m scripts.backfill_voyage_actuals` (dry-run par défaut, `--yes` pour
appliquer) rejoue un CSV `leg_code,atd,ata` par le chemin unique — séquence
vérifiée, SOF, recalculs, historique, complétion des legs précédents — en mode
`quiet` (sans notifications). Les dates futures sont ignorées (elles restent du
prévisionnel). Quand l'arrivée réelle est fournie, l'ETA prévisionnelle n'est
**pas** re-ancrée sur l'ATD (`reanchor_eta=False`) : re-ancrer une prévision
aussitôt supplantée par l'ATA fausserait le « prévu » affiché (leg planifié au
1ᵉʳ août parti le 6 juin → ETA tirée de 56 j). Seul un leg aval déjà appareillé
qui bloque le recalage est rapporté comme incident. Le script termine par une
**passe de cohérence** (`voyage_transitions.repair_vessel_sequence`) sur toute la
donnée : un leg arrivé dont un leg ultérieur du navire a appareillé (ATD posé par
l'ancien flux, un import…) est terminé opérationnellement — deux legs « à quai »
côte à côte ne peuvent pas subsister. Jeu de données 2026 :
`scripts/data/voyage_actuals_2026.csv`.

## 6. Historisation

`schedule_revisions` (append-only, survit à la suppression du leg) porte
désormais aussi les mouvements du réel : colonnes `old_atd/new_atd/
old_ata/new_ata` (migration `0136`), sources `departure_declared` /
`arrival_declared`. Chaque événement partage un `batch_id` avec ses recalculs
aval (`source="cascade"`, `trigger_leg_id`). Viewer : fiche leg → bouton
« Historique ». L'audit trail (`activity_logs`) et le journal d'escale unifié
restent complémentaires.

## 7. Granularité de saisie

La **planification** se saisit à la journée (`<input type="date">` sur
ETD/ETA/clôture booking, suggestion et Gantt drag-drop snappés au jour). Le
**réel** (déclarations, SOF) garde l'heure précise. L'ETA re-ancrée au départ
hérite de la précision du réel.

## 8. Création d'un leg — page unique (PLN-08)

Maquette validée le 2026-09-02. `/planning/legs/new` (et l'édition) tient sur
**une page**, sans wizard :

1. **Navire** — un bouton par navire (radios stylés), avec l'état de sa
   séquence (dernier leg, ports, ETA/ATA, phase). Le choix pilote le
   pré-remplissage (`_new_leg_suggestions`, sérialisé en `data-suggestions`).
1 bis. **Leg de référence — « Chaîner après »** (correctif 2026-09-02). Le
   défaut reste le **dernier leg par ETD** : créer un leg, c'est le plus souvent
   prolonger la ligne. Mais ce défaut est faux dès qu'un voyage lointain est
   déjà saisi — un leg de janvier 2027 captait le chaînage des legs de l'année
   en cours (« il a repris le leg A alors qu'on programme le D »), avec l'ETD
   **et** le POL qui en découlent. Le formulaire expose donc les derniers legs
   du navire (`chain_options`, 8 max, ETD décroissant) : changer de référence
   redérive ETD, POL, escale et rang. Le sélecteur est masqué quand il n'y a
   qu'une option — un choix à une entrée est du bruit.
2. **Départ / Arrivée côte à côte** — ports habituels (`Port.is_shortcut`,
   repli : **BRSSO São Sebastião, FRFEC Fécamp**) → filtres Zone / Pays / Port →
   **recherche libre** par saisie (nom ou LOCODE, tous les ports actifs, sans
   filtre zone/pays). Le **POL se remplit automatiquement** = POD du dernier leg
   du navire (continuité), via l'événement `leg:pick-port` (`leg-cascade.js`).
3. **Dates** — ETD pré-rempli (ETA/ATA du leg précédent + escale, jour ouvré du
   port), ETA calculée (distance × élongation ÷ vitesse, arrondie au jour) ;
   tous deux modifiables. **Escale saisie en jours**
   (`port_stay_planned_days` → stockée en heures ×24 ; le champ historique en
   heures reste accepté). Vitesse / élongation derrière un repli.
4. **Réservation** — une seule case « Ouvrir à la réservation » ; capacité et
   clôture reprennent les défauts (navire, ETD − 48 h), ajustables en édition.
5. **Récapitulatif** — code de leg prévisionnel (rang chronologique de l'année
   de l'ETD suggéré), route, dates, jours de mer, escale.

## 8 bis. Audit de séquence — ce qu'il dit et ce qu'il ne dit pas

`audit_planning_sequence` compare, navire par navire et par ETD croissant, la
disponibilité du leg précédent (**arrivée effective + escale planifiée**) au
départ du leg suivant. Trois règles corrigées le 2026-09-02 :

- **Dates effectives** — l'ATA prime sur l'ETA (l'ATD sur l'ETD). Mesurer une
  escale contre une prévision déjà périmée produisait des alertes fausses.
- **Un leg appareillé n'est plus audité** — son ATD est un fait, pas un conflit
  de planification : l'alerte était insoluble par construction.
- **Le message porte les chiffres** — date d'arrivée (en précisant ATA ou ETA),
  durée d'escale, date de disponibilité, départ constaté, manque en jours, et
  les deux corrections possibles (réduire l'escale du précédent, ou décaler le
  départ). « démarre avant la fin de l'escale prévue » ne disait rien
  d'actionnable.

`distance_missing` nomme désormais sa cause : un port sans coordonnées. La
distance théorique (`Leg.distance_nm`) est posée au create/update et vaut
`None` si l'orthodromie n'est pas calculable ; sans elle, l'**écart** et
l'**allongement réel** sont vides eux aussi. Trois voies de réparation :
repli calculé au rendu (`voyage_track.theoretical_distance_nm`, marqué `*`),
saisie des coordonnées dans **Admin → Ports** (qui recalcule les legs du port),
et reprise à froid `scripts/backfill_leg_distances.py`.

## 9. Ce qui reste connu et assumé (non traité ici)

- Le sélecteur de fuseau du formulaire d'horodatage escale reste décoratif
  (saisie interprétée en UTC) — à traiter séparément (ESC-07).
- Le facteur d'émission MRV est daté sur l'ETD (choix de stabilité du
  référentiel) — à arbitrer avec le métier.
- `vessel_readiness` / `crew_border_police_pdf` ne lisent que les affectations
  rattachées à un leg (angle mort documenté côté équipage).
- La page publique `planning_share` continue d'annoncer ETD/ETA prévisionnels
  (document commercial) ; seul le transit affiché passe au réel.
