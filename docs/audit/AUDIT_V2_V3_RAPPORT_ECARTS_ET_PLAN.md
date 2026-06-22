# Audit de continuité fonctionnelle V2 → V3 — Écarts, régressions et plan de reprise

**Projet :** NEWTOWT « mytowt » / `mynewtowt`
**Objet :** comparaison module‑par‑module, UX‑par‑UX, design‑par‑design entre l'ancienne
version (V2, archive `mytowt-main`, mai 2025) et la version actuelle (V3, ce dépôt).
**Principe directeur imposé :** « il faut à minima reprendre l'existant de l'application
précédente » et « reprendre le comportement du modèle pour la partie gérée par les
personnels (staff) ».
**Méthode :** 13 audits approfondis (1 agent par domaine), lecture intégrale des routers,
modèles, services et templates des deux versions, avec mise en situation par persona
opérateur.
**Date :** 2026‑06‑22. **Statut :** rapport d'analyse — aucune ligne de code livrée (proposition d'évolution).

---

## 0. Résumé exécutif

### 0.1 Le constat en une page

La V2 était un **ERP staff pur** : 18 routers, 10 modules métier (planning, commercial,
escale, finance, kpi, captain/onboard, crew, cargo, claims, mrv), aucune plateforme client
ni publique. La V3 est une **plateforme unifiée** beaucoup plus large : elle **ajoute** la
plateforme client (MFA, dashboard, factures, label Anemos), le site vitrine/SEO/blog, le
booking, la caisse de bord, le chatbot Kairos, les tickets, le SIRH, la veille, la
navigation/météo, etc. Sur ces périmètres **nouveaux**, la V3 est un gain franc.

**Mais** la migration V2 → V3 s'est accompagnée d'une **régression systématique du cœur
métier staff**. Presque chaque module historique a perdu des capacités opérationnelles —
le plus souvent : la **correction/suppression** des données saisies, les **exports/PDF
réglementaires**, et des **écrans « cockpit »** denses remplacés par des CRUD minimalistes.
Le LOC des routers le laissait pressentir (cargo 1751→829, MRV 969→237, KPI 962→268,
escale 1030→529) ; l'audit le **confirme et le précise**.

> **Verdict global :** le principe « reprendre à minima l'existant » **n'est pas respecté**
> sur 10 des 12 domaines staff. La V3 est techniquement supérieure (architecture en
> services, SQLAlchemy 2 `Mapped[]`, charte Kairos/CSP‑strict, MFA, signatures IMO, MRV
> auto) mais **fonctionnellement en retrait** pour les opérateurs sur le quotidien.

### 0.2 Carte de chaleur des régressions (partie staff)

| Domaine | Régression | Ruptures bloquantes (P0) | Nature dominante |
|---|---|---|---|
| **Cargo / Packing / Portail** | 🔴🔴🔴 Critique | 18/36 routes disparues, BL déconnecté de la packing list, ~40 champs perdus, 5 écrans portail perdus | Amputation |
| **Onboard / Captain / Claims** | 🔴🔴 Critique | Édition/suppr SOF, docs cargo structurés, pièces jointes leg / docs agent d'escale | Éclatement + amputation |
| **Escale** | 🔴🔴 Critique | Édition/suppr opérations & shifts, pilotage ATA/ATD & statut portuaire, saisie heures réelles | Amputation cockpit |
| **Commercial / Pricing** | 🔴🔴 Critique | Affectation commande→leg, champs commande (format/poids/frais/route/lien grille), édition client, PJ | Repivot incomplet |
| **MRV** | 🔴🔴 Critique (réglementaire) | Export DNV 18 colonnes, Carbon Report PDF + blocage qualité, edit/delete event | Changement de paradigme |
| **Crew** | 🔴🔴 Critique (réglementaire) | Édition fiche marin, export PDF « Crew List » PAF, saisie visa/seaman book, édition/suppr affectation | Amputation CRUD |
| **Admin / Auth / Dashboard** | 🔴 Élevé | CRUD navires, moteur d'alertes du dashboard | Outillage admin amputé |
| **Stowage** | 🔴 Élevé | Vue à bord, drag‑drop réaffectation, édition/suppr item, liste non‑assignés | Régression d'interaction |
| **Finance / KPI** | 🔴 Élevé | Prévisionnel/réalisé, export CSV finance, NOx/SOx | Modèle budgétaire perdu |
| **Planning** | 🔴 Élevé | Brochure commerciale PDF, saisie ATD/ATA + statut | Sorties commerciales perdues |
| **Tracking / Navigation** | 🟠 Majeur | Intégrité (unique/index positions), filtre anti‑saut, 4 endpoints GET supprimés | Contrat API + intégrité |
| **Design / UX / i18n** | 🟠 Majeur | Timezone forms morts, i18n vietnamien effondré, sidebar non filtrée par droits | Câblage perdu |

### 0.3 Les 12 ruptures les plus urgentes (vue direction)

1. **Cargo :** la packing list saisie par l'expéditeur **n'alimente plus le Bill of Lading**
   (deux modèles disjoints). Émission documentaire cassée de bout en bout.
