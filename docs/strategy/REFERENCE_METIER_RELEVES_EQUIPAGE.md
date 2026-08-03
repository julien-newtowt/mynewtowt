# Référence métier — relèves d'équipage (analyse des classeurs Excel)

> **Statut** : analyse terminée le 2026-08-03. **Ce document est la référence
> métier** : toute implémentation dans MyTOWT doit s'y conformer, ou justifier
> explicitement son écart.
>
> Sources analysées (fournies par Yasmin) :
> - `Planning pour bord Yas.xlsx` — 16 feuilles, dont 4 masquées
> - `Planning navire Yas.xlsx` — 8 feuilles
>
> Documents liés : `docs/operations/07-ordre-pr-et-merge.md` (ordre de fusion),
> `PLAN_UPGRADE_PHASE2_2026-08.md` §12 (RAF R11).

---

## 1. Le processus réel (recueilli auprès de l'Armement, 2026-07-30)

1. **Simulation dans Excel** — jours en mer, périodes embarquées / à terre,
   anticipation des relèves, planning cohérent avec les contraintes
   opérationnelles. Cette étape existe **parce que Marad ne donne pas assez de
   visibilité pour planifier** : ce n'est pas un contournement d'outil, c'est un
   manque fonctionnel de Marad.
2. **Décision dans Excel** — une seconde feuille définit et valide les dates
   d'embarquement / débarquement. **C'est la décision d'Armement.**
3. **Transmission à l'agent d'escale** — les informations nécessaires aux RDV
   **PAF**, qui alimentent la **note d'escale**. L'agent d'escale **ne décide
   rien**.
4. **Conformité documentaire** — **déjà couverte par Marad**, qui notifie
   l'Armement en amont des expirations (passeports, titres de séjour, Schengen).

---

## 2. Structure des classeurs

### 2.1 `Planning pour bord` — une feuille par navire

Feuilles navires : `ANEMOS - New TOWT`, `ARTEMIS - New TOWT`, `ATLANTIS`,
`ATLAS`, `VIETNAM`. Chacune porte **deux blocs**.

**Bloc 1 — équipage à bord et sa relève** (colonnes A→N) :

| Col | Champ | Nature |
|---|---|---|
| A | N° de poste | saisi (⚠️ doublons observés) |
| B | Name on board | saisi |
| C | Job title | saisi |
| D | Maning company | `TOWT` ou `PMS` (en-tête parfois libellé `TO`) |
| E | Starting Date | **saisi — la décision d'Armement** |
| F | Contract | **calculé** `=SI(D="TOWT";60;90)` |
| G | Due Off Date | **calculé** `=E+F` |
| H | Days on board | **calculé** `=SI(E="";"";AUJOURDHUI()−E)` |
| I | Reliever Name | saisi (souvent `?`, `TBC`, `POUR AOÛT`) |
| J | Maning company du releveur | `TOWT` / `PMS` |
| K | Depart from home | saisi |
| L | Contract du releveur | **calculé** `=SI(J="TOWT";60;90)` |
| M | Status | liste fermée (cf. §3.3) |
| N | Comments | libre |

**Bloc 2 — « <NAVIRE> Crew Change » : la transmission PAF**

| Champ | Côté montant | Côté descendant |
|---|---|---|
| Position | commun | commun |
| Nom | `On signers` | `Off signers` |
| Nationalité | ✔ | ✔ |
| Date | `ETA` | `ETD` |
| Transport | `Flight / Train N°` | `Flight / Train N°` |
| Heure | `Arrival Time` | `Departure Time` |
| Commentaires | commun | commun |

⚠️ **Le bloc est symétrique** : montants **et** descendants. La description
orale ne mentionnait que les descendants — la PAF contrôle les deux sens.

Commentaires observés : « Prendre RDV à la PAF », « Sera amené par un proche »,
« Débarquement avant le départ des navires avec son véhicule personnel »,
« 2 jours au siège avec Paul VDH avant embarquement ». **Le rappel de RDV PAF est
une note manuelle** — rien ne le déclenche.

### 2.2 Feuilles annexes de `Planning pour bord`

