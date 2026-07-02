# Audit des allégations environnementales — conformité ECGT

> Paquet P3 du rapport `RAPPORT_ARCHITECTURE_UNIQUE.md` (ENV-05/ENV-08).
> Statut : v1.0 — 1er juillet 2026. Échéance réglementaire : **27 septembre
> 2026** (transposition de la directive (UE) 2024/825 « Empowering Consumers
> for the Green Transition », ECGT).

## 1. Ce que l'ECGT interdit (rappel opérationnel)

À compter du 27/09/2026, dans la communication vers les consommateurs :

1. **Allégations environnementales génériques** (« vert », « écologique »,
   « respectueux de l'environnement », « durable », « décarboné » en absolu…)
   **sans excellente performance environnementale reconnue** et démontrable
   attachée à l'allégation.
2. **Allégations de neutralité fondées sur la compensation** (« neutre en
   carbone », « climatiquement neutre » via crédits/offsets) portant sur le
   produit.
3. **Labels de durabilité** non fondés sur un système de certification
   (tierce partie) ou non établis par une autorité publique.
4. **Allégations sur des performances futures** sans engagements clairs,
   objectifs datés et suivi indépendant.
5. Comparaisons environnementales sans base objective vérifiable.

Notre position structurelle est **favorable** : décarbonation physique à la
source, mesurée par lot, méthode publiée, registre de vérification public.
L'audit vise les formulations, pas le fond.

## 2. Doctrine de langage NEWTOWT (à appliquer partout)

- Toujours **des kg absolus de CO₂ évité par expédition**, jamais de
  pourcentage flottant ni de superlatif (« le plus décarboné… »).
- Jamais « **neutre en carbone** », jamais compensation, jamais « produit
  décarboné » (l'allégation porte sur **le transport maritime du lot**).
- « **Certificat Anemos** » (document émis par NEWTOWT) — jamais « label »
  (déjà retiré, PR #115-117), et éviter le **verbe « certifié » isolé** :
  l'ancrer à la réalité (« méthode publiée », « émissions vérifiées EU MRV »,
  « vérifiable en ligne »).
- Ratio autorisé : « **divisées par neuf** » (13,7 / 1,5 = 9,1 — ne jamais
  arrondir vers le haut).
- Toute page portant une allégation doit **lier la substantiation**
  (`/preuves`, méthodologie PDF, `/verify`).

## 3. Constats et traitement (inventaire du 01/07/2026)

| # | Emplacement | Allégation | Risque ECGT | Traitement |
|---|---|---|---|---|
| 1 | `/preuves` (§1 + CTA final) | « Télécharger la méthodologie (PDF) », « Rapport CO₂ annuel (exemple) », « Kit RSE co-brandable » — **liens factices** | Élevé (allégation de preuve non servie) | ✅ **Corrigé** : PDF méthodologie réel (`/preuves/methodologie.pdf`, facteurs versionnés imprimés, fr/en), spécimen de rapport annuel réel (`/preuves/rapport-annuel-exemple.pdf`, marqué SPÉCIMEN), kit RSE reformulé « se prépare avec notre équipe » → `/contact` |
| 2 | `/about` (« Nos engagements ») | « Émissions **divisées par dix** vs cargo conventionnel » | Élevé (surestimation : 13,7/1,5 = 9,1) | ✅ **Corrigé** : « divisées par neuf … (13,7 → 1,5 g CO₂/t·km, méthode publiée) » (fr + en) |
| 3 | `/passagers` | « **Décarboné par nature** » (absolu, générique, service 2027 non opéré) | Élevé | ✅ **Corrigé** : « Propulsé par le vent » + « empreinte carbone du trajet » |
| 4 | `/impact` (titre §Décarbonation + JSON-LD FAQ) | « décarbonation mesurée et **certifiée** », « notre **certification** d'émissions évitées » (auto-certification implicite) | Moyen-élevé (ENV-08 : auto-label) | ✅ **Corrigé** : « mesurée et **vérifiable** », « notre **certificat** d'émissions évitées (méthode publiée, émissions des navires vérifiées EU MRV) », JSON-LD aligné |
| 5 | Footer sitewide + PDF certificat (`brand.mention`) | « Pionnier du transport maritime **décarboné** depuis 2011 » | Moyen (générique/absolu, mais substantiation à 1 clic) | 🟡 **Décision de marque** — options : (a) statu quo documenté (substantiation via /preuves liée dans le même footer) ; (b) « Pionnier du transport de marchandises **à la voile** depuis 2011 » (zéro risque, factuel) ; (c) « … bas carbone … ». Recommandation : **(b)**. Non appliqué unilatéralement (baseline de marque, présente aussi sur le PDF certificat) |
| 6 | Landing (hero lead, bandeau) | « réduction de CO₂ **certifiée Anemos** », « CO₂ **mesuré & certifié** — certificat Anemos » | Moyen | 🟢 **Conservé, documenté** : l'allégation nomme le document (certificat Anemos), la landing lie /preuves et /verify ; à re-tester après vérification tierce (cf. CDC). Ne jamais employer « certifié » sans « Anemos » ou sans lien de preuve |
| 7 | Meta/SEO (`home_meta_title`, description layout, `rd_meta_desc`, chip `rd_decarbonised`) | « transport maritime décarboné (à la voile) » | Faible-moyen | 🟢 **Conservé, documenté** : « décarboné » y est adossé au mécanisme (voile) et les pages liées portent chiffres + certificat ; requête SEO structurante. À réévaluer si la DGCCRF publie une ligne plus stricte |
| 8 | `/solutions/cafe` | « CO₂ évité par lot **certifié Anemos et vérifiable par QR** » | Faible | 🟢 Conforme (allégation spécifique + vérifiabilité immédiate) |
| 9 | `/impact` (qualité) | « surveillance continue de la température et de l'humidité dans chaque cale » | Faible depuis P1 | 🟢 Substantié : relevés exposés au client/portail, Carnet de Bord téléchargeable, données sur la page publique de voyage |
| 10 | Kit B2B2C + page voyage (P2) | Chiffres par lot + QR | Faible | 🟢 Conforme par conception (kg absolus, référence de certificat, lien de vérification) ; la méthodologie §7 donne désormais la **notice de bon usage pour nos clients** (ne pas étendre l'allégation au produit) |
| 11 | `/about/anemos` | Formule + facteurs publiés | Faible | 🟢 Conforme — rester la page de référence en ligne, le PDF devient la version opposable datée/versionnée |
| 12 | Équivalences CO₂ (arbres / vols / km camion) | Dataviz d'équivalences | Faible-moyen | 🟡 **À faire (suivi)** : afficher la source des ratios d'équivalence à proximité immédiate (une ligne « base de calcul : … ») sur `_co2_equivalences*.html` |

## 4. Ce que la directive ne nous impose PAS (à ne pas sur-corriger)

- L'ECGT n'interdit pas les allégations **spécifiques, chiffrées et
  substantiées** — c'est exactement notre modèle : ne pas appauvrir le
  discours, le sourcer.
- Le « certificat Anemos » n'est pas un « label de durabilité » au sens du
  texte tant qu'il est présenté comme **notre attestation documentaire par
  expédition** (méthode publiée, registre public) et non comme un badge de
  conformité tiers. La vérification tierce (CDC joint) transforme ce point de
  vigilance en avantage définitif.

## 5. Suites

1. **CDC vérification tierce** : `CDC_VERIFICATION_TIERCE_ANEMOS.md` —
   consultation à lancer immédiatement (rétro-planning avant le 27/09/2026).
2. Arbitrer le point 5 (baseline footer) — décision de marque.
3. Point 12 : sourcer les équivalences (petite PR dédiée).
4. Ré-audit rapide des supports **hors site** (kits PDF envoyés, posts
   LinkedIn, dossiers commerciaux) avec la même grille §2 avant le 15/09/2026.
5. Inscrire la grille §2 dans le processus éditorial (relecture de toute
   nouvelle page/clé i18n contre cette doctrine).