2. **Crew :** plus d'**édition de fiche marin** ni d'**export PDF « Crew List » pour la PAF**
   (obligation réglementaire à l'escale).
3. **MRV :** export **DNV Veracity** réduit de 18 → 9 colonnes (+ colonne IMO vide par bug),
   **Carbon Report PDF** disparu, **blocage qualité** supprimé → livrable réglementaire non conforme.
4. **Escale :** l'agent ne peut plus **poser ATA/ATD / faire progresser le statut portuaire**,
   ni **corriger/supprimer** une opération ou un shift.
5. **Onboard :** le capitaine ne peut plus **corriger un SOF**, ni **remplir un document
   cargo guidé** (NOR/LOP/HOLDS), ni **déposer les documents de l'agent d'escale** (BL signés, lettres de protestation).
6. **Commercial :** plus d'**écran d'affectation commande→leg**, et la commande a perdu
   format/poids/frais/route/PJ et le **lien vers la grille tarifaire**.
7. **Finance :** suppression du **suivi prévisionnel / réalisé** (cœur du contrôle de gestion) et de l'**export CSV**.
8. **Admin :** plus de **CRUD navires** (création d'une unité de flotte impossible hors seed).
9. **Dashboard :** disparition du **moteur d'alertes** (retards, ETA dépassées, conflits de
   port, départs imminents, escales non verrouillées, commandes non affectées).
10. **Stowage :** plus de **vue à bord** ni de **drag‑drop** de réaffectation des palettes.
11. **Sécurité :** **rate‑limiting du login mot de passe** supprimé ; **rate‑limit du portail
    token** non rebranché ; **sidebar** affichée intégralement quel que soit le rôle.
12. **i18n :** catalogue **vietnamien** réduit à 15 clés (≈ 442 en V2) ; **fuseaux horaires**
    des formulaires (UTC/Paris/Port local) non câblés.

### 0.4 Ce qu'il faut absolument préserver de la V3

Architecture en services testables ; SQLAlchemy 2 `Mapped[]` + Decimal ; charte « Nouvelle
Étoile » via tokens W3C + Kairos + CSP‑strict ; **MFA staff & client**, alertes device,
**éditeur de matrice de permissions** (overrides DB) ; **signatures/lock IMO** (SOF, noon,
watch) ; **noon report officiel + PWA offline** ; **MRV auto** (noon/SOF) ; **Carbon Report
par leg** (intensités t·nm) ; **label Anemos** (certificat par booking + RSE annuel) ;
**cascade de dates** élargie ; **scénarios de planning** what‑if ; **navigation + météo
historisée** ; **référentiel d'arrimage éditable** ; **rollup finance** ; modules nouveaux
(booking, cashbox, tickets, SIRH, chat, veille).

---

## 1. Méthodologie et périmètre

- **Sources comparées :** V2 = `/tmp/oldver/mytowt-main` (archive fournie) ; V3 = ce dépôt `mynewtowt`.
- **13 audits** : Planning · Commercial/Pricing · Cargo/Packing/Portail · Escale ·
  Onboard/Captain/Claims · Crew · Stowage · MRV · Finance/KPI · Tracking/Navigation ·
  Admin/Auth/Dashboard · Design/UX/i18n · Inventaire des modules V3‑only.
- **Lecture intégrale** des routers (jusqu'à 2201 lignes), des modèles, des services et des templates.
- **Personas** : chaque audit a rejoué le parcours quotidien de l'opérateur concerné pour
  qualifier la continuité (peut‑il toujours faire son travail ?).
- **Distinction systématique** entre *déplacé vers un service* (faux positif, OK) et
  *réellement absent* (régression).

---

## 2. Détail des écarts par module

> Convention de sévérité : **P0** = bloquant (capacité métier ou conformité perdue) ·
> **P1** = majeur (dégradation forte) · **P2** = mineur / confort.
> Les « Gains V3 » listés sont à **préserver** lors de toute reprise.

### 2.1 Planning / Planification de flotte
*Persona : le planificateur d'armement / chef d'exploitation.*

**P0**
- **Brochure commerciale imprimable** (`/planning/pdf/commercial`, FR/EN, filtres
  navire/origine/destination, vues chrono/route/destination, summary box, sélection
  leg‑à‑leg) : **disparue**. La vue publique par token existe, mais plus l'écran staff de génération/impression.
- **Saisie ATD/ATA + statut** sur le leg : le wizard V3 ne saisit ni le réalisé ni le statut
  manuel (champs présents sur le modèle mais alimentés ailleurs — à exposer au planificateur).

**P1**
- **Export CSV du planning réel** (15 colonnes) supprimé (seul l'export *scénario* subsiste).
- **Fiche destinataire + historique des partages** : `PlanningShare` a perdu
  `recipient_name/company/email/notes` et `legs_ids` (sélection sur‑mesure) et `lang`
  → suivi commercial « qui a reçu quel planning » perdu, partage public EN cassé.
- **Détection de retard** (`notify_delay` ≥ 4 h vs `eta_ref/etd_ref`) : champs conservés mais **plus exploités**.
- **Vue « par port »** (toutes les escales d'un port donné) réduite aux seuls conflits.

**P2** : vue carte des routes sur l'écran planning ; raccourcis ports pilotés par
`Port.is_shortcut` (codés en dur dans le template V3) ; toggle Tableau/Gantt/Carte ;
vérifier la politique de droits (V2 réservait l'écriture à admin/manager).

**Gains V3 :** scénarios what‑if (drag‑drop Gantt, comparaison au réel) ; validation
d'intégrité renforcée (chevauchement, continuité, vitesse) ; conflits serveur sur
intervalles ; cascade élargie (escale/dockers/notif clients) ; jours ouvrés portuaires ;
création depuis carte ; partage avec période/expiration/compteur.

---

### 2.2 Commercial + Tarification (Pricing / Grilles / Offres / Commandes / Devis)
*Persona : le responsable commercial / ship‑broker.*

**P0**
- **Écran d'affectation commande → leg** (legs filtrés par route, suggestion, badge « hors
  délai ») : **disparu**. `OrderAssignment` existe au modèle mais **aucune route ne l'écrit**.
- **Champs riches de la commande** supprimés : `palette_format`, `weight_per_palette`,
  `thc_included`, `booking_fee`, `documentation_fee`, `delivery_date_start/end`,
  `departure/arrival_locode`, `attachment_*`, **`rate_grid_id/line_id`** (lien commande↔grille).
- **Édition client** : aucune route (création seule) → impossible de corriger un client sans SQL.
- **Pièces jointes commande** (bon signé/contrat) : supprimées.

**P1**
- **Conversion offre→commande** réduite à un 1‑clic (perte de l'écran d'ajustement route/qty/prix).
- **Push Pipedrive Deal** sur offre/commande : plus créé (seul l'import org en masse subsiste).
- **Dashboard performance/conversion par grille + CA** : disparu.
- **Lookup tarif grille** (`rate-lookup` HTMX) dans le formulaire commande : absent côté staff.
- **Auto‑création packing list + notifications** à la confirmation : devenue manuelle.

**P2** : recherche Pipedrive ciblée dans le form client ; import multi‑routes depuis le
planning dans la grille ; override tarif par tranche ; filtres listes grilles/offres ;
statuts intermédiaires commande (loaded/delivered).

**Gains V3 :** outil de **devis public** (PDF, rate‑limit, honeypot, leads) + ajustement
staff ; modèle tarifaire enrichi (surcharge IMDG, minimum de facturation, options
per_palette/tonne/booking) ; sync Pipedrive en masse ; recherche client texte + fiche
client ; adresses BL structurées ; verrouillage de grille active ; dashboard remplissage des legs.

---

### 2.3 Cargo + Packing List + Bill of Lading + Portail expéditeur
*Personas : l'agent cargo/logistique ET l'expéditeur (token `/p/{token}`).* — **Module le plus régressé.**

**P0**
- **Bill of Lading déconnecté de la packing list** : le BL PDF V3 lit `booking.items`, **pas
  les `PackingListBatch`** saisis par l'expéditeur. Les deux flux ne se rejoignent jamais.
  Numérotation BL persistante `TUAW_{voyage}_{seq:03d}` + anti‑doublon : perdues.
- **Adresses structurées shipper/notify/consignee** (15 champs) supprimées du batch →
  mentions **obligatoires** du connaissement absentes.
- **Édition / suppression de batch** (staff et portail) : disparues — on ne peut qu'**ajouter**.
  L'**audit field‑by‑field** (`PackingListAudit`) est alimenté mais **non consultable** (ni route ni écran).
- **Arrival Notice** (PDF) : disparu.
- **Upload de documents sur le portail token** `/p/{token}/documents` : disparu (ne survit que dans `/me` client à compte).

**P1**
- **Import/Export Excel** (PL, voyage entier, template pré‑rempli) : tout disparu — canal de saisie de masse perdu.
- **Rate‑limiting du token portail** : non rebranché (le service existe) → régression sécurité.
- **Multilingue du portail** (fr/en/es/pt‑br/vi) : portail V3 figé en français (clients export US/BR/VN).
- **Écrans portail Voyage** (3 phases + carte position + équipage) et **Guide** (process,
  grille tarifaire, AMS/ISF US, FAQ) : disparus. **Fiche navire** réduite à une stat‑card.
- **Champs goods riches** (`type_of_goods`, `description_of_goods`, `cases_quantity`,
  `units_per_case`, `cargo_value_usd`, surface/volume/density auto) supprimés ; la notion de
  **complétude documentaire douanière** a disparu.

**P2** : pré‑remplissage du batch à la création ; alertes IMDG/écart palettes ; marquage
messages lus + badge non‑lus ; suppression PL staff ; CO₂ saved portail ; auto‑fill dimensions par type de palette.

**Gains V3 :** socle de données plus propre (`PackingList` rattachable à order **XOR**
booking) ; services testables ; PDF WeasyPrint ; intégration du plan d'arrimage dans le home
portail (confidentialité inter‑clients) ; `safe_files` durci ; espace client `/me`.

> ⚠️ **Le `CLAUDE.md` affirme pour Cargo « ✅ batches, audit, lock, messagerie » — c'est
> inexact** : audit non consultable, édition absente, BL déconnecté.

---

### 2.4 Escale / Port call
*Persona : l'agent d'escale / agent portuaire.*

**P0**
- **Édition ET suppression** des opérations et des shifts dockers : disparues (création seule).
  Toute erreur de saisie est irréversible.
- **Pilotage du statut portuaire / pose ATA‑ATD** depuis l'escale : disparu (flux « pilote
  arrivée → à quai → pilote départ »), avec lui la **propagation aux legs aval**, le **recalcul
  finance OPEX** et les **notifications arrivée/départ**.
- **Saisie manuelle des heures réelles** des opérations : seul `now()` via boutons
  Démarrer/Terminer → impossible de saisir a posteriori (cas standard).

**P1**
- **`intervenant`** (nom/société) et **durées prévue/réelle** : champs supprimés du modèle.
- **Productivité dockers** (pal/h, écart % `rate_delta_pct`) : supprimée.
- **Couplage opération ↔ équipage** (embarquement/débarquement créant `CrewAssignment`) +
  **billetterie équipage** (vue + alertes dates) + **auto‑PAF Fécamp** : disparus.
- **Multi‑timezone** sur les datetimes (UTC/Paris/Port local) + bornes à quai : perdus.

**P2** : timeline « flux opérationnel », activités parallèles, métriques performance
navigation, commandes commerciales du leg, liens Packing Lists / impression stowage FR‑EN.

**Gains V3 :** PDF SOF WeasyPrint ; lien escale↔onboard (SOF auto) ; occupation par cale ;
verrouillage tracé (`escale_locked_at/by`) ; champs dockers enrichis (company, nb_dockers,
target/done) ; direction IMPORT/EXPORT. **À nettoyer :** `staff/escale/detail.html` est du **code mort**.

---

### 2.5 Onboard / Captain + Claims
*Persona : le capitaine / officier à bord.*

**P0**
- **Édition + suppression d'un événement SOF** (non signé) : disparues → pas de correction d'une faute de saisie.
- **Documents cargo : formulaires structurés par type** (`data_json`) : disparus, remplacés
  par un `body` texte libre. Types **HOLDS_CERT / KEY_MEETING / PRE_MEETING** supprimés, 6 LOP
  fusionnés en 2 ; picker Master/Officer depuis crew embarqué perdu.
- **Pièces jointes leg** (`OnboardAttachment`, 8 catégories) + **zone « Documents agent
  d'escale »** (BL signés, lettres de protestation, constats) : modèles **supprimés**.

**P1**
- **Messagerie de bord** : perte du fil **scope navire** (continuité inter‑legs), de
  l'**autocomplete @mentions**, du **bot répondeur intelligent** (placeholder en V3), des
  **messages système‑journal** et de la suppression de message.
- **Clôture d'escale** : perte du **PDF récapitulatif**, de la **checklist documentaire**, de
  l'étape **reopen** et de l'état **locked** distinct.
- **Claims** : perte du détail financier (**franchise / indemnité / reste‑à‑charge** +
  propagation `LegFinance.claims_cost`), du rattachement **crew_member / order_assignment**,
  du **SOF auto** à la déclaration, de la timeline 9 types + **PJ par entrée**.
- **Notifications onboard** (`OnboardNotification`) : modèle supprimé, plus d'alertes in‑page.

**P2** : fuseau par événement SOF ; historisation SOF de la génération de documents ; export
Word des docs cargo ; pièces jointes multiples par document ; champs contexte/lieu incident sur claims.

**Gains V3 :** signatures/lock IMO (SOF, noon, watch, hash SHA‑256) ; **Noon Report officiel
TOWT** complet + pré‑rempli + ROB auto‑chaîné ; **PWA offline** ; **journal de quart** ; MRV
auto ; hooks SOF→statuts/bookings ; conformité ISM/ISPS + registre visiteurs ; next‑port
briefing ; clôture→KPI+finance ; claims enrichis (war_risk/third_party, contrat d'assurance
structuré, historique de provision, reporting + délai moyen + CSV).

---

### 2.6 Crew / Équipage
*Persona : le bosco / responsable équipage.*

**P0**
- **Édition de la fiche marin** : aucune route (création seule) → impossible de mettre à jour passeport/visa/coordonnées.
- **Export PDF « Crew List » pour la PAF** (`/crew/border-police/{vessel_id}`, bilingue) : disparu (**obligation réglementaire**).
- **Formulaire complet** exposant `visa_us/visa_br`, `seaman_book`, `date_of_birth`,
  nationalité : champs au modèle mais **aucun écran ne les saisit** (données mortes).
- **Édition + suppression d'une affectation** (`CrewAssignment`) : disparues.

**P1**
- **Upload + download de la PJ d'un billet** : `file_path` orphelin (aucune route).
- **API équipage/navire** (`/crew/api/vessel/{id}`) : disparue (consommée par escale/onboard).
- **Auto‑opération PAF Fécamp** + **alerte billet hors fenêtre d'escale** : disparues.
- **Anti‑overlap** d'embarquement ; **suppression/désactivation** marin ; **suppression** billet.

**P2** : marqueur « étranger » (dérivé nationalité hors Schengen) ; vue billetterie globale ;
calendrier individuel détaillé (dans la fiche) ; filtre rôle UI ; badges rôle labellisés/colorés.

**Gains V3 :** **compliance Schengen réelle** (présence ports Schengen, 90/180 persisté) +
garde‑fou embarquement avec override tracé ; armement réglementaire par navire ; fiche marin
détaillée ; **sync Marad** ; billets enrichis ; affectation au leg.
**Arbitrage :** `CrewAssignment.leg_id` est désormais **obligatoire** → impossible d'embarquer
hors d'un leg (V2 le permettait). **Congés marins** migrés vers RH (séparation de droits crew↔rh à valider).

---

### 2.7 Stowage / Plan d'arrimage
*Persona : le responsable arrimage / second capitaine.*

**P0**
- **Vue « à bord »** dédiée (`/stowage/onboard/{leg_id}`, perm captain) : supprimée.
- **Drag‑drop de réaffectation de zone** (`/move`) : supprimé.
- **Édition / suppression d'une affectation** (item) : absentes (seul « Suggérer auto » réécrase tout).
- **Liste des batches non assignés** (« reste à arrimer ») + affectation directe à une zone : disparue.

**P1**
- **Bilinguisme FR/EN** (plan + PDF + labels zones) : perdu.
- **Capacités réelles par zone × format × gerbage** (`ZONE_CAPACITIES` xlsx) : remplacées par une capacité EPAL unique + coefficient (moins fidèle).
- **Formats BARRIQUE120/140** absents du select (coefficients pourtant présents).
- **Arrimage avant cargo doc** (placeholder order→item) : perdu.

**P2** : vraie vue SVG top‑down par pont (backlog #3 toujours non livré) ; select 24 classes
IMO (vs texte libre) ; politique de blocage dur vs avertissement (changement assumé) ; coupe latérale profil.

**Gains V3 :** **référentiel `StowageZoneSpec` éditable par classe** (admin) ; workflow de
statut du plan ; **moteur d'avertissements** (surcharge, résistance, gerbage, ségrégation) ;
item enrichi (HS, IMDG, UN, cubage, gerbé) ; **repérage visuel** (locate batch/order) réutilisé
(claims, portail, escale) ; PDF WeasyPrint ; permissions cohérentes (`cargo`).

---

### 2.8 MRV (émissions UE)
*Persona : le data analyst / responsable MRV‑RSE.* — **Régression réglementaire.**

**P0**
- **Export DNV Veracity** : passé de **18 colonnes** exactes à **9** (séparateur `;`, nom de
  fichier figé) + **bug : colonne IMO toujours vide** → CSV inexploitable par Veracity.
- **Carbon Report PDF** (ReportLab paysage : résumé + table d'events) : remplacé par un `.txt`
  de 4 lignes + une page HTML par leg. **Blocage si erreurs qualité** supprimé.
- **Édition + suppression d'un event** : disparues → données non amendables.

**P1**
- **Source de vérité** : les **4 compteurs DO** (port/stbd ME, fwd/aft gen) et le calcul
  conso ME/AE + ROB calculé ont disparu (la conso vient désormais des noon reports). Choix à **trancher et documenter**.
- **Contrôle qualité multi‑règles** (compteurs monotones, ROB, cargo transit) réduit à 1 règle
  (ROB ±2 t, jamais bloquante) et seulement pour les events auto‑générés.
- **Position DMS** (lat/lon deg/min/NS‑EW) + auto‑remplissage GPS : supprimée (exigée par DNV).
- **UI d'édition des paramètres MRV** (densité MDO, seuil de déviation) : supprimée.

**P2** : vue détail leg (table d'events ligne‑à‑ligne + badges qualité) ; agrégat par leg sur
le dashboard ; filtres navire/année sur le rapport ; suggestions SOF→MRV ; bunkering date / cargo MRV.

**Gains V3 :** **sync auto** noon/SOF → MRVEvent (idempotente) ; **Carbon Report par leg**
(intensités /NM, /t, /t·nm EU MRV + CO₂ évité) ; **facteur CO₂ versionné** (`/admin/co2`) ;
modèles XLSX officiels TOWT téléchargeables ; conformité charte (vs Poppins V2).

---

### 2.9 Finance + KPI
*Personas : le contrôleur de gestion ET le data analyst / direction.*

**P0**
- **Suivi Prévisionnel vs Réalisé** (double colonne sur 5 postes : CA, portuaire, quai, OPEX
  mer, opérations) : supprimé. `LegFinance` ne stocke plus qu'une marge réelle consolidée →
  **le contrôle de gestion perd sa fonction première**.
- **Export CSV Finance** (18 colonnes) : supprimé.
- **NOx évité / SOx évité** : **totalement disparus** (modèle `EmissionParameter` supprimé).

**P1**
- **Onglet Exploitation** (taux d'activité, **écart planning ETD→ATD par leg**, vitesse
  d'exploitation, durée moyenne par route) : disparu.
- **Équivalences CO₂** (vols Paris‑NYC, containers Asie‑EU) : disparues.
- **Vue KPI consolidée** : les 5 onglets V2 sont éclatés entre `/kpi`, `/mrv/carbon` et
  `/dashboard/analytics` sans navigation unifiée.
- **Détail exposition assurance** (provisions/indemnités/franchises) en KPI : perdu.

**P2** : histogrammes Commerce (typologie chargeur, format palette, tranche prix) ;
productivité dockers en KPI ; Letters of Protest ; filtre par route ; flag `accessible` du
port ; recherche/filtre pays config ports ; **tous les bar‑charts ont disparu** (100 % tables).

**Gains V3 :** **auto‑alimentation KPI** (bookings+SOF) avec verrou manuel ; **Carbon Report
CFOTE_09** (DO/CO₂ réels, intensités t·nm) ; **rollup finance FLX‑05** ; **CRUD OPEX** ;
**PortConfig opérationnel** (contacts, VHF, restrictions, jours fermés) ; facteurs CO₂
versionnés ; coût sinistres intégré à la marge ; Anemos ; Decimal partout ; variance N‑1.

> ⚠️ **Migration de données :** `LegFinance` forecast/actual et `quay_cost` **n'ont pas de
> cible** en V3 (risque de perte historique) ; `LegKPI.cargo_tons` (t, Float) → `tonnage_kg`
> (kg, Decimal) impose un **×1000** ; `PortConfig.port_cost_total` à ventiler en agence+pilote ; `accessible` sans cible.

---

### 2.10 Tracking flotte + Navigation / Performance
*Persona : l'opérateur de suivi de flotte / fleet ops.*

**P0 (intégrité & contrat)**
- **`UniqueConstraint(vessel_id, recorded_at)` + index** supprimés du modèle de position →
  idempotence faite en Python (1 SELECT/ligne, lourd sur ZIP), risque de doublon concurrent, lectures non indexées.
- **Filtre anti‑saut > 50 NM** supprimé du calcul de distance réelle → **distance surévaluée**
  et écart réel/théorique faussé.
- **4 endpoints GET supprimés** (`/latest`, `/positions/{id}`, `/leg/{id}/track`,
  `/navigation-kpis`) → tout consommateur externe/JS casse (404). Décider de leur sort.

**P1**
- **Vue KPI navigation agrégée par année** (avg/max SOG, point_count, distances par leg) : disparue.
- **`avg_speed_kn`** (déjà calculé) et **`real_elongation`** non affichés dans le tableau Navigation.
- **`import_batch`** (traçabilité du fichier source) + `created_at` : supprimés.

**P2** : codage couleur de statut (à quai/en mer) sur les marqueurs live ; aligner/versionner
les clés JSON de la réponse d'upload ; documenter le 401→403 sur token invalide.

**Gains V3 :** **ingestion multi‑format robuste** (CSV/ZIP/XLSX/urlencoded, délimiteur auto,
colonnes tolérantes, `TRACKING_VESSEL_MAP`) — **rétro‑compatible** avec le format satcom V2 ;
page `/tracking` (live + historique filtrable) ; **module Navigation/Performance** complet ;
**météo historisée** (`vessel_weather`, cron 30 min) ; MapLibre + charte Kairos.

> **Compatibilité Power Automate :** l'**ingestion** (écriture) est rétro‑compatible et
> enrichie. La **lecture** (réponse JSON de l'upload + 4 endpoints GET) est **rompue** : tout flux PA qui en dépend doit être réécrit.

---

### 2.11 Administration + Auth + Dashboard staff
*Personas : l'administrateur système ET le collaborateur staff lambda.*

**P0**
- **CRUD Navires** (create/edit : code, IMO, flag, dwt, capacité, vitesse, élongation) :
  **aucune route ni écran** (création uniquement via `scripts/seed_demo.py`).
- **Moteur d'alertes du dashboard** (`compute_alerts`, 6 familles, deep‑links, tri par
  sévérité) : **totalement absent** → le collaborateur perd la vue proactive du quotidien.

**P1**
- **Rate‑limiting du login mot de passe** : supprimé (seul `/login/mfa` est limité) → brute‑force non freiné.
- **KPI métier du dashboard** (CA prévisionnel, CO₂ évité, taux de remplissage) : perdus (4 cartes au lieu de 6).
- **Notifications cargo (PL soumises) + compagnie (ATA/ATD)** sur l'accueil : disparues.
- **Imports** (utilisateurs Excel + template, planning CSV) et **Exports/Purges DB**
  (global/sélectif/fichiers, purge ciblée, reset, cleanups temporels) : disparus (backlog #4/#5).
- **Matrice de permissions** : `data_analyst` **perd l'accès admin** (était dans `ADMIN_ROLES` en V2) — à confirmer.

**P2** : écran Pipedrive (set + test token, aujourd'hui en `.env` seulement) ; paramètres
émissions NOx/SOx + MRV éditables ; lock/unlock escales en masse ; activity‑log (filtre user +
pagination) ; table prochains départs ; auditer le bypass admin du middleware maintenance ;
**discoverabilité admin** (exposer dans la sidebar `/admin/co2`, `/opex`, `/insurance`, `/maintenance`, `/permissions`, `/activity-logs`).

**Gains V3 :** **MFA TOTP staff** (codes de récupération, reset admin, device de confiance) ;
détection nouvel appareil + alertes email ; **tableau de bord sécurité** ; **éditeur de matrice
de permissions** (overrides DB, cache, fail‑closed) ; CO₂ versionné ; référentiel d'arrimage ;
feature flags ; session role‑aware ; `assigned_vessel_id` ; admin éclaté en pages dédiées ; CSP‑strict.

---

### 2.12 Design system / UX / i18n (transverse)
*Persona : l'utilisateur staff au quotidien + le designer/intégrateur.*

**P0 (câblage perdu)**
- **Sélecteur de fuseau horaire dans les formulaires** (`tz-input-wrap` + `tz-select`,
  UTC/Paris/Port local) : utilisé dans 9 templates V2, **0 en V3** — `towt-tz.js` + CSS livrés
  mais **morts**. Saisie d'heures portuaires (SOF, escale, ETA) sans fuseau → ambiguïté.
- **Catalogue i18n vietnamien** : **15 clés** en V3 (≈ 442 en V2) → UI quasi 100 % francophone pour un utilisateur VN.
- **Filtrage de la sidebar par permission** (`has_any_access`) : supprimé → tous les liens
  affichés à tous, clics menant à des 403.

**P1**
- **Horloge « prochain port »** de la sidebar : disparue (endpoint `/api/ports/next-clocks` vivant mais plus appelé).
- **Cloche de notifications** du topbar : menu **placeholder statique** non branché au vrai flux.
- **Sélecteur de langue** en UI staff : absent (mécanisme dispo via `/lang/`).

**P2** : enrichir `errors/403|404` (icône + carte) ; réintroduire `.empty-state` ; charger les
graisses Manrope 300/800 si utilisées ; nettoyer le CSS mort (`.tz-*`, `.sidebar-clock`).

**Gains V3 :** tokens W3C → CSS fidèle + dark‑mode ; architecture templates en couches ;
**JS 100 % externe** (CSP‑strict) + toast/modal server‑driven (`HX-Trigger`) ; topbar riche ;
navigation **groupée repliable** (tous les modules présents, mieux structurés) ;
accessibilité (skip‑link, aria) ; **JetBrains Mono** ; `templating.py` enrichi (brand
multilingue, `asset()` cache‑busting, JSON‑LD). **Tokens de design byte‑identiques à V2 → charte préservée à 100 %.**

---

### 2.13 Modules V3‑only (évolutions) — points de consolidation

Périmètre purement nouveau (pas de régression V2) mais avec de la dette à solder :

1. **Factures client** (`client_invoice` + `services/invoicing`) : **dormant** (`/me/invoices`
   redirige vers `/me/documents`). Trancher : activer l'export comptable (modèle prêt) ou retirer le code mort.
2. **Congés** : `CrewLeave` (marins, hérité V2) et `HrAbsence` (sédentaires) **coexistent** sans
   unification — « stub historique » à harmoniser.
3. **`erp_scaffold_router`** : plusieurs slugs (escale/crew/finance/mrv/claims/tracking/admin)
   en **collision** avec de vrais routers — ne garder que `analytics`.
4. **API publique v1** : **read‑only**, auth `X-API-Key` annoncée mais **non appliquée** sur les routes lues → à sécuriser avant tout usage B2B.
5. **Veille** : phase IA (synthèse/scoring) annoncée mais absente (P2).
6. **PWA** : SW/manifest servis, offline réel (IndexedDB) au backlog.
7. **Documentation** : corriger `CLAUDE.md` (Insurance n'est **pas** V3‑only ; CO₂→Anemos ;
   statut réel de Cargo, KPI, Finance).

---

## 3. Régressions transverses (vue consolidée)

| Thème transverse | Constat | Modules touchés |
|---|---|---|
| **Pas d'édition/suppression (CRUD amputé)** | Très répandu : création seule, correction impossible | cargo, escale, crew, mrv, onboard/captain (SOF), commercial (client), stowage |
| **Exports / PDF réglementaires perdus** | BL, Arrival Notice, Crew List PAF, DNV 18 colonnes, Carbon Report PDF, CSV finance, brochure planning | cargo, crew, mrv, finance, planning |
| **Écrans « cockpit » → CRUD minimal** | Dashboard, escale, onboard, KPI, portail | dashboard, escale, onboard, kpi, cargo/portail |
| **Multi‑timezone & i18n** | tz‑forms morts, portail mono‑langue, vi.py effondré | design, escale, onboard, cargo |
| **Sécurité de surface** | rate‑limit login & token retirés, sidebar non filtrée, API v1 ouverte | admin/auth, cargo/portail, design, api_v1 |
| **Contrat d'API / intégrité données** | positions sans unique/index, réponse upload changée, endpoints GET supprimés | tracking |
| **Champs de modèle supprimés** | Pertes massives de colonnes (cargo ~40, escale, crew, mrv, finance, claims) | quasi tous |
| **Code mort / collisions** | `escale/detail.html`, `tz-*`/`sidebar-clock`, scaffolds en doublon | escale, design, scaffold |

---

## 4. Analyse par persona — continuité opérationnelle (GO / NO‑GO)

| # | Persona | Peut‑il faire son travail en V3 ? | Principaux points de rupture |
|---|---|---|---|
| P1 | **Planificateur d'armement** | 🟠 **Partiel** | Plus de brochure commerciale, ni d'export CSV, ni de saisie ATD/ATA, ni de suivi des partages |
| P2 | **Responsable commercial / broker** | 🔴 **NON** | Pipeline grille→offre→commande→leg cassé : pas d'affectation au leg, commande appauvrie, pas d'édition client |
| P3a | **Agent cargo / logistique** | 🔴 **NON** | BL déconnecté, pas d'édition batch, pas d'Arrival Notice, pas d'Excel, audit invisible |
| P3b | **Expéditeur / shipper (portail)** | 🔴 **NON** | Ni édition/correction, ni dépôt de documents, ni import Excel, ni multilingue ; écrans Voyage/Guide perdus |
| P4 | **Agent d'escale / portuaire** | 🔴 **NON** | Plus de pilotage ATA/ATD/statut, ni d'édition/suppression des saisies, ni de KPI dockers |
| P5 | **Capitaine / officier à bord** | 🟠 **Partiel** | Gagne le réglementaire (signatures, noon, ISM/ISPS) mais perd correction SOF, docs cargo guidés, PJ/docs agent |
| P6 | **Bosco / responsable équipage** | 🔴 **NON** | Pas d'édition fiche marin, pas de Crew List PAF, pas d'édition d'affectation |
| P7 | **Data analyst / MRV‑RSE** | 🔴 **NON** | Export DNV non conforme, Carbon Report PDF perdu, pas d'édition d'event, NOx/SOx disparus |
| P8 | **Contrôleur de gestion / finance** | 🔴 **NON** | Plus de prévisionnel/réalisé, ni d'export CSV ; granularité des coûts réduite |
| P9 | **Administrateur système** | 🟠 **Partiel** | Gagne MFA/RBAC mais perd CRUD navires, imports/exports/purges, réglages (Pipedrive, émissions) |
| P10 | **Collaborateur staff / manager** | 🟠 **Partiel** | Dashboard appauvri : plus de moteur d'alertes, ni KPI métier, ni notifications cargo/compagnie |
| P11 | **Opérateur suivi de flotte** | 🟠 **Partiel** | Gagne historique+météo mais perd les KPI nav agrégés et l'accès API de lecture |

**Lecture :** **6 personas en NO‑GO** (commercial, cargo, expéditeur, escale, crew, MRV/finance)
et **6 en parité partielle**. Aucune persona staff n'est aujourd'hui en parité complète avec la V2.

---

## 5. Plan d'action de reprise et d'évolution

Le plan est organisé en **lots** priorisés. Chaque reprise d'écran V2 doit être **réécrite en
charte Kairos / Manrope, sans `<script>` inline (CSP‑strict)**, avec `require_permission()` +
`services.activity.record()` — les templates V2 (Poppins, styles/JS inline) ne sont pas réutilisables tels quels.

### Lot 0 — Sécurité & intégrité (rapide, à faire immédiatement)
*Objectif : refermer les régressions de surface. Effort faible, risque élevé si ignoré.*

1. **Rate‑limiting** : rebrancher sur le **POST /login** (mot de passe) et sur le **portail
   `/p/{token}`** (les services existent).
2. **Sidebar** : réintroduire le filtrage `has_any_access(user, module)` par lien.
3. **Tracking** : restaurer `UniqueConstraint(vessel_id, recorded_at)` + index + upsert
   `on_conflict_do_nothing` ; réintroduire le **filtre anti‑saut > 50 NM**.
4. **API v1** : appliquer effectivement l'auth `X-API-Key` avant tout usage B2B.
5. **Décision endpoints tracking GET** : réimplémenter a minima `/latest` (carte) + l'agrégat KPI, ou documenter la rupture.

### Lot 1 — Reprise du cœur métier staff (P0 — bloquants)
*Objectif : restaurer la capacité de travail des 6 personas en NO‑GO.*

| Domaine | Reprises P0 |
|---|---|
| **Cargo** | Reconnecter **BL ↔ `PackingListBatch`** (+ numérotation `TUAW_…` + anti‑doublon) ; **adresses structurées** shipper/notify/consignee ; **édition/suppression de batch** + **vue audit** (données déjà collectées) ; **Arrival Notice** (WeasyPrint) ; **upload documents portail token** |
| **Escale** | **Édition/suppression** opérations & shifts ; **pilotage ATA/ATD + statut portuaire** (+ propagation legs aval, recalcul OPEX, notifications) ; **saisie manuelle des heures réelles** |
| **Onboard/Captain** | **Édition/suppression SOF** non signés ; **documents cargo structurés** (HOLDS_CERT/KEY_MEETING/PRE_MEETING/LOP, picker crew) ; **pièces jointes leg + zone docs agent d'escale** |
| **Crew** | **Édition fiche marin** ; **Crew List PAF** (PDF WeasyPrint) ; **formulaire complet** (visa US/BR, seaman book, naissance) ; **édition/suppression d'affectation** |
| **Commercial** | **Écran d'affectation commande→leg** (filtré route + suggestion + alerte délai) ; **réintroduire les champs commande** (format, poids, frais, dates, route, **lien grille**) ; **édition client** ; **PJ commande** |
| **MRV** | **Export DNV 18 colonnes** (+ correctif IMO) ; **Carbon Report PDF** + **blocage qualité** ; **edit/delete event** |
| **Finance** | **Prévisionnel/réalisé** sur `LegFinance` ; **export CSV** ; **NOx/SOx évités** |
| **Stowage** | **Vue à bord** ; **réaffectation de zone** (drag‑drop ou changement de zone) ; **édition/suppression d'item** ; **liste des non‑assignés** |
| **Admin/Dashboard** | **CRUD navires** ; **moteur d'alertes** du dashboard (6 familles) |
| **Planning** | **Brochure commerciale PDF** (filtres + vues + FR/EN) ; **saisie ATD/ATA + statut** (ou exposition explicite du flux délégué) |
| **Design** | **Recâbler la saisie timezone** des formulaires (partial `staff/_time_input` + `data-port-tz`) ; **restaurer `vi.py`** |

### Lot 2 — Continuité opérationnelle (P1 — majeurs)

- **Cargo :** import/export Excel + template ; multilingue portail ; écrans Voyage & Guide ; champs goods riches ; rate‑limit token (si non fait en Lot 0).
- **Escale :** `intervenant` + durées ; productivité dockers ; couplage op↔crew + billetterie + auto‑PAF Fécamp ; multi‑timezone.
- **Onboard/Claims :** messagerie de bord (fil navire, mentions, bot/Kairos, messages système) ; clôture (PDF + checklist + reopen) ; claims (franchise/indemnité + propagation finance + SOF auto + timeline/PJ) ; notifications onboard.
- **Crew :** upload/download PJ billet ; API équipage/navire ; anti‑overlap ; suppression marin/billet.
- **MRV :** trancher la **source de vérité** (compteurs DO vs noon) ; contrôle qualité multi‑règles ; position DMS ; UI params MRV.
- **Finance/KPI :** onglet Exploitation ; équivalences CO₂ ; vue KPI consolidée ; détail assurance.
- **Commercial :** conversion offre→commande éditable ; push Pipedrive Deal ; dashboard conversion/CA ; lookup tarif grille ; auto‑PL à la confirmation.
- **Planning :** export CSV ; fiche destinataire + historique partages ; détection de retard ; i18n partage ; sélection leg‑à‑leg ; vue par port.
- **Tracking :** vue KPI nav agrégée année ; afficher `avg_speed`/`real_elongation` ; `import_batch`.
- **Admin :** rate‑limit login (si non fait) ; KPI métier dashboard ; notifications cargo/compagnie ; imports Excel users + CSV planning ; exports/purges DB.
- **Design :** horloge prochain port ; cloche notif branchée ; sélecteur de langue.

### Lot 3 — Parité fine & confort (P2)

Reprise des éléments P2 listés par module (vue carte planning, vue SVG top‑down par pont,
histogrammes KPI, filtres listes, badges/labels, `.empty-state`, pages d'erreur enrichies,
discoverabilité admin, nettoyage du code mort, etc.).

### Lot 4 — Évolutions (au‑delà de la parité)

- **Consolider les modules V3‑only :** trancher `client_invoice` ; unifier congés
  `CrewLeave`/`HrAbsence` ; nettoyer `erp_scaffold` ; veille IA (P2) ; PWA offline réel.
- **Capitaliser sur les gains V3 :** brancher le **bot de bord** sur Kairos AI ; étendre la
  **météo/navigation** aux KPI ; généraliser les **signatures IMO** aux documents repris ;
  exploiter le **référentiel d'arrimage** pour une vraie vue top‑down.
- **Backlog produit existant** (CLAUDE.md) : certificats CO₂ PDF (couvert par Anemos),
  générateurs DOCX (BL/offre), exports admin ZIP, purges ciblées, notifications email.

### Séquencement recommandé

```
Lot 0 (sécurité/intégrité)  ──► quelques jours, en parallèle de tout
Lot 1 (P0 cœur métier)      ──► priorité absolue ; ordonner par persona NO-GO :
                                 ① Cargo+Escale (chaîne export documentaire)
                                 ② Crew+MRV (conformité réglementaire)
                                 ③ Commercial (pipeline) + Onboard (geste capitaine)
                                 ④ Finance/Planning/Stowage/Admin
Lot 2 (P1)                  ──► après stabilisation de chaque module en Lot 1
Lot 3 (P2) + Lot 4 (évol.)  ──► itératif
```

---

## 6. Risques de migration de données à anticiper

- **Finance :** `LegFinance` forecast/actual et `quay_cost` **sans cible** en V3 → définir la
  reprise avant d'écraser l'historique ; `LegKPI.cargo_tons` (t) → `tonnage_kg` (kg) = **×1000**.
- **Ports :** `PortConfig.port_cost_total` à ventiler en `agency_fee_eur` + `pilot_fee_eur` ; flag `accessible` sans cible.
- **Cargo :** ~40 colonnes `PackingListBatch` supprimées (adresses, références, goods) — toute
  reprise impose une **remigration de schéma** + reprise des données existantes.
- **Tracking :** renommages `sog`→`sog_kn`, `cog`→`cog_deg` ; perte `leg_id`/`import_batch`/`created_at`.
- **Crew :** `CrewAssignment.vessel_id`→`leg_id` (obligatoire) ; `first/last_name`→`full_name`.
- **MRV / Claims / Escale / Onboard :** nombreux champs/relations supprimés (voir §2) — cartographier avant reprise.

---

## 7. Recommandations de gouvernance

1. **Corriger `CLAUDE.md`** : statuts inexacts (Cargo « ✅ audit/lock » ; Finance/KPI ;
   Insurance présentée à tort comme V3‑only ; CO₂→Anemos). Documenter les **ré‑absorptions**.
2. **Documenter les décisions de design assumées** (ex. stowage « avertir, ne jamais bloquer » ;
   MRV source noon vs compteurs ; congés migrés vers RH ; `CrewAssignment.leg_id` obligatoire) pour ne pas les recompter comme régressions.
3. **Nettoyer le code mort** (`escale/detail.html`, CSS `.tz-*`/`.sidebar-clock`, scaffolds en doublon) pour éviter les erreurs de maintenance.
4. **Ajouter une matrice de tests de non‑régression par persona** (les 12 parcours du §4) au pipeline `pytest`.
5. **Versionner le contrat d'API tracking** si des flux Power Automate consomment la réponse d'upload ou les endpoints GET.

---

## 8. Points d'arbitrage à trancher avec le métier

Ces décisions conditionnent le périmètre du Lot 1 et relèvent du choix produit/métier :

1. **MRV — source de vérité** : revenir aux **4 compteurs DO** (méthode V2) ou officialiser les
   **noon reports** comme source unique ? (impacte tout le module et la conformité).
2. **Finance — modèle budgétaire** : restaurer le **prévisionnel/réalisé** complet (5 postes ×
   2 colonnes) ou un modèle budget/réel simplifié ?
3. **Stowage / Escale — politique de blocage** : conserver « avertir sans bloquer » (V3) ou
   réintroduire des **blocages durs** sur zones/capacités critiques ?
4. **Crew — embarquement hors leg** : autoriser l'affectation sans leg (V2) ou maintenir `leg_id` obligatoire (V3) ?
5. **Cargo — facturation** : activer `client_invoice`/`invoicing` ou confirmer la facturation hors plateforme (et retirer le code dormant) ?
6. **Portail expéditeur** : cible = portail token riche (V2) **et** espace client `/me`, ou
   convergence vers `/me` (auth) avec un portail token minimal ?
7. **Droits `data_analyst`** : restaurer son accès admin (V2) ou acter la restriction V3 ?

---

## 9. Annexe — empreinte comparée des routers (indicateur, pas une preuve)

| Module | LOC V2 | LOC V3 | Δ | Verdict d'audit |
|---|---:|---:|---|---|
| cargo | 1751 | 829 (3 fichiers) | −53 % | 🔴🔴🔴 régression confirmée |
| mrv | 969 | 237 | −76 % | 🔴🔴 régression (pas qu'un déplacement) |
| kpi | 962 | 268 | −72 % | 🔴 régression partielle (services + analytics) |
| escale | 1030 | 529 | −49 % | 🔴🔴 régression confirmée |
| crew | 821 | 604 | −26 % | 🔴🔴 régression (CRUD + PAF) |
| admin | 1796 | 1299 | −28 % | 🔴 régression outillage |
| onboard | 2201 | 986 + captain 1000 | ≈ stable | 🔴🔴 éclatement + pertes ciblées |
| planning | 1123 (+158) | 755 + scénario 582 | + | 🔴 sorties commerciales perdues |
| commercial | 646 + pricing 1326 | 1966 + devis 406 | + | 🔴🔴 repivot incomplet (ordres) |
| finance | 442 | 562 | + | 🔴 prévisionnel/réalisé perdu |
| tracking | 475 | 598 + nav 344 | + | 🟠 contrat API rompu |
| claims | 311 | 723 | + | 🟠 enrichi mais détail financier perdu |
| stowage | 641 | 493 | −23 % | 🔴 interaction d'arrimage perdue |

> Le LOC n'est qu'un signal : plusieurs modules ont **grossi tout en régressant**
> fonctionnellement (commercial, finance, claims), car le code a basculé vers de nouveaux
> périmètres (devis public, booking, rollup) sans reprendre l'outillage staff existant.

---

*Fin du rapport. Les 13 audits détaillés (un par domaine) ont alimenté cette synthèse ; chaque
constat est traçable aux fichiers cités (chemins `app/routers`, `app/models`, `app/services`,
`app/templates` des deux versions).*
