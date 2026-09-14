# Note pour arbitrage — remplacer le facteur 1,5 gCO₂/t·km par la part de propulsion vélique

| | |
|---|---|
| **Pour** | Julien Gondé — arbitrage |
| **De** | Yasmin Ponce, via l'assistant de développement |
| **Date** | 2026-09-11 |
| **Décidé en amont** | Yasmin, 2026-09-11 : *« la valeur 1,5 gCO₂/t·km date d'un ancien modèle qui n'est plus à jour et qui peut être aperçu comme du greenwashing, il faudra l'écarter. »* Le **retrait est acté et appliqué**. |
| **À arbitrer** | Ce qu'on met **à la place**, sur la vitrine et le site chargeurs. Rien n'a été substitué : ce serait trancher cette question. |
| **Urgence** | Directive (UE) 2024/825 en **application pleine au 27/09/2026**. |

---

## 1. Le constat

`towt_co2_ef = 1,5 gCO₂/t·km` est un paramètre d'administration hérité de la V2,
publié sur la vitrine comme « **notre facteur d'émission certifié** ».

| Source | Intensité |
|---|---|
| MyTOWT — vitrine et tunnel de réservation | **1,5** gCO₂/t·km |
| Méthodologie de performance environnementale **v3.0** — approche Métier (28 voyages commerciaux, §9.3) | **12,7** |
| Méthodologie v3.0 — approche MRV (§8.5) | **17,1** |
| Méthodologie **v2.2** — `EFsv` 2025, déjà périmée | 12,3 |

**Rapport : 8,5.** Sur l'exemple que la vitrine donnait elle-même — 12 palettes de
vin, 1,2 t, Le Havre → New York, 3 200 NM — l'application annonçait **10,7 kg de
CO₂** là où l'intensité Métier de la méthodologie donne **90,3 kg**.

⚠️ **Deux réserves à lever, et elles comptent.**

1. Le facteur est **versionné en base** (`co2_variables`, écran `/admin/co2`).
   La valeur 1,5 est le **repli codé** ; la production porte peut-être autre
   chose. **Personne n'a vérifié la valeur réellement servie en production.**
2. L'hypothèse la plus probable est que 1,5 décrit l'**ancienne flotte TOWT** —
   des voiliers bien plus petits — et non la classe Phoenix. Si c'est le cas, le
   chiffre n'était pas faux à l'origine : il a simplement survécu au changement
   de flotte.

Le sens de l'erreur est **défavorable** : elle fait paraître nos émissions plus
faibles qu'elles ne sont. C'est la direction la plus exposée sous la directive.

---

## 2. Ce qui a déjà été retiré

Le facteur ne s'affiche plus nulle part vers l'extérieur. Rien n'a été mis à la
place — c'est l'objet de cet arbitrage.

| Surface | Ce qui portait 1,5 | État |
|---|---|---|
| `/about` | « Émissions divisées par neuf (13,7 → 1,5) » | remplacé par la vérification EU MRV / THETIS-MRV |
| `/about/anemos` | grand chiffre « 1,5 g CO₂/t·km », tableau comparatif, **formule** `(13,7 − 1,5) × t × km ÷ 1000`, exemple chiffré à 86,7 kg | retirés |
| `/routes/{code}` | onglet **éco-calculateur** entier : table, curseur de tonnage, équivalences arbres / vols / camions | retiré, `route-eco.js` supprimé |
| `/verify` | repli d'affichage sur « 1,5 » | le facteur affiché est désormais **dérivé** du certificat, donc reproductible |
| Tunnel de réservation (site chargeurs) | estimateur CO₂ live | retiré, `booking-co2.js` supprimé |
| Certificat Anemos (PDF) | ligne « 1,5 » + mention « facteur forfaitaire » | retirées |
| Carnet de bord (PDF chargeur) | facteurs 1,5 / 13,7 en dur | passés à `—` |

> 🔴 **Deux pièges rencontrés, à connaître si on revient sur ce terrain.**
> `booking-co2.js` et `route-eco.js` portaient **1,5 et 13,7 en repli codé** :
> retirer les attributs de données dans le gabarit n'aurait **rien désactivé**,
> le calcul aurait continué en silence. Les deux scripts ont donc été supprimés,
> pas seulement débranchés.

**Reste à traiter** : la page `/preuves` et le **PDF de méthodologie que
l'application génère et sert** (`/preuves/methodologie.pdf`), dont tout le
contenu est l'ancienne méthode. C'est la pièce la plus exposée, puisque c'est
précisément le document qu'un régulateur demanderait.

---

## 3. Ce qui est proposé à la place

**La part du temps de navigation sous voiles** — l'indicateur que la
méthodologie v3.0 désigne comme **le seul à porter vers l'extérieur de notre
propre initiative** (§1.2 bis, décision du 08/09/2026).

### Pourquoi celui-là

La méthodologie l'argumente par ce qu'il **ne contient pas** :

