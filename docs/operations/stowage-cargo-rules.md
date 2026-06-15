# Règles de gestion & consignes de cargaison — Plan de chargement

> Module **Stowage** (`/stowage`) · référentiel admin `/admin/stowage-specs`
> Source métier : *plan théorique de chargement café* (classe Phoenix —
> Anemos & sister-ships). Décisions produit : voir l'historique de la
> fonctionnalité « capacité de chargement du navire ».

Ce document décrit comment NEWTOWT modélise la **capacité de chargement** du
navire, les **contraintes d'arrimage**, la **remontée de la packing list** dans
le plan, et le **repérage visuel** de la marchandise à bord.

---

## 1. Structure du navire — 18 zones

Tous les navires d'une même classe partagent une structure identique :

```
2 cales (HOLD)  ×  3 ponts (DECK)  ×  3 blocs (BLOCK)  =  18 zones
```

| Axe | Codes | Signification |
|---|---|---|
| **DECK** (pont) | `SUP` / `MIL` / `INF` | Supérieur / Intermédiaire / Inférieur |
| **HOLD** (cale) | `AR` / `AV` | Arrière (poupe) / Avant (proue) |
| **BLOCK** (bloc) | `AR` / `MIL` / `AV` | Arrière / Milieu / Avant dans la cale |

**Convention de nommage :** `{DECK}_{HOLD}_{BLOCK}` — ex. `INF_AR_MIL` =
pont inférieur, cale arrière, bloc milieu.

Le document fournisseur parle de **« 6 cales ségréguées »** : ce sont les 6
combinaisons pont × cale (`SUP_AR`, `MIL_AR`, `INF_AR`, `SUP_AV`, `MIL_AV`,
`INF_AV`), chacune découpée en 3 blocs → 18 zones.

**Ordre de chargement** : arrière → avant, bas → haut
(`INF_AR_AR` = 1 … `SUP_AV_AV` = 18), cf. `ZONE_LOADING_ORDER`.

**Zones dédiées** (`DANGEROUS_ZONES`) : `SUP_AV_*` reçoivent en priorité les
marchandises **dangereuses (IMO)** et **hors-gabarit**.

Chaque niveau de bloc distingue deux assises :
- **Base** : palettes posées au sol.
- **Gerbé** (stack) : palettes empilées au-dessus de la base.

---

## 2. Référentiel par classe de navire

Le référentiel `StowageZoneSpec` porte, pour chaque **(classe, zone)** :

| Champ | Rôle |
|---|---|
| `capacity_epal` | Capacité en **palettes EPAL-équivalentes** (étude Eupal). |
| `max_load_t` | **Charge admissible** de la zone (tonnes) — contrôle de surcharge. |
| `max_pallet_weight_kg` | **Poids max d'une palette** admis sur ce pont. |
| `stack_allowed` | Gerbage géométriquement possible. |
| `heavy_stack_allowed` | Gerbage des **palettes lourdes** autorisé. |
| `segregated` | Cale **ségréguée** (température & humidité contrôlées). |
| `notes` | Commentaire libre. |

> Le référentiel est **éditable** dans `/admin/stowage-specs` (par classe).
> Toute classe non saisie retombe sur le **référentiel théorique** fourni par
> `app/services/stowage_specs.py` (valeurs Phoenix par défaut). L'ouverture de
> la page admin matérialise (seed idempotent) le référentiel théorique en base
> pour permettre l'édition zone par zone.

### Capacités Phoenix (palettes EPAL-équivalentes — étude Eupal)

| Cale \ bloc | AR | MIL | AV | Total cale |
|---|---:|---:|---:|---:|
| `SUP_AR` | 68 | 40 | 70 | 178 |
| `MIL_AR` | 58 | 35 | 70 | 163 |
| `INF_AR` | 19 | 45 | 64 | 128 |
| `SUP_AV` | 63 | 41 | 66 | 170 |
| `MIL_AV` | 67 | 34 | 64 | 165 |
| `INF_AV` | 65 | 50 | 59 | 174 |

**Total navire : 978 palettes EPAL** (≈ 821 t en café Eupal).

### Résistance de pont (Phoenix)

