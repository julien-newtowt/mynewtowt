# Audit d'alignement — MyTOWT × Méthodologie de performance environnementale v3.0

| | |
|---|---|
| **Date** | 2026-09-11 |
| **Objet** | Compatibilité du module MRV / environnement de MyTOWT avec `Environmental Performance Methodology - MRV and Operational approaches - v3.0 EN` (10/09/2026) |
| **État de l'application auditée** | `main` à `bd124505` — module MRV dédié (PR #204) et pipeline commercial (PR #205) fusionnés |
| **Nature** | Audit de conformité **en lecture seule**. Aucune ligne de code modifiée. |
| **Statut de la méthodologie** | **Proposition en attente d'approbation** du Responsable Environnement. Les écarts ci-dessous sont donc à lire comme « l'application ne suit pas encore une référence qui n'est pas encore approuvée », pas comme des défauts avérés. |

> **Périmètre.** L'audit porte sur les grandeurs *calculées et affichées* par
> MyTOWT. Il ne porte pas sur les procédures de production de la donnée (Noon /
> Carbon Reports, relevés ROB, BDN), qui relèvent du **Manuel SMS chapitre
> VII.8** — cadre primaire sur ce périmètre (méthodologie §1.5) et qui n'est pas
> dans ce dépôt.

---

## 1. Synthèse

**Le moteur de calcul est bien plus proche de la méthodologie que ne le laisse
penser le vocabulaire employé à l'écran.** Les trois assiettes de consommation
dont la méthodologie a besoin sont déjà calculées, séparées et correctes. Les
deux approches — MRV et Métier — existent déjà sous les noms « méthode C » et
« méthode B ». La règle d'agrégation la plus facile à rater (« jamais une
moyenne de ratios ») est respectée, et la règle du zéro vrai des voyages sur
lest aussi.

**Trois écarts, en revanche, portent réellement à conséquence**, et ils ne sont
pas de même nature :

1. 🔴 **Ce qui sort vers le client repose sur une base de comparaison que la
   méthodologie a explicitement retirée** (le conventionnel 13,7 gCO₂/t.km).
   C'est le seul écart qui expose l'entreprise, et son échéance est proche.
2. 🟠 **L'approche Métier est calculée avec le numérateur de l'approche MRV** :
   le mouillage manque au numérateur alors que la méthodologie l'y met.
   L'application dispose pourtant déjà du bon chiffre — il n'est pas branché.
3. 🟠 **Le profil de propulsion — désormais l'indicateur de tête — est dilué**
   par les tranches à l'arrêt, que la méthodologie exclut explicitement du
   dénominateur.

**Deux blocs entiers de la méthodologie n'existent pas** dans l'application : la
base de décarbonation « nous-mêmes sans voiles » (§11.2) et la simulation
carburant HVO (§12).

**Le risque principal n'est pas un risque de calcul, c'est un risque
d'allégation.** Le calcul réglementaire (MRV) est essentiellement juste ; c'est
la comparaison publiée qui ne l'est plus.

---

## 2. Ce qui est déjà aligné

Ce chapitre existe parce qu'un audit qui ne liste que les écarts fait croire à
une refonte là où il s'agit de retouches. **La structure est bonne.**