| Feuille | Contenu | Usage |
|---|---|---|
| `Glossaire` | `TBN` = To be nominated · `TBC` = To be confirmed · `NC` = New comer | Vocabulaire d'incertitude |
| `listes` | Énumérations `Status` et `Manning` | Listes de validation |
| `TICKETING REQUEST OFFSIGNERS` | Formulaire : Full NAME, Vessel, Departure Date, Departure From, Arrival to (aéroport ou gare), Comments (heure préférée) | **Demande de billet** à l'agence |
| `Feuil4` | Nom · Débarquement prévisionnel · Fonction · **Date fin de congés** · Commentaire | Le **volet « à terre »** |
| `Feuil3` | Position × Cabinage (affectation de poste par navire) | Armement par navire |
| `Feuil5`, `Feuil7` | Copies antérieures des blocs navires | Archives de travail |
| `liste des marins` (masquée, 1801 lignes) | Person name · Activity start · Activity end · Vessel · Position · Company short name | ⚠️ Navires **BOURBON**, sociétés `BMSG`/`BINL` ⇒ **vivier de CV externe, pas des embarquements NEWTOWT** (à confirmer) |

### 2.3 `Planning navire`

| Feuille | Contenu |
|---|---|
| `Sailing Schedules_061025` | **Calendrier jour par jour** par navire : une ligne de dates, une ligne d'état (`en mer` / `au port`), par blocs mensuels |
| `Anemos`, `Artemis`, `Atlantis` | Détail par navire |
| `data` | **Tables de référence** (cf. §3.1) |
| `prévi` | Prévisionnel commandants / chefs par navire, révision de grille salariale, **plan d'embauche** |
| `OUEST CONSEIL`, `Feuil2` | Annexes |

> 🔑 **`Sailing Schedules` duplique ce que MyTOWT sait déjà** : la table `legs`
> porte ETD/ETA/ATD/ATA par navire et `/planning` en affiche le Gantt. **Le manque
> n'est pas le planning navire, c'est la couche équipage posée dessus.**

---

## 3. Règles métier extraites

### 3.1 🔑 Coefficients d'acquisition de congés — le vrai moteur de la simulation

Feuille `data` du classeur `Planning navire`. **C'est la découverte principale** :
la simulation n'est pas un simple `début + 60`, c'est un **grand livre
d'acquisition**.

| Statut | Coeff. TOWT | Statut PMS | Coeff. PMS |
|---|---|---|---|
| embarqué | **0,9** | embarqué PMS | **1,0** |
| embarqué autre | 0,9 | embarqué PMS autre | 1,0 |
| **débarqué** | **−1** | débarqué PMS | 0 |
| formation | 0,6 | débarqué PMS autre | 0 |
| AT (accident du travail) | 0,2 | `-` | 0 |
| télétravail | 0,9 | | |
| embarqué cadet | 0,3 | | |
| conduite | 0 | | |
| Dispo | 0 | | |

Lecture : chaque jour embarqué **crédite** 0,9 jour de congé (1,0 pour le
personnel PMS) ; chaque jour à terre en **débite** 1. C'est ce solde qui permet
d'« anticiper les futures relèves ».

**Cela explique `Feuil4`**, où les fins de congés sont calculées en
`débarquement + 12`, `+ 55`, `+ 59` — durées apparemment arbitraires, en réalité
**dérivées du solde acquis**.

### 🔴 Portée confirmée par Yasmin (2026-08-03) — ce n'est pas du pilotage

> **« Fixé par la société et transmis à Silae pour la paie. »**

Ces coefficients sont donc **normatifs et alimentent la paie**. Trois conséquences
qui changent la nature du lot :

1. **Ce n'est pas un indicateur, c'est un calcul de droits.** Une erreur ne
   produit pas un écran faux — elle produit un **bulletin de paie faux**. Le
   niveau d'exigence de test et de traçabilité est celui de la paie, pas celui
   d'un tableau de bord.
2. **La cible d'intégration existe déjà** dans MyTOWT : `SilaeExportBatch`,
   `services/silae_export.build_evp_csv` (EVP = éléments variables de paie, CSV
   `;` + BOM UTF-8), route et journal des lots dans `rh_router` (L5), avec le
   cycle `generated` → `sent` → …
3. **Le pont marin ↔ salarié existe** : `Employee.crew_member_id` (FK optionnelle
   vers `crew_members`). Le chaînage
   `relève → jours par statut → coefficient → EVP → Silae` est donc **plombé de
   bout en bout sauf le maillon central**, qui est précisément celui qui manque.

⚠️ **Le cadet est confirmé à `0,3` par jour embarqué** (réponse 2 de Yasmin).

