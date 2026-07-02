# Rapport stratégique — L'architecture unique NEWTOWT

> **ERP × Marché × Marque** — associer le marketing, l'activité économique
> et le fonctionnement organisé de l'entité dans une seule architecture.
>
> Statut : document fondateur de travail, v1.0, daté du 1er juillet 2026.
> Méthode : 4 volets d'analyse menés en parallèle — (1) synthèse des 77
> documents d'audit/stratégie du dépôt, (2) cartographie du code et du
> front-end réellement implémentés, (3) veille concurrentielle transport
> vélique (web, 01/07/2026), (4) analyse du marché café et du salon World
> of Coffee Bruxelles (25–27 juin 2026). Sources en annexe C.
> Ce rapport se lit avec `docs/strategy/00-vision.md` (vision produit) et
> `docs/audit/2026-06-12-audit-360/` (audits) ; il ne les remplace pas,
> il les articule avec le marché.

---

## 0. Résumé exécutif

### 0.1 Le verdict en une phrase

NEWTOWT ne vend pas du fret : NEWTOWT vend **de la preuve qui fait
vendre** — et l'ERP `mynewtowt` est déjà, sans le savoir complètement,
une **machine à fabriquer cette preuve**. L'architecture unique demandée
existe à 80 % dans le code ; il manque le dernier kilomètre : **montrer
au client (et au client du client) les données que le bord capture déjà**.

### 0.2 La doctrine de l'architecture unique

> **Aucune promesse publiée qui ne soit adossée à une donnée de l'ERP.
> Aucune donnée de l'ERP qui ne produise pas un actif commercial.**

Une seule colonne vertébrale de données relie les quatre fonctions de
l'entreprise :

```
OPÉRATIONS (ERP)          →  PREUVES (actifs)          →  RÉCIT (canaux)              →  REVENUS
booking · leg · noon      →  certificat Anemos ·        →  site · kit B2B2C · QR pack  →  conversion ·
reports · T°/H% cales ·   →  courbes de cale · carte    →  presse · LinkedIn ·          →  fidélisation ·
MRV · positions GPS ·     →  du trajet · photos ·       →  packaging des clients        →  premium prix
SOF · météo               →  taux de service            →                               →  ↩ réinvesti en flotte
```

Chaque événement opérationnel (un noon report, une position GPS, un
débarquement) doit générer automatiquement un actif marketing ; chaque
claim marketing doit pouvoir être tracé jusqu'à la ligne de base qui le
justifie. C'est exactement l'exigence de la réglementation anti-
greenwashing (ECGT, applicable au 27/09/2026) — notre contrainte de
conformité et notre avantage concurrentiel sont **le même objet**.

### 0.3 Les 6 découvertes structurantes de l'analyse

1. **La preuve thermique dort dans la base.** L'ERP capture température
   et humidité **par cale, deux fois par jour** (noon reports, 7
   emplacements) et un « Carnet de Bord » PDF complet existe (courbes,
   photos, points remarquables)… mais son router **n'est pas monté** et
   **aucune donnée mesurée n'est jamais montrée au client** — alors que
   « température et humidité surveillées en continu » est l'argument n°1
   de la landing et du discours café.
2. **Le verrou du marché n'est pas le prix, c'est la preuve.** Le surcoût
   voile ≈ +1 €/kg de vert ≈ **+0,30 € par paquet de 250 g (1 à 5 % du
   prix facing)**, très en-dessous du consentement à payer mesuré
   (+9,7 % en moyenne, +15 % chez les Gen Z *si le bénéfice est prouvé*).
3. **Le vrai concurrent n'est pas un autre voilier.** C'est le
   *book & claim* biocarburant des majors (~300 €/tCO₂ évitée, achetable
   en 3 clics chez Hapag-Lloyd) contre ~4 000–6 000 €/tCO₂ pour la voile
   [estimation]. Conclusion : **ne jamais vendre du €/tCO₂** — vendre
   l'insetting physique réel, la qualité produit et le récit B2B2C.
4. **Le B2B2C est déjà validé par le marché.** Belco revend la voile à
   ~1 500 torréfacteurs avec kit marketing clé en main ; Café William met
   sa « Wind Series » chez Costco Canada ; le label ANEMOS (créé 2017,
   QR par paquet) est le format de preuve que la filière a adopté.
5. **La confiance est le produit n°1 post-reprise.** Après la liquidation
   d'avril 2026 et la reprise validée le 7 mai (Crédit Mutuel, Ceres/
   Mulliez, Après Demain, Kefen), la reconquête passe par la **régularité
   démontrée** : ligne Fécamp ↔ São Sebastião cadencée, calendrier
   publié, taux de service mesuré — l'ERP sait déjà tout mesurer.
6. **Le calendrier réglementaire joue pour nous.** ECGT (27/09/2026) :
   claims génériques et « neutre en carbone » par compensation interdits ;
   EUDR (30/12/2026) : toute la filière café s'équipe en traçabilité par
   lot ; MRV étendu aux 400–5 000 GT : nos navires produisent une preuve
   officielle d'intensité carbone. Trois obligations qui valorisent
   exactement ce que nous savons faire.

### 0.4 Les 3 priorités (avant toute autre dépense)

| # | Priorité | Pourquoi maintenant |
|---|---|---|
| 1 | **Boucler la chaîne de preuve** : monter le Carnet de Bord, exposer T°/H% de cale au client, créer la **page publique de voyage par lot** (destination du QR sur le paquet) | C'est le différenciateur café que personne d'autre n'a ; 80 % du travail est déjà fait |
| 2 | **Blinder les claims avant le 27/09/2026** (ECGT) : méthodologie téléchargeable réelle, vérification tierce du facteur d'émission, wording audité | Transforme un risque juridique en argument de vente (« opposable en audit ») |
| 3 | **Publier la confiance** : preuve sociale (logos, témoignages, chiffres cumulés), kit presse réel, taux de service par ligne, PT-BR/EN complets | Post-liquidation, chaque signal d'exploitation régulière vaut plus qu'une campagne |

---

# PARTIE 1 — TECHNIQUE : cohérence globale de l'ERP

## 1.1 Ce qui est en place (état au 01/07/2026)

**Périmètre.** Une seule application FastAPI/PostgreSQL sert 4 audiences :
staff ERP (16 modules, 8 rôles, permissions C/M/S), clients authentifiés
(`/me`, MFA), expéditeurs par token (`/p/{token}`, 90 j, multilingue
fr/en/es/pt-br), public (vitrine + catalogue routes + devis + booking
invité). 37 routers montés, ~87 migrations, 710 tests verts, parité
V2→V3 P0 à 100 % (`tests/regression/test_v2_parity.py`).