| Exigence de la méthodologie | État dans MyTOWT | Référence |
|---|---|---|
| Facteur CO₂ MDO **3,206** tCO₂/t, TtW, MEPC.391(81) | ✅ exact, versionné en base + repli codé | [referential_env.py:137](../../app/services/referential_env.py#L137) |
| Facteurs CH₄ / N₂O de la même ligne MEPC.391(81) | ✅ 0,00005 / 0,00018 — reconstituent exactement l'écart de 0,0491 t annoncé au §4.2 | [referential_env.py:138-139](../../app/services/referential_env.py#L138) |
| Intensité WtT amont **17,7** gCO₂eq/MJ, **distincte** du TtW, jamais sommée en silence | ✅ grandeur séparée, documentée comme telle | [emission_ledger.py:157](../../app/services/emission_ledger.py#L157) |
| PCI du MDO **42,7 MJ/kg** | ✅ `MDO_LHV_MJ_PER_T = 42700` | [emission_ledger.py:66](../../app/services/emission_ledger.py#L66) |
| Assiette MRV = conso **hors mouillage** (§1.2, §4.4) | ✅ `do_consumed = conso_hors_mouillage` | [emission_ledger.py:659](../../app/services/emission_ledger.py#L659) |
| Assiette Métier = conso **berth-to-berth, mouillage inclus** | ✅ **calculée** (`conso_total_t`, mouillage compris, escale exclue) — mais non branchée, cf. écart E2 | [inter_event_compute.py:560](../../app/services/inter_event_compute.py#L560) |
| Escale **exclue des deux approches**, portée séparément | ✅ calculée à part par continuité ROB, jamais sommée aux intensités | [emission_ledger.py:425](../../app/services/emission_ledger.py#L425) |
| Charge de référence **770 t = 1 100 × 70 %** | ✅ `vessel_capacity_ref_t` = 1100, `occupancy_rate_pct` = 70, paramétrables | [kpi_env.py:112](../../app/services/kpi_env.py#L112) |
| Cargo MRV = **0 sur lest** (règl. 2016/1928) | ✅ dérivé, jamais saisi | [inter_event_compute.py:508](../../app/services/inter_event_compute.py#L508) |
| Cargo MRV = *deadweight carried* **déclaré par le bord**, jamais recalculé à terre (§6.1) | ✅ saisi au Departure, porté tel quel | [inter_event_compute.py:502](../../app/services/inter_event_compute.py#L502) |
| **Jamais une moyenne de ratios** : sommer les absolus, puis le ratio (§8.2 r.1) | ✅ `aggregate_ef` somme CO₂ et t.km séparément | [kpi_env.py:436](../../app/services/kpi_env.py#L436) |
| **Zéro vrai conservé dans les agrégats** : le sur-lest apporte son CO₂ sans t.km (§8.2 r.3) | ✅ explicitement documenté et implémenté | [kpi_env.py:429-433](../../app/services/kpi_env.py#L429) |
| Trois lectures distinctes, jamais mélangées (A = B/L, B = Métier, C = MRV) | ✅ `_check_method`, sélecteur explicite | [kpi_env.py:371](../../app/services/kpi_env.py#L371) |
| Profil de propulsion sur **6 créneaux / jour, résolution 4 h** | ✅ `NAV_TIME_SLOTS` = 16/20/00/04/08/12 | [nav_event.py:105](../../app/models/nav_event.py#L105) |
| Voile = au moins une des voiles `ON` ; moteur = charge > 0 | ✅ `j0 ∨ fwd_j1 ∨ fwd_ms ∨ aft_j1 ∨ aft_ms` | [kpi_env.py:726](../../app/services/kpi_env.py#L726) |
| Créneau **sans relevé** ignoré, jamais compté « à l'arrêt » (§7.3) | ✅ exclu du dénominateur, documenté | [kpi_env.py:757](../../app/services/kpi_env.py#L757) |
| Conversion **1 nm = 1,852 km**, systématique | ✅ `NM_TO_KM` | [co2.py:27](../../app/services/co2.py#L27) |
| Aucune correction silencieuse de la donnée source (§6.5) | ✅ moteur de règles R01-R26, résultats horodatés, seuils snapshottés | `validation_engine` |
| Dépendance du profil à la **complétude des Noon Reports**, à rendre visible (§7.6 l. 3) | ✅ règle **R29**, informative et jamais bloquante, sur la complétude des relevés de voilure | [validation_engine.py:1214](../../app/services/validation_engine.py#L1214) |
| Marquage provisoire d'un voyage non vérifié (§6.6) | ✅ statuts `brouillon`/`finalise`/`valide_siege`, brouillons exclus des calculs | `event_capture` |

> **Un point mérite d'être relevé au crédit de l'application** : la méthodologie
> note au §4.6 que « *l'outil ne calcule pas encore la consommation d'escale — 
> elle est lue mais pas publiée* ». **MyTOWT, lui, la calcule** (continuité ROB,
> repli sur delta de compteurs). C'est précisément la grandeur qui manquait pour
> réconcilier nos intensités avec la valeur publiée sur THETIS-MRV.

---

## 3. Écarts

### E1 🔴 — Ce qui sort vers le client repose sur une base retirée

**Fait.** Toute la chaîne « CO₂ évité » de MyTOWT compare nos émissions à un
**cargo conventionnel à 13,7 gCO₂/t.km** :

- [co2.py:26](../../app/services/co2.py#L26) — `CONV_CO2_EF_G_PER_TKM = 13.7`
- [anemos.py:196](../../app/services/anemos.py#L196) — **certificats CO₂ Anemos** (par booking et RSE annuel), remis au client
- [carnet_bord.py:517](../../app/services/carnet_bord.py#L517) — **carnet de bord public**, valeurs codées en dur
- [dashboard_kpis.py:66](../../app/services/dashboard_kpis.py#L66), [kpi.py:128](../../app/services/kpi.py#L128), [emission_ledger.py:707](../../app/services/emission_ledger.py#L707)
- [kpi_env.py:113](../../app/services/kpi_env.py#L113) — `ef_container_ship_gco2_tkm` = 16, paramètre du dashboard

**Et surtout, sur la vitrine publique** — c'est le point le plus exposé :

- [vitrine_router.py:198-235](../../app/routers/vitrine_router.py#L198) — la page
  **`/preuves`**, dont l'objet même est de *substantier* nos allégations, publie
  un CO₂ évité calculé à 13,7 ;
- [vitrine_router.py:140](../../app/routers/vitrine_router.py#L140) —
  l'application **génère et sert un PDF de méthodologie public**
  (`NEWTOWT_Methodologie_Anemos_v…pdf`), construit sur les facteurs 1,5 / 13,7 ;
- [public_router.py:396](../../app/routers/public_router.py#L396) — CO₂ évité par
  palette sur les pages de routes ;
- `landing.html`, `about_anemos.html`, `verify.html`, `devis_result.html`.

> **Deux méthodologies coexistent donc publiquement** : celle que MyTOWT sert en
> PDF depuis `/preuves` (modèle Anemos, 1,5 / 13,7) et la v3.0 interne, qui a
> écarté cette base. La v3.0 §1.4 prévoit justement une **note de méthode
> opposable** extraite d'elle — rédigée le 10/09/2026 et en attente
> d'approbation. Le PDF servi par l'application en tient aujourd'hui lieu, sur
> l'ancien modèle.

**Ce que dit la méthodologie.** §11.1 : cette base — le porte-conteneurs,
`EFct` = 13,7 de la v2.2 — **est écartée**, décision du 8 septembre 2026, au
motif que la relation entre la taille d'un navire et son facteur d'émission
n'est pas linéaire : le segment retenu détermine le résultat sur une plage de
87 % à 96 %. Et la méthodologie ajoute : « ***Ne pas réintroduire cette base***
sans une nouvelle décision explicite. »

**Conséquence.** Les certificats Anemos et le carnet de bord publient
aujourd'hui un taux de décarbonation construit sur exactement la base que la
méthodologie juge non défendable au titre de la directive (UE) 2024/825 —
laquelle est **en application pleine au 27 septembre 2026**, soit dans 16 jours.

**Second écart, de même famille** : le comparateur aérien du dashboard vaut
**800** gCO₂/t.km ([kpi_env.py:114](../../app/services/kpi_env.py#L114)) là où la
méthodologie §11.3 retient **630** (0,63 kgCO₂e/t.km, part *Opération* seule de
la Base Empreinte ADEME, le total de 0,80 incluant l'amont). L'application
compare donc un TtW à un WtW — l'erreur de périmètre que le §4.1 désigne comme
« la plus facile à commettre de bonne foi » — et **surestime de 27 %** la
référence aérienne.

> **Nuance à ne pas perdre.** `co2.estimate` reste légitime comme
> *comparateur conventionnel de devis* : c'est un ordre de grandeur commercial,
> pas une allégation environnementale. L'écart naît de son usage **dans un
> certificat remis au client**, pas de son existence.

---

### E2 🟠 — L'approche Métier est calculée avec le numérateur de l'approche MRV

**Fait.** Les trois méthodes A/B/C partagent le même numérateur,
`summary.co2_t`, qui est l'émission de l'assiette **hors mouillage**
([kpi_env.py:327](../../app/services/kpi_env.py#L327) → [emission_ledger.py:659](../../app/services/emission_ledger.py#L659)).

**Ce que dit la méthodologie.** §1.2 et §9.1 : le numérateur de l'approche
Métier est `CO2_i^Op = Fc_i × F_CO2`, sur la consommation **berth-to-berth,
mouillage et dérive inclus**. Seule l'approche MRV prend `Fc_i^MRV`. Le §10
range la lecture B/L du même côté que Métier.

| Lecture | Numérateur attendu | Numérateur appliqué | Verdict |
|---|---|---|---|
| Méthode C = `EF^MRV` | hors mouillage | hors mouillage | ✅ |
| Méthode B = `EF^Op` | **mouillage inclus** | hors mouillage | ❌ |
| Méthode A = `EF^BL` | **mouillage inclus** | hors mouillage | ❌ |

**Ampleur mesurée.** Sur la période, un seul voyage a mouillé — 3AVNRE6 —
pour **1,96 tCO₂**, soit 0,1 % (méthodologie §4.4, tableau §13). **L'écart est
donc aujourd'hui négligeable en valeur.** Il ne l'est pas en principe : la
méthodologie insiste pour que la distinction soit tranchée « **avant** qu'elle
ne devienne significative, pas après ».

**Le correctif est petit.** L'application calcule déjà le bon chiffre :
`co2_with_anchoring_t` = trajet + mouillage
([mrv_emission_views.py:131](../../app/services/mrv_emission_views.py#L131))
**est exactement** `CO2_i^Op`. Il n'est simplement pas branché sur la méthode B.

**Écart de vocabulaire associé, et il compte.** L'application présente ce total
comme « **hors MRV**, à réserver à l'analyse interne »
([fr.py:1594](../../app/i18n/fr.py#L1594)). C'est juste au regard de l'approche
MRV, et trompeur au regard de la méthodologie : le mouillage est un composant
**publié** de l'approche Métier, qui s'adresse aux chargeurs, à la direction et
au reporting RSE. Le présenter comme un indicateur interne le déclasse.

---

### E3 🟠 — Le profil de propulsion est dilué par les tranches à l'arrêt

**Fait.** Le dénominateur des pourcentages est `filled_slots`, c'est-à-dire
**toutes** les tranches porteuses d'un relevé — catégorie `statique` comprise
([kpi_env.py:781-786](../../app/services/kpi_env.py#L781)).

**Ce que dit la méthodologie.** §7.2 : « *Stopped* … **exclu du dénominateur** :
le navire ne fait pas route ». §7.4 : « part du mode m = tranches en mode m /
tranches **de navigation** ». Et l'avertissement du §7.2 vise mot pour mot ce
défaut : « les pourcentages publiés se rapportent au **temps de navigation**, et
non au temps calendaire — formulation à respecter, faute de quoi le chiffre
serait **dilué par les jours à quai** ».

**Conséquence.** MyTOWT **sous-estime** la part de voile de tout le facteur
`statique / (navigation + statique)`. Le sens de l'erreur est prudent, mais le
chiffre ne peut pas coïncider avec les 36,1 % / 84,7 % publiés par la
méthodologie — deux sources internes donneraient deux chiffres différents pour
le même indicateur, désormais **indicateur de communication de tête**.

**Second manque, structurel.** `propulsion_profile` n'existe **qu'au voyage**
([kpi_env.py:837](../../app/services/kpi_env.py#L837)). Aucune agrégation flotte
ni période. Or la méthodologie publie des chiffres de **périmètre** (4 097
tranches flotte, 3 170 sur 28 voyages commerciaux, 617 sur les 5 voyages 2026) —
et exige que l'agrégation se fasse **par cumul de tranches, jamais par moyenne
des pourcentages de voyage** (§7.4). L'indicateur de tête n'est donc pas
produisible à l'échelle où il est communiqué.

---

### E4 🟡 — GWP : AR4 dans l'application, AR5 dans la méthodologie

**Fait.** [emission_ledger.py:88-89](../../app/services/emission_ledger.py#L88) :
`GWP_CH4 = 25`, `GWP_N2O = 298`, au motif « Annexe I, règlement EU 2015/757 ».

**Ce que dit la méthodologie.** §4.2 et hypothèse **A11** : GWP100 **AR5**,
**CH₄ = 28, N₂O = 265**, valeurs de MEPC.391(81) §2.4 — donc *citables* et non
simplement défendables.

**Ampleur, exacte.** Le CO₂eq TtW du MDO vaut **3,26089** t/t dans
l'application contre **3,2551** t/t dans la méthodologie : **+0,18 %**.

**Portée limitée, mais réelle.** Les intensités publiées sont en **CO₂ seul**
(facteur 3,206) : elles ne bougent pas. Le facteur GES ne sert qu'au **taux de
décarbonation** (§4.3) — que l'application ne calcule pas encore. L'écart est
donc aujourd'hui sans effet sur un chiffre publié, mais il porterait dès que la
base « sans voiles » sera implémentée.

**À arbitrer plutôt qu'à corriger d'office** : les deux sources sont réelles et
ne visent pas la même obligation. Ce n'est pas une décision d'ingénierie.

---

### E5 🟡 — Distance : le repli n'est pas signalé

**Fait.** La distance canonique est la somme des **haversines entre événements**
([inter_event_compute.py:291](../../app/services/inter_event_compute.py#L291)).
En l'absence de résumé, `kpi_env` retombe **silencieusement** sur
`leg.distance_nm` ([kpi_env.py:330](../../app/services/kpi_env.py#L330)), qui
est une **orthodromie × coefficient d'élongation** — donc une distance
*théorique*.

**Ce que dit la méthodologie.** §5.3 : distance **mesurée** (fond, intégration
de la vitesse le long de la trace GPS satcom), niveau 1 de la hiérarchie
ISO 14083. Et surtout : « **Repli** : si la distance mesurée est indisponible ou
manifestement fausse, l'orthodromie majorée de 15 % s'applique, et **le voyage
est signalé** comme reposant sur une donnée de niveau 2. »

**Deux écarts distincts, de gravité inégale :**

- le repli existe mais **n'est pas signalé** — une intensité calculée sur une
  distance théorique est aujourd'hui indiscernable d'une intensité mesurée.
  C'est le point à corriger ;
- la distance « mesurée » est une **haversine entre événements**, pas une
  intégration de la trace. Déjà identifié comme dette dans `CLAUDE.md`
  (« distance loguée réelle à intégrer »). Sur une route en grand cercle
  l'écart reste modeste ; il grandit avec le routage météo — c'est-à-dire
  précisément sur nos voyages.

---

### E6 🟡 — Voyage sur lest : dash motivé plutôt que tonne de référence

**Fait.** Au voyage, méthodes A et C renvoient `None` + motif `NA_BALLAST`
([kpi_env.py:394](../../app/services/kpi_env.py#L394)) — « pas de valeur
fabriquée ».

**Ce que dit la méthodologie.** §9.2 / hypothèse **A8** : au voyage, le facteur
est calculé **comme si le voyage avait porté 1 tonne**, « ce qui **rend le
voyage sur lest visible** au lieu de le cacher derrière un tiret », marqué comme
tel et **exclu des graphiques multi-voyages**. Dans les agrégats, le zéro vrai
est conservé — ce que l'application fait déjà.

**Divergence assumée, à arbitrer.** La posture de MyTOWT (« un tiret **motivé**
vaut mieux qu'une valeur fabriquée ») est cohérente avec la règle maison « un
KPI structurellement inatteignable est pire qu'un KPI absent ». Celle de la
méthodologie rend le sur-lest impossible à ignorer. **Les deux se défendent ;
elles ne peuvent pas coexister** sans que deux écrans donnent deux réponses.

---

## 4. Ce qui manque entièrement

| Bloc | Référence | Constat |
|---|---|---|
| **Base de décarbonation « nous-mêmes sans voiles »** | §11.2, hypothèse A9 | **Absent.** Aucune trace de `858 kW`, `212 g/kWh`, `4,37 t/j`, du facteur de route directe `1,211` ni d'une vitesse d'essai par navire (ANEMOS 11,36 / ARTEMIS 12,07 / ATLANTIS 11,86 kn). C'est le cœur du taux de décarbonation publiable — et le seul chiffre de comparaison que la méthodologie juge défendable. |
| **Taux de décarbonation `%DR`** | §11.2 | **Absent**, corollaire du précédent. |
| **Vitesse d'essai par navire** | §11.2 | **Aucun champ** au référentiel `Vessel`. La méthodologie insiste : les sisterships diffèrent de ~12 % en puissance à vitesse égale, et prendre ANEMOS pour toute la flotte flattait ARTEMIS de 2,6 points. |
| **Travail de transport publié (t.km)** | §8.1, §9.1, annexe E | Calculé **en interne** comme dénominateur, jamais exposé comme grandeur. L'annexe E le liste pourtant parmi les champs publiés (`transport_work_mrv_t_km`, `transport_work_simulated_t_km`). |
| **Simulation HVO** | §12 | **Absente.** `EmissionFactor` sait porter plusieurs carburants, mais aucune simulation de substitution (+25 % masse, périmètre WtW obligatoire des deux côtés). |
| **Contrôles de vraisemblance du cargo MRV** | §8.3 | La règle « 0 sur lest » est implémentée. Les **deux garde-fous** ne le sont pas : cargo MRV **≥ cargo facturé**, et cargo MRV **≤ port en lourd (1 584,9 t)**. `Vessel.deadweight_t` existe ([vessel.py:123](../../app/models/vessel.py#L123)) mais n'est pas consommé par le moteur de règles. La méthodologie souligne que ces contrôles sont **les deux seules vérifications possibles à terre**, le calcul de déplacement n'existant qu'à bord. |
| **Réconciliation THETIS-MRV** | §4.6 | L'escale est calculée mais n'est **jamais présentée comme ce qu'elle est** : l'écart qui explique pourquoi la valeur officielle THETIS-MRV sera **supérieure** aux deux intensités. Le §4.6 avertit que faute de le dire, un lecteur conclura à une incohérence ou à une sous-déclaration. |

---

## 5. Risques

| # | Risque | Niveau | Raison |
|---|---|---|---|
| R1 | **Allégation environnementale non substantiable** — vitrine publique, page `/preuves`, PDF de méthodologie servi par l'application, certificats Anemos et carnet de bord, tous adossés à une base écartée (E1) | 🔴 **Critique** | Directive (UE) 2024/825 en **application pleine au 27/09/2026**, soit dans 16 jours. La page dont la fonction est de *prouver* nos chiffres les construit sur la base que la méthodologie juge non défendable — et l'application sert son propre PDF de méthodologie, concurrent de la v3.0. |
| R2 | **Deux chiffres internes pour le même indicateur de tête** (E3) | 🟠 Élevé | La méthodologie publie 36,1 % / 84,7 % ; MyTOWT produira mécaniquement moins. Un écart entre l'outil de reporting et l'ERP est le genre d'incohérence qu'un vérificateur trouve en premier. |
| R3 | **L'intensité Métier ne mesure pas ce qu'elle annonce** (E2) | 🟠 Élevé *en principe*, 🟢 faible *en valeur* | 0,1 % aujourd'hui. Le risque est que l'écart devienne significatif sans que personne ne le rouvre. |
| R4 | **Intensité calculée sur distance théorique, indiscernable d'une mesurée** (E5) | 🟡 Modéré | Un voyage sans capture événementielle produit une intensité d'apparence identique, sans marquage de niveau 2. |
| R5 | **Cargo MRV hors plafond non détecté** (§4) | 🟡 Modéré | La donnée n'est vérifiable qu'à bord ; les deux garde-fous à terre sont justement ceux qui manquent. C'est ce type de contrôle qui a permis de détecter l'erreur de saisie des voyages sur lest. |
| R6 | **Divergence GWP sans effet aujourd'hui, active demain** (E4) | 🟢 Faible | Sans portée tant que le taux de décarbonation n'existe pas. À trancher **avant** de l'implémenter, pas après. |

---

## 6. Recommandation

**Ne pas traiter cet audit comme un chantier unique.** Les écarts n'ont ni la
même urgence, ni le même coût, ni le même décideur.

### Lot 1 — Arrêter l'allégation (E1) · urgence réglementaire

Le seul lot dont l'échéance est imposée de l'extérieur. Trois options, par ordre
de coût croissant :

1. **Suspendre l'affichage du CO₂ évité** partout où il sort de l'entreprise —
   vitrine (`/preuves`, landing, routes, `/verify`), PDF de méthodologie servi
   par l'application, certificats Anemos, carnet de bord — tant qu'aucune base
   approuvée n'est disponible, et conserver le chiffre en interne. Coût faible,
   effet immédiat. C'est la seule option tenable avant le 27/09 si le Lot 3
   n'est pas livré d'ici là.
2. **Basculer sur la base « sans voiles »** — suppose le Lot 3 livré.
3. **Corriger au minimum le comparateur aérien** (800 → 630) et retirer le
   paramètre porte-conteneurs, sans attendre le reste.

> **Ce lot n'est pas une décision d'ingénierie.** Il touche ce que l'entreprise
> affirme publiquement : il revient au Responsable Environnement et à la
> direction, l'informatique n'étant que l'exécutant.

### Lot 2 — Rebrancher ce qui est déjà calculé (E2, E3) · coût faible

- brancher la méthode B (et A) sur `co2_total` plutôt que `co2_hors_mouillage` ;
- exclure `statique` du dénominateur du profil de propulsion, et publier le
  nombre de tranches retenues à côté du pourcentage (§7.6 l. 3) ;
- ajouter l'agrégation flotte/période du profil, **par cumul de tranches** ;
- renommer : le mouillage n'est pas « hors MRV / interne » mais « approche
  Métier » ; l'escale n'est pas « périmètre MRV » mais « écart de réconciliation
  THETIS-MRV ».

Aucune formule nouvelle. L'essentiel du lot est du câblage et du vocabulaire —
mais c'est le vocabulaire qui décide de ce qu'un lecteur croit lire.

### Lot 3 — Construire la base « sans voiles » (§4) · coût moyen

Le bloc manquant à plus forte valeur : c'est lui qui rend un taux de
décarbonation publiable. Suppose d'ajouter au référentiel `Vessel` la vitesse
d'essai, la puissance installée et la consommation spécifique, **chacune
tracée à son document de classification** (REF-07 / REF-08). À faire **après**
l'arbitrage GWP (E4), dont il dépend.

### Lot 4 — Garde-fous et traçabilité · coût faible

Contrôles cargo MRV (≥ B/L, ≤ port en lourd), marquage du repli de distance en
niveau 2, exposition du travail de transport. La méthodologie note qu'« *un
garde-fou dont on n'a pas montré qu'il se déclenche ne garde rien* » : prévoir
le test aux bornes.

### Non recommandé à ce stade

**La simulation HVO** (§12). La méthodologie elle-même la donne comme bloquée
sur une réserve non levée — la filière du HVO auprès du fournisseur (§12.5) — et
aucun chiffre ne peut être publié avant. L'implémenter maintenant produirait un
écran dont la seule mention honnête serait « ne pas utiliser ».

---

## 7. Points à arbitrer — hors compétence de l'informatique

1. **E4 — GWP** : AR4 (25/298, EU 2015/757) ou AR5 (28/265, MEPC.391(81)) ?
   Les deux sources sont réelles et ne visent pas la même obligation.
2. **E6 — voyage sur lest** : tonne de référence (méthodologie, rend visible) ou
   tiret motivé (MyTOWT, ne fabrique rien) ? Une seule posture doit survivre.
3. **Périmètre de l'escale** : confirmer qu'elle sort des deux approches et ne
   sert qu'à la réconciliation THETIS-MRV. La décision maison du 2026-09-04
   (« port emissions = émissions d'escale », périmètre MRV) est **antérieure** à
   la méthodologie v3.0 et la contredit.
4. **Statut de la méthodologie** : tant qu'elle n'est pas approuvée par le
   Responsable Environnement ni enregistrée au module QHSE de MARAD™ (§14.2),
   aligner MyTOWT dessus revient à suivre une cible mobile. L'ordre naturel est
   approbation d'abord, alignement ensuite — **sauf pour le Lot 1**, dont
   l'échéance ne dépend pas de cette approbation.

---

## 8. Ce que cet audit n'a pas vérifié

- **La justesse des données sources** (Noon / Carbon Reports, ROB, BDN) : hors
  dépôt, et régie par le SMS.
- **L'outil « Reporting provisoire traversées »**, qui produit aujourd'hui les
  chiffres de la méthodologie. Le recouvrement fonctionnel avec le module MRV de
  MyTOWT est réel et n'a pas été instruit : **lequel des deux fait foi** est une
  question ouverte, et elle conditionne l'ampleur de tout alignement.
- **Les valeurs en production** : aucun accès. Les constats portent sur le code,
  pas sur les chiffres réellement affichés aujourd'hui.

---

## 9. Ce que les audits ont trouvé après coup (2026-09-11, second passage)

Quatre audits indépendants ont été lancés une fois le travail réputé terminé.
**Ils ont trouvé que le Lot 1 était très incomplet**, et la nature de l'oubli
mérite d'être consignée plus que la liste elle-même.

### La cause racine : une garantie affirmée mais jamais écrite

Un commentaire de `app/services/anemos.py` annonçait qu'une sentinelle
`tests/regression/test_no_outward_co2_claim.py` ferait échouer la suite si un
gabarit sortant réaffichait les champs retirés. **Ce fichier n'existait pas.**
La garantie était fausse, et c'est très probablement ce qui a fait croire le
travail achevé. Quinze surfaces ont survécu au premier passage.

La sentinelle existe désormais, et elle a été écrite **avant** de reprendre les
retraits : c'est elle qui a fourni la liste, pas la mémoire. Elle porte quatre
filets, chacun né d'un oubli réel :

1. les **facteurs** (13,7 / 1,5) sur les surfaces sortantes ;
2. les **variables** d'évitement rendues dans un gabarit ;
3. les **catalogues i18n** — c'est par là que la formule
   `(13,7 − 1,5) × tonnage × distance` survivait sur `/preuves` ;
4. les **mots** — le kit B2B2C avait échappé aux trois premiers en nommant sa
   variable `co2_kg`, parfaitement générique.

### Les oublis les plus graves

| Surface | Ce qu'elle portait encore |
|---|---|
| **`/preuves`** | la formule elle-même, sur la page dont l'objet est de *substantier* |
| **Rapport RSE annuel (PDF + CSV)** | remis au client pour son Bilan Carbone scope 3, il citait 13,7 en note de méthode |
| **Carnet de bord, ch. 5** | pire qu'avant : les certificats passés à `NULL` par la migration `0146` y imprimaient « **0 kg** » et « **0 %** » — un chiffre fabriqué là où il n'y avait plus de donnée |
| **Cartes sociales SVG** | la surface la plus **diffusée** de toutes, faite pour être republiée |
| Kit B2B2C, `/devis`, `/voyage/{ref}`, tableau de bord client, landing, `/impact`, récits café/cacao | idem, plus un **troisième script** JS à repli codé |

### Deux erreurs commises pendant la correction, corrigées

- **J'ai emporté le QR de vérification** du kit avec l'allégation — une
  régression fonctionnelle que personne n'avait demandée. Restauré : le QR reste
  utile, il mène désormais à une page qui montre des émissions **mesurées**.
- **Ma propre sentinelle rapportait de faux numéros de ligne** (elle comptait
  après suppression des commentaires). Un garde-fou qui désigne le mauvais
  endroit fait perdre plus de temps qu'il n'en gagne.

### Le trou que l'audit méthodologique a trouvé

Les EF **persistés** dans `voyage_emission_summaries` gardaient le numérateur
MRV pour les méthodes A et B. J'avais corrigé `kpi_env.leg_ef` — **fonction qui
n'a aucun appelant applicatif**. Le test que j'avais écrit validait donc du code
mort, pendant que la page voyage, l'export PDF et le **DOCX remis à un tiers**
continuaient de servir la valeur non corrigée.

Corrigé à la source (`emission_ledger.compute_for_leg`), là où les valeurs sont
calculées et persistées.

### Un défaut de même famille, trouvé au passage

`_emissions_provider` ramène une distance inconnue (`Leg.distance_nm = None`,
cas réel quand un port n'a pas de coordonnées) à `Decimal(0)`. Un tel voyage
apportait son CO₂ au numérateur sans apporter la moindre tonne-kilomètre —
**le mécanisme même que la méthodologie chiffre à +26 %**, appliqué cette fois à
la distance et non au cargo. Il sort désormais des deux termes.

### Troisième passage (2026-09-14) : durcissement de la sentinelle et ce qu'il a trouvé

La sentinelle du filet 4 (les mots) ne scannait ni les catalogues i18n, ni les
services Python qui composent du texte sortant sans gabarit Jinja
(`_social_readme` du kit ZIP, les récits d'origine). Les deux angles morts ont
été comblés (`OUTWARD_PY_FILES`, `_i18n_sources()`), avec deux garde-fous pour
ne pas confondre allégation et faux positif : une liste explicite de clés i18n
**vérifiées comme internes** (`/kpi`, `/dashboard-perf` — la méthodologie
§1.2 bis leur réserve le droit de porter ces grandeurs), et deux clés où
« conteneur conventionnel » qualifie la **protection thermique de la
cargaison**, jamais une comparaison d'émissions.

Le durcissement a immédiatement trouvé quatre résidus réels, dont un bug de
production caractérisé :

- 🔴 **`app/services/cacao_stories.py` n'avait jamais reçu la correction
  appliquée à `coffee_stories.py`** (la verticale sœur, « même contrat »
  d'après son propre docstring). La page publique `/solutions/cacao` rendait
  encore, pour de vrai, « **260 kg de CO₂ évités, vérifiables** » / « **290
  kg of CO₂ avoided** » / « **240 kg de CO₂ évités** » — trois valeurs
  d'exemple **chiffrées**, avec comparaison explicite à « un transport
  conventionnel équivalent » dans les récits longs. Corrigé à l'identique du
  traitement café : `_MARKETING_EXAMPLE[...]["co2_kg"] = None`, `_co2_phrase`
  (format long) supprimée, `_co2_phrase_short` neutralisée (`del co2_kg`),
  neuf gabarits de récit long (3 origines × 3 langues) réécrits pour ne plus
  chiffrer d'évitement. Tests unitaires alignés sur `test_coffee_stories.py`.
- 🔴 **`/voyage/{ref}` (traçabilité consommateur) portait un bloc entier**
  gardé par `{% if co2_kg %}`, avec `-{{ co2_kg }} kg of CO₂ avoided` et « vs
  an equivalent conventional cargo ship ». `co2_kg` était déjà ramené à `None`
  côté routeur (défense en profondeur déjà appliquée), donc le bloc ne
  s'affichait plus — mais les clés i18n `vg_co2_title`/`vg_co2_text`
  auraient intégralement ressuscité l'allégation au premier retour d'un
  `co2_kg` non nul (fusion malheureuse, copier-coller...). Le gabarit est
  reclé sur `{% if cert %}` (le certificat existe, indépendamment de tout
  chiffre) et les deux clés ne chiffrent plus rien.
- 🟠 **SEO/traçabilité** : `home_meta_desc` (meta-description de la page
  d'accueil, indexée par les moteurs) et `vg_lead` (chapô de `/voyage/{ref}`)
  portaient encore « avoided CO₂ measured per lot » / « the CO₂ avoided ».
  Reformulés en « emissions measured per lot, EU MRV-verified ».
- 🟡 **`/preuves`, section formule** : le texte de repli posé au premier
  passage (« méthode en cours de révision ») employait encore le mot
  « émissions évitées ». Reformulé en un fait daté (« la formule de
  comparaison publiée ici a été retirée, méthodologie v3.0 §11.1 ») plutôt
  qu'une promesse implicite de remplacement — cohérent avec le §1.2 bis, qui
  n'autorise aucune publication proactive d'un chiffre de substitution.
- 🟡 Une clé i18n orpheline (`home_sched_co2_badge`, « Avoided CO₂ / pallet »,
  zéro lecteur dans aucun gabarit) et un compteur social-proof mort
  (`sp_counter_co2`, gardé derrière un compteur toujours à zéro depuis un
  retrait antérieur) ont été nettoyés par cohérence, bien que non atteignables
  en production.

**Bug de calcul confirmé et corrigé** : `kpi_env.aggregate_ef`, méthode C, ne
filtrait les voyages « exploitables » que sur `cargo_mrv_t is not None` — pas
sur `has_kpi`. Un voyage à cargo MRV saisi mais CO₂ pas encore calculé (CO₂
ramené à 0 par `_emissions_provider`, `has_kpi=False`) apportait ses
tonnes-kilomètres au dénominateur **sans** apporter son CO₂ au numérateur,
violant la règle §8.2 n°2 (déjà appliquée à la distance, cf. ci-dessus, pas
encore au filtre `usable` lui-même) : l'EF agrégé en ressortait divisé par un
facteur proche de 2. Corrigé (`usable = [... r.has_kpi]`), avec un test de
régression dédié (`test_aggregate_ef_method_c_excludes_unknown_co2_from_denominator`).

**Nettoyage cosmétique** : un bloc dupliqué verbatim dans
`emission_ledger.LedgerResult` (champ `co2_op_t` + docstring, deux fois) et
dans `compute_for_leg` (calcul de l'assiette Métier, deux fois) — retiré.
`decarbonation.vessel_baseline_speed()` — zéro appelant, zéro test propre,
`fleet_summary` lit déjà `Vessel.baseline_speed_kn` en bloc pour tous les
navires chargés — supprimé plutôt que branché (le brancher aurait réintroduit
un aller-retour DB par navire là où l'implémentation actuelle n'en fait
aucun). CSS mort du curseur interactif retiré (`newtowt-public.css:893-956`,
classes `__compass`/`__kicker`/`__control`/`__range`/`__grid`/`__tile`/…) —
seules `.co2eq`, `.co2eq__foot` et `.co2eq__qr` survivent, encore utilisées
par `_verification_qr.html`.

**Décision explicitement différée** : `VesselKpiBlock.propulsion` (profil de
propulsion agrégé au périmètre flotte/navire, `scope_propulsion_profile()`)
est calculé et testé mais **n'a encore aucun lecteur gabarit** — contrairement
à `.decarbonation`, affiché dans `_fleet_fragment.html`. La méthodologie en
fait pourtant l'indicateur de communication de tête (§1.2 bis). Ce n'est pas
un bug (rien ne l'affiche, donc rien n'affiche une valeur fausse), mais une
carte KPI dédiée demanderait de nouvelles clés i18n × 5 langues et un choix de
présentation (barre de segments colorée, cf. `voyage.html` pour le patron
existant au niveau d'un seul voyage) — une décision produit, pas une
correction de conformité, donc non prise silencieusement ici.