### 3.2 Durée de contrat — un **défaut**, pas une règle

La formule est `=SI(manning="TOWT";60;90)`. Mais des valeurs **saisies en dur**
la contournent régulièrement :

- `Feuil5` F6 = **60** pour un chef mécanicien **PMS** (la règle donnerait 90) ;
- `Feuil5` F8 = **70** pour un élève (ni 60 ni 90) ;
- `ANEMOS` G7/G8/G11/G12 et `Feuil7` F9 : **dates de fin due écrites à la main**,
  donc figées si la date de début change.

### ✅ Réponse de Yasmin (2026-08-03) : **« Paramétrable par l'Armement »**

Les durées ne sont donc **pas** une constante métier à coder : ce sont des
**paramètres dont l'Armement est propriétaire**. Le modèle doit exposer un écran
de paramétrage, pas un littéral dans le code.

> 🔁 **Motif déjà présent dans le dépôt, à réutiliser** : le MRV v2 applique
> exactement cette règle (« **zéro seuil en dur** ») via
> `validation_rule_thresholds` + `validation_engine.get_threshold` — cache 60 s,
> résolution **fail-closed** `(règle, navire)` → `(règle, NULL)` → défaut codé,
> avec override par navire et **snapshot des seuils consommés** pour la
> reproductibilité d'audit. Le lot relèves devrait s'aligner sur ce motif plutôt
> que d'inventer un second mécanisme de paramétrage. La reproductibilité d'audit
> y est d'autant plus nécessaire que le résultat part en paie (§3.1).

### ✅ Précision de Yasmin (2026-08-03) : override **avec justification obligatoire**

> **« Paramétrable, avec possibilité de modif pour des cas en particulier avec
> mot de justification. »**

Le modèle attendu est donc à **trois niveaux**, et le troisième n'est pas
optionnel :

| Niveau | Nature | Qui |
|---|---|---|
| 1. Défaut paramétrable | durée par manning / par poste, en base | Armement (écran de paramétrage) |
| 2. Override par cas | valeur différente sur une relève précise | Armement |
| 3. **Justification textuelle** | **obligatoire** dès qu'un override est posé | l'auteur de l'override |

⇒ **Refuser l'enregistrement d'un override sans justification.** C'est ce qui
distingue les valeurs `60` / `70` / `90` saisies en dur dans Excel — aujourd'hui
indiscernables d'une erreur — d'une décision assumée et lisible six mois plus
tard. Le résultat partant en paie (§3.1), la justification est aussi la trace
d'audit en cas de contestation.

### 🟡 Valeurs par défaut — **une contradiction à lever avant implémentation**

Deux réponses successives de Yasmin le 2026-08-03 s'opposent sur la durée d'un
élève :

| # | Réponse | Élève |
|---|---|---|
| 1 | « **60** » (à la question « quelle valeur par défaut pour un élève ? ») | 60 j |
| 2 | « Pour les marins embauché TOWT, **uniquement les élèves embarquent 90 jours**. Les autres embarquent 60 » | **90 j** |

**Lecture retenue à titre provisoire : la réponse 2**, plus tardive, plus
explicite et auto-cohérente. Elle donne :

| Population | Durée par défaut |
|---|---|
| **TOWT** — hors élève | **60 j** |
| **TOWT** — élève / Deck Cadet | **90 j** |
| **PMS** | **90 j** |

⚠️ **Cette lecture inverse l'arbitrage des classeurs** par rapport à la réponse 1 :
- `ANEMOS` F13/F14 = `90` **en dur** pour les élèves ⇒ **CORRECT** ;
- `ARTEMIS` F13/F14 = `=SI(poste="DECK CADET";60;90)` ⇒ renvoie **60** pour un
  élève ⇒ **la formule est FAUSSE**.

🔴 **À confirmer avant de coder** — le résultat part en paie (§3.1), et les deux
réponses conduisent à des soldes de congés différents. Tant que ce n'est pas
confirmé, **ne pas figer la valeur en base**.

**Conséquence de modélisation, valable dans les deux lectures** : le défaut d'un
élève dépend de son **poste**, pas seulement de sa société de manning. La règle
`SI(manning="TOWT";60;90)` ne suffit donc pas — il faut une résolution en cascade
**(poste, manning) → (manning) → défaut**, exactement la forme que
`validation_engine.get_threshold` implémente déjà pour le MRV.

