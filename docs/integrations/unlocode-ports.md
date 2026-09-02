# Référentiel des ports — UN/LOCODE (UNECE)

> Source de vérité des **codes de ports** de `mynewtowt`. `Port.locode` est la
> clé métier : elle apparaît dans les `leg_code`, les grilles tarifaires, les
> connaissements et les pages publiques. Elle ne s'improvise pas.

## 1. Ce que fournit chaque source

| Source | Contenu | Fraîcheur | Accès | Licence |
|---|---|---|---|---|
| **UN/LOCODE (UNECE)** — officiel | le référentiel des codes | 2 éditions/an (mars, septembre) | zip CSV, **pas d'API** | ODC-PDDL (domaine public) |
| **`datasets/un-locode`** (miroir GitHub) | même liste, CSV propre, coordonnées **DDMM** | suit UNECE avec retard (v2024.2.0 au 2026-09-02, UNECE étant à 2025-1) | fichier brut HTTPS | ODC-PDDL |
| **`cristan/improved-un-locodes`** | idem + colonne `CoordinatesDecimal` | suit le miroir ci-dessus | fichier brut HTTPS | PDDL + **ODbL** sur la part OSM |
| **NGA World Port Index (Pub 150)** | ~3 700 **vrais** ports maritimes (profondeurs, mouillages, installations) | mensuelle | CSV/shapefile + FeatureServer REST GeoJSON, sans clé | domaine public US |
| OpenStreetMap / Overpass | live, sans LOCODE, bruité | temps réel | API | ODbL |

**Choix retenu** (2026-09-02) : le miroir **géolocalisé** est la source par
défaut du chargeur. Mesuré sur les fichiers réels :

| Fichier | lignes exploitées | ports maritimes retenus |
|---|---|---|
| `code-list.csv` (miroir brut) | 93 045 / 116 213 | **11 763** |
| `code-list-improved.csv` (défaut) | 114 575 / 116 075 | **16 669** |

Les ~23 000 lignes perdues par le miroir brut le sont faute de coordonnées :
UNECE en laisse 20 % sans position, **dont de vrais ports** (Manille en était
absente). Sans coordonnées, pas d'orthodromie — donc pas de distance théorique,
et par ricochet ni écart ni allongement réel sur les legs concernés.

## 2. Attribution obligatoire (ODbL)

La colonne `CoordinatesDecimal` est en partie dérivée d'**OpenStreetMap**
(Nominatim) et de **Wikidata** : 5 578 positions de ports y sont corrigées.
Cette part est sous **ODbL 1.0**. Toute republication de ces positions —
carte de la vitrine publique, export client, PDF — doit porter l'attribution
`© OpenStreetMap contributors`. Les fonds de carte MapLibre/MapTiler la portent
déjà ; **un export tabulaire de coordonnées, non.**

Les entrées issues du miroir géolocalisé sont marquées `Port.source =
"unlocode-improved"` : c'est ce qui permet de savoir ce qui est concerné.

## 3. Hiérarchie des sources — ce qui écrase quoi

`services.ports.may_overwrite` (vérifié par
`tests/integration/test_ports_upsert_sources.py`) :

| `Port.source` | Priorité | Écrasé par |
|---|---|---|
| `manual` | 30 | rien d'automatique — **correction humaine intouchable** |
| `world_ports` | 20 | `manual` uniquement (catalogue embarqué, curé à la main) |
| `unlocode-improved` | 15 | `manual`, `world_ports`, ou un ré-import de lui-même |
| tout le reste (`unlocode`, `datagouv:*`, `file:*`, `csv`) | 10 | dernière source importée |

Sans cette hiérarchie, un rafraîchissement UN/LOCODE **dégradait** les
coordonnées curées : le catalogue embarqué place Fécamp à 49,7594 / 0,3742, là
où UN/LOCODE l'arrondit à la minute d'arc (49,75 / 0,38333, ~1 km d'écart). Le
chargeur promettait déjà « ne remplace jamais une entrée manuelle » — promesse
creuse : aucune source n'écrivait `manual`.

**Conséquence pratique** : une coordonnée corrigée dans **Admin → Ports** doit
passer la source à `manual`, sans quoi le prochain import l'efface.

## 4. Filtre maritime, et ses limites

Le filtre est la **position 1 du code fonction** (`Function[0] == "1"` = port
accessible par voie d'eau). Positions suivantes : 2 rail, 3 route, 4 aéroport,
5 échange postal, 6 plateforme multimodale, 7 transport fixe, B point de
passage frontalier.

⚠ **Ce filtre rate de vrais ports.** Le code fonction est déclaratif et parfois
faux ou périmé. Cas relevé le 2026-09-02 :

- `REPDG` « Pointe des Galets » — fonction `--3-----` (route seule) et statut
  **`XX` = entrée qui sera retirée de la prochaine édition** ;
- `RELPT` « Le Port » — fonction `1-3-5---`, statut `AF` (approuvé par
  l'organisme national de facilitation), −20,9333 / 55,3167 : **c'est le code
  du port de La Réunion.**

Le chargeur **n'ajoute pas** les entrées en statut `XX`, mais ne supprime
jamais un port déjà en base : un code retiré chez UNECE peut rester porté par
un booking ou un connaissement passé. **Ne jamais purger le référentiel sur le
critère du code fonction.**

Statuts UN/LOCODE considérés comme officiellement approuvés : `AA`, `AC`, `AF`,
`AI`, `AM`, `AS`. Sur les 17 527 entrées à fonction port, 7 398 cumulent un
statut approuvé **et** des coordonnées — sous-ensemble utile si l'on veut un
jour un référentiel resserré.

## 5. Rafraîchir le référentiel

```bash
# Catalogue embarqué + ports de France (data.gouv.fr)
docker compose exec app python -m scripts.load_ports

# + long tail mondiale UN/LOCODE géolocalisée (recommandé)
docker compose exec app python -m scripts.load_ports --with-unlocode

# Repli sur le miroir brut UNECE (coordonnées DDMM, couverture moindre)
docker compose exec app python -m scripts.load_ports --with-unlocode \
  --unlocode-url https://raw.githubusercontent.com/datasets/un-locode/master/data/code-list.csv
```

Le chargeur est **idempotent** et journalise un compte-rendu explicite
(lignes lues, retenues, écartées et pourquoi, ports maritimes, insérés,
mis à jour). Un import muet ne se contrôle pas : lire ce rapport fait partie de
l'opération.

**Egress requis** : `raw.githubusercontent.com` (UN/LOCODE) et
`www.data.gouv.fr` (ports français). À vérifier depuis le serveur avant de
planifier un rafraîchissement automatique.

## 6. Ce qui n'est pas fait (et pourquoi)

- **Pas de cron automatique** : UNECE publie deux fois par an, un
  rafraîchissement manuel à cette cadence suffit. Un cron mensuel se
  justifierait surtout adossé au WPI (mise à jour mensuelle) — décision non
  prise.
- **Pas de croisement NGA World Port Index** : c'est la seule source qui dit
  quels ports sont *réellement* commerciaux (profondeurs, installations, taille
  de bassin) et elle porte un champ UN/LOCODE. Elle exigerait une migration
  (nouveaux champs) et un egress vers `msi.nga.mil` — à trancher.
- **Pas de passage au zip officiel UNECE** : le miroir GitHub est en retard
  d'une édition. Lire l'officiel supprime cette dépendance, au prix d'un
  dézippage et d'un suivi du motif d'URL par édition.