| Pont | Poids palette max | Gerbage lourd | Lecture |
|---|---:|:---:|---|
| `SUP` (supérieur) | **1,2 t** | ❌ | Pont le plus léger : pas de palette US 1,4 t, pas de gerbage des palettes portuaires chargées de sacs 70 kg. |
| `MIL` (intermédiaire) | 1,4 t | ✅ | Palettes 1,4 t et gerbage lourd admis. |
| `INF` (inférieur) | 1,4 t | ✅ | Pont le plus résistant. |

Le plafond de charge par zone `max_load_t` est dérivé de la charge max observée
par cale dans le plan théorique, répartie sur les blocs au prorata de leur
capacité.

---

## 3. Formats de palette & conditionnements (« études »)

Le poids d'une palette dépend du conditionnement. Les coefficients
EPAL-équivalents (`PALETTE_COEFFICIENTS`, `app/models/commercial.py`) :

| Format | Coeff. EPAL-éq |
|---|---:|
| `EPAL` (Eupal) | 1,00 |
| `USPAL` | 1,20 |
| `PORTPAL` (portuaire) | 1,20 |
| `IBC` | 1,30 |
| `BIGBAG` | 1,25 |
| `BARRIQUE120` | 1,50 |
| `BARRIQUE140` | 2,00 |

Les **8 études** du plan théorique (Portuaire 60/70 kg, Eupal 60/70 kg, Big Bag
US 1,2 t / 1,4 t, et mix) sont des **scénarios de conditionnement homogène** du
navire. Elles servent de **référence de capacité** (la capacité retenue par
zone = étude Eupal) et documentent les limites de résistance. NEWTOWT ne fige
pas une étude par voyage : le plan est constitué lot par lot, et les contraintes
sont contrôlées en continu (cf. §5).

Tonnages totaux indicatifs par étude (synthèse plan) : Portuaire 60 kg ≈ 824 t ·
Portuaire 70 kg ≈ 716 t · Eupal ≈ 821 t · Big Bag 1,2 t ≈ 943 t · Mix Big Bag
1,2/1,4 t ≈ 1 021 t (capacité max).

---

## 4. Remontée de la packing list dans le plan

Quand on génère le plan (**« Suggérer auto »**), chaque lot (`PackingListBatch`)
est recopié dans une affectation `StowageItem`, **figeant la photo arrimage** :

| Packing list (`PackingListBatch`) | → | Plan d'arrimage (`StowageItem`) |
|---|---|---|
| `pallet_format`, `pallet_count` | → | idem |
| `weight_kg` | → | `weight_kg` (poids total du lot) |
| `description` | → | `description` |
| `hs_code`, `imdg_class`, `un_number` | → | classement (HS / IMDG / UN) |
| `length_cm`, `width_cm`, `height_cm`, `cubage_m3` | → | **dimensions & hauteur** |
| `stackable` | → | `stackable` |
| `hazardous` | → | `is_dangerous` |
| (dimensions > gabarit panier) | → | `is_oversized` |

Le panier de manutention standard (`BASKET`) : 380 × 150 cm, hauteur 2,2 m,
CMU 5,1 t. Au-delà → **hors-gabarit** (`is_oversized`) → zones `SUP_AV`.

---

## 5. Contrôles & politique d'avertissement

**Politique : avertissement seul.** Aucune affectation n'est jamais bloquée —
l'opérateur reste décisionnaire. Les non-conformités sont **signalées**
(panneau d'avertissements + pastilles sur le schéma et le tableau de zones).

`services.stowage.evaluate_plan(db, leg_id)` produit, par zone et au global :

| Contrôle | Déclencheur |
|---|---|
| **Surcharge zone** | `Σ poids lots > max_load_t` de la zone. |
| **Capacité dépassée** | `Σ palettes EPAL-éq > capacity_epal`. |
| **Résistance pont** | poids unitaire palette `> max_pallet_weight_kg`. |
| **Gerbage lourd interdit** | lot gerbé + palette lourde (≥ 900 kg) sur un pont sans `heavy_stack_allowed`. |
| **Lot non gerbable gerbé** | `is_stacked` alors que `stackable = false`. |
| **Hors zone dédiée** | IMO / hors-gabarit affecté hors `SUP_AV`. |

Niveaux : `warn` (surcharge, résistance, gerbage) · `info` (placement hors zone
recommandée). Le récap global affiche palettes, capacité EPAL-éq, tonnage chargé
et résistance totale du navire.

---

## 6. Repérage visuel de la cargaison à bord

Un **schéma navire** (coupe longitudinale, 3 ponts × 6 positions de longueur)
visualise l'occupation des 18 zones et **positionne** une marchandise (repère
📍). Composant réutilisable : macro `ship_map(...)` dans
`app/templates/staff/stowage/_ship_map.html`.