Sous la lecture retenue, TOWT et PMS convergent d'ailleurs à 90 j pour les
élèves — mais par deux chemins différents, ce qui ne dispense pas de la cascade
(rien ne garantit que ça reste vrai après un changement de paramètre).

C'est précisément le genre d'écart qu'un tableur exprime mal : les deux classeurs
se contredisent, et **aucun des deux ne porte de justification** permettant de
savoir lequel fait foi.

### 3.3 Énumérations

**Status** (feuille `listes`) — c'est un **workflow**, pas un simple état :

```
Ouvert
 → Confirmed
 → Confirmed + OM OK + saisie OK
 → Visa en cours + OM OK
 → Visa en cours + OM OK + saisie OK
 → Visa OK + OM OK + saisie OK
 → Waiting to Embark
```

Trois dimensions y sont **encodées dans une seule chaîne** : confirmation du
marin, **OM** (ordre de mission), **saisie** (vraisemblablement dans Marad), et
l'état du visa. Une modélisation propre les séparerait en champs distincts.

**Manning** : `TOWT` | `PMS`.

**Position navire** (feuille `data`) : `en mer` | `au port` | `-` — correspond aux
états du `Sailing Schedules`.

**Avenant en cours** : `Non` | `Temporaire` | `Définitif`.
**Mutuelle** : `Oui` | `Non` | `en cours`.

### 3.4 Flotte de référence

Feuille `data`, colonne `Navire` : `ANEMOS`, `ARTEMIS`, `ATLANTIS`, `ATLAS`,
`ARCHIMEDES`, `ASTERIAS`, ~~`ATHENAIS`~~, ~~`ARIES`~~.

✅ **Réponse de Yasmin (2026-08-03) : `ARIES` et `ATHENAIS` sont des navires
annulés pour l'instant.** Ne pas les modéliser ; ne pas les supprimer de la
référence pour autant — « pour l'instant » n'est pas « définitivement ».

✅ **La feuille `VIETNAM` n'est pas un navire** (confirmé par Yasmin le
2026-08-03) : c'est le **personnel qui se trouve au Vietnam**, recensé par poste
(Master, Chief Officer, Mate, Chief Engineer, Fitter/Oiler, BOSUN, AB1, AB2, Cook,
Deck Cadet).

⚠️ **Conséquence de modélisation** : une affectation peut donc être rattachée à un
**lieu** et non à un navire. Cela recoupe l'arbitrage A4 (`CrewAssignment.leg_id`
nullable pour l'embarquement hors voyage) mais va plus loin : ici il n'y a **ni
leg, ni navire**.

✅ **Coefficient tranché (Yasmin, 2026-08-03) : « Le personnel au Vietnam est en
position embarquée. »** Il relève donc du statut `embarqué` — soit **0,9 j/j** pour
le personnel TOWT et **1,0 j/j** pour le personnel PMS (§3.1). Pas de statut
particulier à créer.

Lecture métier : ces marins supervisent la construction / la livraison d'un navire
au Vietnam. Ils ne sont ni à terre au repos, ni en congés — ils travaillent, donc
ils acquièrent comme s'ils étaient à bord. **Le modèle doit donc autoriser un
embarquement sans navire ni leg tout en le comptant comme embarqué**, ce qui
interdit de déduire le statut de la seule présence d'un `vessel_id`.

### 3.5 Postes — **quatre vocabulaires** pour les mêmes fonctions

| Source | Exemples |
|---|---|
| Bloc 1 (anglais complet) | `Master`, `Chief Officer`, `Mate`, `Chief Engineer`, `BOSUN`, `AB1`, `AB2`, `Fitter`, `Cook`, `Deck Cadet`, `Electrotech` |
| Bloc 2 (abréviations) | `MASTER`, `CHENG`, `CHOFF`, `MATE`, `BOSCO`, `AB 1`, `AB 2`, `FITTER`, `COOK`, `CADET`, `ELECTRICAL ENGINEERING OFFICER ASSISTANT` |
| `Feuil3` (français) | `Capitaine`, `Second Capitaine`, `Lieutenant Pont`, `Chef Mécanicien`, `BOSUN`, `Matelot`, `Cuisinier`, `Cadet` |
| `data` (codes étoilés) | `MASTER*`, `CE*`, `CO*`, `MATE*`, `CADET`, `ELECT`, `BOSUN*`, `FITTER*`, `AB*`, `AB1*`, `AB2*`, `COOK*`, `MASTER Db`, `CE Db` |

