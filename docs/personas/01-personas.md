# Personas et parcours utilisateur

> Étape 9 — `/architecture`. Personas simulés pour piloter l'évolution
> de l'architecture, de l'UX et de l'organisation des flux.

## 1. Personas internes

### 1.1 Capitaine de bord — *Mathilde, 38 ans*

**Rôle système** : `marins` + `captain` (lecture étendue).

**Contexte** : commande l'Anemos. À bord 60 % du temps. Sur tablette
durcie 10". Wi-Fi satellite intermittent.

**Objectifs** :
- Voir l'état complet du leg en 30 secondes en arrivant en passerelle.
- Saisir noon report, journal de quart, check-lists ISM/ISPS sans
  internet.
- Recevoir les ETA shifts portuaires en push.

**Frustrations actuelles (V2)** :
- Tout est éparpillé entre `/onboard`, `/escale`, `/cargo`.
- Pas de météo embarquée.
- Pas de journal de quart structuré.

**Apport V3** :
- **Onboard 4 espaces** (escale / navigation / cargo / crew).
- **PWA installable** avec offline-tolerant.
- **Mode haute lisibilité** (gros boutons, contraste AAA).
- **Météo Windy intégrée**.

**Parcours type** :
1. Ouvre la PWA installée sur tablette.
2. Landing `/onboard` → KPI strip (ETA, distance, vent, tickets).
3. Tap "Navigation" → noon report rapide.
4. Tap "Cargo" → vérifie manifest avant arrivée port.
5. Tap "Équipage" → coche check-list ISPS.

### 1.2 Agent d'escale — *Tomé, 29 ans*

**Rôle système** : `operation`.

**Contexte** : sur quai à Fécamp ou New York. Smartphone Android +
poste bureau. Gère 2-3 escales en parallèle.

**Objectifs** :
- Démarrer une escale dès l'ATA.
- Coordonner dockers, douane, presse, technique.
- Suivre les opérations import vs export.
- Ouvrir un ticket P1 si problème.

**Apport V3** :
- **Escale split Import/Export** clair visuellement.
- **Ticketing kanban** P1/P2/P3 avec SLA auto.
- **Mobile-first** sur le quai.
- **Notifications push** ETA shift navire.

**Parcours type** :
1. ATA notifié → ouvre `/escale/{leg_id}`.
2. Onglet Import → assigne docker shift.
3. Onglet Export → vérifie palettes chargées.
4. Onglet Commun → coche pilotage, douane.
5. Si avarie → bouton FAB ouvre ticket P1 (auto-assign manager).

### 1.3 Responsable RH — *Khadija, 45 ans*

**Rôle système** : `armement` + droits RH étendus (nouveau).

**Contexte** : siège Paris. Bureau, 2 écrans. Travaille avec Excel +
SIRH externe partiellement.

**Objectifs** :
- Visualiser le calendrier d'embarquement annuel.
- Suivre la compliance Schengen des marins étrangers.
- Gérer les congés, absences, paie variable.
- Préparer la liste police aux frontières.

**Apport V3** :
- **Module RH dédié** (`/rh`) : congés, contrats, compliance.
- **Dashboard RH** : effectif embarqué, rotation, alertes Schengen.
- **Export PAF** : PDF liste équipage avec MMSI + IMO.
- **Notifications** : pré-alerte expiration certificat marin.

**Parcours type** :
1. Connecte le matin → `/rh/dashboard`.
2. Voit 2 alertes Schengen → ouvre détail.
3. Crée un crew assignment pour la rotation du leg 1F.
4. Génère et envoie PAF en PDF.
5. Saisit congés validés du mois.

### 1.4 Superintendant — *Pierre, 52 ans*

**Rôle système** : `technique`.

**Contexte** : voyage entre les ports d'attache et les chantiers
techniques. Bureau Marseille.

**Objectifs** :
- Planifier la maintenance entre 2 legs.
- Tenir le registre des certifications navire (SOLAS, ISM).
- Lever des tickets techniques (réparation, pièce détachée).
- Suivre les avaries cargo (claims hull).

**Apport V3** :
- **Module Technique** : registre certifs + jauges expiration.
- **Ticketing technique** lié aux escales.
- **Claims hull** avec lien navire concerné.

