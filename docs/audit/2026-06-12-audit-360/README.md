# Audit 360° — `mynewtowt` : produit, parcours, flux & architecture

> **Date** : 2026-06-12 · **Version auditée** : commit `34a9e80` (main / branche d'audit)
> **Commanditaire** : direction NEWTOWT · **Nature** : audit interne multi-personas
> **Complémentarité** : le [repo-audit du 2026-06-10](../2026-06-10-repo-audit.md) couvre
> la qualité technique (CI, tests, sécurité, dette). Le présent audit couvre le
> **métier** : adéquation commerciale, preuve environnementale, flux opérationnels,
> architecture d'usage. Les deux se référencent mutuellement, sans se recouvrir.

---

## 1. Objet et contexte

La vision produit ([`docs/strategy/00-vision.md`](../../strategy/00-vision.md)) fixe
l'ambition : *« réserver, suivre, mesurer le transport vélique avec la même exigence
que les acteurs de la grande conteneurisation (CMA-CGM, MSC, Maersk), tout en gardant
l'ADN décarboné »*. Le contexte sectoriel 2026 rend cette exigence existentielle :
le cargo à voile a connu la défaillance d'un pionnier au printemps 2026 et la
consolidation s'accélère ([Figaro Nautisme, avr. 2026](https://figaronautisme.meteoconsult.fr/actus-nautisme-bateaux/2026-04-01/87037-transport-cargo-a-la-voile-ou-en-est-vraiment-le-secteur-en-2026)).
La conversion commerciale, la preuve RSE opposable et l'efficacité opérationnelle ne
sont pas des sujets de confort : ce sont les trois conditions de survie du modèle.

Cet audit installe une **structure d'audit réutilisable** (personas, grilles,
registre de constats) et livre une **première passe complète** sur quatre volets.

## 2. Structure de l'audit — quatre volets, quatre personas

| # | Volet | Persona auditeur | Question centrale | Document |
|---|-------|------------------|-------------------|----------|
| 1 | Marketing / Commercial (frontend) | **Sophie R.**, responsable commerciale fret | Le site vitrine répond-il aux attendus d'un chargeur ? Le prospect trouve-t-il ce qu'il cherche ? Le parcours de réservation convertit-il ? | [`01-audit-commercial.md`](01-audit-commercial.md) |
| 2 | Marketing environnemental / RSE (frontend) | **Maëlle V.**, responsable marketing & RSE | Un acheteur devant **prouver** la sensibilité RSE de son achat trouve-t-il les marqueurs et les preuves nécessaires, avant et après réservation ? | [`02-audit-marketing-environnemental.md`](02-audit-marketing-environnemental.md) |
| 3 | Audit fonctionnel des flux (backend) | Auditeur fonctionnel (binôme métier/SI) | Les flux de gestion et flux opérationnels actifs sont-ils complets, cohérents, automatisés là où ils doivent l'être ? | [`03-audit-fonctionnel-flux.md`](03-audit-fonctionnel-flux.md) |
| 4 | Architecture applicative | **Karim B.**, chef de projet informatique senior | Comment restructurer l'application pour un isolement par métier compréhensible et un usage réel en environnement maritime ? | [`04-proposition-architecture.md`](04-proposition-architecture.md) |

## 3. Méthode

1. **Lecture statique exhaustive** du code et des templates (3 balayages parallèles :
   vitrine/booking/espace client · flux backend module par module · architecture/wiring),
   chaque constat est référencé `fichier:ligne`.
2. **Parcours simulés** : déroulé écran par écran du tunnel de réservation sur une
   route ouverte (volet 1) et du parcours post-réservation d'une expédition livrée
   (volet 2), tels que le code les produit réellement.
3. **Prescrit vs réalisé** : confrontation systématique avec la spécification de
   référence [`NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md`](../../strategy/NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md)
   (modes opératoires module par module) — c'est le socle du volet « confirmer /
   challenger / conforter ».
4. **Benchmark externe** (recherche web du 2026-06-12) : Maersk Spot / Emissions
   Studio / ECO Delivery, CMA CGM eBusiness (déjà benchmarké dans
   [`docs/booking/01`](../../booking/01-cale-booking-platform.md)), et pairs
   véliques (Neoline, Grain de Sail, Windcoop, Windshift).

### Conventions de notation (communes aux 4 volets)