**Accès au repérage — depuis toute information qui positionne une cargaison :**

| Vue | Accès |
|---|---|
| Plan d'arrimage | `/stowage/legs/{leg_id}` — schéma complet + occupation. |
| Lot (packing list) | bouton **📍 Localiser** → `/stowage/locate/batch/{batch_id}`. |
| Commande commerciale | **📍 Localiser à bord** → `/stowage/locate/order/{order_id}`. |
| Claim cargo | lien **Plan d'arrimage →** depuis la position cale du claim. |
| Escale (dockers) | occupation par cale + lien plan d'arrimage. |
| **Espace client** | section **Position à bord** dans le détail du booking. |
| **Portail expéditeur** | section **Position de votre marchandise à bord** (`/p/{token}`). |

> **Confidentialité inter-clients.** Côté client et portail, seules les
> positions **du client** sont affichées (repère 📍, libellé de zone, nombre de
> palettes). L'occupation globale du navire, les tonnages et les avertissements
> ne sont **jamais** exposés en dehors du staff
> (`locate_for_packing_list` + `ship_map(None, …)`).

Couleurs du schéma (staff) : libre · < 70 % · 70–100 % · **surcharge** ;
marqueurs 📍 position recherchée, ⚠ alerte de zone, ❄ cale ségréguée.

---

## 7. Export PDF

`/stowage/legs/{leg_id}/plan.pdf` génère un **plan de chargement imprimable**
(WeasyPrint, template `pdf/stowage_plan.html`) : en-tête voyage/navire, schéma
des 18 zones coloré par occupation, liste des avertissements, table des lots
affectés (zone, lot, description, format, poids, classement, gerbage).

---

## 8. Consignes opérationnelles (remarques du plan)

1. L'espace à l'intérieur des **épontilles** est un espace de **manutention**
   (ne pas y compter de capacité de stockage).
2. Le transport de **sacs de 70 kg** repose en majorité sur la **bonne
   palettisation** : sacs ne dépassant pas de la palette, filmés et cerclés de
   façon **industrielle** (et non artisanale).
3. Pour le chargement de **palettes portuaires**, prévoir un lot de **15 à 20
   palettes sur Eupal** pour combler les trous lors du chargement.
4. Cales **ségréguées** : température & humidité contrôlées pour les denrées
   (café). Chargement autonome (grues + chariots électriques).

---

## 9. Référence technique

| Élément | Emplacement |
|---|---|
| Modèles | `app/models/stowage.py` (`StowagePlan`, `StowageItem`, `StowageZoneSpec`) ; `app/models/vessel.py` (`vessel_class`). |
| Référentiel | `app/services/stowage_specs.py` (valeurs Phoenix, `get_specs`, `ensure_specs`). |
| Logique | `app/services/stowage.py` (`suggest_assignments`, `evaluate_plan`, `locate_*`, `parse_zone`, `zone_label`). |
| Routes | `app/routers/stowage_router.py` (plan, suggest, approve, PDF, locate). |
| Admin | `app/routers/admin_router.py` (`/admin/stowage-specs`). |
| Vues | `app/templates/staff/stowage/` (`plan.html`, `locate.html`, `_ship_map.html`) ; `pdf/stowage_plan.html`. |
| Migration | `migrations/versions/20260615_0037_stowage_specs_capacity.py`. |

### Glossaire

- **EPAL-équivalent** : unité de capacité normalisée par `PALETTE_COEFFICIENTS`.
- **Gerbé / Base** : palette empilée (stack) / posée au sol.
- **Épontille** : pilier vertical structurel de la cale.
- **Ségrégué** : cale à atmosphère contrôlée (température / humidité).
- **Hors-gabarit** : lot dépassant le panier standard (380 × 150 × 220 cm, 5,1 t).
