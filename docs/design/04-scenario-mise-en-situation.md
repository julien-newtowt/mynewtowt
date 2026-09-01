# Mise en situation — scénario de recette de la reprise UX (Phases 1-3)

> **Date** : 2026-08-30 · **Branche** : `claude/mytowt-legacy-ux-recovery-79gjf0`
> Parcours guidé pour la mise en situation complète sur la version en
> cours. Chaque étape porte ses points de contrôle ✅. Prévoir ~45 min à
> deux (un pilote clavier, un observateur qui note).

## 0. Préparation

```bash
docker compose up -d
docker compose exec app python -m scripts.seed_demo   # jeu de données démo
```

Comptes utiles : un `operation` (cockpit escale, cargo, sinistres), un
`marins` (espace bord), un `manager_maritime` (clôture étape 3), un
compte client (ou création en direct au parcours 5). Choisir un leg
« vivant » : à quai ou proche de l'être, avec commandes affectées.

## 1. Agent d'escale — cockpit (`/escale`) · Phase 1

1. Filtre navire → année → leg : le leg choisi reste mémorisé (cookie).
   ✅ le bandeau leg affiche POL→POD, badges à quai/verrou.
2. Sous-navigation collante : cliquer chaque ancre (Statut → Commercial).
   ✅ la barre reste visible au scroll, les ancres ne passent pas sous la topbar.
3. Statut portuaire : poser l'ATA avec le sélecteur de fuseau (port local).
   ✅ toast de confirmation, la page NE se recharge PAS, le badge passe « à quai ».
4. Opérations : créer une opération EXPORT (formulaire replié « Nouvelle
   opération »), puis Démarrer / Terminer depuis la ligne.
   ✅ split Import / Export / Commun ; zéro rechargement ; création d'une
   opération `embarquement` avec un marin → l'affectation équipage apparaît,
   et au port FR une ligne PAF mise en évidence (badge réglementaire).
5. Laisser passer l'heure planifiée d'une opération non démarrée.
   ✅ badge « retard » sur la ligne + KPI « En retard » du bandeau.
6. Dockers : créer un shift, pointer les palettes réalisées dans la ligne.
   ✅ barre de progression et cadence (Δ %) se mettent à jour sans rechargement.
7. Équipage : cliquer un nom de marin. ✅ ouvre la fiche du module Équipage.
8. Verrouiller l'escale. ✅ formulaires masqués, alerte « lecture seule »,
   et (si clôture non engagée) l'alerte croisée inverse apparaît sur la
   carte Documents & SOF.

## 2. Journal & documents (`/escale/legs/{id}/journal`) · Phase 2

1. Carte « Documents & SOF » du cockpit : ✅ compteurs SOF / documents /
   PJ, **connaissements par état** (projets / à signer / signés),
   **sinistres du leg**, les deux PDF SOF côte à côte.
2. Bouton « Journal d'escale » : ✅ timeline groupée par journée mêlant
   bord et terre (ATA, SOF, opérations réelles, documents, BL, PJ,
   tickets, sinistres, clôture), filtres par type fonctionnels.
3. Rapprochement des SOF : saisir côté bord un SOF `NOR` sans opération
   correspondante côté terre. ✅ l'écart apparaît (« présent au bord,
   sans opération correspondante »), et disparaît une fois l'opération créée.
4. Tickets : depuis le cockpit, ouvrir le kanban filtré. ✅ bandeau
   « Filtré sur le leg — retirer », création préremplie sur le leg.

## 3. Commandant — espace bord (`/captain`, compte `marins`) · Phases 1-2

1. Header : ✅ liens « Escale (terre) », « Journal d'escale » présents ;
   « Sinistres du leg » ABSENT pour `marins` (permission non accordée —
   décision produit en attente), présent pour `operation`.