**Parcours type** :
1. Connexion → `/admin/vessels` → état flotte.
2. Filtre par "certifs expirant dans 90 j" → priorise.
3. Crée ticket technique sur Anemos pour la prochaine escale.
4. Suit le ticket → ajoute photos, valide résolution.

### 1.5 Commercial — *Inès, 31 ans*

**Rôle système** : `commercial`.

**Contexte** : Paris + déplacements clients. Linkedin + Pipedrive +
téléphone constamment.

**Objectifs** :
- Vendre la capacité disponible des prochains legs.
- Gérer pipeline Pipedrive synchronisé.
- Émettre cotations et offres tarifaires.
- Voir et confirmer les bookings auto-générés par le portail client.

**Apport V3** :
- **Backoffice booking** : voir les `submitted`, confirmer, refuser.
- **Dashboard commercial** : funnel, top routes, LTV par segment.
- **Pricing dynamique** : suggestions de prix par leg en fonction de
  l'occupation et de l'historique.
- **Cotation rapide** depuis Pipedrive.

**Parcours type** :
1. Mail booking submitted → clique lien `/booking/42` interne.
2. Vérifie capacité + IMDG + segment client.
3. Click "Confirmer" → email auto au client, facture générée.
4. Vue dashboard → 12 bookings à confirmer aujourd'hui.
5. Crée une cotation pour un prospect (`/commercial/quotes/new`).

### 1.6 Prospect — *David, 42 ans*

**Profil** : importateur vin chilien, n'a jamais utilisé NEWTOWT.

**Contexte** : a vu une pub Linkedin, arrive sur `my.newtowt.eu`.

**Objectifs** :
- Comprendre l'offre et les routes disponibles.
- Évaluer si c'est moins cher ou plus cher qu'un container.
- Simuler une expédition (40 palettes vin du Chili → France).
- Échanger éventuellement avec un commercial.

**Apport V3** :
- **Landing publique** claire et engageante.
- **Recherche de routes** sans inscription.
- **Cotation invité** (sans compte) avec captcha.
- **Chatbot Kairos AI** pour répondre aux questions courantes.
- **Calculateur CO₂** comparatif.

**Parcours type** :
1. Atterrit sur `/`.
2. Recherche "FR → US" → voit 8 legs.
3. Clic sur 1A → page détail + capacité restante.
4. Bouton "Réserver" → wizard étape 1.
5. À étape 3, on lui demande de créer un compte → conversion.

### 1.7 Client occasionnel — *Léa, brasserie artisanale*

**Profil** : exporte 4-6 fois par an des bières en US.

**Contexte** : a un compte client depuis 1 an, 5 bookings réalisés.

**Objectifs** :
- Réserver rapidement (elle connaît la maison).
- Récupérer ses BL et factures.
- Avoir son certificat CO₂ pour son rapport RSE.

**Apport V3** :
- **Dashboard client `/me`** synthétique.
- **Nouvelle résa** en 90 secondes (route + cargo).
- **Téléchargement** BL/facture/CO₂ en 1 clic.

### 1.8 Client B2B grand compte — *Yann, chef logistique*

**Profil** : grand vigneron français, 100 palettes/an vers US et BR.

**Contexte** : a une grille tarifaire négociée. Veut s'intégrer en API.

**Objectifs** :
- Réserver via son ERP via API.
- Recevoir webhooks de statut.
- Avoir reporting annuel CO₂ pour son bilan.

**Apport V3** :
- **API REST v1** documentée OpenAPI.
- **Webhooks** signés HMAC pour status changes.
- **Reporting** mensuel automatique CSV + PDF.
- **SLA** dédié + escalade contact commercial.

## 2. Synthèse — Évolutions d'architecture nécessaires

### 2.1 Découpage routeurs