| | Profil de propulsion | Intensités `EF` | Taux de décarbonation |
|---|---|---|---|
| Facteur d'émission | **aucun** | 3,206 tCO₂/t | 3,2551 t CO₂e/t |
| Cargaison de référence | **aucune** | cargo MRV ou 770 t | 770 t |
| Scénario de comparaison | **aucun** | aucun | navire sans voiles |
| Convention sectorielle | **aucune** | 70 % | 70 % |
| Ce dont il dépend | **la seule qualité des Noon Reports** | facteurs, conventions, périmètres | tout ce qui précède, plus une base |

Chaque « aucun » est une objection qu'on ne peut pas nous opposer. Là où
« 12,7 gCO₂/t·km » suppose un facteur, un périmètre, une cargaison
conventionnelle et une méthode de distance, « **36,1 % du temps de navigation
sous voile seule** » ne suppose que la lecture correcte des Noon Reports.

### Ce qui est déjà disponible

L'indicateur **existe et est calculé** (`kpi_env.build_propulsion_profile`),
sur les créneaux de 4 h des Noon Reports. Deux correctifs livrés le 2026-09-11
le rendent publiable :

- les tranches **à l'arrêt** sortent du dénominateur — sans quoi la part de
  voile était diluée par les jours à quai et ne pouvait pas coïncider avec les
  chiffres de la méthodologie ;
- l'**agrégation de périmètre** (flotte, période) existe enfin, par cumul de
  tranches et jamais par moyenne des pourcentages de voyage.

Il est **déjà affiché** sur `/verify`, où il a remplacé le CO₂ évité.

Chiffres publiés par la méthodologie au 10/09/2026 :

| Périmètre | Tranches | Voile seule | Voile + moteur | Moteur | **Avec voiles** |
|---|---|---|---|---|---|
| Flotte, tous voyages suivis | 4 097 | 50,9 % | 33,8 % | 15,3 % | **84,7 %** |
| 28 voyages commerciaux | 3 170 | 51,2 % | 35,1 % | 13,8 % | 86,3 % |
| **5 voyages 2026** | 617 | **36,1 %** | 48,6 % | 15,2 % | **84,7 %** |

---

## 4. Les trois limites à énoncer avec le chiffre

La méthodologie les pose elle-même (§7.6), et la première est celle qu'un
interlocuteur averti soulèvera :

1. **Le temps n'est pas l'énergie.** Une tranche « voile + moteur » compte
   pareil que le moteur soit à 5 % ou à 80 % de charge. La formulation correcte
   est « part du **temps de navigation** », jamais « part de la propulsion ».
2. **Résolution de 4 heures.** Une manœuvre de trente minutes peut basculer
   toute une tranche. L'effet se moyenne sur un voyage, pas sur une traversée
   courte.
3. **Dépendance totale à la complétude des Noon Reports.** Le nombre de
   tranches retenues doit être publié **à côté** du pourcentage.

Et un point que la méthodologie souligne, à ne pas escamoter : la part de voile
seule est **plus basse** sur les 5 voyages 2026 (36,1 %) que sur l'historique de
la flotte (50,9 %). Toute communication sur la période de reprise doit citer
**36,1 %**, pas le chiffre historique plus flatteur.

---

## 5. Ce qu'il faut arbitrer

1. **Le principe.** Remplace-t-on par la part de propulsion vélique, ou
   laisse-t-on ces surfaces sans indicateur chiffré ?
2. **Le périmètre communiqué.** Flotte entière (50,9 % de voile seule) ou
   période de reprise (36,1 %) ? La méthodologie recommande le second dès qu'on
   parle de la période courante.
3. **La granularité par surface.** Un chiffre de flotte sur la landing, un
   chiffre par voyage sur `/verify` et le certificat ? Les deux sont calculables.
4. **Le sort de `/preuves` et de son PDF de méthodologie.** Trois options :
   suspendre les deux documents jusqu'à approbation de la v3.0 ; les réécrire
   sur la part de propulsion ; ou publier la **note de méthode opposable**
   prévue au §1.4 de la méthodologie — elle est rédigée depuis le 10/09/2026 et
   n'attend que l'approbation de la Responsable Environnement.
5. **La valeur de `towt_co2_ef` en production.** Indépendamment du remplacement :
   vérifier `/admin/co2`, et décider du sort des certificats déjà émis en
   `method = 'theoretical'`, qui ont été calculés dessus.

---

## 6. Ce que l'assistant a fait sans arbitrage, et pourquoi

Deux gestes ont été posés sans attendre, parce qu'ils ne créent aucune
affirmation nouvelle :

- **le retrait** du facteur des surfaces sortantes — c'était la décision de
  Yasmin, pas un arbitrage ;
- **une garde sur le certificat** : le chiffre d'émission n'est affiché que
  s'il vient du grand livre (`method = 'declared'`). Sans données déclarées, le
  certificat **dit** que les émissions ne sont pas encore consolidées, au lieu
  d'afficher un forfait qui passerait pour une mesure.

Là où une phrase devenait vide après retrait, elle a été remplacée par ce qui
reste **vérifiable sans aucun facteur** : émissions surveillées, déclarées et
vérifiées par un organisme accrédité sous EU MRV, publiques au registre
THETIS-MRV de l'EMSA. C'est un constat, pas un argument commercial — la
rédaction commerciale revient à qui en a la charge.