**Trajectoire qualité.** L'audit du 10/06 notait B− (« le code est bon,
la couche de vérification était une fiction ») ; l'audit V2→V3 du 22/06
constatait 10 domaines régressés et 6 personas NO-GO ; l'audit 360 du
12/06 chiffrait le tunnel de réservation à 2/5 et l'intégration des flux
à 1,5/5. Depuis : campagne de reprise P0/P1 (PR #50→#68), arbitrages
métier tranchés (`ARBITRAGES.md`), cycles 2/3 de l'audit 360 exécutés,
puis **vagues 0→3 orientées conversion** : refonte landing « Nouvelle
Étoile », pages `/impact` + `/preuves` + `/verify` (QR), `/solutions/cafe`,
wizard booking invité avec autocréation de compte, devis public sans
compte + relance J+1, certificat Anemos par booking, kit B2B2C + espace
marque client. **La dynamique de remédiation est réelle et rapide.**

**La chaîne de la preuve, telle qu'implémentée :**

```
Devis public /devis (sans compte, PDF, lead Pipedrive, relance J+1)
   → Wizard booking 3 étapes en session invité (IMDG + FDS si dangereux)
      → autocréation du compte à la validation (bascule connexion si email connu)
   → Booking confirmé (backoffice staff) → grille d'annulation 0/25/50/100 %
   → Leg : positions GPS (5 min) + météo Windy (30 min) + noon reports signés
      (dont T°/H% par cale ×2/jour) + SOF → jalons client
   → MRV (export DNV 18 col.) + facteurs CO₂ versionnés (/admin/co2)
   → Certificat Anemos auto au débarquement (PDF + QR /verify, méthode imprimée,
      mesuré vs théorique) + rapport annuel + CSV Bilan Carbone®
   → Kit B2B2C par expédition (/me/bookings/{ref}/kit + .pdf) : logos croisés,
      équivalences CO₂, récit d'origine café (origine/région/producteur), QR
   → analytics_events : 8 événements de funnel + dashboard commercial staff
```

Aucun concurrent vélique n'a l'équivalent (aucun n'a même de réservation
en ligne — cf. Partie 2). **C'est l'actif stratégique n°1 de la compagnie
après les navires eux-mêmes.**

## 1.2 Matrice promesse publiée ↔ preuve délivrée

La cohérence « site ↔ ERP » se juge claim par claim :

| Promesse publiée (verbatim du site) | Donnée/feature ERP | État |
|---|---|---|
| « température et humidité des cales surveillées en continu » (landing, hero) | `noon_report_holds` : T°/H% minuit + midi, 7 emplacements de cale | 🔴 **Capturé mais jamais montré** au client ni à l'expéditeur |
| « CO₂ mesuré & certifié — certificat Anemos » | `AnemosCertificate` + noon reports + facteurs versionnés | ✅ Complet, méthode imprimée sur le PDF |
| « vérifiable par QR » (/solutions/cafe) | `/verify/{ref}` public, sans PII, rate-limité | ✅ |
| « Un récit qui se vend… prêts pour votre packaging » (landing, pilier 3) | Kit B2B2C PDF + espace marque `/me/brand` | 🟡 Kit OK, mais **pas de page publique scannable par le consommateur** (le QR mène au certificat, pas au voyage) |
| « Une flotte qui navigue, pas un projet » | `/fleet` : carte publique des positions réelles | ✅ Unique dans le secteur |
| « plannings annoncés 6 mois à l'avance » (/about) | Catalogue `/routes` sur legs `is_bookable` | 🟡 Vrai si la discipline de saisie planning est tenue ; taux de service non publié |
| « cales ségréguées… maîtrise passive, sans réfrigération active » (fiche route) | `StowageZoneSpec.segregated`, 18 zones, référentiel café Phoenix | ✅ Honnête et documenté |
| « Méthodologie (PDF) · Rapport CO₂ annuel (exemple) · Kit RSE co-brandable » (/preuves) | — | 🔴 **Liens factices** (renvoient vers /contact) |
| Kit presse, photothèque (/presse) | — | 🔴 Placeholders « fichier à venir » |
| hreflang fr/en/es/pt-br déclaré (SEO) | Catalogues i18n | 🟡 Pages piliers FR-only ou FR/EN ; **ES/PT-BR incomplets alors que la ligne est brésilienne** |

**Lecture.** Le discours est bon — remarquablement bon, même (« Votre
marchandise traverse l'Atlantique sans la réchauffer », les 3 piliers
Qualité/Preuve/Récit sont exactement le bon cadrage marché, cf. Partie 2).
Le problème n'est pas le message : c'est que **3 claims sur 10 ne sont
pas encore servis par l'outil**, dont le claim café central. À l'inverse,
la donnée la plus différenciante (relevés de cale) n'a aucun débouché
commercial. C'est le sens exact de la doctrine §0.2.

## 1.3 Écarts et dettes à résorber (priorisés)

**P0 — bloquants pour la promesse marché**
1. `carnet_bord_router.py` **non importé dans `app/main.py`** : Carnet de
   Bord (chapitre 4 = conditions de transport avec courbes T°/H%, photos
   `VoyagePhoto`, points remarquables `VoyageHighlight`) totalement
   inaccessible. Fonctionnalité morte à réactiver, sécuriser, relier.
2. T°/H% de cale invisibles côté `/me` et `/p/{token}` (0 occurrence de
   `temperature|humidit` dans ces templates) ; `BookingItem.temperature_min/max`
   dormants (aucun formulaire, aucun affichage).
3. Téléchargements annoncés sur `/preuves` et `/presse` inexistants.
4. Pas de page publique de voyage par lot → le QR B2B2C ne mène qu'au
   certificat (chiffre), pas à l'histoire (carte, photos, cale, équipage).

**P1 — crédibilité et portée**
5. ENV-05/ENV-08 : facteur 1,5 g CO₂/t·km auto-déclaré ; pas de
   vérificateur tiers nommé, pas d'alignement ISO 14083/GLEC — à traiter
   avant l'ECGT (27/09/2026).
6. i18n : PT-BR/ES incomplets vs hreflang (risque SEO + incohérence avec
   la ligne Brésil) ; contenu éditorial FR en dur sur /impact, /presse,
   /navigation.
7. Open Graph sans image ni description, pas de twitter:card → partages
   sociaux aveugles pour une marque très photogénique.
8. Mesure marketing partielle : aucun événement sur /impact, /preuves,
   /solutions/cafe, /contact ; scans `/verify` non comptés ; pas d'UTM.
9. Preuve sociale : zéro logo client, zéro témoignage, blog `/carnet`
   quasi vide (« un actif gâché » — audit marketing).

**P2 — hygiène de référentiel (à trancher une fois pour toutes)**
10. Incohérences internes : flotte (6 noms au PCA — Anemos, Artemis,
    Atlantis, Atlas, Archimedes, Asterias — vs « Aphrodite/Pélican » dans
    un brief obsolète ; presse : 6 commandés, 2 annulés, 4 en livraison
    07/2026→06/2027) ; capacité (850 EPAL booking vs 978 EPAL référentiel
    Phoenix vs « > 1 200 t » page flotte) ; format `leg_code` (3 versions
    en circulation) ; page `/passagers` 2027 vs doctrine « passagers
    arrêtés mai 2026 ». Chacune de ces incohérences finira dans un dossier
    de presse ou un audit client si elle n'est pas arbitrée.
11. Dettes résiduelles du repo-audit : rate-limit login staff, révocation
    de sessions, WebAuthn promis/absent, mailing e-mail HTML (backlog #6).

## 1.4 Verdict de cohérence

**L'ERP est cohérent, riche, et va dans la bonne direction** — la
succession audits → arbitrages → reprise → vagues conversion a produit un
outil que la vision (§`00-vision.md` : « réserver, suivre, mesurer avec
l'exigence des grands ») décrit fidèlement. Le système de preuve
(certificat + QR + MRV + tracking public) est **au-dessus du standard du
secteur**, y compris chez les majors sur la transparence méthodologique.
Ce qui manque n'est pas une refonte : c'est **la jonction entre trois
tuyaux déjà posés** (données de cale → client ; voyage → public ; claims
→ fichiers réels). C'est un travail de semaines, pas de trimestres.

---

# PARTIE 2 — MARCHÉ, CONCURRENCE ET CIBLES

## 2.1 Environnement (PESTEL synthétique)

| Facteur | Faits saillants 2025-2026 | Effet pour NEWTOWT |
|---|---|---|
| **Politique/Réglementaire** | EU ETS maritime à 100 % en 2026 (+CH₄/N₂O) ; FuelEU Maritime (récompense vent −5 %) ; MRV étendu 400–5 000 GT (nos navires) ; CSRD recentrée >1 000 salariés/450 M€ (Omnibus) ; **ECGT applicable 27/09/2026** ; **EUDR café/cacao 30/12/2026** | Le conventionnel renchérit (+45 % de surcharges ETS) sans combler l'écart ; la preuve physique mesurée devient une exigence légale — notre terrain |
| **Économique** | Arabica record 4,41 $/lb (02/2025), « new normal » 2,60–3,20 $/lb en 2026 ; vert à 7–9 $/kg ; consolidation négociants (Hartree/Volcafe, Sucafina, NKG) | Surcoût voile **dilué** dans un vert cher ; budgets conformité EUDR concurrencent le budget fret premium |
| **Socioculturel** | WTP durable : +9,7 % moyen (PwC), 65 % Gen Z prêts à payer plus, +15 % si bénéfice **prouvé** ; storytelling d'origine déjà standard du specialty | La voile ajoute le « 2e chapitre » du récit (le voyage) ; l'écart intention/achat impose un surcoût facial faible — c'est le cas (+0,30 €/paquet) |
| **Technologique** | Vélique-assisté en explosion : >100 grands navires équipés, doublement annuel, ~10 000 attendus en 2030 ; rotors/ailes 5–20 % d'économie | « Propulsé par le vent » sera banalisé d'ici 2030 → imposer la métrique **gCO₂/t·nm par leg** pour distinguer −90 % (vélique principal) de −5/15 % (assisté) |
| **Environnemental** | Transport ≈ 15–20 % de l'empreinte du café (export maritime 6–11 %) ; ACV café 3,5–17 kg CO₂e/kg | Poste le plus facile à décarboner d'un coup (−90 %) ; ne jamais survendre en « café neutre » |
| **Légal** | Green Claims retirée (06/2025) mais ECGT en vigueur 09/2026 : claims génériques et neutralité-par-compensation interdits | Auto-label Anemos à consolider (vérif tierce) — sinon risque ; bien fait, c'est une arme commerciale |

## 2.2 Le cercle concurrentiel — 4 cercles

**Cercle 1 — vélique pur (concurrents directs).** Cinq navires modernes
en mer mi-2026 : Anemos, Artemis (NEWTOWT), Grain de Sail II, Neoliner
Origin, Canopée. Secteur **darwinien** : TOWT liquidé puis repris,
EcoClipper en faillite, De Gallant coulée (2024), Ceiba suspendu.
Chaque survivant a un angle :

| Acteur | Angle | Capacité | Routes | Clients | À surveiller |
|---|---|---|---|---|---|
| **NEWTOWT** | Ligne régulière massifiée café/palettes, label Anemos, −90 % | 2×1 100 t (+4 navires 07/26–06/27) | Fécamp ↔ São Sebastião + transat | Belco, Terres de Café, Café William, Dagobert… | Notre exécution post-reprise |
| Grain de Sail | Marque D2C intégrée (chocolat/café) + 3PL | 350 t (GDS II) ; GDS III 570 EVP en projet | St-Malo ↔ NY ↔ Caraïbes | Soi-même + tiers | Le passage conteneurisé GDS III |
| Neoline | RoRo industriel −80 %, adossé CMA CGM | 5 300 t / 265 EVP | St-Nazaire ↔ Baltimore/Halifax mensuel | Renault, Hennessy, Clarins, Longchamp… | Aspire les gros chargeurs industriels |
| VELA | **Vitesse** (NY en ~13 j), pharma/luxe, 40 M€ levés | Trimaran 67 m, 48 000 t/an visées | Normandie ↔ USA | Takeda, Champagne Thiénot | 1re transat commerciale fin 2026 |
| Windcoop | Coopérative militante, chargeurs actionnaires | 210 EVP + 40 reefer (05/2027) | Marseille ↔ Madagascar | Arcadie, Ethiquable, Lobodis (actionnaire) | Son modèle de fidélisation par le capital |
| Zéphyr & Borée | Vélique-assisté industriel sur mesure | Canopée 121 m | Europe ↔ Kourou (Ariane 6) | ArianeGroup | Références de fiabilité (99,6 % dispo voiles) |

**Cercle 2 — vélique-assisté** (Norsepower, bound4blue, Ayro…) :
banalisation du mot « voile » à horizon 2030 → risque de confusion,
réponse = métrique d'intensité publiée.

**Cercle 3 — les offres « green » des majors (le vrai concurrent).**
Maersk ECO Delivery, CMA CGM ACT+, Hapag-Lloyd Ship Green : biocarburant
en *book & claim*, **achetable en self-service dans le tunnel de
réservation**, ~300 €/tCO₂ évitée. Pour un acheteur scope 3 rationnel, la
voile ne gagne jamais sur le €/tCO₂ (~4 000–6 000 €/tCO₂ [estimation]).
Elle gagne sur : l'insetting **physique** (la marchandise voyage
réellement bas carbone — pas d'allocation comptable), la **qualité
produit** (cales tempérées vs conteneur qui surchauffe aux tropiques),
et le **récit B2B2C imprimable sur le paquet**. Toute la communication
doit être construite pour déplacer la comparaison du €/tCO₂ vers le
€/paquet-vendu-plus-cher.

**Cercle 4 — le statu quo conteneur** : 0,05–0,10 €/kg. C'est lui qui
fixe l'ancre prix ; l'ETS 2026 le renchérit (+45 % de surcharges) sans
changer l'ordre de grandeur. Ne pas se battre là.

## 2.3 Porter — 5 forces (condensé)

- **Rivalité intra-vélique** : modérée — angles différenciés, mais la
  capacité totale du secteur va ×4 d'ici 2027 (nos 4 navires + GDS III +
  Miaraka + flotte VELA) : le remplissage devient le nerf de la guerre.
- **Substituts** : forts (book & claim ; vélique-assisté ; ne rien faire).
- **Pouvoir des clients** : fort sur les ancres (Belco = concentration à
  la fois cliente et distributrice), faible sur la longue traîne des
  torréfacteurs (400+ comptes potentiels identifiés via le modèle Belco).
- **Pouvoir des fournisseurs** : chantiers (Piriou), ports, équipages
  STCW rares — significatif, mitigé par la standardisation de la classe.
- **Barrières à l'entrée** : élevées (20–40 M€/navire + preuve
  d'exploitation + confiance filière) ; entrant le mieux armé : VELA.

## 2.4 Le marché café — enseignements World of Coffee Bruxelles 2026

Le salon (25–27/06/2026, Brussels Expo, 1re édition belge, 430+
exposants, ~20 000 professionnels) confirme la structure de la cible :

1. **La chaîne de valeur y est entière** : producteurs/coopératives
   (Producer Village), négociants-importateurs de vert (Green Coffee
   Connect — dont **Belco, exposant**), ~120 torréfacteurs (Roaster
   Villages), certificateurs, équipementiers, **logistique/emballage**
   (Ecotact et ses liners de conteneur : la concurrence « qualité » voyage
   aussi en conteneur amélioré).
2. **Les deux angoisses du moment** : le coût du vert (rally arabica la
   veille de l'ouverture) et la **conformité EUDR** (session dédiée,
   Dr. J. Grabs). Toute la filière s'équipe en traçabilité par lot
   (géolocalisation parcelles, DDS annuelles au 30/12/2026) — notre
   traçabilité transport peut **se greffer** sur cette infrastructure
   déjà payée par le marché.
3. **La durabilité est un thème de scène centrale**, plus un stand de
   niche : revenus décents, régénératif (Cerrado Mineiro), économie
   circulaire. Le transport décarboné y est un chapitre naturel — encore
   faiblement représenté : **place à prendre**.
4. **Rendez-vous** : prochaine édition World of Coffee : New Orleans
   2027 (porte d'entrée du marché US, cohérente avec les routes
   transatlantiques) ; éditions européennes SCA à suivre pour 2027+.

**Économie du premium café** (à marteler dans tout le matériel
commercial) : +1 €HT/kg de vert FCA Le Havre → **+0,30 € par paquet de
250 g** (~1–5 % du prix facing d'un specialty à 6–12 €) → très inférieur
au WTP mesuré. Belco vise 90 % de ses volumes à la voile en 2030 (7
traversées/an dès 2026) ; Terres de Café 70 % dès 2026 ; Café William
vend sa « Wind Series » **chez Costco** — le B2B2C vélique scale déjà
au-delà du specialty militant.

## 2.5 B2B ou B2B2C ? — Réponse : **B2B contractuel, B2B2C par conception**

La question posée (« Est-ce que nous parlons B2B ? B2B2C ? ») a une
réponse nette :

- **Contractuellement, nous sommes B2B** : nos clients sont des
  importateurs de vert, des torréfacteurs, des marques, des brokers. Ce
  sont eux qui réservent, payent, réclament des documents et des SLA.
- **Mais la valeur qui justifie notre prix se réalise en B2B2C** : ce que
  l'importateur achète, c'est la capacité de son torréfacteur à vendre
  son paquet +0,30 € plus cher avec une histoire vraie. **Le
  consommateur final ne sera jamais notre client, mais il est notre
  argument de vente.**

La cascade de valeur à outiller :

```
NEWTOWT ──(capacité + preuve + kit)──▶ IMPORTATEUR (ex. Belco)
   │                                        │ revend la voile à ~1 500 torréfacteurs
   │                                        ▼
   │                                   TORRÉFACTEUR ──(paquet + QR + récit)──▶ CONSOMMATEUR
   │                                        ▲                                      │ scanne, lit,
   └──(page publique du voyage, QR)─────────┴──────────────────────────────────────┘ paye le premium
```

Chaque flèche doit être servie par un actif de la plateforme :
capacité → catalogue `/routes` + booking ; preuve → certificat Anemos +
MRV + données de cale ; kit → `/me/bookings/{ref}/kit` + espace marque ;
QR consommateur → **page publique de voyage (à créer)**. Le seul maillon
manquant de la cascade est le dernier — et c'est celui qui fait vendre
tous les autres.

## 2.6 Personas marché (complément café aux personas produit existants)

`docs/personas/01-personas.md` couvre les personas *produit* (capitaine,
commercial, prospect vin…). Voici les 5 personas *marché* qui manquaient,
calés sur les cas réels documentés :

**M1 — L'importateur-ancre** (dir. général d'un importateur de café vert,
type Belco : ~60 M€ CA, 1 500 clients torréfacteurs, ~70 % du marché
artisanal français). *Achète* : de la capacité régulière pluriannuelle
(7 traversées/an), un statut privilégié sur la ligne qu'il a contribué à
sauver. *Revend* : la voile en marque ombrelle (« Fresh Coffee Clean
Ocean ») avec kit marketing à ses torréfacteurs. *Exige* : régularité
absolue (il a promis à 1 500 clients), données par lot, continuité EUDR,
co-branding. *Objection* : « vous avez fait faillite il y a 3 mois ».
*Message* : « La ligne est cadencée, la capacité est contractuelle, la
preuve est industrialisée — vos torréfacteurs vendent plus cher. »

**M2 — Le torréfacteur specialty** (8–30 salariés, B Corp en cours,
achète son vert via M1 ou en direct petits lots). *Achète* : une histoire
exclusive par lot (« première traversée d'Artemis », micro-lot par
navire), des visuels, un QR pack. *Exige* : simplicité (il n'a pas de
service logistique), prix exprimé en centimes/paquet. *Canal* : souvent
indirect via M1 — le kit doit donc être **en marque blanche**. *Message* :
« +0,30 € le paquet, une histoire que personne d'autre n'a, prête à
imprimer. »

**M3 — La marque grand compte / industriel RSE** (torréfacteur industriel
type Malongo/Café William, distributeur, maison de spiritueux). *Achète* :
de l'insetting physique pour son scope 3 (CSRD), des volumes
pluriannuels, un récit retail-ready (précédent : Wind Series chez
Costco). *Exige* : certificats opposables (ECGT), méthodologie vérifiée,
API/reporting, SLA. *Message* : « L'insetting physique, mesuré lot par
lot, opposable en audit — et un storytelling qui tient en GMS. »

**M4 — Le broker / commissionnaire de transport.** *Achète* : de la
cotation rapide, des documents automatiques (BL DOCX, packing list,
Arrival Notice), de la visibilité capacité. *Exige* : API (`/api/v1`),
grille d'annulation claire (déjà en place : 0/25/50/100 %), réactivité.
*Message* : « Cotation en ligne en 2 minutes, documents générés, capacité
visible en temps réel. »

**M5 — Le double lecteur de la preuve** : (a) le **consommateur final**
qui scanne le QR — veut une histoire en 30 secondes (carte du trajet,
photos, équipage, « ce paquet a évité X kg de CO₂ ») dans sa langue ;
(b) **l'auditeur RSE/commissaire** du client M3 — veut la méthode, les
facteurs, la vérification MRV. Deux lectures du même objet : la page
publique de voyage pour (a), `/preuves` + certificat pour (b). Ne jamais
les mélanger dans un même écran.

## 2.7 La matrice « attentes → preuves → où dans l'outil » (clé de voûte)

| Attente (persona) | Preuve à fournir | Surface dans l'architecture | État |
|---|---|---|---|
| CO₂ par lot pour scope 3 (M1, M3) | Certificat Anemos PDF + CSV Bilan Carbone® + méthode | `/me/anemos`, `/me/bookings/{ref}/anemos.pdf` | ✅ |
| Qualité du grain préservée (M1, M2) | Courbes T°/H% par cale, relevés signés | Carnet de Bord + détail booking + portail | 🔴 à brancher (P1) |
| Histoire à imprimer sur le paquet (M2) | Kit par expédition + QR public multilingue | `/me/bookings/{ref}/kit` ✅ + **page voyage publique** | 🟡 kit OK, page à créer |
| Confiance post-reprise (tous) | Calendrier publié + taux de service + tracking live | `/routes`, `/fleet` ✅ + OTIF publié | 🟡 mesure existante, publication à faire |
| Continuité EUDR (M1, M3) | Custody documentaire lot → cale → port | Portail `/p/{token}` documents | 🟡 socle OK, « pack conformité » à formaliser |
| Claims opposables ECGT (M3) | Méthodologie téléchargeable + vérif tierce + wording audité | `/preuves` | 🔴 fichiers factices, vérif tierce absente |
| Réservation sans friction (M2, M4) | Devis sans compte + wizard invité + API | `/devis`, `/booking/new`, `/api/v1` | ✅ unique dans le secteur |
| Moments médias co-brandés (M1, M3) | Kit presse réel, photothèque, dossier par arrivée | `/presse` | 🔴 placeholders |
| Récit consommateur 30 s (M5a) | Page publique du voyage du lot, 4 langues | — | 🔴 à créer (P2) |
| Méthode auditable (M5b) | /preuves + registre /verify | `/preuves`, `/verify` | ✅ (sauf fichiers) |

Cette matrice **est** l'architecture unique demandée : chaque ligne relie
une attente de marché, une donnée opérationnelle et une surface produit.
Elle sert de contrat entre le commercial, le marketing et la tech.

## 2.8 SWOT NEWTOWT

| | Interne | Externe |
|---|---|---|
| **+** | **Forces** : seule flotte vélique industrielle multi-navires dédiée palettes/café ; chaîne de preuve logicielle inégalée (booking self-service, certificat auto, QR, tracking public, MRV) ; label Anemos installé dans la filière depuis 2017 ; actionnariat solide (Crédit Mutuel, Ceres/Mulliez, Après Demain, Kefen) ; surcoût dilué dans un café cher ; charte « Nouvelle Étoile » cohérente et déjà codée | **Opportunités** : ECGT 09/2026 (la preuve devient obligatoire → notre terrain) ; EUDR 12/2026 (traçabilité déjà financée par la filière) ; CSRD grands comptes ; capacité ×3 avec les 4 livraisons ; verticales cacao/spiritueux/thé sur le même modèle ; précédent GMS (Costco) ; club chargeurs façon Windcoop ; salons SCA (New Orleans 2027) |
| **−** | **Faiblesses** : image post-liquidation à reconstruire ; facteur d'émission auto-déclaré (vérif tierce absente) ; preuve thermique non exposée ; PT-BR/ES incomplets face à une ligne brésilienne ; zéro preuve sociale publiée ; dépendance forte au café et à un client-ancre ; incohérences de référentiel (flotte/capacité/leg_code) | **Menaces** : book & claim des majors 10× moins cher au tCO₂ ; VELA sur le premium rapide fin 2026 ; banalisation « wind-powered » d'ici 2030 ; re-défaillance si remplissage insuffisant pendant la montée en flotte ; accusation de greenwashing si claims mal cadrés ; budgets conformité EUDR concurrençant le fret premium |

## 2.9 Positionnement et proposition de valeur

**Positionnement** (une phrase, usage interne) :

> Pour les importateurs et marques de produits sensibles et à forte
> valeur narrative (café d'abord), NEWTOWT est **la ligne maritime
> vélique régulière qui livre la marchandise intacte avec sa preuve** —
> qualité mesurée, carbone évité certifié, récit prêt à vendre — quand
> les alternatives n'offrent soit qu'un prix (conteneur), soit qu'un
> certificat comptable (book & claim), soit qu'un projet (concurrents
> non opérationnels).

**Le prix se vend comme un budget marketing, pas comme un coût de
transport** : +0,30 €/paquet achète (1) un produit mieux conservé,
(2) un label différenciant en rayon, (3) un contenu de communication
inépuisable, (4) des points de certification (B Corp, SBTi, rapport
CSRD). Aucun média à 0,30 € le contact ne délivre autant.

---

# PARTIE 3 — FRONT-END ET STRATÉGIE DE COMMUNICATION

## 3.1 Ce qui est déjà bon (à ne pas toucher)

- **Le message central est trouvé.** « *Votre marchandise traverse
  l'Atlantique sans la réchauffer.* » + « *Ni votre cargaison, ni la
  planète.* » est exactement le double bénéfice (qualité + climat) que le
  marché achète — le garder comme promesse de marque.
- **Les 3 piliers de la landing** (« Qualité préservée / Décarbonation
  prouvée / Un récit qui se vend ») recouvrent précisément les 3 attentes
  B2B2C documentées. La phrase « *Vous ne vendez pas qu'un transport :
  vous transmettez une qualité, une preuve et un récit à vos propres
  clients* » est la meilleure formulation B2B2C du secteur — l'ériger en
  colonne vertébrale de tous les supports.
- **/preuves est une page rare** (périmètre TtW assumé, « CO₂, pas des
  CO₂e », formule publiée, distinction mesuré/théorique imprimée sur le
  certificat) : posture d'honnêteté radicale à conserver telle quelle.
- **/solutions/cafe, /verify, /fleet, le devis sans compte et le wizard
  invité** : uniques dans le secteur, à défendre comme des avantages.
- **SEO/IA** : robots.txt pro-crawlers IA, llms.txt, sitemap hreflang,
  JSON-LD Organization/FAQ — socle sain.

## 3.2 La maison de message (messaging house)

```
PROMESSE (inchangée)
« Votre marchandise traverse l'Atlantique sans la réchauffer.
  Ni votre cargaison, ni la planète. »

PILIER 1 — QUALITÉ         PILIER 2 — PREUVE           PILIER 3 — RÉCIT
« La marchandise arrive    « Le CO₂ évité, mesuré      « Une histoire vraie,
comme elle est partie. »   par lot et vérifiable. »    prête à vendre. »
                                                        
PREUVES: cales ségréguées, PREUVES: certificat Anemos, PREUVES: kit par
T°/H% relevées 2×/jour     QR /verify, MRV vérifié,    expédition, page publique
par cale, courbes          facteurs versionnés,        du voyage (QR pack),
consultables, ventilation  méthode imprimée,           photos/équipage, espace
6 vol/h                    vérificateur tiers (P3)     marque co-brandé

SOCLE TRANSVERSE (post-reprise) — RÉGULARITÉ
« Une flotte qui navigue, pas un projet. »
PREUVES: 2 navires en exploitation + 4 livraisons 07/26–06/27, ligne
Fécamp ↔ São Sebastião cadencée, calendrier 12 mois publié, positions
live /fleet, taux de service par ligne publié.
```

**Le message clé à transférer aux clients** (la phrase qui doit se
retrouver dans les pitchs commerciaux, le site, LinkedIn et les kits) :

> **FR** : « Nous ne vendons pas du fret : nous livrons, avec chaque
> palette, la qualité intacte, le carbone évité certifié et l'histoire
> qui fait vendre. »
> **EN** : "We don't sell freight: with every pallet we deliver intact
> quality, certified avoided carbon, and a story that sells."

Déclinaisons par audience : M1 « vos torréfacteurs vendent plus cher » ;
M2 « +0,30 € le paquet, une histoire que personne d'autre n'a » ; M3
« l'insetting physique, opposable en audit » ; M5a (consommateur, via QR) :
« **Ce café a traversé l'Atlantique à la voile. Suivez sa traversée.** »

**Règles de langage (conformité ECGT, dès maintenant)** : jamais
« neutre en carbone », jamais « écologique/vert » sans chiffre ; toujours
« X kg de CO₂ évités, méthode publiée, certificat n° » ; kg absolus
plutôt que pourcentages (doctrine interne déjà actée) ; le mot « label »
est banni au profit de « certificat Anemos » (déjà fait, PR #115-117).

## 3.3 Stratégie front-end — les chantiers

1. **La page publique de voyage** `/voyage/{ref}` (nouvelle) — la
   destination du QR imprimé sur le paquet : carte du trajet réel
   (positions GPS), dates, navire, vitesse du vent, photos du chargement
   et de l'équipage (`VoyagePhoto`), température moyenne de cale, kg CO₂
   évités (lien certificat /verify), récit d'origine (coffee_stories),
   en 4 langues, sans PII, opt-in du client, rate-limitée. **C'est le
   produit B2B2C.** Le consommateur y passe 30 secondes ; le torréfacteur
   la vend ; l'importateur la co-brande.
2. **La preuve sociale** : bandeau logos clients (avec accords écrits),
   3 témoignages (importateur, torréfacteur, marque), compteurs cumulés
   temps réel (palettes transportées, tCO₂ évitées, traversées) calculés
   depuis la base — pas des chiffres statiques.
3. **Le kit presse réel** (/presse) : pack logos, dossier PDF, 20 photos
   HD libres de droits presse, fiches navires, contact média réel +
   `og:image`/`twitter:card` sur tout le site (chaque partage LinkedIn
   d'une fiche route doit montrer le navire).
4. **i18n aligné sur la stratégie** : EN 100 % des pages piliers
   (acheteurs européens/US), **PT-BR complet** (ligne Brésil,
   exportateurs de Santos/São Sebastião), ES ensuite ; sinon retirer les
   hreflang correspondants (risque SEO).
5. **Les verticales** : dupliquer le pattern `/solutions/cafe` vers
   `/solutions/cacao` puis `/solutions/vins-spiritueux` (clients déjà
   documentés : Dagobert, précédents MMPJ cognac/champagne) — chaque
   verticale avec capture de lead dédiée.
6. **Le carnet** : cadence éditoriale bimensuelle minimum — chaque
   arrivée de navire, chaque étape des 4 constructions (Atlantis,
   Asterias, Atlas, Archimedes), portraits d'équipage. La construction de
   la flotte 2026-2027 est un feuilleton naturel de 12 mois.

## 3.4 Plan de mesure de performance

**Funnel AARRR mappé sur l'existant** (`analytics_events`, 8 événements
en place) + extensions nécessaires :

| Étape | Événements existants | À ajouter |
|---|---|---|
| Acquisition | `landing_view`, `route_view` | vues `/solutions/*`, `/impact`, `/preuves` ; capture UTM (champ `detail`) ; référents |
| Activation | `quote_generated`, `book_click`, `quote_pdf_download` | `contact_submitted`, `devis→wizard` (pré-remplissage utilisé) |
| Revenu | `booking_submitted`, `booking_confirmed`, `account_created` | valeur € et nb palettes sur l'événement |
| Rétention | — (calculable en SQL) | événement `rebooking` (client existant) ; alerte désengagement 90 j (déjà spécifiée dans data-strategy) |
| Recommandation (B2B2C) | — | **`verify_lookup` (scans QR)**, `kit_generated`, `kit_download`, `brand_configured`, `voyage_page_view` (nouvelle page) |

**Tableau de bord unique (à afficher en réunion hebdo)** :

| KPI | Source | Cible 6 mois (cohérente `00-vision.md`) |
|---|---|---|
| North Star produit : palettes réservées/mois par clients récurrents avec rapport CO₂ téléchargé | bookings + anemos | croissance continue (référence à poser M0) |
| **North Star B2B2C : scans QR consommateur/mois** | `verify_lookup` + `voyage_page_view` | >1 000/mois à 6 mois du lancement page voyage |
| Conversion landing → booking confirmé | funnel | ≥ 5 % (cible personas) |
| Conversion devis → booking | funnel | ≥ 25 % (relance J+1 active) |
| % réservations self-service | funnel | 30 % → 60 % à 12 mois |
| Taux de remplissage par départ | occupancy_pct | +5 pts |
| Taux de service (|ATA−ETA| < 24 h) | on_time_pct | ≥ 90 % **et publié** |
| Kits B2B2C générés / bookings café | kit events | ≥ 60 % (mesure l'usage réel du récit) |
| Part de voix presse « transport vélique » | veille `/veille` (IA score) | n°1 FR sur le sujet café |

**Précaution de mesure** : rester 100 % server-side (aucun tracker
tiers — cohérent CSP et RGPD) ; c'est un argument de plus (« même notre
analytics est sobre »).

## 3.5 Canaux hors site

- **LinkedIn** (canal B2B primaire) : 3 formats récurrents — (1) le
  feuilleton de la flotte (chantiers, essais, livraisons), (2) les
  chiffres de preuve par traversée (X t, Y kg CO₂ évités, courbe de
  cale), (3) les success stories clients co-brandées. La page
  `/actualites` fait déjà le pont.
- **Presse** : chaque arrivée = un moment média organisé (dossier,
  visite de cale, dégustation à quai avec les torréfacteurs livrés) —
  précédents documentés (arrivée Le Havre 10/2024 : ~950 t de Colombie
  largement médiatisées).
- **Salons** : World of Coffee New Orleans 2027 (marché US), éditions SCA
  européennes, side-events « logistique décarbonée » adossés aux
  sessions EUDR ; stands partagés avec les importateurs-ancres.
- **Le packaging des clients** : notre média le plus puissant est le
  linéaire de nos clients — chaque QR imprimé est une impression
  publicitaire gratuite et durable. D'où la priorité absolue de la page
  voyage.

---

# PARTIE 4 — PLAN PAS-À-PAS : les prompts de mise en œuvre

Conformément à la méthode convenue (étudier → analyser → rapporter →
**avancer pas à pas**), voici les paquets de travail ordonnés. Chaque
paquet contient un prompt prêt à lancer dans une prochaine session.
Ordre recommandé : P1 → P2 → P3 → P5 → P4 → le reste selon capacité.

### Chantier A — Boucler la chaîne de preuve (produit)

**P1 — Réactiver le Carnet de Bord et exposer les conditions de cale**
Livrable : router monté + accès staff/client/portail + section
« Conditions de transport » (T°/H% par cale, min/max/moyenne, courbes)
dans le détail booking client et le portail expéditeur.
> *Prompt : « Monte `carnet_bord_router` dans l'application (revue
> sécurité/permissions incluse), corrige ses docstrings, et expose les
> relevés de température/humidité de cale (`noon_report_holds`) au client
> dans `/me/bookings/{ref}` et à l'expéditeur dans `/p/{token}/voyage` :
> section “Conditions de transport” avec moyennes, min/max et courbe par
> cale concernée par le lot, plus lien vers le Carnet de Bord PDF.
> Ajoute les tests. Respecte CLAUDE.md (flush, permissions, CSP). »*

**P2 — La page publique de voyage (destination du QR pack)**
Livrable : `/voyage/{ref}` publique, 4 langues, opt-in client, sans PII,
rate-limitée ; carte du trajet réel, photos, CO₂ (lien /verify), T° de
cale, récit d'origine ; QR généré dans le kit pointant dessus.
> *Prompt : « Crée la page publique `/voyage/{booking_ref}` destinée aux
> consommateurs finaux scannant le QR d'un paquet : carte MapLibre du
> trajet réel (vessel_positions du leg), navire, dates, photos
> (`VoyagePhoto`), kg CO₂ évités avec lien /verify, température moyenne
> de cale, récit d'origine (coffee_stories), en fr/en/es/pt-br, activée
> par un opt-in du client dans /me, sans aucune PII, rate-limitée, avec
> événement analytics `voyage_page_view`. Mets à jour le kit B2B2C pour
> que son QR pointe vers cette page. »*

**P3 — Conformité des claims (échéance ECGT : 27/09/2026)**
Livrable : méthodologie PDF réelle téléchargeable sur /preuves, audit du
wording sitewide (checklist ECGT), dossier de consultation pour un
vérificateur tiers du facteur 1,5 g CO₂/t·km, feuille de route ISO
14083/GLEC (ENV-05/ENV-08).
> *Prompt : « Rédige et intègre la méthodologie Anemos en PDF réel
> (WeasyPrint) téléchargeable sur /preuves, audite tous les claims
> environnementaux du site contre la directive ECGT (liste des écarts +
> corrections), et prépare le cahier des charges de vérification tierce
> du facteur d'émission (candidats type Bureau Veritas/DNV, alignement
> ISO 14083/GLEC). »*

**P4 — Harmoniser le référentiel d'entreprise**
Livrable : une seule vérité pour flotte (6 navires, noms, statuts),
capacité (EPAL par navire), format `leg_code`, statut passagers —
corrigée partout (code, docs, site, CLAUDE.md).
> *Prompt : « Fais l'inventaire des incohérences de référentiel (flotte,
> capacités 850/978 EPAL, format leg_code, mentions passagers), propose
> l'arbitrage en une page, puis applique la version tranchée partout :
> modèles, seeds, templates, i18n, docs, CLAUDE.md. »*