2. SOF : saisir, corriger un non-signé, signer. ✅ le signé devient
   immuable (l'édition renvoie 409) et apparaît « signé » au journal.
3. Documents cargo guidés : émettre un NOR, le signer. ✅ visible au
   journal et compté au cockpit.
4. Clôture : soumettre (bord) → valider (operations) → approuver
   (manager). ✅ stepper, checklist, PDF récapitulatif ; chaque étape
   horodatée au journal.
5. Connaissements `/captain/bl` : changer le leg dans le filtre.
   ✅ la liste se met à jour sans cliquer « Afficher » (Phase 3) ;
   « Fiche PL » ouvre la packing list ; signer un lot validé client.

## 4. Marchandise — packing lists (`/cargo/packing-lists`) · Phases 2-3

1. Index : ✅ colonnes Voyage / Navire, filtre par leg avec bandeau de retrait.
2. Fiche PL : ✅ bandeau de contexte voyage (liens cockpit / bord / BL /
   cale), sous-navigation collante, référence de commande cliquable.
3. Verrouiller/déverrouiller la PL, ajouter un lot (formulaire replié),
   envoyer un message au portail. ✅ zéro rechargement, toasts.
4. Workflow BL : générer le draft → valider (client ou pour-compte) →
   signer côté bord → réviser. ✅ le gel à la signature refuse l'édition
   (409), la révision produit `_R2`, le registre de remise distingue
   téléchargement / attestation / confirmation client.
5. Portail expéditeur `/p/{token}` : ✅ export Excel des lots saisis
   (nouveau), formulaire d'ajout replié, import upsert refusé si lot gelé.

## 5. Sinistres (`/claims`, compte `operation`) · Phase 3

1. Déclarer un sinistre cargo sur le leg. ✅ SOF `CLAIM_DECLARED` posé,
   visible au journal d'escale et compté au cockpit.
2. Fiche : ✅ badge de statut ENFIN coloré pour les 6 statuts ;
   formulaires assurance/provision/position repliés ; note et révision de
   provision sans rechargement (toasts) ; l'historique de provision trace
   montant/motif/auteur.
3. `/onboard/navigation` en compte `marins` : ✅ les boutons sinistres ne
   s'affichent plus (fini le 403 garanti).

## 6. Client (`/me`) · Phases 2-3

1. Wizard `/booking/new` en **invité** : 3 étapes, compte autocréé à la
   validation. ✅ l'écran de confirmation accueille dans l'espace client
   (transition expliquée, plus de bascule muette).
2. Dashboard : ✅ bloc « En mer actuellement » avec lien direct suivi ;
   titres de topbar corrects sur toutes les pages ; entrée « Facturation »
   dans la sidebar (page explicite hors plateforme).
3. Notifications : marquer lu. ✅ sans rechargement. Messagerie d'une
   réservation : poster. ✅ le message part (rechargement classique —
   le formulaire vit dans un partial partagé, HTMX à y étendre plus tard).
   Publier/dépublier la page de voyage : ✅ sans rechargement.
4. Connaissements client : valider un projet de BL, puis confirmer la
   réception des originaux. ✅ sans rechargement, états en clair.
5. Compte : changer le mot de passe (règle 12 caractères, vérification de
   l'actuel). ✅ plus aucun lien mort ; export RGPD / suppression =
   demande via contact (décision produit en attente).
6. `/devis` public en étant connecté : ✅ le texte ne promet plus la
   grille négociée et renvoie vers « Estimations tarifaires ».

## 7. Design system (transverse) · Phase 3

- ✅ Toggle de densité des tableaux dans la topbar staff (préférence par
  navigateur), focus visible au clavier sur onglets/boutons icône/nav
  (tabuler à travers le cockpit), pages 404/403 avec issue de secours,
  états vides homogènes (icône + titre + action).

## À noter pendant la mise en situation (connu, assumé)

- Une erreur métier sous HTMX (ex. escale verrouillée entre deux gestes)
  n'affiche pas encore de toast d'erreur — recharger la page.
- PJ multiples par document cargo : différé (migration nécessaire).
- Les 7 décisions produit du plan (§9.3/§10.4) restent ouvertes — la mise
  en situation est le bon moment pour les trancher : permission
  marins↔sinistres, 301 des anciens liens BL, BL self-service du portail
  commande, statut de règlement client, RGPD automatisé, machine à états
  claims, split Import/Export des lots cargo.
