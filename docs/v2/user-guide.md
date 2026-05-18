# Guide utilisateur — Kairos V2 (squelette)

> Ce guide sera développé en parallèle de l'implémentation V2. Le squelette ci-dessous fixe la structure cible et le périmètre de chaque section.

## Préface

- À qui s'adresse ce guide.
- Conventions typographiques.
- Comment trouver de l'aide (chatbot Kairos AI, ticketing).

## 1. Premiers pas

- Connexion (login + 2FA si activé).
- Tour de l'interface (sidebar regroupée, command palette, dark/light).
- Mon compte : préférences langue, fuseau horaire, mot de passe.
- Notifications : cloche, email, push.

## 2. Par rôle

### 2.1 Administrateur
- Gestion utilisateurs (création, rôles, désactivation).
- Reset password user.
- Maintenance mode (bascule, message).
- Exports / purges DB (avec audit).
- Feature flags (rollout V2 progressif).
- Rotation secrets, audit logs.

### 2.2 Opération
- Créer un leg (navire, ports, ETD/ETA, palettes).
- Modifier un leg + propagation cascade.
- Démarrer / clôturer une escale (ATA, ATD, opérations, dockers).
- Déclarer un ticket P1.

### 2.3 Armement / Équipage
- Constituer un rôle d'équipage (assignment).
- Embarquements / débarquements.
- Liste police aux frontières (export).

### 2.4 Commercial
- Créer un client.
- Saisir une commande.
- Affecter une commande à un leg.
- Suivre les paiements / les BL signés.
- Portail client (génération + envoi du lien).

### 2.5 Marins (lecture)
- Consulter les legs à venir.
- Consulter le manifest cargo.
- Consulter sa rotation.
- Ouvrir un ticket (médical, sécurité…).

### 2.6 Manager maritime
- Vue dashboard flotte.
- KPIs cross-navires.
- Validation tickets.
- Reporting SLA.

### 2.7 Data Analyst
- Exploration KPIs / Finance.
- Exports CSV/Excel.
- Analytics MRV (CO₂).

## 3. Modules

### 3.1 Dashboard
- Bento configurable, drag-to-reorder.
- Cards disponibles (cf. `../ux/mockups.md` §1).

### 3.2 Planning
- Vue Gantt multi-navires.
- Drag-and-drop ETD/ETA + cascade.
- Détection conflits port.
- Partage public via lien token.

### 3.3 Commercial
- Pipedrive sync.
- Grilles tarifaires.
- Offres, contrats, factures.

### 3.4 Cargo
- Commandes & affectations.
- Packing lists (création, validation, audit).
- Bill of Lading.
- Portail client (token).

### 3.5 Escale
- Vue Import / Export (split par direction).
- Docker shifts.
- Opérations parallèles (presse, douane, technique, armement).
- Tickets escale.

### 3.6 Onboard (4 espaces)
- Landing avec 4 tuiles (cf. `../captain/onboard-v2-spec.md`).
- Escale, Navigation, Cargo, Équipage.

### 3.7 Crew
- Membres d'équipage, rotations, contrats.
- Crew tickets (transports embarquement/débarquement).
- Documents (CIN, médicaux).

### 3.8 Finance
- LegFinance (revenue / costs / margin).
- Port configs.
- OPEX paramètres.
- Insurance contracts.

### 3.9 KPI
- Tonnage transporté.
- On-time performance.
- Utilisation navire.

### 3.10 MRV
- Émissions CO₂ par leg.
- Paramètres EU MRV.
- Reporting annuel.

### 3.11 Claims
- Déclaration sinistre (cargo, crew, hull).
- Documents (factures, expertises).
- Timeline + provision.
- Lien assureur.

### 3.12 Stowage
- Plan d'arrimage par leg.
- Zones de stockage navire.
- Visualisation 3D simplifiée.

## 4. Cas d'usage transverses

### 4.1 Décaler un ETD de 24 h
1. Ouvrir le leg.
2. Modifier ETD.
3. Cascade automatique vers legs aval (preview).
4. Confirmer → propagation aux escales, commandes, packing lists.
5. Notifications auto aux clients impactés.

### 4.2 Démarrer une escale
1. Vessel arrive → set ATA.
2. Création auto des opérations standard (NOR, pilotage).
3. Démarrer docker shifts.
4. Suivi temps réel palettes import / export.
5. Clôture : ATD + cargo documents complets.

### 4.3 Ouvrir un ticket P1
1. Bouton FAB depuis n'importe quelle page escale ou onboard.
2. Catégorie + titre + description + photos.
3. Auto-assignment selon catégorie.
4. Notifications instantanées au manager.

### 4.4 Utiliser Kairos AI
- Cmd+K pour ouvrir.
- Exemple : *"Quelle est la position d'Anemos ?"*, *"Combien de palettes pour CRG-AB12 ?"*.
- L'agent cite ses sources avec liens cliquables.

## 5. Référence

- Glossaire maritime ([`glossary.md`](glossary.md) — à créer).
- Codes leg / leg_code ([`glossary.md#leg-code`]).
- Permissions matrix.
- Webhooks et API ([`api-reference.md`](api-reference.md) — à créer).

## 6. Dépannage

- "Je ne vois pas le menu Passagers" → module désactivé post-liquidation, normal.
- "Mon mot de passe est expiré" → change-password screen au login.
- "Erreur 403 sur une page" → permission insuffisante pour ce rôle, contacter admin.
- "Le chatbot ne répond pas" → quota mensuel atteint, réessayer en début de mois.