```
app/routers/
├── public/                      # NOUVEAU - sans auth
│   ├── landing_router.py
│   ├── routes_search_router.py
│   ├── about_router.py
│   └── api_v1_router.py
├── client/                      # NOUVEAU - auth client
│   ├── client_auth_router.py
│   ├── client_dashboard_router.py
│   ├── booking_router.py
│   ├── client_invoices_router.py
│   ├── client_co2_router.py
│   └── client_tracking_router.py
├── staff/                       # auth staff
│   ├── auth_router.py
│   ├── admin_router.py          # subdivisé
│   ├── dashboard_router.py
│   ├── planning_router.py
│   ├── commercial_router.py
│   ├── cargo_router.py
│   ├── escale_router.py
│   ├── onboard/                 # NOUVEAU - 4 espaces
│   │   ├── landing.py
│   │   ├── escale.py
│   │   ├── navigation.py
│   │   ├── cargo.py
│   │   └── crew.py
│   ├── crew_router.py
│   ├── rh_router.py             # NOUVEAU
│   ├── finance_router.py
│   ├── kpi_router.py
│   ├── mrv_router.py
│   ├── claims_router.py
│   ├── tickets_router.py        # NOUVEAU
│   ├── booking_backoffice_router.py  # NOUVEAU
│   ├── analytics_router.py
│   └── chat_router.py           # NOUVEAU
└── webhooks/
    ├── stripe_webhook_router.py # NOUVEAU
    └── pipedrive_webhook_router.py
```

### 2.2 Découpage services

```
app/services/
├── auth.py
├── permissions.py
├── booking.py                   # NOUVEAU
├── pricing.py                   # NOUVEAU
├── capacity.py                  # NOUVEAU
├── invoicing.py                 # NOUVEAU
├── co2_certificate.py           # NOUVEAU
├── stowage_optimizer.py
├── date_propagation.py
├── escale_direction.py
├── notifications.py
├── feature_flags.py
├── chatbot.py                   # NOUVEAU
├── ticketing.py                 # NOUVEAU
├── analytics.py                 # NOUVEAU
├── stripe.py                    # NOUVEAU
├── anthropic.py                 # NOUVEAU
├── pipedrive.py
└── windy.py                     # NOUVEAU
```

### 2.3 Flux UX différenciés

Layout dynamique selon contexte :

- **Public** (`/`, `/routes`, `/about`) → `templates/public/base.html`
  (header marketing, footer, design clair).
- **Client connecté** (`/me/*`) → `templates/client/base.html`
  (sidebar dédiée, navigation simplifiée 5 items).
- **Staff** (`/dashboard`, `/planning`, ...) → `templates/staff/base.html`
  (sidebar Kairos complète, 12 modules).
- **PWA pont** (`/onboard/*`) → `templates/onboard/base.html`
  (touch-first, mode haute lisibilité, offline).

### 2.4 Permissions multi-tenant

Évolution de `require_permission` :

```python
@require_permission_or("booking", "C", scope=BookingScope.OWN)
async def view_booking(ref: str, current: ClientAccount = ...):
    """
    Si l'user est client : on vérifie qu'il est propriétaire du booking.
    Si l'user est staff : on applique la matrice rôle classique.
    """
```

Helper `is_booking_owner(client, booking) -> bool` dans
`app/services/permissions_helpers.py`.

## 3. Use cases simulés (golden paths)

### UC-01 Prospect réserve sa première palette

**Acteurs** : Prospect → Commercial.

```
P  ──visite──> /
P  ──recherche──> /routes?from=FR&to=US
P  ──sélectionne──> /routes/1AFRUS6
P  ──clic──> /booking/new/step-1-route
P  ──saisie 12 EPAL vin──> /booking/new/step-2-cargo
P  ──crée compte──> /booking/new/step-3-account
P  ──valide──> /booking/new/step-4-confirm
APP ──email──> P + email équipe ops
C  ──reçoit notif──> /booking/42
C  ──confirme──> APP émet facture + BL draft
P  ──email confirmation──> /me/bookings/BK-2026-0042
```

KPI : durée totale parcours prospect < 8 minutes.

### UC-02 Client récurrent réserve via portail

```
L  ──connexion──> /me
L  ──clic nouvelle résa──> /booking/new
L  ──étape 1 + 2──> /booking/new/step-3-confirm
L  ──étape 3 paiement Stripe──> webhook OK
APP ──auto-confirme──> BL généré + facture payée
```

KPI : durée < 2 minutes (segment recurring).

### UC-03 Décaler un ETD de 24h

```
OPS ──édite──> /planning/legs/12/edit (ETD +24h)
APP ──cascade──> recalcule eta_eta des legs aval
APP ──cascade──> recalcule escale ops planned dates
APP ──cascade──> recalcule order delivery dates
APP ──cascade──> recalcule booking ETA + notifie clients
APP ──email──> tous clients impactés (X bookings)
APP ──ticket──> P2 auto si > 12 h shift
DASH ──variance──> alertes affichées sur dashboard exec
```