Plus l'énumération propre de MyTOWT (`CREW_ROLES`). ⇒ **Une liste canonique avec
alias d'affichage est indispensable.** Hypothèses à confirmer : `*` = poste
obligatoire, `Db` = doublure (le terme apparaît en clair dans `ATLANTIS` :
« BOUCHER Pierrick, doublure LE RAT Matthieu »).

---

## 4. ⚠️ Deux conventions de comptage — à ne surtout pas unifier

Excel calcule les jours à bord en `AUJOURDHUI() − date de début` : **le jour
d'embarquement compte pour zéro**. MyTOWT compte de façon **inclusive**
(du 1er au 10 = 10 jours).

Exemple réel (`ANEMOS`, DELANNOY embarqué le 2026-06-02, au 2026-08-03) :
Excel affiche **62**, MyTOWT en compterait **63**.

**Les deux ont raison, parce qu'ils ne mesurent pas la même chose :**

| Quantité | Convention | Pourquoi |
|---|---|---|
| **Jours à bord** (contrat) | **exclusive** (durée écoulée) | Cohérent en interne : contrat 60 j ⇒ fin due = début + 60 |
| **Jours de présence Schengen** | **inclusive** | La règle 90/180 compte le jour d'entrée **et** le jour de sortie comme jours de présence |

⇒ **Ne pas unifier : nommer.** Confondre les deux casse soit un contrat, soit une
conformité réglementaire. Toute nouvelle fonction de comptage doit dire dans son
nom et sa docstring laquelle des deux elle applique.

Note : la feuille masquée `liste des marins` utilise une **troisième** forme
(`Activity end = 23:59:59`, soit une borne de fin inclusive) — cohérente avec
l'inclusif, mais issue d'un système externe.

---

## 5. Anomalies relevées — à trancher par l'Armement

Distinction importante : certaines sont des **overrides volontaires**, d'autres
des **formules cassées**. Seule l'Armement peut faire le tri.

### Sans ambiguïté — formules cassées

| Où | Problème |
|---|---|
| `VIETNAM` L5, `Feuil5` L7 | `=SI(#REF!="TOWT";60;90)` — **référence détruite**, la formule ne calcule plus rien |
| Colonne L (contrat du releveur), **les 4 navires** | Pointe systématiquement **1 ou 2 lignes trop bas** : `L9→J11`, `L10→J11`, `L11→J12`, `L13→J15`… ⇒ lit la société de manning **du marin suivant**. Symptôme d'insertions de lignes |
| `Feuil4` D8, D14 | `=B1+12` et `=B12+55` au lieu de `B8` / `B14` — références à la mauvaise ligne |
| `ARTEMIS` G12 | Le cuisinier n'a **aucune** date de fin due |
| `ATLANTIS` F9 | Contrat vide, mais `G9 = E9+F9` l'utilise quand même |
| `ANEMOS`/`ARTEMIS` A10-A11 | N° de poste **7 en doublon** |

### Ambigu — override volontaire ou erreur ?

Les durées saisies en dur (60 / 70 / 90) et les dates de fin due manuelles
(cf. §3.2). **Si volontaire**, le logiciel doit offrir un override tracé.

> 🔑 **Ces anomalies sont le meilleur argument du lot** : ce sont des erreurs
> **silencieuses**. Un `#REF!` ou une référence décalée d'une ligne ne se voit pas
> à la lecture, et se propage dans une décision d'embarquement.

---

## 6. Ce que MyTOWT possède déjà