- **Sévérité** : 🔴 Critique (menace la promesse produit ou le chiffre d'affaires) ·
  🟠 Majeur · 🟡 Mineur · ⚪ Observation.
- **[F]** fait vérifié dans le code (référence fournie) · **[J]** jugement d'auditeur.
- **Identifiants de constats** : `COM-xx` (commercial), `ENV-xx` (environnemental),
  `FLX-xx` (flux), `ARC-xx` (architecture). Un constat vit dans son document ;
  le registre ci-dessous consolide les majeurs.
- **Notes par dimension** : /5, où 3 = « tient la route avec réserves », 5 =
  « niveau des leaders du marché ».

### Limites assumées

Audit statique : pas de tests utilisateurs réels, pas de données de production,
pas de mesure de trafic. Les volumétries citées (poids de pages, profondeur de
navigation) sont calculées depuis le code, non mesurées en mer.

## 4. Synthèse exécutive

### 4.1 Notes par dimension

| Dimension | Note | Lecture |
|---|---|---|
| Contenu vitrine (fond commercial & environnemental) | **3,5/5** | Riche et différenciant (prix publics, capacité temps réel, méthodologie CO₂ publiée) — rare dans le vélique |
| Tunnel de réservation (conversion) | **2/5** | Mur d'authentification dès l'étape 1, pas de devis invité, pas d'encaissement |
| Preuve RSE opposable (post-achat) | **3/5** | Certificat nominatif automatique = excellent ; mais preuve théorique, non adossée au mesuré |
| Couverture fonctionnelle ERP | **3,5/5** | 17 modules couverts, RBAC solide, audit trail systématique |
| Intégration des flux (automatisations) | **1,5/5** | 13 ruptures de chaîne : double/triple saisies, cascades absentes |
| Adéquation terrain (bord / quai) | **1/5** | Zéro offline, CDN sur liaison satellite, navigation profonde — la promesse PWA (roadmap T+2) n'existe pas |
| Lisibilité de l'architecture | **2,5/5** | Saine en couches (pas de cycles), mais organisation technique ≠ organisation métier |

### 4.2 Le fil rouge

> **Le produit tient sa promesse documentaire, pas encore sa promesse
> opérationnelle.** Tout ce qui est *déclaratif* est remarquablement soigné
> (certificats, audit trail, méthodologie publiée, signatures SOF immuables).
> Tout ce qui est *dynamique* est interrompu : le prospect est arrêté avant le prix,
> les événements réels du bord (ATD/ATA, fuel, positions) ne pilotent ni les statuts
> clients, ni le MRV, ni la finance, et l'équipage travaille avec un outil pensé
> pour un bureau fibré.

### 4.3 Constats transverses majeurs (registre consolidé)

| ID | Sév. | Constat (résumé) | Preuve | Volet |
|---|---|---|---|---|
| COM-01 | 🔴 | Le wizard de réservation exige un compte dès l'étape 1 (la spec prévoyait l'inscription en étape 3) — friction de conversion maximale au moment le plus fragile | `app/routers/booking_router.py:47-49` | 1 |
| COM-02 | 🔴 | Aucun devis instantané sans compte alors que toutes les briques existent (prix public, calcul de quote) — standard marché depuis Maersk Spot / Quick Quotes | `services/pricing.py` non exposé en public | 1 |
| FLX-01 | 🔴 | **Survente structurelle possible** : la capacité publique ne décompte que les `bookings` ; les commandes du rail commercial classique affectées au même leg ne réservent rien | `app/services/capacity.py:86-90` | 3 |
| FLX-02 | 🔴 | Les événements réels du bord (ATD/ATA, SOF) ne déclenchent rien : statuts bookings avancés à la main au backoffice, promesse de tracking client fragile | `staff_booking_router.py` (advance manuel) | 3 |
| ARC-01 | 🔴 | Zéro capacité offline pour le bord (pas de service worker, manifest, file locale) alors que vision et personas la promettent | `app/static/` (absence vérifiée) | 4 |
| ENV-02 | 🔴 | Facteurs d'émission codés en dur sans gouvernance ni versionnage ; la table `co2_variables` prévue par la spec a disparu du modèle V3 | `app/services/co2.py:14-16` | 2 |
| ENV-03 | 🟠 | Certificat Anemos calculé sur facteur forfaitaire + distance plan (repli 3 000 NM en dur) — jamais réconcilié avec le fuel mesuré (noon reports/MRV) ni la route réelle (tracking) | `app/services/anemos.py:31-44` | 2 |
| COM-04 | 🟠 | Les leads du formulaire `/contact` dorment en base : ni synchronisation Pipedrive, ni notification à l'équipe commerciale | `vitrine_router.py` POST contact | 1 |
| COM-05 | 🟠 | Pas d'encaissement ni de suivi de paiement (`paid_at` jamais renseigné) — le DSO n'est pas piloté | `services/invoicing.py` | 1 |
| FLX-03 | 🟠 | Triple saisie du fuel : noon report → événement MRV → export DNV ; le mapping `SOF_TO_MRV_MAP` existe mais n'est pas câblé | `services/mrv_export.py:17-24` | 3 |
| FLX-05 | 🟠 | Finance par leg jamais auto-alimentée (coûts dockers, revenus bookings, OPEX) ; `OpexParameter` créée mais jamais lue | `finance_router.py:175-237` | 3 |
| FLX-06 | 🟠 | Conformité Schengen calculée à la lecture, jamais persistée ; aucune barrière d'affectation d'un marin non conforme ; règle d'appareillage `REQUIRED_ROLES` (spec) absente | `crew_router.py:137-185` | 3 |
| ARC-03 | 🟠 | Organisation technique ≠ métier : 33 routers plats, `modules_router.py` (981 l.) agrège onboard+RH+tracking+ports+analytics, doubles expositions stubs/réels | `app/routers/` | 4 |
| ARC-04 | 🟠 | Une seule surface staff pour tous les métiers : sidebar 11 groupes codée en dur, non filtrée par rôle (un marin voit Commercial/Finance → 403) | `templates/staff/_layout.html` | 4 |
| ENV-01 | 🟠 | Revendications incohérentes : « −95 % CO₂ » (landing, impact) vs « −89 % » (fiches routes, méthodologie) — vulnérable au premier acheteur attentif | `landing.html` vs `about_anemos.html` | 2 |

