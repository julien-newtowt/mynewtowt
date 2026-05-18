# Mockups V2 — Kairos

Spec textuelle des écrans prioritaires. Les maquettes Figma seront livrées à part dans une branche dédiée. Ce document fournit les contraintes UX pour chaque écran clé.

## 1. Dashboard V2 — Bento

### Objectif
Vue synthétique 30 s — l'utilisateur sait où en est la flotte sans cliquer.

### Layout (desktop)
12 colonnes × hauteur variable. Cards drag-réordonnables, chaque user persiste son layout.

**Cards par défaut** :
- **HÉRO (col 8 × row 2)** : carte Mapbox/Leaflet centrée Atlantique avec les 4 navires animés (positions `vessel_positions` rafraîchies WebSocket).
- **Météo (col 4 × row 1)** : 4 mini-tuiles vent/houle au prochain port de chaque navire.
- **Prochains départs (col 4 × row 1)** : liste horizontale 7 jours, codes legs colorés.
- **KPIs flotte (col 4 × row 1)** : 4 chiffres (legs en cours, palettes embarquées, marge mois, on-time %).
- **Alertes (col 4 × row 1)** : tickets P1 ouverts + retards > 12 h.
- **Activité récente (col 4 × row 2)** : flux temps réel (logins, modifs leg, commentaires tickets).

### Mobile
Stack vertical, hero map en premier (60vh), cards en cascade, bottom-nav fixe.

## 2. Planning Gantt V2

### Objectif
Manipuler les ETD/ETA en drag-and-drop, voir les conflits inter-navires en un coup d'œil.

### Layout
- **Timeline horizontale** (X = jours, Y = navires).
- 4 lignes (Anemos, Artemis, Atlantis, Atlas).
- Bars colorées par status (cf. tokens `--status-*`).
- Drag bord gauche d'une bar → modifie ETD, drag bord droit → ETA, drag corps → translation.
- Modale de confirmation au drop : "Décaler 3 legs en aval ? [Aperçu]".
- Aperçu cascade : highlight des legs impactés pendant le drag.

### Conflits port
- Quand 2 navires sont planifiés au même port en chevauchement → halo rouge + tooltip.

### Vue alternative
- Toggle "Calendrier mensuel" (cellules jours × navires, max 1 escale par cellule).
- Toggle "Liste" (table classique pour saisie rapide).

## 3. Escale détail V2 — Split Import / Export

### Objectif
Distinguer visuellement les opérations d'import (débarquements arrivée) et d'export (embarquements départ).

### Layout
- Header : navire, port, ATA → ATD (timer live si vessel à quai).
- 2 colonnes égales :
  - **🔵 IMPORT** (couleur `--cargo-import` / lavande) : palettes débarquées (POD = port courant), docker shifts dédiés, signatures BL, photos déchargement.
  - **🟠 EXPORT** (couleur `--cargo-export` / ambre) : palettes embarquées (POL = port courant), docker shifts dédiés, mate's receipts, photos chargement.
- Footer commun : opérations parallèles (avitaillement, douane, presse, technique) — timeline horizontale en bas.

### Mobile
Tabs Import / Export / Commun.

## 4. Onboard V2 — 4 espaces

Voir [`../captain/onboard-v2-spec.md`](../captain/onboard-v2-spec.md) pour le détail métier.

### Landing `/onboard`
Hero 4 tuiles (one-tap entry) :
- 🟢 **Escale** (compteur ops en cours)
- 🌊 **Navigation** (vitesse + ETA en grand)
- 📦 **Cargo & Doc** (% closure checklist)
- 👥 **Équipage** (compteur à bord)

KPI strip en haut : ETA prochain port · distance restante · vent · alertes ouvertes.

### Sous-pages (tabs persistants)
- `/onboard/escale` — opérations import/export (réutilise mockup 3).
- `/onboard/navigation` — noon reports, journal de quart, météo.
- `/onboard/cargo` — manifest live, BL, mate's receipts.
- `/onboard/crew` — rotation, check-lists ISM/ISPS, visiteurs.

## 5. Command palette `Cmd+K`

### Layout
Modale centre écran, 600 px de large, fond `--bg-1`, blur backdrop.
- Input top : "Rechercher un leg, escale, client…"
- Liste résultats groupée :
  - **Legs** (3 max) — leg_code, navire, dates
  - **Escales** (3 max) — port, navire, dates
  - **Clients** (3 max) — nom, n° commandes
  - **Actions** : "Créer un leg", "Démarrer escale", "Ouvrir ticket P1"
- Navigation flèches haut/bas, Enter pour sélectionner.

## 6. Chatbot widget

### Layout
- Bouton flottant bottom-right, icon `message-circle` + halo `--accent`.
- Click → panel 380 × 600 (mobile : full-screen).
- Header : nom Kairos AI, status "en ligne", bouton fermer.
- Bulles utilisateur droite (`--bg-2`), bulles assistant gauche (`--bg-1` + border `--bg-3`).
- Input bottom + bouton send (icon `arrow-up` sur fond `--accent`).
- Citations : sous chaque réponse, chips "Voir leg 1AFRUS6" cliquables.

## 7. Ticketing escale — Kanban

### Layout
4 colonnes : Open · In Progress · Pending External · Resolved.
Cards :
- Header : badge priorité (P1 rouge, P2 ambre, P3 gris) + reference + temps écoulé.
- Body : titre + courte description.
- Footer : avatar assigné + compteur commentaires.
- Drag entre colonnes pour changer status.

Filter bar top : navire · port · catégorie · priorité · auteur.

## Livrables Figma (à produire)

| Écran | Variants |
|-------|----------|
| Dashboard | desktop + mobile + dark/light |
| Planning Gantt | desktop + drag-state + conflit |
| Escale split | desktop + mobile (tabs) |
| Onboard landing | mobile-first + desktop |
| Onboard navigation | noon report form |
| Command palette | overlay state |
| Chatbot | widget collapsed + expanded |
| Ticketing kanban | desktop + mobile (swipe colonnes) |

Style guide (couleurs, type, composants) à exporter en parallèle pour intégration directe dans `tokens.css`.