### UC-04 Lever un ticket P1 médical

```
M  ──pont──> bouton FAB
M  ──formulaire──> catégorie=medical, priorité=P1
APP ──auto-assign──> manager_maritime
APP ──notif push + email──> manager
APP ──sla_target_at──> created_at + 2h
MGR ──ouvre──> /tickets/T-2026-0042
MGR ──coordonne──> commentaire externe + appel SAMU
MGR ──résolve──> resolved
M  ──valide──> closed
```

### UC-05 Générer son rapport CO₂ annuel

```
L  ──connecté──> /me/co2
L  ──filtre 2026──> tous certifs annuels
L  ──télécharge PDF rapport──> WeasyPrint render serveur
L  ──intègre dans son bilan RSE──> (externe)
```

### UC-06 Manager flotte consulte le dashboard

```
MGR ──ouvre──> /dashboard/exec
DASH ──affiche──> KPIs + carte + variance vs N-1
MGR ──filtre période──> Q1 2026
MGR ──drill-down──> top 5 alertes ouvertes
MGR ──exporte──> PDF rapport mensuel CODIR
```

### UC-07 Commandant saisit son noon report offline

```
C  ──en mer offline──> /onboard/navigation
C  ──formulaire──> noon report (position, vitesse, vent, fuel)
PWA ──stocke local──> IndexedDB
C  ──retour wifi──> sync auto
APP ──persiste──> noon_reports table
APP ──alerte si écart fuel anomal──> ticket auto
```

### UC-08 Agent escale gère import/export

```
O  ──ATA enregistré──> /escale/{leg}/index
O  ──onglet Import──> assigne docker shift
O  ──saisit palettes débarquées──> validate
O  ──onglet Export──> photo chargement + mate's receipt
O  ──vérifie tous status──> ATD ready
O  ──set ATD──> closure escale + auto-cascade
```

## 4. Évolution UX par interaction

### 4.1 Sidebar dynamique

La sidebar staff réorganisée par groupes :

```
🌊 Pilotage
  · Dashboard
  · Planning
  · Tracking

📦 Cargo
  · Commercial
  · Bookings (NEW)
  · Commandes
  · Packing lists
  · BL
  · Stowage

⚓ Opérations
  · Escale
  · Onboard
  · Tickets (NEW)
  · Crew

👥 RH (NEW)
  · Membres équipage
  · Congés
  · Compliance

💶 Performance
  · Finance
  · KPI
  · MRV
  · Claims
  · Analytics (NEW)

⚙️ Admin
  · Utilisateurs
  · Référentiels
  · Feature flags (NEW)
  · Activity logs
```

Personnalisation : chaque user peut épingler 3 raccourcis.

### 4.2 Command palette Cmd+K

Recherche globale unifiée : legs, escales, bookings, clients, users,
ports, navires, docs, tickets, actions ("créer leg", "ouvrir ticket P1").

### 4.3 Notifications

Cloche en haut à droite :

- Badge count tickets P1 ouverts.
- Liste des 10 dernières notifications.
- Mark all as read.
- Préférences (push, email, slack) par type d'événement dans
  `/account/notifications`.

### 4.4 Onboarding nouveau user

Au premier login :

1. Tour guidé 5 étapes (sidebar, command palette, dashboard, chatbot,
   help).
2. Tutoriels vidéo embarqués (max 90 s chacun).
3. Glossaire maritime accessible via `?` ou Cmd+K → "glossaire".

## 5. Métrique d'adoption par persona

| Persona | Métrique clé | Cible 6 mois |
|---------|--------------|--------------|
| Capitaine | % noon reports saisis | > 95 % |
| Agent escale | % escales avec tickets résolus < SLA | > 90 % |
| RH | % alertes Schengen traitées avant échéance | 100 % |
| Superintendant | % certifs renouvelés avant expiration | 100 % |
| Commercial | Taux conversion booking submitted → confirmed | > 80 % |
| Prospect | Taux conversion landing → booking confirmé | > 5 % |
| Client occasionnel | NPS | > 40 |
| Client B2B grand compte | Adoption API | > 60 % des bookings |
