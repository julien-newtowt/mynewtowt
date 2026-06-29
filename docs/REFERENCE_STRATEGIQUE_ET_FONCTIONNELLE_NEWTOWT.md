# Document de référence — Plateforme NEWTOWT `mynewtowt`
## Contexte, comportements, stratégie & alignement sectoriel (v3.0.0)

> **Nature.** Document de référence maître, **autoportant**, destiné à être **ingéré
> par des IA** (outils d'analyse, de conception, de revue, d'aide au développement)
> et lu par des humains reprenant le projet. Il consolide en un seul fichier
> l'intégralité des **contextes** et **comportements** de la plateforme NEWTOWT V3,
> ainsi que la **couche stratégique, marketing, sectorielle et concurrentielle**
> nécessaire à l'alignement et à la poursuite du développement.
>
> **Objectifs.** (a) Assurer la **continuité du développement** sans relire tout le
> code ; (b) permettre un **alignement stratégique et marketing** ; (c) donner une
> **compréhension du spectre sectoriel et concurrentiel** ; (d) cartographier les
> **outils et fonctionnalités déjà en place**.
>
> **Périmètre & date.** Branche `claude/app-reference-docs-q4vx4g`, dérivé du code
> au **2026‑06‑29**. Version applicative **3.0.0**.
>
> **Hiérarchie de vérité.** En cas de divergence : le **code** prime, puis ce
> document, puis les autres docs, puis `CLAUDE.md` (qui comporte des inexactitudes
> historiques signalées au §16). Documents jumeaux à lire en complément :
> `docs/audit/DOCUMENT_REFERENCE_CONTEXTE_APPLICATION.md` (réf. factuelle code),
> `docs/strategy/00-vision.md`, `docs/audit/2026-06-12-audit-360/*` (audit 360°),
> `docs/personas/01-personas.md`.

---

## 0. Mode d'emploi (pour une IA qui ingère ce document)

- **Pour du conseil stratégique / marketing :** lire §1 → §5 (synthèse, vision,
  secteur/concurrence, marque/RSE, personas) puis §16 (écarts vs marché).
- **Pour de la conception produit / UX :** §5 (personas/parcours), §6
  (fonctionnel), §11 (design system), §17 (roadmap).
- **Pour du développement / refactor :** §6 (modules), §7 (architecture), §8
  (données), §9 (sécurité/RBAC), §10 (intégrations), §15 (arbitrages).
- **Invariants de comportement à supposer vrais :** §7.4 (patterns). Tout nouveau
  code doit les respecter.
- **Chiffres-clés vérifiés sur le code** (à utiliser comme référence) : 9 rôles ·
  17 modules de permission · ~95 tables (≈52 modules de modèles) · 90 migrations
  Alembic (dernière `20260629_0084`) · ~40 routers · ~90 services · ~230 templates
  Jinja · 5 langues · 126 fichiers de test.

---

## 1. Synthèse exécutive

**NEWTOWT** (TransOceanic Wind Transport, ex‑TOWT) est un **armateur français du
transport maritime décarboné à la voile**, pionnier depuis **2011**. `mynewtowt`
est sa **plateforme unifiée V3** : un seul déploiement FastAPI qui fusionne trois
univers historiquement séparés —

1. **ERP interne** (exploitation maritime complète : ~18 modules métier) ;
2. **Plateforme client publique** (vitrine marketing + catalogue de routes +
   réservation de cale + compte B2B authentifié avec MFA + factures + certificats
   CO₂) ;
3. **Portail expéditeur par token** (`/p/{token}` : packing list, messagerie,
   documents, suivi — sans compte).

**Proposition de valeur.** Offrir le **transport vélique de marchandises** avec « la
même exigence que la grande conteneurisation (CMA‑CGM, MSC, Maersk) » tout en
conservant un **ADN décarboné** et une **transparence environnementale** (formule
CO₂ publiée, éco‑calculateur, certificat « Anemos » émis automatiquement à la
livraison).

**Position concurrentielle (spectre).** NEWTOWT est **en avance sur les pairs
véliques** (Neoline, Grain de Sail, Windcoop) grâce à un catalogue de routes
publiques avec **capacité temps réel + prix + CO₂** et un **tracker de flotte
public** — choses qu'aucun vélique ne montre. Le **gap avec les géants conteneur**
tient en quatre briques identifiées et documentées : **devis instantané sans
compte, inscription tardive dans le tunnel, encaissement en ligne, jalons de suivi
automatiques**.

**État de maturité.** Le cœur ERP et la plateforme sont fonctionnels et riches ; la
**chaîne de preuve RSE** et l'**exploitation commerciale du tunnel** (conversion,
paiement, nurturing, multilingue réel) sont les chantiers prioritaires.

---

## 2. Vision, positionnement & proposition de valeur

### 2.1 Énoncé de vision (source : `docs/strategy/00-vision.md`)
Faire de `mynewtowt` **la plateforme unique** qui pilote la compagnie et permet aux
clients de **réserver, suivre, mesurer** le transport vélique de leurs marchandises,
avec l'exigence des leaders conteneur et l'ADN décarboné de la marque.

### 2.2 Différenciation V2 (TOWT) → V3 (NEWTOWT)

| Capacité | V2 | V3 |
|---|---|---|
| ERP collaborateurs | ✅ | ✅ enrichi |
| Portail expéditeur par token | ✅ (packing list seule) | ✅ + compte authentifié + dashboard |
| **Réservation d'espace en cale** | ❌ | ✅ **NOUVEAU** (planning + booking wizard) |
| **Compte client persistant** | ❌ | ✅ espace `/me` (MFA, factures, CO₂) |
| **Catalogue routes/legs publics** | ❌ partiel | ✅ moteur de recherche + capacité live |
| Rapports CO₂ par client | certificat unique | ✅ + dashboard cumulé + label Anemos |
| Suivi claims côté client | ❌ | ✅ |
| Documentation auto | packing list | ✅ packing + BL + facture + certificat |
| Chatbot IA (Kairos / Newtowt Agent) | 🟡 planifié | ✅ intégré (Claude) |
| Sécurité (MFA, audit, DLP) | 🟡 | ✅ renforcée |

### 2.3 Boussole produit (North Star)
**Nombre de palettes réservées par mois, par des clients récurrents, avec rapport
CO₂ téléchargé** — mesure l'adoption simultanée des trois capacités fondatrices :
réservation, fidélisation, transparence environnementale.

### 2.4 Les cinq promesses
1. **Transparence radicale** (position cargo, CO₂ évité visibles du client).
2. **Simplicité maritime** (quai → navire → cale → réservation en quelques écrans).
3. **Fiabilité commerciale** (capacité/ETA/prix promis = livrés ; tout écart tracé).
4. **Sécurité de l'information** (niveau grand compte : RGPD, audit, MFA).
5. **Continuité opérationnelle** (chaque action journalisée et rejouable).

### 2.5 Audiences & préfixes d'URL

| Audience | Préfixes | Auth | Rôle |
|---|---|---|---|
| **Staff / ERP** | `/planning`, `/escale`, `/cargo`, `/admin`… | cookie signé staff + MFA TOTP | exploitation maritime |
| **Client** | `/me`, `/booking` | cookie signé client (30 j) + MFA | réservation, factures, CO₂ |
| **Public** | `/`, `/routes`, `/devis`, `/contact` | anonyme | marketing, SEO, leads |
| **Portail expéditeur** | `/p/{token}` | token UUID 90 j (jamais en clair) | packing list, docs, messagerie |

