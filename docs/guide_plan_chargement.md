# Guide Plan de Chargement Navire

## Introduction

Le plan de chargement permet de positionner chaque batch de marchandise dans une zone precise du navire. Cette evolution remplace l'ancien systeme a 2 cales (avant/arriere) par un zonage fin en **18 zones**.

---

## Structure du navire

Le navire est divise en :
- **3 ponts** (niveaux) : INF (inferieur), MIL (intermediaire), SUP (superieur)
- **2 cales** par pont : AR (arriere), AV (avant)
- **3 blocs** par cale : AR (arriere), MIL (milieu), AV (avant)

### Nomenclature des zones

Chaque zone est identifiee par : `{PONT}_{CALE}_{BLOC}`

Exemple : `INF_AR_MIL` = Cale inferieure, partie arriere, bloc milieu

### Tableau des 18 zones

| Pont | Cale Arriere | Cale Avant |
|------|-------------|-----------|
| SUP (superieure) | SUP_AR_AR, SUP_AR_MIL, SUP_AR_AV | SUP_AV_AR, SUP_AV_MIL, SUP_AV_AV |
| MIL (intermediaire) | MIL_AR_AR, MIL_AR_MIL, MIL_AR_AV | MIL_AV_AR, MIL_AV_MIL, MIL_AV_AV |
| INF (inferieure) | INF_AR_AR, INF_AR_MIL, INF_AR_AV | INF_AV_AR, INF_AV_MIL, INF_AV_AV |

### Ordre de chargement

Le chargement se fait systematiquement :
1. **Arriere vers avant** (AR puis AV)
2. **Bas vers haut** (INF puis MIL puis SUP)

Ordre : INF_AR_AR (1) -> INF_AR_MIL (2) -> ... -> SUP_AV_AV (18)

### Contraintes speciales

Les zones **SUP_AV_AR, SUP_AV_MIL, SUP_AV_AV** sont reservees pour :
- Les **marchandises dangereuses** (classification IMO)
- Les **colis hors-format** depassant les dimensions du panier

### Dimensions du panier

- Surface libre : **380 x 150 cm**
- Hauteur : **2,2 m**
- CMU : **5,1 t**
- Poids vide : **2,2 t**

Tout colis depassant ces dimensions est automatiquement dirige vers les zones SUP_AV.

---

## Formats de palette

7 formats sont disponibles :

| Code | Description | Dimensions |
|------|------------|-----------|
| EPAL | Europalette | 120 x 80 cm |
| USPAL | US Pallet | 120 x 100 cm |
| PORTPAL | Palette Portuaire | 120 x 100 cm |
| IBC | IBC (+6cm) | 120 x 106 cm |
| BIGBAG | Big Bag Palettise | 120 x 103 cm |
| BARRIQUE120 | Barrique 120x120 | 123 x 123 cm |
| BARRIQUE140 | Barrique 140x140 | 143 x 143 cm |

---

## Qui fait quoi ?

### Equipe Operations / Escale

**Ou :** Module Escale > Plan de chargement

**Quoi :**
1. Ouvrir le plan de chargement du leg concerne
2. Voir les batches non assignes
3. Utiliser le bouton **Auto-assign** pour affecter automatiquement tous les batches selon les regles de chargement
4. Ou affecter manuellement chaque batch en selectionnant une zone
5. Imprimer le plan en **francais** ou **anglais** pour les operateurs portuaires

**Validation :**
- Le systeme **bloque** si la capacite ou le poids max d'une zone est depasse
- Les marchandises dangereuses ou hors-format sont automatiquement dirigees vers SUP_AV

**Document imprimable :**
Le document contient :
- Schema du navire avec code couleur de remplissage
- Tableau detaille : Zone, Batch, Client, Type cargo, Dangereux, Nb palettes, Type palettes, Stackable, Poids unitaire, Poids total, N BL

### Equipage / On Board

**Ou :** Module On Board > bouton "Chargement"

**Quoi :**
1. Visualiser l'etat du chargement en temps reel
2. **Deplacer un batch** d'une zone a l'autre par glisser-deposer (drag & drop)
3. Le deplacement est silencieux (mise a jour en base sans notification)

**Contraintes respectees :**
- Un batch dangereux ou hors-format ne peut pas sortir des zones SUP_AV
- La capacite et le poids max de la zone cible sont verifies

### Equipe Commerciale

**Ou :** Module Commercial > colonne Actions de chaque commande

**Quoi :**
- Un bouton "Plan de chargement" apparait a cote de chaque commande affectee a un leg
- Permet de consulter le plan de chargement complet du leg

### Chargeur / Client (Extranet)

**Ou :** Portail client > onglet "Position"

**Quoi :**
- Le client voit la position de chaque batch de sa marchandise dans le navire
- Un mini-schema du navire met en surbrillance la zone ou se trouve son lot
- L'information est disponible des que l'operateur a affecte le batch a une zone

### Claims / Sinistres

**Ou :** Module Claims (automatique)

**Quoi :**
- Lors de la creation d'un claim de type "Cargo", la position de la marchandise est **automatiquement recuperee** depuis le plan de chargement
- La zone est affichee dans le detail du claim avec un lien vers le plan complet
- Cette information facilite l'analyse des dommages et la determination des responsabilites

---

## Donnees techniques de reference

Les surfaces et resistances par zone sont issues du fichier technique :
`easy_chargement_navire_complet.xlsx` (racine du projet)

Les capacites en palettes par zone et par format sont precalculees et integrees dans le code :
`app/models/stowage.py` (constantes ZONE_DEFINITIONS et ZONE_CAPACITIES)

### Resistances des ponts

| Pont | Resistance |
|------|-----------|
| INF (inferieur) | 2,5 t/m2 |
| MIL (intermediaire) | 1,0 a 2,0 t/m2 selon zone |
| SUP (superieur) | 1,0 t/m2 |

---

## Deploiement

### Migration base de donnees

Executer le script SQL de migration :
```bash
docker exec towt-app-v2 python3 -c "
import asyncio
from app.database import engine
from sqlalchemy import text
async def migrate():
    async with engine.begin() as conn:
        with open('migration_stowage.sql') as f:
            sql = f.read()
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if stmt and not stmt.startswith('--'):
                await conn.execute(text(stmt))
asyncio.run(migrate())
"
```

### Redemarrage

```bash
docker restart towt-app-v2
```

### Verification

1. Acceder a `/escale` et selectionner un leg
2. Cliquer sur "Gerer le plan de chargement"
3. Verifier que les batches s'affichent
4. Tester l'auto-assign
5. Tester l'impression FR/EN
6. Tester le drag & drop depuis On Board
7. Verifier le portail client (onglet Position)
8. Creer un claim cargo et verifier que la zone s'affiche