Le détail (et la trentaine de constats 🟡/⚪) vit dans chaque document.

### 4.4 Quick wins (≤ 2 semaines, indépendants de toute refonte)

1. **Devis instantané public** sur la fiche route : formulaire palettes → prix
   indicatif, sans compte (les fonctions `pricing.compute_quote` et la capacité
   existent déjà). → COM-02
2. **Notifier l'équipe commerciale** (email + notification interne) à chaque
   `ContactRequest` et à chaque booking `submitted` ; pousser le lead dans
   Pipedrive (`utils/pipedrive.py` existe, non câblé). → COM-04
3. **Harmoniser les chiffres CO₂** (une seule valeur sourcée, −89 % ou la valeur
   par route) sur landing / impact / fiches routes / certificat. → ENV-01
4. **Filtrer la sidebar par permissions** (le helper `has_any_access` existe). → ARC-04
5. **Décompter les commandes classiques dans la capacité publique** (ou interdire
   l'affectation d'orders sur un leg `is_bookable`). → FLX-01
6. **Auto-héberger fonts/HTMX/Lucide** et poser des en-têtes de cache sur
   `/static` : −200 Ko et suppression de 3 domaines tiers sur liaison satellite. → ARC-02

## 5. Comment rejouer cet audit

Structure conçue pour être re-déroulée à chaque jalon majeur (release, nouveau
module, refonte) :

1. Re-dérouler les **parcours simulés** des volets 1 et 2 (sections « Parcours »)
   et mettre à jour les verdicts écran par écran.
2. Re-vérifier le **registre des constats** (§4.3 + tableaux par document) :
   chaque ID passe à `corrigé / partiel / ouvert`, avec commit de référence.
3. Re-générer la **matrice prescrit vs réalisé** du volet 3 si la note de
   continuité opérationnelle évolue.
4. Mettre à jour le **benchmark** (les capacités des leaders bougent vite —
   sources datées dans le volet 1).
5. Conserver la convention : un nouveau constat = nouvel ID séquentiel, jamais de
   réutilisation.

---

*Documents du dossier : [cadre & synthèse (ce fichier)](README.md) ·
[01 commercial](01-audit-commercial.md) · [02 environnemental](02-audit-marketing-environnemental.md) ·
[03 flux fonctionnels](03-audit-fonctionnel-flux.md) · [04 architecture](04-proposition-architecture.md).*