---

## 3. Contexte sectoriel & paysage concurrentiel (le « spectre »)

### 3.1 Secteur du transport vélique décarboné (état 2026)
Niche en **consolidation** (un pionnier a fait défaut en 2026 — la vitesse de
conversion prospect→cash est un enjeu de survie, pas d'optimisation). NEWTOWT s'y
distingue par **15 ans d'exploitation** et des **données opérationnelles longues**.

- **Flotte** : voiliers‑cargo (Anemos, Artemis…), classe TSC 80 ; capacité de cale
  exprimée en palettes EPAL ; vitesse typique 7–9 nœuds ; routes Europe (Le Havre,
  Fécamp) ↔ Amériques (New York, Boston, São Sebastião) ↔ Açores (Ponta Delgada).
- **Argument CO₂** : facteur **1,5 g CO₂/t·km** (vélique) contre **13,7 g** (référence
  conventionnelle, « IMO Fourth GHG Study ») → réduction revendiquée **≈ 89–95 %**.

### 3.2 Pairs véliques (concurrence directe)

| Acteur | Ce qu'il montre publiquement | Leçon pour NEWTOWT |
|---|---|---|
| **Neoline** (Neoliner Origin, 136 m, −80 %) | Marque B2B forte, **logos de références clients** | La preuve sociale est le nerf de la guerre — NEWTOWT n'en affiche aucune |
| **Grain de Sail** | Marque B2C/B2B intégrée, **storytelling produit** | Le carnet/blog vide est un actif gâché |
| **Windcoop** | Transparence coopérative, ligne Marseille–Madagascar | Sur la transparence prix/capacité, NEWTOWT est **déjà devant** |

### 3.3 Benchmark vs géants conteneur (Maersk, CMA‑CGM, MSC, Hapag‑Lloyd, ZIM)

| Capacité (standard leaders) | NEWTOWT — état réel |
|---|---|
| Devis instantané **sans compte** (Hapag Quick Quotes, Maersk Spot) | ❌ compte obligatoire dès l'étape 1 du wizard |
| Prix ferme + garantie de chargement (Maersk Spot) | 🟡 prix indicatif, confirmation manuelle « < 4 h » non mesurée |
| Recherche routes/horaires publique | ✅ **au‑dessus du standard** : montre la **capacité restante** |
| Paiement en ligne / compte courant | ❌ facture PDF + virement non suivi (Stripe retiré en V3.1) |
| Tracking conteneur temps réel (myMSC, Maersk Hub) | 🟡 carte navire réelle ✅, mais **jalons booking avancés à la main** |
| Dashboard émissions (Maersk Emissions Studio, ISO 14083) | 🟡 cumul + certificats ✅ ; **pas d'export annuel consolidé**, ISO 14083 absent |
| API B2B + webhooks (EDI/API) | ❌ API v1 **lecture seule**, pas de création de booking, pas de webhooks |
| Alertes proactives (retard/ETA — ZIM ZIMonitor) | ❌ ETA shift = notification interne staff uniquement |

**Lecture d'ensemble.** NEWTOWT a déjà construit ce que les véliques n'ont pas. Le
gap « grands acteurs » se résume aux **4 briques** : devis sans compte · inscription
tardive · encaissement · jalons automatiques.

### 3.4 Réglementation & conformité (contexte d'achat B2B)
- **EU MRV** (UE 2015/757) : reporting d'émissions maritimes ; export **DNV
  Veracity** (format CSV) — implémenté en interne (module MRV).
- **GHG Protocol Scope 3, cat. 4** ; **CSRD / ESRS E1** ; **ISO 14083:2023 / GLEC**
  (standard cité par les acheteurs ; **absent** aujourd'hui) ; **Bilan Carbone® /
  Base Empreinte ADEME**.
- **RGPD** (UE 2016/679) : DPO `dpo@newtowt.eu`, droits d'accès/portabilité/oubli.
- **SOLAS / ISM / ISPS** : check‑lists et traçabilité audit (à bord).
- **Directive Green Claims** (anti‑greenwashing) : le « label Anemos » étant
  aujourd'hui une **auto‑déclaration** (pas de registre public ni de vérificateur
  tiers nommé), c'est un **risque réputationnel** à traiter.

---

## 4. Marque, marketing & preuve environnementale

### 4.1 Charte « Nouvelle Étoile » (identité)
Source de vérité : `docs/design/newtowt-design-tokens.json`. Palette **60/20/10/10** :

| Couleur | Hex | Variable | Rôle | Ratio |
|---|---|---|---|---|
| Teal NEWTOWT | `#0D5966` | `--teal` | dominante (titres, structures, sidebar staff) | 60 % |
| Vert NEWTOWT | `#87BD29` | `--vert` | succès, baseline, signal CO₂/éco | 20 % |
| Cuivre NEWTOWT | `#B47148` | `--cuivre` | préfixe « NEW », transition, warnings | 10 % |
| Sable NEWTOWT | `#EFE6D6` | `--sable` | fonds éditoriaux, citations | 10 % |

**Polices** : **Manrope** (UI + corps), **DM Serif Display** (accents éditoriaux),
**JetBrains Mono** (codes leg `1CFRBR6`, MMSI, IMO, LOCODE, heures UTC — copiables).
**Principes** : maritime (rose des vents), sobriété B2B (pas d'effets gratuits),
lisibilité réglementaire (mono), vert = signal environnemental.

### 4.2 Argumentaire environnemental & label Anemos
**Forces (transparence rare dans le secteur)** :
- Page **`/about/anemos`** (bilingue FR/EN) : publie le **facteur 1,5 g CO₂/t·km**,
  la référence **13,7 g**, **la formule** `(13,7 − 1,5) × tonnage × distance ÷ 1000`,
  un exemple chiffré (1,2 t de vin, 3 200 NM → 86,7 kg évités).
- **Éco‑calculateur** sur chaque fiche route ; **CO₂ visible à chaque étape** du
  tunnel (cartes routes, fiche, étape 1 du wizard).
- **Certificat Anemos émis automatiquement au débarquement** (idempotent, un seul
  par booking) — **différenciant majeur** du parcours.

**Faiblesses à corriger (chaîne de preuve)** :
- **Chiffres incohérents** (« −95 % » landing vs « −89 % » fiches/certificat).
- **Certificat = facteur forfaitaire × distance** (repli **3 000 NM** si ports mal
  référencés) **jamais réconcilié** avec le **fuel réellement consommé** (noon
  reports / MRV) ni la **route réelle** (positions satcom). Données mesurées
  collectées mais **non utilisées** par le certificat.
- **Référentiels manquants** : ISO 14083 / GLEC non cités ; périmètre **WtW/TtW** non
  précisé ; tout en **CO₂**, jamais **CO₂e**.
- **« Label » sans tiers** : pas de vérificateur nommé, ni n° vérifiable, ni
  registre → renommer en « certificat d'émissions évitées » **ou** constituer le
  label (référentiel publié + audit tiers).
- **Pas d'export RSE annuel consolidé** (la North Star « rapport CO₂ téléchargé »
  n'a pas d'objet annuel à télécharger).

### 4.3 Stratégie data / analytics (source : `docs/analytics/01-data-strategy.md`)
- **Volumétrie** : legs ~200/an ; positions navires ~500 k points/an ; bookings
  5–20 k/an ; SOF events ~50 k/an.
- **Architecture** : Postgres unique, schémas `public` (OLTP) + `analytics` (OLAP) ;
  pas de data‑warehouse externe (volume insuffisant). Outils cibles : dbt, Metabase,
  OpenTelemetry, **pgvector** (embeddings RAG du chatbot).
- **Dashboards** : exécutif, commercial, opérations, finance, MRV/CO₂, RH, client.

---

## 5. Audiences & personas

### 5.1 Personas internes (8 rôles métier) — `docs/personas/01-personas.md`

| Persona | Rôle système | Quotidien & apport V3 |
|---|---|---|
| **Mathilde**, capitaine | `marins` (+captain) | État leg en 30 s, **noon report offline**, météo embarquée ; **Onboard 4 espaces** + PWA + mode haute lisibilité |
| **Tomé**, agent d'escale | `operation` | Démarre l'escale à l'ATA, coordonne dockers/douane ; **escale split Import/Export**, **ticketing kanban SLA**, mobile quai |
| **Khadija**, RH | `armement`/`rh` | Calendrier d'embarquement, **compliance Schengen**, congés/paie ; **module RH/SIRH**, export PAF, alertes |
| **Pierre**, superintendant | `technique` | Maintenance, certifs ISM/SOLAS, claims hull ; registre certifs + ticketing technique |
| **Inès**, commerciale | `commercial` | Vend la capacité, pipeline Pipedrive, cotations ; **backoffice booking**, dashboard funnel, pricing par leg |
| **Manager maritime** | `manager_maritime` | Vue flotte, KPI, escalades ; **dashboard exécutif**, rollup finance, variance |
| **Data analyst** | `data_analyst` | KPI/finance/MRV ; accès **ciblé** CO₂/émissions (hors admin global), facteurs versionnés |
| **Administrateur** | `administrateur` | Users, navires, audit, sécurité ; éditeur de matrice RBAC, activity logs, dashboard sécurité |

### 5.2 Personas externes (clients / prospects)

| Profil | Besoin | Apport V3 |
|---|---|---|
| **David**, prospect (importateur vin) | comprendre l'offre, simuler, comparer au conteneur | landing, **recherche sans inscription**, devis invité, calculateur CO₂, chatbot (staff only aujourd'hui) |
| **Léa**, cliente occasionnelle (brasserie) | réserver vite, récupérer BL/facture/CO₂ | **dashboard `/me`**, nouvelle résa rapide, téléchargements 1 clic |
| **Yann**, grand compte logistique | réserver via API, webhooks, reporting CO₂ annuel | API v1 (lecture seule **aujourd'hui**), reporting (webhooks **au backlog**) |

### 5.3 Parcours de référence (golden paths)
- **UC‑01** Prospect réserve sa 1ʳᵉ palette (cible < 8 min).
- **UC‑03** Décaler un ETD de 24 h → **cascade** ETA aval + escale + booking + notif
  clients (+ ticket P2 si > 12 h).
- **UC‑05** Générer le rapport CO₂ annuel (filtrer l'année → PDF WeasyPrint).
- **UC‑07** Capitaine saisit un noon report **offline** → IndexedDB → sync au retour
  du wifi.

---

## 6. Cartographie fonctionnelle complète

> Convention : **route racine** · *ce que l'utilisateur fait* · **entités** · règles
> notables. « ✅ » = comportement implémenté sur cette branche.

### 6.1 Audience PUBLIC (vitrine, SEO, leads, devis)

- **Vitrine** `/` · landing (6 prochaines traversées réservables), `/flotte`
  (classe TSC 80), `/impact` (qualité cargo : cales à température de mer, ventilation,
  capteurs **LACOE©**), `/navigation`, `/about` + `/about/anemos` (méthodo CO₂),
  `/presse`, `/recrutement`, `/passagers`, `/actualites` + `/carnet` (blog, **vides**
  aujourd'hui), pages légales (`/about/legal|privacy|terms`).
- **Catalogue routes** `/routes` (filtres pays/dates → legs `is_bookable`, ETD futur,
  50 max) · `/routes/{leg_code}` (timeline POL→POD, agent portuaire, carte, cut‑off,
  éco‑calculateur, CTA réserver) · `/fleet` (**tracker public** des positions réelles).
- **Devis public** `/devis` (GET/POST, `/devis/{ref}` + `.pdf`) · simulateur
  POL/POD + lignes palettes + tonnage + dangereux ; **honeypot** + **rate‑limit
  10/30 min/IP** ; résolution de **grille** (client → défaut), brackets dégressifs,
  surcharge dangereux **+25 %** ; persistance `Quote` (réf. `DEV‑YYYY‑…`) ;
  **historique de consultation** `QuoteView` ; cookie `towt_pending_quote` (2 h)
  pour pré‑remplir le booking.
- **Contact / leads** `/contact` → `ContactRequest` (statut `new`) + relais
  **best‑effort** : **Pipedrive** (org + deal, pipeline « Deals from web ») +
  **notification in‑app** (rôle commercial) + **email** (`commercial_inbox_email`).
- **SEO / IA** : `robots.txt` (autorise explicitement GPTBot, Claude, PerplexityBot…
  ; bloque `/admin /me /booking /p /api`), **`llms.txt`** (facts pour LLM),
  `sitemap.xml` (hreflang fr/en/es/pt‑br), **JSON‑LD** (Organization, Service,
  FAQPage, BreadcrumbList).

### 6.2 Audience CLIENT (`/me`) + BOOKING (`/booking`)

- **Auth client** : `/me/login` (rate‑limit 10/10 min, **anti‑énumération** :
  message unique + bcrypt fictif), `/me/register` (mot de passe ≥ 12, auto‑linking
  au `commercial_client` par email), **MFA TOTP** (`/me/login/mfa`, codes de
  récupération `xxxx‑xxxx‑xxxx`, **trusted device 24 h**, alerte email nouvel
  appareil).
- **Dashboard `/me`** : bookings récents, compteur d'actifs, **CO₂ évité cumulé**
  (Σ certificats), notifications non lues.
- **Bookings `/me/bookings/{ref}`** : détail (vérif. propriété), **messagerie**
  client↔staff, upload/download de documents (customs/msds/other), localisation
  stowage **propre à la PL** (jamais l'occupation globale → confidentialité).
- **Suivi `/me/track/{ref}`** : timeline des statuts + **position live** du navire.
- **Certificats `/me/anemos`** : liste des labels Anemos par expédition ;
  **`/me/anemos/report/{year}.pdf|.csv`** = rapport RSE annuel (mention « valorisable
  Bilan Carbone® scope 3 cat. 4 »).
- **`/me/invoices`** : page **explicative** — facturation **hors plateforme** par
  virement (modèle `ClientInvoice` **dormant**, arbitrage A5).
- **Wizard `/booking` (3 étapes)** : (1) choix du leg réservable ; (2) cargaison
  (lignes palettes format/poids/empilable/dangereux + adresses pickup/delivery,
  pré‑remplissage devis) ; (3) récap + CGV → `Booking` `submitted` (réf.
  `BK‑YYYY‑…`) + `PackingList` (token 90 j) + notif/email « confirmation sous 4 h ».
- **Cycle de vie booking** : `submitted → confirmed → loaded → at_sea → discharged
  → delivered` (ou `cancelled`). Chaque transition → notif in‑app + email
  best‑effort. À `discharged` → **émission auto du certificat Anemos**.
- **Capacité** : dispo = `capacity_palettes − Σ réserves` (bookings + commandes,
  coefficients format EPAL 1.0 / USPAL 1.2…) ; **verrou pessimiste** au `confirm()`
  contre les courses. Note : les `draft` ne gèlent pas la capacité (arbitrage à
  documenter commercialement).

### 6.3 Audience PORTAIL EXPÉDITEUR (`/p/{token}`)

- **Token** : UUID hex 24 car., **90 j** (`410 Gone` si expiré), **rate‑limit
  60/10 min/IP**, **jamais loggé en clair** (SHA‑256 dans `portal_access_logs` avec
  IP/UA/chemin).
- **Packing list** : home (récap commande/booking, navire, voyage, repérage à bord),
  CRUD **batches** (shipper/consignee/notify + adresses structurées + marchandise +
  HS code + IMDG + poids), **complétude %** (champs requis pour le BL).
- **Audit field‑by‑field** (`PackingListAudit` : acteur, champ, ancienne/nouvelle
  valeur) sur chaque modification, **import/export Excel** (limite 5 Mo,
  anti‑injection de formule).
- **Documents** (upload customs/msds/other, `safe_files`), **messagerie sécurisée**
  (`PortalMessage`), **suivi voyage** (3 phases + position satellite), **fiche
  navire**, **multilingue** fr/en/es/pt‑br/vi.

### 6.4 ERP STAFF — modules (route racine · synthèse comportementale)

1. **Planning `/planning`** — Gantt + table + carte ; CRUD legs avec **suggestions**
   (ETD = ATA/ETA du dernier leg + escale ; POL = POD précédent ; glissement si port
   fermé le WE) ; **détection de conflits portuaires** ; **cascade de dates** (legs
   aval + escale + commandes + notif clients) ; **scénarios what‑if** (drag‑drop,
   comparaison au réel, export CSV) ; **partage public par token** (sélection
   leg‑à‑leg, langue, expiration, compteur d'accès) ; **brochure commerciale PDF**
   FR/EN. *Entités :* `Leg`, `PlanningScenario`, `PlanningShare`.

2. **Commercial `/commercial`** — clients FF/Shipper (CRUD + désactivation, sync
   Pipedrive) ; **grilles tarifaires** (`RG‑YYYY‑…`, OPEX → `base_rate = OPEX × jours
   nav ÷ 850 palettes`, brackets dégressifs, surcharge dangereux, options
   per_palette/tonne/booking, **une seule grille active** par client) ; **offres**
   (`RO‑YYYY‑…`) ; **commandes** (`ORD‑YYYY‑…`, affectation → leg filtrée par route
   + alerte hors délai, **ventilation multi‑legs du CA**) ; dashboard funnel.
   *Entités :* `commercial_clients`, `rate_grids/_line/_option`, `rate_offers`,
   `commercial_orders`, `order_assignment`, `quotes`.

3. **Cargo / Packing / BL `/cargo`** — bookings « émissibles » → générateurs **BL
   (PDF + DOCX)**, packing list, facture, **certificat Anemos**, booking note ;
   numérotation BL `TUAW_{leg}_{seq}` anti‑doublon ; **Arrival Notice** ; **audit
   immuable** des batches ; messagerie portail. *Entités :* `packing_lists`,
   `packing_list_batch/_document/_audit`, `cargo_documents`, `anemos_certificate`.

4. **Escale `/escale`** — opérations portuaires IMPORT/EXPORT/BOTH (planned/actual,
   coûts prévus/réels) ; **shifts dockers** (cadence pal/h + écart %) ; **pilotage
   statut ATA/ATD** idempotent + recalcul OPEX + notifications **EOSP/SOSP** ;
   couplage **op ↔ équipage** + **PAF auto** (ports FR) ; **sélecteur de fuseau** ;
   **verrouillage** d'escale (+ SOF auto `ESCALE_COMPLETED`). *Entités :*
   `escale_operations`, `docker_shifts`.

5. **Onboard / Captain `/captain` + `/carnet-bord`** — **SOF** (EOSP/SOSP/NOR/pilot…
   ) avec **signature/lock** (hash SHA‑256) ; **ETA shifts** (9 motifs codifiés) ;
   **documents cargo** structurés (NOR/LOP/Mate's Receipt, signataire choisi dans
   l'équipage) ; **messagerie de bord** (@mentions, `@MYTOWT_BOT`) ; **Noon Report
   officiel TOWT** + ROB chaîné + **PWA offline** + journal de quart ; **Carnet de
   bord ANEMOS** (highlights/photos). Hooks **best‑effort** : SOF → MRV, SOF
   départ/arrivée → ATD/ATA + statuts booking. *Entités :* `sof_events`,
   `noon_reports` (+ engine/weather/sail/hold), `watch_logs`, `eta_shifts`,
   `cargo_documents`, `on_board_message`, `voyage_highlights/_photos`.

6. **Crew `/crew`** — fiche marin (visa US/BR, seaman book, nationalité) ;
   **affectations** (anti‑chevauchement, **embarquement hors leg autorisé** —
   `leg_id` nullable) ; **billets de transport** ; **compliance Schengen** réelle
   (90/180 j persisté, garde‑fou override) ; **Crew List PAF** PDF bilingue ;
   **armement réglementaire** (rôles requis vs présents) ; **sync Marad lecture
   seule**. *Entités :* `crew_members`, `crew_assignments`, `crew_certifications`,
   `crew_leaves`, `crew_ticket`, `marad_crew_schedule`.

7. **Stowage `/stowage`** — plan d'arrimage **18 zones** (3 ponts × 2 cales × 3
   blocs), **algo glouton** de placement, réaffectation/retrait d'item, **évaluation
   capacité/poids/IMDG**, **politique de blocage configurable par zone** (avertir vs
   bloquer dur — arbitrage A3), vue **SVG top‑down**, référentiel **IMDG bilingue**,
   localisation cargo pour claims. *Entités :* `stowage_plans`, `stowage_items`,
   `stowage_zone_spec`.

8. **Claims `/claims`** — sinistres (`CLM‑YYYY‑…`, types cargo/crew/hull/war/third) ;
   workflow **6 statuts** (`open → in_review → provisioned → settled/rejected →
   closed`) ; **historique de provision** + **timeline** immuable ; **SOF auto**
   `CLAIM_DECLARED` + notification gestionnaire ; auto‑résolution de la position cale.
   *Entités :* `claims`, `claim_documents/_timeline_entry/_provision_history`.

9. **MRV `/mrv`** (émissions UE) — **hybride** (arbitrage A1) : noon auto **+ 4
   compteurs DO** de contrôle ; calcul ME/AE/**ROB chaîné** ; **contrôle qualité
   multi‑règles** (verrouillé par tests) ; **export DNV Veracity 18 colonnes** ;
   **Carbon Report PDF** par leg (intensités /NM, /t, /t·nm, CO₂ évité) avec blocage
   qualité ; position **DMS** auto depuis GPS ; **facteur CO₂ versionné** (`/admin/co2`,
   facteur MDO ≈ 3,15 t CO₂/t). *Entités :* `mrv_events`, `mrv_parameters`,
   `co2_variable`.

10. **Navigation `/performance/navigation`** — multi‑legs/multi‑navires : carte (1
    couleur/leg, points GPS + trait + route théorique), **tableau comparatif**
    (réelle/théorique/écart/durée/restant, **allongement**, SOG), **météo le long du
    trajet** + blocs « conditions actuelles » (rose des vents, Beaufort, pression,
    visibilité, T°). Filtre anti‑saut > 50 NM. *Entités :* `vessel_position`,
    `VesselWeatherHistory`.

11. **Finance `/finance`** — **prévisionnel/réel** par poste sur `LegFinance` (CA,
    portuaire, quai, OPEX mer, autres) + marge + **écarts** (arbitrage A2) ; **export
    CSV** ; **NOx/SOx évités** ; **rollup** par navire/période ; onglet
    **Exploitation** (écart ETD→ATD, vitesse, durée) ; **CRUD OPEX** ; **détail
    assurance** (provision/indemnité/franchise). *Entités :* `leg_finance`,
    `opex_parameter`, `port_config`, `insurance_contract`.

12. **KPI `/kpi`** — `LegKPI` auto‑alimenté (tonnage, distance, durée, vitesse,
    on‑time, occupancy, **CO₂ évité/émis**) ; **équivalences pédagogiques** ; **vue
    consolidée** (commerce/flotte/env/exploitation) ; **Carbon Report par leg**.
    *Entités :* `leg_kpi`, `co2_variable`.

13. **Tickets `/tickets`** — kanban (`open → in_progress → resolved → closed`),
    priorités **P1/P2/P3** avec **SLA** codifiés, **escalade** au manager si dépassé
    (cron externe `/api/tickets/escalate-sla`). *Entités :* `tickets`,
    `ticket_comments`.

14. **Cashbox `/cashbox`** — caisse de bord par navire, **multi‑devise** (EUR/USD/VND…
    ), mouvements income/expense + reçus, **clôture mensuelle** (verrou + export CSV +
    comptage), balances temps réel. *Entités :* `onboard_cashbox`, `cashbox_movement`,
    `cashbox_closure`.

15. **RH / SIRH `/rh`** — **congés marins** (`CrewLeave`) **unifiés** avec absences
    sédentaires (`HrAbsence`) ; dossiers **employés** (CRUD + import CSV) ; **contrats
    & avenants** + alertes ; **EVP** + verrouillage de période + **export Silae CSV**
    (journal de lots) ; **coffre‑fort bulletins** ; **entretiens** ; **reporting RH**
    (turnover, ancienneté, pyramide des âges) ; self‑service `/rh/moi`. *Entités :*
    `employees`, `employment_contract`, `hr_absence`, `hr_review`,
    `payroll_variable`, `payslip`, `silae_export_batch`, `crew_leaves`.

16. **Veille `/veille`** — flux **NewsData.io** (sources configurables : query/pays/
    langues/catégorie, **ciblage par rôle**), **scoring de pertinence** (IA si clé,
    sinon heuristique), **digest quotidien** IA, refresh cron. *Entités :*
    `news_sources`, `news_items`, `news_digest`.

17. **Chat Kairos AI / Newtowt Agent `/chat`** — assistant **Claude** (staff),
    **toggle admin** (feature flag → 403 si off), **détection d'injection**, **outils
    read‑only** (recherche leg/booking, position navire, legs actifs) avec **re‑check
    de permission par outil**, **token/coût** tracés. *Entités :* `chat_conversation`,
    `chat_message`.

18. **Admin `/admin`** — users (CRUD, rôles, `must_change_password`, import Excel),
    **éditeur de matrice de permissions** (overrides DB), maintenance, **activity
    logs**, **CO₂** (facteur versionné, NOx/SOx), OPEX, assurance, navires,
    **exports/purges DB whitelistés**, dashboard sécurité. *Entités :* `users`,
    `role_permission`, `activity_log`, `feature_flag`, `insurance_contract`, …

---

## 7. Architecture technique & exécution

### 7.1 Stack
FastAPI 0.115 / Python 3.12 / Uvicorn · SQLAlchemy 2 **async** (`Mapped[]`) +
asyncpg · **PostgreSQL 16** · Alembic · **HTMX 2 + Alpine.js (léger) + Jinja2 SSR**
· design system **Kairos** (CSS maison) · Lucide (CDN) · cookies signés
`itsdangerous` + bcrypt + **MFA TOTP** · **MapLibre GL** + Mapbox/MapTiler · météo
**Open‑Meteo** (défaut) / **Windy** (option) · IA **Anthropic/Claude** · **WeasyPrint**
(PDF) + `python-docx` + `openpyxl` · OpenTelemetry + Prometheus + Sentry · Docker +
docker‑compose + **Caddy** (TLS Let's Encrypt).

### 7.2 Bootstrap (`app/main.py` → `create_app`)
**Ordre des middlewares** (externe → interne) : `CORS` → `SecurityHeaders` →
`Maintenance` → `CSRF` → `ForcePasswordChange` → `ForceMfaForAdmin`. **~40 routers**
montés (public/vitrine, auth staff/client, ERP phases 1–4, veille, admin, API).
Endpoints `GET /health` et `/.well-known/security.txt`. **Startup** :
`enforce_production_safety()` (refus si secret faible / mot de passe DB par défaut)
puis `init_db()` (`create_all` **dev seulement** ; prod = Alembic).

### 7.3 Configuration (`app/config.py`) — variables clés

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_ENV` | development | `production` active les refus de démarrage |
| `SECRET_KEY` | requis | ≥ 32 car., refusé si faible |
| `DATABASE_URL` | requis | doit être `postgresql+asyncpg://` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 480 | session staff **8 h** (marins/manager_maritime : **14 j**) |
| `CLIENT_SESSION_DAYS` | 30 | session client persistante |
| `REQUIRE_MFA_FOR_ADMIN` | True | force la mise en place MFA admin |
| `UPLOAD_DIR` | var/uploads | stockage des PJ |
| `ANTHROPIC_API_KEY` | None | chatbot + digest veille (off si absent) |
| `PIPEDRIVE_API_TOKEN` / `…PIPELINE_NAME` | None / Deals from web | sync CRM leads/deals |
| `WINDY_API_KEY` | None | météo (fallback Open‑Meteo) |
| `MAPBOX_TOKEN` / `MAPTILER_TOKEN` | None | tuiles carto (préf. MapTiler) |
| `PUBLIC_API_KEY` | None | `/api/v1/*` (**503 si absent**) |
| `TRACKING_API_TOKEN` | None | `POST /api/tracking/*` (Power Automate, 503 si absent) |
| `WEATHER_API_TOKEN` | None | cron `POST /api/weather/refresh` (snapshot Windy 30 min) |
| `VEILLE_API_TOKEN` / `NEWSDATA_API_KEY` | None | veille (cron `POST /api/veille/refresh`) |
| `TICKETS_SLA_API_TOKEN` | None | cron `POST /api/tickets/escalate-sla` |
| `MARAD_*` | base/token/header | sync crew **lecture seule** (no‑op sans token) |
| `SMTP_*`, `COMMERCIAL_INBOX_EMAIL` | None | email transactionnel + routage leads |
| `SENTRY_DSN`, `OTEL_…`, `PROMETHEUS_METRICS` | — / — / True | observabilité |

> **Secure‑by‑default :** tous les endpoints cron renvoient **503** quand leur token
> n'est pas configuré. **Stripe retiré (V3.1)** — facturation par **virement**,
> aucun paiement traité par l'app.

### 7.4 Patterns & invariants de comportement (contrats — à supposer vrais)
- **DB** : `get_db()` = **auto‑commit en succès / rollback en exception**. Une route
  fait **`await db.flush()`**, **jamais `commit()`**. **Decimal** pour
  monétaire/poids/intensités. Schéma prod = **Alembic seul**, migrations **additives**.
- **Mutations** : `validate → modify → flush → RedirectResponse(303)`. HTMX :
  `hx-request` → header **`HX-Redirect`** ; toasts/modales via **`HX-Trigger`**.
- **RBAC** : chaque endpoint protégé porte
  **`Depends(require_permission(module, "C"|"M"|"S"))`** ; pré‑charge le compteur de
  notifications (fail‑soft).
- **CSP stricte** : **aucun `<script>`/`onclick` inline**, JS 100 % externe ;
  confirmations destructives via `data-confirm`.
- **Audit** : `services.activity.record()` sur **toute écriture staff** (append‑only).
- **Uploads** : `safe_files` (extension + taille + magic number + nom aléatoire +
  anti‑traversal) ; pré‑filtre `Content-Length` → 413.
- **Exports tableur** : `csv_safe` neutralise l'injection de formule (CSV + XLSX).
- **SQL dynamique** : jamais de f‑string sur identifiant → whitelist + `bindparams()`.
- **Templates** : `base.html` → layout par audience (`staff/`, `client/`, `public/`,
  `portal/`). Filtres `|money`, `|flag`, `|date` ; helper `t(key, lang)`.

---

## 8. Modèle de données (~95 tables, ≈52 modules de modèles)

> Regroupement par domaine (rôle en une ligne). Source : `app/models/*`.

- **Auth & sécurité** : `users` (staff, 9 rôles, MFA) · `client_accounts` (clients,
  segment occasional/recurring/key_account) · `known_devices` (fingerprint SHA‑256,
  polymorphe) · `mfa_recovery_codes` (hachés, single‑use) · `role_permission`
  (overrides RBAC) · `activity_log` (audit append‑only, PII masquée).
- **Planning / navigation** : `vessels` · `ports` · `legs` (ETD/ETA/ATD/ATA, statut,
  capacité, verrous escale/clôture) · `planning_scenario` · `planning_share`.
- **Booking / client** : `bookings` (workflow 7 statuts) · `booking_items` ·
  `booking_message` · `client_invoice` (**dormant**).
- **Cargo / packing** : `packing_lists` (`order_id` XOR `booking_id`) ·
  `packing_list_batch/_document/_audit` · `portal_access_log` (token SHA‑256).
- **Commercial / pricing** : `commercial_clients` · `rate_grids/_line/_option` ·
  `rate_offers` · `commercial_orders` · `order_assignment` · `quotes` (+ `quote_view`).
- **Escale** : `escale_operations` · `docker_shifts`.
- **Crew / RH** : `crew_members` · `crew_assignments` · `crew_certifications` ·
  `crew_leaves` · `crew_ticket` · `marad_crew_schedule` · `employees` ·
  `employment_contract` · `hr_absence` · `hr_review` · `payroll_variable` ·
  `payslip` · `silae_export_batch`.
- **Capitaine / bord** : `noon_reports` (+ `_engine/_weather/_sail/_hold`) ·
  `watch_logs` · `onboard_checklist` · `visitor_log` · `sof_events` ·
  `cargo_documents` · `eta_shifts` · `on_board_message` (+ mentions).
- **Stowage** : `stowage_plans` · `stowage_items` · `stowage_zone_spec`.
- **Claims** : `claims` · `claim_documents/_timeline_entry/_provision_history` ·
  `vessel_position` (tracking GPS).
- **Finance / KPI / MRV** : `leg_finance` · `leg_kpi` · `opex_parameter` ·
  `port_config` · `insurance_contract` · `mrv_events` · `mrv_parameters` ·
  `co2_variable` (facteur **versionné** : `effective_date`, `is_current`).
- **Contenu / veille** : `blog_posts` (carnet/actualité) · `voyage_highlights` ·
  `voyage_photos` · `anemos_certificate` · `news_sources` · `news_items` ·
  `news_digest`.
- **Divers** : `contact_requests` · `tickets` · `ticket_comments` ·
  `onboard_cashbox` · `cashbox_movement` · `cashbox_closure` · `notifications` ·
  `feature_flag` · `rate_limit` · `chat_conversation` · `chat_message`.

---

## 9. Sécurité, authentification & permissions

### 9.1 Authentification (`app/auth.py`)
Deux contextes **indépendants**, cookies signés `itsdangerous` (salt distinct) :
**staff** (`towt_session`, 8 h ; **14 j** pour `marins`/`manager_maritime`) et
**client** (`towt_client_session`, 30 j). Cookies MFA intermédiaires 5 min,
**trusted device** 24 h. Hash **bcrypt** (`passlib`).

### 9.2 RBAC (`app/permissions.py`) — **9 rôles × 17 modules × {C,M,S}**
- **Rôles** : `administrateur`, `operation`, `armement`, `technique`,
  `data_analyst`, `marins`, `commercial`, `manager_maritime`, `rh` (+ alias legacy :
  admin→administrateur, manager/operator→operation, viewer→data_analyst).
- **Modules** : planning, commercial, escale, cargo, finance, kpi, captain, crew,
  claims, mrv, **rh**, booking, tickets, analytics, chat, **veille**, admin.
- **Niveaux** : `C` (Consult) < `M` (Modify) < `S` (Suppress). Cellule ∈
  `""|C|CM|CMS`.
- **Mécanique** : matrice codée en dur `_MATRIX` = **défaut**, surchargée par des
  **overrides en base** (`role_permission`, écran `/admin/permissions`, cache 60 s).
  Le chemin requête (`require_permission`) lit la **matrice effective** ; toute
  erreur DB **retombe fail‑closed sur `_MATRIX`**. Garde‑fou : `(administrateur,
  admin)` toujours forcé au défaut (jamais de lock‑out). Les helpers synchrones
  (`has_permission`, `can_view/edit/delete`) servent **l'affichage** (sidebar, UI),
  **pas** le contrôle d'accès.
- **Arbitrages RBAC** : `data_analyst` a `finance`/`analytics` = CMS mais **pas**
  d'accès admin ; `rh` écrit sur `rh`, consulte ailleurs, **pas** d'accès finance par
  défaut. RBAC **au niveau module** (pas de scoping par objet) — assumé.

### 9.3 Surfaces de sécurité (garanties)

| Surface | Comportement |
|---|---|
| **CSRF** | double‑submit cookie `towt_csrf` (lisible JS) ; HTMX auto via `csrf-htmx.js` ; exempt `/api/*` (token), `/health`, `/metrics` |
| **CSP** | `default-src 'self'` ; script `'self' + unpkg` ; pas d'inline ; carto whitelistée (mapbox/maptiler/maplibre/nominatim) ; HSTS 1 an, `X‑Frame‑Options SAMEORIGIN`, `object-src 'none'` |
| **MFA** | TOTP (pyotp, fenêtre ±1), QR via `segno` (CSP‑safe), 10 recovery codes SHA‑256 single‑use, activation anti‑lock‑out |
| **Device** | fingerprint SHA‑256 (UA tronqué + IP /24/48) → alerte email nouvel appareil |
| **Portail token** | UUID 24 car. 90 j, jamais en clair (SHA‑256), `portal_access_logs`, rate‑limit 60/10 min |
| **API v1** | `X‑API‑Key` constant‑time, **fail‑closed 503** sans clé |
| **Uploads** | `safe_files` (ext+taille+magic+nom aléatoire+anti‑traversal), 413 pré‑lecture |
| **Audit** | `activity_logs` append‑only, PII masquée (`a***@domain`) |
| **Maintenance** | marqueur `/tmp/.maintenance` → 503 (bypass admin/health/static) |
| **Prod safety** | refus de démarrer si secret/mot de passe DB faible |

### 9.4 Chatbot — sécurité IA
Modèle `claude-sonnet-4-6`, max 1024 tokens out, **détection d'injection** (refuse
« ignore previous », « system: »…), **outils read‑only** avec **re‑vérification de
permission par appel** (le LLM n'est jamais de confiance), coût input/output tracé.

---

## 10. Intégrations externes & contrats

| Intégration | Sens | Contrat / dégradation |
|---|---|---|
| **Anthropic / Claude** | sortant | chatbot + digest veille ; off si pas de clé |
| **Open‑Meteo** | sortant | météo marine **par défaut** (sans clé) |
| **Windy** | sortant | météo premium (option) ; fallback Open‑Meteo |
| **Power Automate — tracking** | entrant | `POST /api/tracking/*` (`X‑API‑Token`) ; CSV/ZIP/XLSX tolérant ; 503 sans token |
| **Power Automate — météo** | entrant (cron 30 min) | `POST /api/weather/refresh` → `vessel_weather` |
| **Power Automate — veille** | entrant (cron) | `POST /api/veille/refresh` → NewsData.io |
| **Power Automate — SLA tickets** | entrant (cron) | `POST /api/tickets/escalate-sla` |
| **NewsData.io** | sortant | veille (dédup `external_id`/SHA‑256) ; 503 sans clé |
| **Pipedrive** | sortant | leads/devis → org + Deal (pipeline par nom) ; no‑op sans token |
| **Marad (MaraSoft)** | entrant **lecture seule** | sync crew (réconciliation `marad_id`) ; champs ERP jamais écrasés ; no‑op sans token |
| **Silae** | sortant | export EVP CSV (paie) ; Silae reste maître des données sensibles |
| **MapLibre + Mapbox/MapTiler + Nominatim** | sortant | tuiles + géocodage ; UI graceful sans token |
| **SMTP** | sortant | email transactionnel + alertes sécurité + routage leads ; no‑op si absent |
| **Sentry / OpenTelemetry / Prometheus** | sortant | observabilité (Prometheus on par défaut) |
| **API publique v1** | sortant (B2B) | `/api/v1/*` **read‑only**, `X‑API‑Key`, **fermée sans clé** ; pas de création booking, pas de webhooks |

---

## 11. Design system « Kairos » & front

- **Tokens** (`tokens.css`, source `newtowt-design-tokens.json`) : palette §4.1,
  échelle d'espacement base 4 px, rayons 2/4/8 px, ombres teal‑alignées, durées
  200/400 ms, easing `cubic-bezier(0.16,1,0.3,1)`, support `prefers-reduced-motion`.
- **Composants** (`kairos.css`, ~1800 lignes) : `.card`/`.card-elevated`,
  `.btn-*`, `.pill-*`, `.badge-*`, `.alert-*`, `.kpi-card`/`.kpi-strip`,
  `.capacity-gauge` (vert→cuivre→danger), `.data-table`, `.vessel-tabs`,
  `.vessel-status-badge`, `.leg-code`/`.leg-chip`/`.leg-summary`, `.port-badge`,
  `.sidebar-clock` (UTC + heure au port), `.toast`, `.modal-card`.
- **Layouts** : `base.html` → `public/_layout_v2`, `staff/_layout` (sidebar
  teal‑dark 256 px collapsible, 16 groupes), `client/_layout` (FR/EN inline),
  `portal/_layout`. ~230 templates.
- **JS** (~33 fichiers, tous `defer`, **pas de bundler**) : `csrf-htmx`/`csrf-forms`,
  `towt-tz`/`clock` (fuseaux), `forms` (anti‑double‑submit), `modal`/`toast`,
  `sidebar`/`topbar-menus`, cartes MapLibre (`fleet-map`, `route-map`, `leg-map`,
  `navigation-map`), `booking-co2` (calcul live), `leg-cascade`/`leg-wizard`,
  `scenario-gantt-drag`, `chat`, **PWA** (`pwa-onboard`, `onboard-idb`,
  `onboard-offline`).
- **Accessibilité** : cibles tactiles 44–48 px, focus visible (halo cuivre),
  skip‑link, ARIA live (toasts), contraste WCAG AA (AAA visé en mode bord).

---

## 12. Internationalisation (i18n)
**5 langues** : `fr` (référence), `en`, `es`, `pt-br`, `vi` (catalogues Python sous
`app/i18n/`). **Résolution** : `?lang=` → cookie `towt_lang` (via `/lang/{lang}`) →
`user.language` → `Accept-Language` → `fr`. Helper `t(key, lang)`, filtres `|money`,
`|flag`, `|date`. **Limite connue** : le « chrome » (nav/labels) est en clés i18n,
mais **beaucoup de texte éditorial public est en français codé en dur** (vitrine
quasi monolingue FR de fait ; `/about/anemos` réellement bilingue) — frein d'achat
pour les routes US/BR (EN/PT‑BR à prioriser). Détail : `docs/i18n/translation-audit.md`.

---

## 13. PWA / offline (à bord)
`manifest.json` (`start_url: /onboard`, thème teal) + `sw.js` (cache‑first
`/static/*`, network‑first `/onboard/*`, fallback `/offline.html`). POST jamais
interceptés → **file IndexedDB** (`onboard-idb`) rejouée par **Background Sync**
(`onboard-offline`) à la reconnexion, **dédoublonnage** par UUID côté serveur. Cible
persona capitaine (satcom intermittent).

---

## 14. Tests, CI/CD & déploiement
- **Tests** : **126 fichiers** (`tests/unit`, `tests/integration`, `tests/regression`).
  `tests/regression/test_v2_parity.py` = tableau de bord vivant de la **parité
  V2↔V3** (gaps P0 = vide). Conftest : Postgres de test + fixtures async.
- **CI** (`.github/workflows/ci.yml`) : **lint** (ruff, black, mypy informatif),
  **test** (pytest + couverture, service Postgres 16), **security** (bandit,
  pip‑audit, gitleaks), **build** Docker sur `main`.
- **Migrations** : **90** révisions Alembic (`YYYYMMDD_00XX_…`, dernière
  `20260629_0084_news_ai_layer`), toutes **additives**.
- **Déploiement** : Docker Compose (app Python 3.12‑slim + Postgres 16 + **Caddy**
  TLS Let's Encrypt) ; volumes `pgdata`/`uploads`/`caddy_*` ; `FORWARDED_ALLOW_IPS`.
- **Process qualité** : implémenter → tester → `/code-review` → `/security-review` →
  CI verte → merge squash.

---

## 15. Arbitrages stratégiques actés (`docs/audit/backlog/ARBITRAGES.md`, 2026‑06‑22)
À **ne pas recompter comme régressions** :
- **A1 — MRV hybride** : noon auto (gain V3) **+** compteurs DO de contrôle.
- **A2 — Finance** : prévisionnel/réel complet (5 postes × 2 colonnes) restauré.
- **A3 — Stowage** : **avertir par défaut + blocage dur configurable** par zone.
- **A4 — Crew** : **embarquement hors leg** autorisé (`leg_id` nullable).
- **A5 — Cargo facturation** : **hors plateforme** ; `ClientInvoice`/`invoicing`
  dormant ; `/me/invoices` = page explicite.
- **A6 — Portail** : token riche **et** espace `/me` authentifié coexistent.
- **A7 — `data_analyst`** : réglages CO₂/MRV via permission ciblée, **hors admin global**.
- **Divers** : **certificats CO₂ = label Anemos** (pas de PDF nominatif par client) ;
  suppression utilisateur = **désactivation** (`is_active`) ; **facteur CO₂
  versionné** ; congés marins migrés crew → RH ; pas de module **passengers** (retiré
  en v3.0.0).

---

## 16. Écarts connus, dette & gap competitif (pour outils d'analyse)

### 16.1 Gaps commerciaux prioritaires (audit 2026‑06‑12, IDs `COM‑xx`)
- **COM‑01/02** : compte obligatoire dès l'étape 1 ; **aucun devis instantané
  public** (le calcul existe pourtant) → friction de conversion.
- **COM‑03** : **tarif par défaut 38 €/palette** appliqué silencieusement si le prix
  public d'un leg est absent (risque de vendre une transatlantique à 38 €/pal.).
- **COM‑04** : leads `/contact` **best‑effort** (Pipedrive/email/notif), SLA « 4 h »
  **non mesuré**.
- **COM‑05** : **pas d'encaissement** ni suivi de paiement (`paid_at` jamais
  alimenté) ; relances manuelles.
- **COM‑06** : **deux pipelines** parallèles (orders vs bookings) — pas de vue
  unifiée du remplissage/CA par leg.
- **COM‑07** : **vitrine monolingue FR de fait** ; EN/ES/PT‑BR squelettiques.
- **COM‑08/09/10/12** : politique d'annulation absente ; drafts ne gèlent pas la
  capacité ; aucun nurturing ; **zéro preuve sociale** (témoignages/références/blog
  vide).

### 16.2 Gaps preuve RSE (IDs `ENV‑xx`)
- **ENV‑01** : `−95 %` vs `−89 %` coexistent publiquement → une seule vérité, sourcée.
- **ENV‑02/03** : facteur **forfaitaire** non réconcilié avec le mesuré ; repli
  **3 000 NM** silencieux → afficher la méthode honnêtement (étape 1), réconcilier
  mesuré/théorique (étapes 2‑3).
- **ENV‑04/05** : vérificateur tiers **non nommé**, pas de n° vérifiable ; **ISO
  14083 / GLEC / WtW‑TtW / CO₂e** absents.
- **ENV‑06** : **pas d'export RSE annuel consolidé** (la North Star).
- **ENV‑08** : « label Anemos » = auto‑déclaration → renommer **ou** constituer le
  label (registre + tiers).

### 16.3 Dette technique & points d'attention
- `CLAUDE.md` partiellement inexact (Cargo audit/lock, Insurance « V3‑only », statuts
  Finance/KPI ; il déclare encore **8 rôles / 16 modules** alors que le **code en a 9
  et 17** — `rh` et `veille` ajoutés). À recouper avec ce document.
- Modules **dormants/à consolider** : `client_invoice`/`invoicing` ; contrat **API
  tracking** lecture (GET) rompu vs V2 (à versionner) ; PWA offline étendu aux SOF
  (backlog).
- Risques de **migration de données** V2 : `LegKPI.cargo_tons` (t) → `tonnage_kg`
  (kg, ×1000) ; ~40 colonnes `PackingListBatch` ; renommages tracking/crew.

---

## 17. Roadmap & priorités

### 17.1 Priorités court terme (6–12 mois) — pour tenir la comparaison « grands acteurs »
1. **Fermer le tunnel** : devis instantané sans compte (COM‑02), inscription à
   l'étape 3 (COM‑01), refus d'ouverture booking sans prix public (COM‑03),
   encaissement + suivi (COM‑05).
2. **Robustifier la preuve RSE** : méthode honnête sur le PDF, export annuel
   consolidé (ENV‑06), vérificateur nommé + ISO 14083 (ENV‑04/05), page « Preuves ».
3. **Instrumenter le funnel** (analytics public) : cible conversion landing→booking
   ≥ 5 %, submitted→confirmed < 4 h, self‑service ≥ 30 % à 6 mois.
4. **Localiser EN puis PT‑BR** (routes US/BR).
5. **Unifier orders/bookings** (COM‑06) et **automatiser les jalons** de suivi
   (FLX‑02 : SOF réels → statuts client).

### 17.2 Backlog soldé (rappel — déjà livré)
DOCX BL + offre ; stowage SVG ; exports/purges DB whitelistés ; congés unifiés
(EVO‑02) ; veille IA (EVO‑04) ; PWA offline IndexedDB + Background Sync (EVO‑05).

### 17.3 Cap produit (horizon, source vision/cycle 3)
Onboard 4 espaces étendu (check‑lists ISM/ISPS offline) ; analytics cumul + RAG
chatbot ; multi‑devise + webhooks B2B ; (exploratoire) marketplace co‑chargement,
certificats CO₂ ancrés en registre.

---

## 18. Glossaire maritime
`Leg` (segment port A→B) · `leg_code` `{seq}{vessel}{dep}{arr}{year}` (ex. `1CFRBR6`)
· `ETD/ETA` (estimés) · `ATD/ATA` (réels) · `Escale` (à quai) · `SOF` (Statement of
Facts) · `BL/BOL` (Bill of Lading) · `POL/POD` (Port of Loading/Discharge) ·
`LOCODE` (UN, 5 car.) · `OPEX` (coût journalier d'exploitation) · `EOSP/SOSP`
(End/Start Of Sea Passage) · `MRV` (réglementation UE émissions) · `MDO/DO` (Marine
Diesel Oil) · `ROB` (Remaining On Board) · `Schengen` (90 j/180) · `PAF` (Police Aux
Frontières) · `DNV Veracity` (plateforme de vérification MRV) · `IMDG` (marchandises
dangereuses) · `EPAL/USPAL` (formats de palette) · `t·nm` (intensité : tonne‑mille
nautique) · **Anemos** (label/certificat CO₂ évité + navire).

---

## 19. Sources (dans le dépôt)
- Code : `app/main.py`, `app/config.py`, `app/permissions.py`, `app/auth.py`,
  `app/models/*`, `app/routers/*`, `app/services/*`, `app/static/css/*`,
  `app/i18n/*`.
- Stratégie : `docs/strategy/00-vision.md`, `…/CAHIER_DES_CHARGES_SIRH.md`,
  `…/NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md`.
- Audit 360° : `docs/audit/2026-06-12-audit-360/01..06` (commercial,
  marketing‑environnemental, fonctionnel, architecture, personas, cycle 3).
- Personas : `docs/personas/01-personas.md`. Arbitrages :
  `docs/audit/backlog/ARBITRAGES.md`. Design :
  `docs/design/newtowt-design-tokens.json`, `…/01-design-handoff.md`,
  `…/02-redesign-brief.md`. Data : `docs/analytics/01-data-strategy.md`. i18n :
  `docs/i18n/translation-audit.md`.
- Réf. jumelle (factuelle code) : `docs/audit/DOCUMENT_REFERENCE_CONTEXTE_APPLICATION.md`.

---

*Document de référence stratégique & fonctionnel — dérivé du code et des docs au
2026‑06‑29 (branche `claude/app-reference-docs-q4vx4g`). À régénérer après toute
évolution majeure de schéma, de matrice RBAC, d'intégration externe ou de
positionnement commercial.*