### Chantier B — Le site qui vend (front-end)

**P5 — Preuve sociale + partage social + kit presse réel**
> *Prompt : « Ajoute à la landing un bandeau logos clients + 3
> témoignages (contenus fournis), des compteurs cumulés calculés en base
> (palettes, tCO₂ évitées, traversées) ; génère og:image/twitter:card
> pour toutes les pages publiques ; remplace les placeholders de /presse
> par un vrai pack (logos, dossier PDF, photothèque) à partir des assets
> fournis. »*

**P6 — i18n stratégique (Brésil d'abord)**
> *Prompt : « Complète PT-BR à 100 % sur les pages piliers (landing,
> routes, solutions/cafe, preuves, impact, flotte, devis, booking,
> portail), puis EN, puis ES ; aligne les hreflang sur la réalité ;
> supprime les bandeaux “traduction en cours” des pages traduites. »*

**P7 — Verticales cacao et vins/spiritueux**
> *Prompt : « Duplique le pattern /solutions/cafe pour /solutions/cacao
> et /solutions/vins-spiritueux : récit qualité (T° de cale, chai à
> fûts), gabarits de fiches produit, dataviz CO₂, capture de lead par
> verticale, i18n fr/en. Active les tuiles correspondantes de la
> landing. »*

**P8 — Cadence éditoriale du carnet**
> *Prompt : « Outille le carnet : flux RSS, catégories (arrivées,
> chantier, équipage, clients), gabarit d'article avec photos, et rédige
> les 6 premiers articles à partir des faits du dossier (livraisons
> Atlantis/Asterias/Atlas/Archimedes, ligne Brésil, arrivées café). »*

### Chantier C — La mesure (marketing ops)

**P9 — Étendre l'instrumentation du funnel**
> *Prompt : « Étends analytics_events : vues /solutions/*, /impact,
> /preuves, contact_submitted, verify_lookup (scans QR), kit_generated,
> kit_download, voyage_page_view, rebooking ; capture UTM dans detail ;
> ajoute au dashboard commercial staff une vue “B2B2C” (scans QR, kits,
> pages voyage) et une vue “funnel complet” avec les cibles du rapport
> (conversion ≥5 %, self-service 30 %). »*

**P10 — Publier la fiabilité**
> *Prompt : « Calcule le taux de service par ligne (|ATA−ETA| < 24 h,
> on_time_pct existant), affiche-le sur les fiches routes et une section
> “Nos départs tenus” de la landing, avec la méthodologie ; ajoute
> l'alerte interne si le taux passe sous 90 %. »*

### Chantier D — Go-to-market (commercial/marque — partiellement hors code)

**P11 — Programme comptes-ancres et club chargeurs café**
Contrats de capacité pluriannuels (volumes garantis ↔ priorité + tarif +
co-branding), inspiré du verrouillage Windcoop (sans ouverture de
capital) ; s'appuyer sur `PLAN_GRILLES_MULTIROUTES.md` (déjà prêt) pour
le volet grilles tarifaires.
> *Prompt : « Implémente les grilles tarifaires multi-routes selon
> docs/strategy/PLAN_GRILLES_MULTIROUTES.md et ajoute au CRM/commercial
> la notion de compte-ancre (engagement volume annuel, priorité de
> capacité, statut co-branding) visible dans le backoffice booking. »*

**P12 — Calendrier événementiel et social kit**
Plan 12 mois : 4 livraisons de navires + arrivées café = ~15 moments
médias ; formats LinkedIn prêts à poster pour les clients (co-brandés,
générés depuis le kit).
> *Prompt : « Ajoute au kit B2B2C un volet “social” : 3 visuels prêts à
> poster (formats LinkedIn/Instagram) générés par expédition avec la
> charte Nouvelle Étoile, le chiffre CO₂ du lot et le QR voyage ;
> prépare le rétroplanning des moments médias 2026-2027 à partir du
> planning flotte. »*

**P13 — API de preuves et widget embarquable** (après P2)
> *Prompt : « Expose une API publique en lecture `/api/v1/proof/{ref}`
> (JSON : CO₂, méthode, navire, dates, lien voyage) et un widget
> embarquable “Shipped by sail — NEWTOWT” (script + iframe conforme CSP)
> que les torréfacteurs peuvent poser sur leurs fiches produit. »*

**P14 — Pack conformité EUDR** (fenêtre : avant le 30/12/2026)
> *Prompt : « Ajoute au portail expéditeur un emplacement documentaire
> “EUDR” par lot (référence DDS, géolocalisation d'origine fournie par le
> client, chaîne de custody transport générée par nos données) exporté
> dans le dossier de preuve du booking. »*

### Ce que je recommande de lancer en premier

**P1 + P2 la même semaine** (c'est la clé de voûte : la donnée existe,
le récit existe, il manque la jonction), puis **P3 sans attendre**
(l'échéance ECGT du 27/09/2026 est la seule date dure), puis **P5/P6**
(confiance + Brésil). Les visuels et le storytelling détaillé (textes de
la page voyage, articles du carnet, social kit) se définiront pas à pas
sur la base de la maison de message §3.2 — chaque prompt ci-dessus peut
être raffiné ensemble avant lancement.

---

## Annexe A — Réponses directes aux questions posées

1. **« L'ERP est-il cohérent ? »** Oui — architecture saine, discours et
   produit alignés à ~80 %, chaîne de preuve unique dans le secteur ;
   3 jonctions manquantes (cale→client, voyage→public, claims→fichiers)
   et un référentiel à harmoniser. Cf. Partie 1.
2. **« B2B ou B2B2C ? »** B2B contractuel, **B2B2C par conception** — le
   consommateur final n'est pas notre client mais notre argument de
   vente ; l'outillage du dernier maillon (QR → page voyage) est la
   priorité produit n°1. Cf. §2.5.
3. **« Le message clé ? »** « Nous ne vendons pas du fret : nous livrons,
   avec chaque palette, la qualité intacte, le carbone évité certifié et
   l'histoire qui fait vendre. » — décliné par persona et par canal,
   sous la promesse existante « Votre marchandise traverse l'Atlantique
   sans la réchauffer ». Cf. §3.2.
4. **« Le site répond-il à la demande ? »** Le socle est au-dessus du
   marché (devis/booking sans compte, /preuves, /verify, /fleet) ; les
   manques sont la preuve sociale, la page consommateur, le PT-BR, les
   fichiers de preuve réels et la mesure. Cf. Parties 1 et 3.

## Annexe B — Limites et données à revalider

- Recherches web du 01/07/2026 : plusieurs sites (dont towt.eu,
  graindesail.com, worldofcoffee.org) ont refusé le fetch direct (403
  proxy) — faits recoupés via presse et extraits indexés.
- Les ratios €/tCO₂ évitée (voile vs book & claim) sont des
  **estimations d'ordre de grandeur**, à recalculer avec nos coûts réels
  avant tout usage commercial.
- Chiffres de fréquentation définitifs de WoC Bruxelles 2026 non publiés
  au 01/07/2026.
- Effectif repris lors de la reprise : 37/48 (JMM) vs 40/45 (France 3) —
  à faire confirmer en interne avant toute communication.
- Capacité commerciale par navire (850 vs 978 EPAL, 1 100 t presse vs
  821 t café référentiel) : l'arbitrage P4 fait foi.

## Annexe C — Sources principales (sélection)

**Reprise TOWT → NEWTOWT** : Journal de la Marine Marchande (05/2026) ;
France 3 Normandie (05/2026) ; Voxlog « Towt maintient le cap et devient
Newtowt » ; Normandie Maritime ; Supply Chain Magazine « NewTowt reprend
la mer, cap sur le Brésil » ; Le Journal des Entreprises (Piriou, 04/2026) ;
Figaro Nautisme « Anemos et Artemis : le café le plus décarboné du monde »
(04/01/2026).
**Concurrence** : Mer et Marine (Neoliner Origin, GDS II/III, Windcoop
Miaraka, De Gallant) ; Neoline.eu (communiqués, clients) ; CMA CGM Group
(escale Neoliner) ; wind.coop ; vela-transport.com + Le Journal des
Entreprises (levée 40 M€, contrat Takeda) ; Zéphyr & Borée (Canopée,
99,6 % dispo) ; NOEMA « The Quest for Clean Cargo » (Sailcargo,
Fairtransport, EcoClipper).
**Substituts & réglementation** : Maersk ECO Delivery + SBTi (02/2024) ;
CMA CGM ACT+ ; Hapag-Lloyd Ship Green ; GCMD (prix carbone ~300 $/t) ;
Commission européenne (FAQ ETS maritime, Q&A FuelEU) ; DNV (MRV 400 GT) ;
Portail RSE gouv + Norton Rose Fulbright (Omnibus CSRD) ; Conseil UE
(EUDR 04+18/12/2025) ; Hogan Lovells (paquet simplification 05/2026) ;
Senken/ClimateJargonBuster (Green Claims retirée, ECGT 27/09/2026) ; IWSA
(cap des 100 navires) ; Norsepower (02/2026) ; bound4blue.
**Café & B2B2C** : europe.worldofcoffee.org ; Comunicaffe ; Daily Coffee
News (WoC 2026, New Orleans 2027, Principles of Procurement 03/2026) ;
Sprudge ; Bpifrance Big Média (Belco) ; Terre Majeure (Belco × TOWT) ;
belco.fr/durabilité ; Terres de Café (70 %/100 % voile) ; Malongo ;
Retail Insider + Newswire (Café William, Wind Series/Costco) ; Circuits
Bio (surcoût +1 €/kg, +0,30 €/250 g) ; CBI.eu (demande café Europe,
certifié) ; Fortune Business Insights (specialty Europe) ; SAGE/MDPI/
ResearchGate (WTP café durable) ; Deloitte Gen Z Survey 2025 ; Arbor
(PwC +9,7 %, Forrester +15 %) ; Wikipédia TOWT (label ANEMOS 2017) ;
Ethiquable/Process Alimentaire/Valrhona (Windcoop, Lobodis).

**Sources internes** : `docs/strategy/00-vision.md` ;
`docs/audit/2026-06-10-repo-audit.md` ; `docs/audit/2026-06-12-audit-360/`
(6 volets) ; `docs/audit/AUDIT_V2_V3_RAPPORT_ECARTS_ET_PLAN.md` ;
`docs/audit/backlog/ARBITRAGES.md` ; `docs/operations/stowage-cargo-rules.md` ;
`docs/design/newtowt-design-tokens.json` ; `docs/personas/01-personas.md` ;
`docs/analytics/01-data-strategy.md` ; `docs/vitrine-construction-plan.md` ;
code `app/` (routers, models, templates, i18n) au commit `f43fc8b`.