| Besoin | Existant | Écart |
|---|---|---|
| Calendrier navire `en mer` / `au port` | ✅ `legs` (ETD/ETA/ATD/ATA) + Gantt `/planning` | **Aucun** — le classeur duplique l'ERP |
| Liste d'équipage PAF | 🟡 `/crew/border-police/{vessel_id}`, PDF bilingue FR/EN | Lit **les affectations rattachées à un leg**, donc pas les décisions d'Armement. Manque **n° de vol/train** et **heure de départ** |
| Billets de transport | 🟡 `CrewTicket` : `mode`, `reference`, `carrier`, `departure_at`, `departure_location` | Champs présents — **exactement** ceux du formulaire de demande de billet |
| Jours embarqués par marin | 🟡 `embarked_days_by_member` (corrigé le 2026-07-30, union d'ensembles de jours) | Convention **inclusive** ⇒ ne correspond **pas** aux « Days on board » d'Excel (cf. §4) |
| Congés | 🟡 `CrewLeave` (demande / approbation) + `services/leaves.py` | **Aucune logique d'acquisition ni de solde** — `leaves.py` ne fait que lister et compter. C'est le gros manque face au §3.1 |
| Conformité passeport / Schengen | ✅ existant, et **déjà couvert par Marad** en amont | Déprioritisé volontairement |
| Registre des relèves | ❌ | `marad_crew_schedules` (lecture seule) et `crew_assignments` (saisie d'escale). **Aucune notion de décision de relève** |
| Simulation / anticipation | ❌ | Tout est dans Excel |

---

## 7. Questions ouvertes (Yasmin se renseigne auprès de l'Armement)

Réponses de Yasmin, transmises par l'Armement le **2026-08-03**.

| # | Question | Réponse |
|---|---|---|
| 1 | Les coefficients d'acquisition font-ils foi ? Alimentent-ils la paie ? | ✅ **« Fixé par la société et transmis à Silae pour la paie »** ⇒ normatifs, adjacents à la paie (cf. §3.1) |
| 2 | Élèves : 60 ou 90 jours ? | 🟡 **CONTRADICTOIRE** — deux réponses opposées le 2026-08-03 (« 60 », puis « uniquement les élèves embarquent 90 jours »). Lecture provisoire : **90 j**. **À confirmer avant de coder** (cf. §3.2). Le coefficient d'acquisition, lui, est confirmé à `0,3 / jour embarqué` |
| 3 | Les overrides de durée sont-ils volontaires ? | ✅ **« Paramétrable, avec possibilité de modif pour des cas en particulier avec mot de justification »** ⇒ défaut en base + override par cas + **justification obligatoire** (cf. §3.2) |
| 4 | Flotte | ✅ `ARIES` et `ATHENAIS` **annulés pour l'instant** |
| 5 | `Db` = doublure ? `*` = poste obligatoire ? | ✅ **« Doublure et l'étoile, ça veut dire obligatoire »** ⇒ l'astérisque marque un **poste obligatoire**, et un poste `Db` signale une **doublure obligatoire**. Lecture à confirmer : un navire ne peut appareiller sans le poste étoilé pourvu **ni sans doublure désignée** là où elle est exigée |
| 6 | `liste des marins` = vivier de CV externe ? | ✅ **« Annuaire à ne pas ajouter en MyTOWT »** ⇒ **hors périmètre**, définitivement. Les 1801 lignes ne sont pas importées |
| 7 | Feuille `VIETNAM` = équipe de supervision de construction ? | ✅ **« Personnel qui est au Vietnam »** ⇒ pas un navire, un **lieu**. Conséquence de modélisation en §3.4 |

### Ce que les réponses changent

**Le lot est un calcul de droits, pas un outil de pilotage.** La réponse 1 est la
plus structurante : le résultat part en paie. Cela déplace le niveau d'exigence
(tests, traçabilité, reproductibilité d'audit) et impose de s'aligner sur le motif
`validation_engine` déjà en place pour le MRV.

**Le périmètre se réduit sur deux fronts** : l'annuaire de 1801 lignes est
explicitement exclu (réponse 6), et le planning navire existe déjà dans l'ERP
(§2.3). Ce qui reste est plus étroit mais plus exigeant.

**Les 7 questions ont reçu une réponse**, et le coefficient du personnel au
Vietnam est également tranché (`embarqué`). La conception du lot n'est plus bloquée.

**Un seul point doit être levé avant d'écrire du code** :

🔴 **La durée par défaut d'un élève** — deux réponses contradictoires (cf. §3.2).
Lecture provisoire retenue : **90 j**. Le résultat partant en paie, cette valeur ne
sera pas figée en base sans confirmation.

Et un point à confirmer **en cours d'implémentation** :

- **La cascade `(poste, manning) → (manning) → défaut`** doit être validée sur
  d'autres postes que l'élève. Y a-t-il d'autres fonctions dont la durée ne suit
  pas la règle par société de manning ? Si l'élève est le seul cas, une exception
  simple suffirait — mais il vaut mieux le savoir avant de choisir la structure de
  paramétrage.
