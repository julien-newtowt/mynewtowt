# MyTOWT — Découverte du logiciel

> Document de découverte fonctionnelle, destiné en premier lieu à un usage
> personnel (notes de prise en main, pas nécessairement un livrable poli pour un
> tiers). Pour le vocabulaire maritime (leg, escale, BL, ETD/ETA…) et le détail
> technique, voir `CLAUDE.md` (racine du repo) et `PROJECT_CONTEXT.md`.

## Sommaire

1. [Comment fonctionne mynewtowt, en langage métier](#1-comment-fonctionne-mynewtowt-en-langage-métier)
2. [Interface — visite guidée par section de la sidebar](#2-interface--visite-guidée-par-section-de-la-sidebar)
3. [Rôles et processus par service (5W + 1H)](#3-rôles-et-processus-par-service-5w--1h)
4. [Portail client ↔ ERP](#4-portail-client--erp)
5. [API et intégrations — détail par donnée reçue](#5-api-et-intégrations--détail-par-donnée-reçue)
6. [Annexes](#6-annexes)

---

## 1. Comment fonctionne mynewtowt, en langage métier

### Les 4 publics de l'application

Le logiciel sert quatre audiences différentes derrière une seule plateforme :

1. **Les collaborateurs NEWTOWT** (9 rôles : administrateur, opération, armement,
   technique, data_analyst, marins, commercial, manager_maritime, RH) — c'est
   l'ERP interne, le cœur du système.
2. **Les clients** (chargeurs qui réservent de l'espace en cale) — un espace
   authentifié pour réserver, suivre, télécharger leurs documents.
3. **Le grand public / la presse** — le site vitrine (landing, catalogue de
   routes, page « preuves » CO2, blog).
4. **Les expéditeurs** (souvent un transitaire ou l'exportateur lui-même) — un
   accès sans compte, via un lien à token, pour remplir la liste de colisage et
   suivre l'expédition.

### Le fil rouge : la vie d'une expédition

Le plus simple pour comprendre comment les modules s'enchaînent est de suivre une
cargaison du premier contact à la livraison :

**1. Devis → Réservation (commercial / booking)**
Un client (ou un prospect) demande un devis, ou réserve directement en ligne en 3
étapes : choix de la route → description de la cargaison (avec déclaration
matières dangereuses si besoin) → récapitulatif + création de compte. Aucune carte
bancaire n'est demandée : NEWTOWT facture le fret uniquement par virement, l'équipe
commerciale confirme sous 4h. Une fois confirmée, la réservation devient une
commande suivie.

**2. Planification (planning)**
Chaque traversée est un « leg » (segment port A → port B). Le planning gère le
calendrier de la flotte en Gantt. Particularité importante : si une date de
départ/arrivée bouge, un moteur recalcule automatiquement en cascade toutes les
dates qui en dépendent (escale, chargement, etc.) et prévient les clients
concernés.

**3. Préparation de la cargaison (cargo)**
C'est le module qui produit les documents : Bill of Lading (BL), liste de
colisage, facture, avis d'arrivée. L'expéditeur externe peut aussi intervenir ici
via son lien à token (sans se connecter) pour compléter le détail des colis.

**4. Escale (escale)**
Quand le navire est à quai : suivi des opérations (import/export), des équipes de
dockers, des horaires réels d'arrivée/départ. Un point notable : une fois l'escale
« verrouillée » administrativement, il faut un niveau de droit plus élevé pour la
déverrouiller que pour la verrouiller — une sécurité volontaire contre les
modifications a posteriori non maîtrisées.

**5. À bord (captain / onboard)**
Le capitaine dispose d'un espace dédié (accessible en PWA, utilisable même avec
une connexion satellite intermittente) : suivi de l'escale, carnet de quart,
gestion de l'équipage, et surtout la saisie des événements de navigation (voir MRV
ci-dessous). Un module « vente à bord » existe aussi pour l'encaissement (espèces
ou carte via Stripe) de ventes aux marins/passagers.

**6. Reporting environnemental (MRV)**
C'est le module le plus abouti techniquement (Annexe 1). Le bord déclare des
événements (départ, arrivée, ancrage, relevé de midi) et des soutages
(avitaillement carburant) — tout le reste (consommations, émissions CO2) est
calculé automatiquement à partir de ces événements, jamais ressaisi. Chaque
événement suit un cycle : brouillon (modifiable uniquement par son auteur) →
finalisé (un moteur de règles vérifie la cohérence, bloque si erreur grave) →
validé (par le siège). Ces données alimentent ensuite les certificats CO2 remis
aux clients (label « Anemos »).

**7. Finance (finance)**
Suivi prévisionnel vs réel par traversée (revenus, coûts, marge), en partie
alimenté automatiquement par les données opérationnelles (réservations, coûts de
dockers).

**8. Équipage & RH (crew / rh)**
Gestion des affectations, conformité Schengen (90/180 jours pour les marins
étrangers), congés, et depuis peu génération automatique du trombinoscope mensuel.

**9. Réclamations (claims)**
Si un problème survient (cargo endommagé, sinistre équipage...), un dossier est
ouvert et suit 6 statuts (ouvert → en review → provisionné → réglé/rejeté →
clôturé), avec assurance et documents rattachés.

### Ce qui rend le tout cohérent

- Un seul système de droits (qui peut consulter/modifier/supprimer quoi)
  s'applique à tous ces modules, par rôle.
- Une seule piste d'audit trace toutes les actions importantes (qui a fait quoi,
  quand).
- Les documents générés (BL, factures, certificats CO2, rapports MRV) sont tous
  produits par le même moteur de mise en page, cohérent avec la charte graphique
  NEWTOWT.

---

## 2. Interface — visite guidée par section de la sidebar

### 2.1. Pilotage

**À quoi ça sert** : c'est le point d'entrée quotidien de n'importe quel
collaborateur — la vue d'ensemble de la flotte et de son calendrier.

- **Tableau de bord** : alertes actives, compteurs clés (bookings à confirmer,
  prochains départs, tickets urgents, taux de remplissage, CO2 évité), carte de
  position de la flotte.
- **Planning** : le calendrier annuel de la flotte en vue Gantt — un "leg" = un
  segment de voyage (ex. `4APTUS6` = navire n°4 "Atlas", 1er voyage de l'année,
  Portugal→USA). Glisser une barre décale automatiquement toutes les dates qui en
  dépendent en cascade (escale, chargement...).
- **Planif. provisoires** : un bac à sable "et si" — on peut simuler un scénario
  de planning sans jamais toucher aux vrais legs, pour tester une hypothèse avant
  de l'engager.
- **Partages publics** : génère un lien public (sans compte) pour partager une vue
  du planning à un tiers.
- **Veille d'actualité** : un fil d'actualité interne (agrégé automatiquement) sur
  le transport maritime, le transport à la voile, le Brésil et la réglementation —
  pour rester informé sans chercher soi-même.

### 2.2. Commercial

**À quoi ça sert** : tout ce qui concerne la vente d'espace de cale aux clients,
avant que la marchandise n'entre dans le circuit opérationnel.

- **Pilotage commercial** : vue d'ensemble — combien de clients actifs, combien de
  commandes ouvertes, taux de remplissage des prochains legs.
- **Clients** : la fiche client (chargeur ou transitaire).
- **Grilles tarifaires** : les grilles de prix négociées par route/client.
- **Devis** : demandes de devis entrantes, à transformer en réservation.
- **Offres** : propositions commerciales formalisées.
- **Commandes** : les commandes confirmées, suivies jusqu'à facturation.

> Point notable : sur base de démo, tout est à 0 (pas de données côté commercial)
> — c'est normal, c'est une base fraîchement initialisée, pas un signe de
> dysfonctionnement.

### 2.3. Cargo

**À quoi ça sert** : la gestion documentaire de la marchandise, une fois qu'une
réservation existe.

- **Bookings** : liste des réservations côté back-office (créées par un client en
  ligne, ou saisies directement par un commercial pour un client déjà identifié).
- **Documents (BL, PL, factures)** : génération du Bill of Lading (titre de
  propriété de la cargaison), de la packing list, de la facture, et du certificat
  CO2 "Anemos". Vérifié en direct : cet écran reste vide tant qu'aucun booking
  n'est confirmé — la saisie détaillée de la cargaison, elle, se fait ailleurs (à
  la réservation, ou via un lien envoyé à l'expéditeur, sans qu'il ait besoin de
  compte).

### 2.4. Opérations

**À quoi ça sert** : le pilotage du navire une fois qu'il est en escale ou en mer.

- **Escale (Import/Export)** : suivi d'un navire à quai — les opérations de
  déchargement/chargement, les équipes de dockers, les horaires réels
  d'arrivée/départ. Une fois l'escale clôturée administrativement, il faut un
  niveau de droit plus élevé pour la rouvrir que pour la fermer (sécurité
  volontaire).
- **Onboard, 4 espaces** : l'espace du commandant à bord, vu en direct — 4 blocs
  (Escale, Navigation/relevé de midi/journal de quart, Cargo & documents,
  Équipage/ISM-ISPS), plus la déclaration d'événements MRV mise en avant. Pensé
  pour fonctionner avec une connexion satellite intermittente.
- **Caisse de bord** : petite caisse du navire (EUR/USD/VND).
- **Vente à bord** : ventes aux marins/passagers (boissons, souvenirs...),
  encaissées en espèces ou par carte (Stripe) — le seul endroit où un paiement en
  ligne existe dans tout le logiciel.

*(Plus bas dans cette section, non détaillés ici : Tickets escale et Tracking flotte.)*

### 2.5. Ressources Humaines (équipage & RH)

**À quoi ça sert** : gérer les personnes, à bord et à terre.

- **Équipage** : affectations des marins par navire/rotation, conformité Schengen
  (un marin étranger ne peut pas dépasser 90 jours sur 180 dans l'espace Schengen
  — l'outil calcule ça automatiquement), et le trombinoscope mensuel.
- **RH** : le module RH plus large — écran de saisie de congés, avec liste des
  demandes en attente. Couvre aussi contrats, alertes d'échéance, bulletins de
  paie.
- **Mon espace RH** : self-service — accessible à tout collaborateur (pas
  seulement RH), pour ses propres congés/absences.

### 2.6. Performance

**À quoi ça sert** : le pilotage financier, environnemental et réglementaire — la
partie la plus riche de l'outil.

- **Finance** : revenu/coûts/marge par voyage, prévisionnel vs réel, gestion des
  ports (frais, contacts) et de l'OPEX.
- **KPI** : vue consolidée + rapport carbone par voyage.
- **Dashboard environnemental** : vue flotte avec CO2 émis, comparateurs vs
  porte-conteneurs/avion explicitement marqués "provisoire", 3 méthodes de calcul
  clairement séparées ("jamais mélangées dans un même chiffre"), état de
  complétude des données par navire.
- **Navigation** : suivi multi-legs avec carte, météo le long du trajet.
- **MRV (reporting carbone réglementaire)** : le module le plus abouti
  techniquement (Annexe 1) — Voyages, Soutages, FLGO (lecture seule Marad),
  Qualité des données, Datasets réglementaires OVDLA/OVDBR, Paramètres, et une
  archive gelée de l'ancien système, clairement étiquetée comme telle.
- **QHSE** : à l'état d'écran minimal ("Phase 0") — pour l'instant, juste un
  import de fichier Excel exporté depuis le "FMS" (système de gestion de flotte
  externe), qui alimente un compteur de rapports importés. Le texte à l'écran dit
  explicitement : "les tableaux de bord par rôle arrivent en Phase 1".
- **Claims** : sinistres (cargo, équipage, coque...), 6 statuts (ouvert → en revue
  → provisionné → réglé/rejeté → clôturé).
- **Analytics** : tableaux de bord agrégés multi-modules (exécutif, commercial,
  opérations) + un rétroplanning médias 2026-2027.

### 2.7. Admin

**À quoi ça sert** : la gouvernance de l'outil lui-même — réservé aux
administrateurs.

- Gestion des utilisateurs et de leurs droits (matrice rôles × modules).
- Référentiel navires (flotte, IMO, capacité), référentiel environnemental
  (cuves/moteurs par navire).
- Facteurs d'émission CO2/CH4/N2O versionnés (traçabilité réglementaire).
- Intégrations externes (Pipedrive/CRM, sécurité, exports/purges de données).
- Activation du chatbot interne ("Newtowt Agent").

### En dehors de l'ERP : les 3 autres visages du logiciel

- **Vitrine publique** (newtowt.eu, sans compte) : site marketing soigné —
  recherche de traversée, argumentaire "décarbonation prouvée", catalogue de
  traversées avec CO2 évité affiché par palette, verticales café/cacao.
- **Réservation client** : un vrai parcours en 3 étapes (Route → Cargaison →
  Récapitulatif), sans paiement en ligne — la confirmation finale et la
  facturation se font par l'équipe commerciale, par virement bancaire.
- **Espace client** (`/me`, avec compte) : tableau de bord perso — expéditions en
  cours, CO2 évité cumulé, réservations, documents, certificats Anemos.

---

## 3. Rôles et processus par service (5W + 1H)

Pour chaque service : un profil type représentatif, puis ses actions clés décrites
en **Quoi / Qui / Quand / Où / Pourquoi / Comment**.

### Organigramme fonctionnel (rappel)

- **Commercial** et **Opérations** sont deux filières gérées **séparément**, chacune
  autonome, sans supervision hiérarchique commune documentée dans l'outil.
- **Manager Maritime** est le rôle hiérarchique **au-dessus de Crew/RH et Technique**
  (voir §3.6) — mais cette supervision organisationnelle ne se traduit pas
  systématiquement par des droits identiques dans l'outil (ex. RH garde son
  autorité exclusive de décision sur les sujets individuels).

### 3.1 Opérations

**Profil type** : chargé(e) d'opérations portuaires et cargo — droits pleins
(consulter/modifier/verrouiller) sur Escale, Cargo, Claims et Tickets ; droits de
modification sur Planning, Booking, Crew et MRV ; lecture sur KPI, QHSE, RH.

| Action (Quoi) | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Ouvrir et organiser une escale | Opérations, avec des entrées croisées d'autres services (voir zoom ci-dessous) | À l'approche d'un port, jusqu'à la clôture de l'escale | Section "Escale" | Coordonner tout ce qui se passe à quai (accostage, dockers, formalités) sur un dossier unique par escale | Le dossier est créé pour un navire/port donné ; chaque intervenant y ajoute des opérations typées ; les opérations sont horodatées, la fermeture demande un niveau d'accès supérieur à l'ouverture (verrouiller est plus dur qu'ouvrir) |
| Traiter un incident cargo (claim) | Opérations (ouverture et suivi), Manager Maritime (arbitrage) | Dès qu'un incident cargo est signalé | Module "Claims" | Encadrer un litige (avarie, manquant, retard) selon un circuit à 6 étapes | Le dossier passe par les statuts ouvert → en examen → provisionné → réglé/rejeté → clos ; sa création génère automatiquement une notification au manager maritime et une entrée dans le journal de bord officiel du navire |
| Gérer les tickets d'escale | Opérations | En continu pendant l'escale | Module "Tickets" (vue kanban) | Suivre les demandes/incidents opérationnels avec un délai de traitement garanti | Chaque ticket a une priorité (P1/P2/P3) qui fixe automatiquement une échéance à sa création ; si l'échéance est dépassée, une escalade automatique se déclenche vers le manager |
| Ajuster un planning suite à un retard | Opérations | Dès qu'un retard ou un changement d'horaire est connu | Module "Planning" | Répercuter un aléa sur la suite du voyage sans casser la cohérence des dates | Déplacer un leg décale automatiquement tous les legs suivants du même navire non encore réalisés, résout les chevauchements, et notifie tous les clients concernés — mais ne touche jamais une escale déjà terminée |

**Zoom : qui écrit quoi dans le dossier d'escale.** Une escale n'est pas remplie par
un seul service — chaque opération saisie est typée, et le type détermine qui la
renseigne dans la pratique :
- **Technique** (soutage, avitaillement, inspection, montée/descente de passerelle) → service Technique
- **Armement** (embarquement, débarquement, fin/début de traversée, passage police aux frontières) → Armement
- **Relations extérieures** (pilote à bord/débarqué, relation presse) → Opérations/Commercial
- **Documentaire** (avis prêt à opérer) → Opérations

### 3.2 Commercial

**Profil type** : chargé(e) commercial(e) — droits pleins sur Commercial et
Booking ; modification sur Cargo ; lecture sur Escale, Captain, KPI, QHSE ; aucun
accès à Claims, Crew, MRV, Tickets.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Créer/négocier une offre | Commercial | Dès qu'une opportunité de fret est identifiée | Module "Commercial" | Transformer un contact en réservation ferme | Saisie du client, de la cargaison, du tarif ; un devis peut aussi être généré et envoyé par lien (PDF) sans que le client ait de compte |
| Confirmer un booking | Commercial | Une fois les conditions actées avec le client | Module "Commercial"/"Booking" | Déclencher la bascule du dossier vers la logistique physique | La confirmation appelle un point de passage unique qui envoie l'email/notification client et crée automatiquement le portail de suivi pour l'expéditeur |
| Relancer un devis non converti | Automatique (déclenché pour le compte du Commercial), avec reprise manuelle possible | Le lendemain (J+1) de l'émission d'un devis resté sans suite | Module "Devis" | Ne pas perdre une opportunité par simple oubli | Une tâche planifiée identifie les devis "en attente" au-delà d'un jour et relance ; la relance s'annule automatiquement si le devis est accepté avant |
| Suivre le remplissage des legs | Commercial | En continu, en amont du départ | Module "Commercial" (grilles de remplissage) | Piloter la vente d'espace en cale par voyage | Chaque leg affiche sa capacité vendue vs disponible (ex. palettes), pour prioriser la relance commerciale sur les legs sous-remplis |

### 3.3 Technique

**Profil type** : responsable technique flotte — droits pleins sur Escale ;
modification sur Captain, MRV, QHSE, Tickets ; lecture sur Planning, Commercial,
Cargo, Crew, Claims, RH. Rattaché hiérarchiquement au Manager Maritime (§3.6).

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Enregistrer une opération technique d'escale | Technique | Pendant l'escale (soutage, avitaillement, inspection, mouvements de passerelle) | Section "Escale" | Tracer les interventions techniques à quai, distinctement des mouvements commerciaux/armement | Sélection du type "technique" dans le dossier d'escale, qui limite les actions possibles à ce sous-ensemble (garde-fou de saisie) |
| Valider les entrées techniques du bord | Technique | Après une déclaration faite par l'équipage | Module "Captain"/"Onboard" | Vérifier la cohérence des données avant qu'elles ne remontent dans le circuit réglementaire | Le Technique peut modifier une déclaration encore en brouillon ; une fois finalisée par le bord, elle passe à un moteur de règles automatique qui bloque les incohérences majeures |
| Superviser la qualité des données environnementales | Technique | En continu + relecture après le contrôle nocturne automatique | Module "MRV" → "Qualité" | Garantir la fiabilité des rapports réglementaires d'émissions avant leur validation finale au siège | Un moteur de règles (une trentaine de contrôles) tourne chaque nuit et classe les anomalies par sévérité ; le Technique consulte et corrige en amont de la validation siège |
| Traiter les tickets techniques | Technique | Dès qu'un ticket est catégorisé "technique" | Module "Tickets" | Résoudre les pannes/anomalies matérielles remontées par le bord ou l'escale | Même mécanique de délai garanti (SLA) que pour les autres tickets, avec escalade automatique en cas de dépassement |

### 3.4 Crew / RH

Ce périmètre a trois volets distincts mais liés : **Crew** (composition/conformité
de l'équipage — Armement), **RH** (contrats, paie, congés — RH), et les **marins**
eux-mêmes (population concernée). Ils sont volontairement séparés dans l'outil :
un marin ne peut pas s'auto-approuver un congé — c'est toujours RH qui décide.

**Profil type 1 — RH** : droits pleins sur RH ; lecture sur Crew, Planning, QHSE, Tickets.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Instruire une demande de congé | RH | À la demande du marin (hors outil) ou en anticipation | Module "RH" → "Congés" | Centraliser la décision d'absence, RH étant seul juge | RH saisit ET valide dans le même geste (peut auto-approuver à la saisie) ; le marin consulte son solde en libre-service (`/rh/moi`) mais ne peut pas soumettre lui-même |
| Gérer un contrat / avenant | RH | À l'embauche, à un changement de poste/salaire | Module "RH" → "Dossier" | Garder une trace juridique à jour par marin/sédentaire | Création du dossier, alertes automatiques sur échéances de contrat |
| Produire l'export de paie | RH | Mensuel, avant clôture de la période | Module "RH" → export Silae | Transmettre les données au prestataire de paie | Export CSV verrouillé par période une fois généré, avec journal des lots pour tracer qui a exporté quoi et quand |

**Profil type 2 — Marin(s)** : lecture sur la majorité des modules ; modification
sur QHSE et Tickets uniquement.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Déclarer un événement à bord | Marins (capitaine/officier) | En navigation ou à quai, selon l'événement | Module "Captain"/"Onboard" | Alimenter le journal de bord et la chaîne réglementaire environnementale | Saisie en brouillon (modifiable par son seul auteur) → finalisation (contrôles automatiques bloquants) → validation |
| Signaler un ticket QHSE ou technique | Marins | Dès qu'un aléa est constaté à bord | Module "Tickets" ou "QHSE" | Faire remonter rapidement un sujet qualité/sécurité/environnement | Ouverture d'un ticket avec priorité, suivi par le service concerné jusqu'à clôture |
| Consulter son solde de congés/planning | Marins | À tout moment | `/rh/moi`, Module "Planning" | Autonomie de consultation sans droit d'action | Vue en lecture seule, aucune soumission possible depuis cet écran |

**Profil type 3 — Armement** : droits pleins sur Crew (rotations, conformité) ;
lecture sur le reste. Rattaché hiérarchiquement au Manager Maritime (§3.6).

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Composer/ajuster une rotation d'équipage | Armement | En amont d'un voyage, ou en remplacement d'urgence | Module "Crew" | Garantir qu'un navire a l'équipage requis, dans les règles | Affectation des marins à un leg (l'embarquement hors leg est autorisé si besoin), avec les compétences/certifications à jour |
| Suivre la conformité Schengen | Armement | En continu | Module "Crew" → conformité | Éviter qu'un marin étranger dépasse son quota de séjour (90 jours / 180) | Calcul automatique du solde de jours par marin à partir de l'historique d'embarquement |
| Injecter les données Marad dans les dossiers équipage | Automatique (pour le compte d'Armement) | Toutes les 30-60 min (cron) | Module "Crew" (résultat visible) | Garder les fiches équipage synchronisées avec le système externe de référence | Synchronisation en lecture seule, additive (une valeur vide côté Marad n'écrase jamais une valeur déjà saisie), champs sensibles jamais importés |

*Précision : Armement gère la composition/conformité de l'équipage (le "qui est
affecté où"), RH gère le contrat individuel et les congés (le "quel est le statut
administratif de cette personne"). Ce sont deux autorités volontairement séparées
sur des objets voisins.*

### 3.5 Admin / Analyst

**Profil type 1 — Administrateur** : droits pleins sur l'intégralité des modules,
y compris la configuration système.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Gérer les comptes et les droits d'accès | Administrateur | À l'arrivée/départ d'un collaborateur, ou pour ajuster un droit ponctuel | Module "Admin" → Utilisateurs / Permissions | Garder le contrôle d'accès à jour et limiter les habilitations au strict nécessaire | Création/désactivation de compte (jamais de suppression pure) ; des exceptions ponctuelles à la matrice de droits standard peuvent être posées cellule par cellule, avec bascule automatique vers les droits standards si un problème technique survient |
| Faire évoluer un facteur d'émission CO₂ | Administrateur | Quand une nouvelle version officielle du facteur est publiée | Module "Admin" → CO2/facteurs d'émission | Garder les calculs environnementaux alignés sur la réglementation en vigueur | Chaque facteur est versionné (jamais écrasé) — les rapports déjà émis gardent la trace du facteur utilisé au moment de leur calcul |
| Activer/désactiver une fonctionnalité | Administrateur | Lors d'un déploiement progressif ou d'un incident | Module "Admin" → Feature flags | Limiter le risque en activant une nouveauté progressivement, ou la désactiver rapidement en cas de souci | Bascule d'un interrupteur, sans déploiement technique, effective en quelques secondes |

**Profil type 2 — Data Analyst** : droits pleins sur Finance et Analytics ;
modification sur MRV ; lecture sur le reste.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Analyser la performance financière | Data Analyst | Mensuel / à la demande | Module "Finance" | Éclairer les décisions de pilotage économique | Comparaison prévisionnel/réel sur 5 postes de coûts, avec calcul des écarts |
| Fiabiliser des données environnementales à fort enjeu analytique | Data Analyst | En continu | Module "MRV" | Croiser expertise data et exigences réglementaires (rôle complémentaire du service Technique, angle "analyse" plutôt que "terrain") | Accès en modification uniquement sur MRV — jamais sur les autres modules opérationnels |
| Produire un reporting KPI transverse | Data Analyst | Périodique | Module "Analytics" | Fournir une vue consolidée à la direction | Extraction et mise en forme de données déjà calculées ailleurs dans l'outil (jamais de recalcul parallèle) |

### 3.6 Manager Maritime — supervision hiérarchique de Crew/RH et Technique

**Profil type** : le patron opérationnel des deux filières Crew/RH et Technique.
Dans l'outil, il dispose de droits pleins sur QHSE, Tickets et Captain (les sujets
où l'arbitrage doit pouvoir trancher vite), de droits de modification sur Crew,
MRV, Claims, Planning, Commercial, Cargo, Booking, et d'une simple lecture sur RH
et sur l'administration système.

| Action | Qui | Quand | Où | Pourquoi | Comment |
|---|---|---|---|---|---|
| Arbitrer une escalade transverse | Manager Maritime | Dès qu'un ticket ou un claim dépasse son délai de traitement, ou qu'un conflit inter-service survient | Module "Tickets" / "Claims" | C'est le rôle prévu pour trancher quand Opérations, Technique ou Armement ne s'accordent pas | Notification automatique à son rôle dès l'ouverture d'un claim ou le dépassement d'un SLA ; il tranche et l'action se répercute sur le service concerné |
| Valider une décision technique critique | Manager Maritime | Sur un sujet Captain/QHSE sensible | Module "Captain" / "QHSE" | Seul rôle non-admin à avoir un droit de verrouillage complet sur ces deux modules | Peut clore/verrouiller un dossier là où Technique ne peut que le modifier |
| Superviser (sans décider) les sujets RH | Manager Maritime | En continu, en lecture | Module "RH" | Rester informé de l'état des équipes qu'il chapeaute, sans reprendre la main sur des décisions RH qui doivent rester à l'autorité RH dédiée | Accès en consultation uniquement — **c'est volontaire** : même le supérieur hiérarchique ne peut pas agir directement sur un dossier RH individuel |

Le point notable : la hiérarchie **organisationnelle** (Manager Maritime au-dessus
de Crew/RH et Technique) ne se traduit pas par une hiérarchie **identique dans
l'outil** — RH garde son autorité exclusive de décision sur les sujets individuels
(contrat, congé, paie), même vis-à-vis de son propre supérieur. C'est cohérent avec
un principe déjà observé ailleurs dans l'app (ex. Bord ↔ Siège sur MRV) : séparer
qui décide de qui supervise.

---

## 4. Portail client ↔ ERP

Trois profils d'utilisateurs externes accèdent chacun à un périmètre distinct,
jamais superposé :

| Profil | Accès | Durée de vie |
|---|---|---|
| Visiteur anonyme | Son panier de réservation en cours, via un cookie de navigateur | 2 heures |
| Client avec compte | Ses propres réservations, factures, certificats CO₂ | Session de connexion |
| Expéditeur (lien dédié) | Une seule liste de colisage, messagerie et documents associés | 90 jours |

Le profil "expéditeur" est important à comprendre : c'est souvent une personne
différente du client qui a réservé et payé — celle qui prépare physiquement la
marchandise sur le terrain. Elle reçoit un lien unique, sans avoir besoin de créer
de compte.

**Le mécanisme central** : chaque changement de statut d'une réservation
(confirmée, chargée, en mer, débarquée, livrée) déclenche automatiquement un email
et une notification, et pour deux statuts précis, une action supplémentaire :
- **Confirmée** → le lien portail est généré et transmis à l'expéditeur.
- **Débarquée/livrée** → le certificat CO₂ (label "Anemos") est émis automatiquement.

**Deux points d'attention utiles sur le terrain** :
- Le lien d'un devis n'exige aucune vérification d'identité : quiconque le possède
  voit le prix et les coordonnées du prospect. C'est un choix délibéré pour
  faciliter le partage du devis, mais sans limite de temps ni de journalisation
  équivalente au portail expéditeur.
- Il existe deux fils de discussion distincts et non reliés entre eux : celui du
  client connecté sur sa réservation, et celui de l'expéditeur sur le portail. Si
  ce sont deux personnes différentes, chacune peut écrire sans que l'autre le voie.

---

## 5. API et intégrations — détail par donnée reçue

| Donnée reçue | Source réelle | Comment elle arrive techniquement | Fréquence | Usage métier |
|---|---|---|---|---|
| **Position des navires** | Terminal satellite de bord (Thalos / SATCOM) | Le boîtier satellite du navire génère un rapport quotidien ; **Power Automate** récupère ce rapport et le transmet à notre outil (fichier CSV ou Excel zippé) via un point d'entrée dédié | Quotidien | Suivi de flotte en temps quasi-réel, historique des trajets |
| **Météo** | **Windy** (fournisseur payant) | **Power Automate** déclenche un point d'entrée dédié dans notre outil, qui lui-même interroge l'API de Windy ; en complément permanent, l'API gratuite **Open-Meteo** est aussi interrogée pour les courants marins, les deux sources étant fusionnées | Toutes les 30 minutes | Cartes météo, conditions de navigation en direct |
| **Équipage (crew)** | **Marad / MaraSoft** (API dédiée) | **Power Automate** déclenche un point d'entrée dans notre outil, qui interroge directement l'API de Marad ; lecture strictement à sens unique — jamais de renvoi d'information vers Marad | Toutes les 30 à 60 minutes (limite technique côté Marad : 1 requête/minute) | Mise à jour des dossiers équipage (hors champs sensibles comme RIB/adresse, jamais importés) |
| **Carburant navire (FLGO)** | **Marad / MaraSoft** (même fournisseur, point d'entrée séparé) | **Power Automate** déclenche un second point d'entrée, dédié, distinct du flux équipage | Cron dédié | Alimentation en lecture seule du volet réglementaire carburant (MRV) |
| **Actualités sectorielles** | **NewsData.io** | **Power Automate** déclenche notre point d'entrée, qui interroge l'API NewsData.io ; un score de pertinence est ensuite calculé (méthode automatique simple, complétée par l'IA si disponible) | Périodique | Veille concurrentielle/marché affichée aux équipes |

**Autres intégrations existantes (hors flux automatisés ci-dessus)** :
- **Pipedrive** (CRM commercial) — synchronisation déclenchée manuellement par un utilisateur (bouton), pas de cron.
- **Claude / Anthropic** (assistant conversationnel "Newtowt Agent") — chaque question interroge nos propres données, jamais plus que ce que l'utilisateur connecté a le droit de voir.
- **SMTP** — envoi d'emails sortants uniquement (confirmations, alertes), sans file d'attente durable en cas d'échec ponctuel.
- **MapTiler / Mapbox** — fond de carte pour les écrans de suivi flotte et navigation.
- **Stripe** — paiement par carte, exclusivement pour la boutique à bord (vente à bord), jamais pour le fret.

---

## 6. Annexes

### Annexe 1 — Module MRV : pourquoi c'est le module le plus abouti

**1. C'est le plus gros et le plus récemment travaillé**
Le fichier qui gère MRV (`mrv_router.py`) fait à lui seul 1646 lignes — largement
le plus volumineux du projet. Et sur les 10 dernières migrations de base de
données avant la rédaction de ce document, 9 concernent directement MRV
(référentiels navire, facteurs d'émission, événements, soutage, rapports,
contrôle qualité...). Concrètement : c'est le module où l'équipe a mis le plus
d'efforts d'ingénierie ces deux derniers mois.

**2. Une règle d'or vérifiée automatiquement, pas juste "sur l'honneur"**
Dans la plupart des modules, une convention de code repose sur la discipline des
développeurs. Ici, il y a un vrai garde-fou : un seul fichier du projet a le droit
de multiplier une consommation de carburant par un facteur d'émission
(`emission_ledger.py`). Tous les autres modules qui affichent du CO2 (dashboard,
KPI, certificats) consultent ce résultat, ils ne recalculent jamais. Et ce n'est
pas qu'une règle écrite dans la doc : il existe un test automatique qui fait
échouer la construction du logiciel si un autre fichier tente ce calcul.
Autrement dit, l'incohérence est rendue impossible techniquement, pas seulement
déconseillée.

**3. Un cycle de vie des données réellement verrouillé**
Pour les réclamations (claims) par exemple, n'importe quel statut peut suivre
n'importe quel autre en pratique (même si la doc décrit un ordre logique) — rien
ne l'empêche dans le code. Pour MRV, c'est l'inverse : un événement est brouillon
(modifiable seulement par son auteur), puis finalisé (un moteur de règles vérifie
la cohérence et peut bloquer si une anomalie grave est détectée), puis validé par
le siège. Chaque transition est réellement contrôlée par le code, pas juste
documentée.

**4. Un moteur de règles paramétrable, pas des seuils codés en dur**
Il existe une trentaine de règles de contrôle qualité (cohérence des
consommations, des positions, etc.). Les seuils utilisés (ex. "consommation
anormalement élevée à partir de X tonnes/jour") ne sont jamais écrits en dur dans
le code — ils vivent en base de données, peuvent être ajustés par navire, et
surtout : chaque contrôle garde une trace du seuil qui a été utilisé au moment du
calcul, pour qu'un audit ultérieur puisse reproduire exactement le résultat même
si le seuil a changé depuis.

**5. Une bascule d'ancienne version proprement gérée**
Une ancienne façon de saisir les données MRV (formulaire manuel, export CSV à 18
colonnes) a été remplacée par cette nouvelle architecture. Plutôt que de laisser
l'ancien code traîner ou le supprimer brutalement, l'équipe l'a explicitement figé
en lecture seule ("archive"), avec un commentaire clair dans le code expliquant
que c'est voulu — signe d'une bascule pilotée, pas d'un abandon en cours de route.

**En résumé** : les autres modules (planning, escale, cargo...) sont solides mais
restent globalement des écrans de saisie/consultation classiques. MRV est le seul
endroit où on trouve : architecture événementielle, garde-fous automatiques
anti-erreur, machine à états réellement appliquée, et paramétrage réglementaire
traçable pour l'audit. Ce niveau de rigueur s'explique sans doute par l'enjeu :
c'est le module qui nourrit une obligation réglementaire européenne (le reporting
carbone MRV) et les certificats CO2 vendus aux clients — les erreurs y ont un coût
légal/commercial direct, contrairement à une réclamation interne mal étiquetée.
