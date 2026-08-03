# Plan d'upgrade — Phase 2 (2026-07-29 → septembre 2026)

> **Objectif** : disposer à la rentrée de septembre d'une version de MyTOWT
> **fonctionnelle, stable et exploitable** par les équipes Opérations.
> Pas d'exhaustivité fonctionnelle — **maximiser la valeur métier en
> minimisant le risque technique**.
>
> Documents liés : `PROJECT_CONTEXT.md` §13-14 (audits), `CLAUDE.md`
> (consignes opérationnelles), `docs/DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`
> (journal quotidien), `docs/user-guide/roles-processus-integrations.md`
> (guide fonctionnel).

---

## 1. Cadre et capacité

| Élément | Valeur |
|---|---|
| Période du plan | 2026-07-30 → 2026-08-12 (10 jours ouvrés) |
| Capacité | 1 personne (Yasmin) + assistance Claude |
| Retour du manager | 2026-08-17 |
| Échéance cible | Rentrée septembre 2026 |
| Marge planifiée | ~20 % (un plan à 100 % de charge dérape) |
| Contrainte externe | Test à bord initialement ~10/08, **décalable** (décision Yasmin 2026-07-29) |

**Accès actuel** : les masters n'ont pas encore reçu leurs identifiants.
Yasmin est la seule personne en écriture — ce qui réduit fortement le risque
de production de données non désirées pendant la période de développement.

---

## 2. Décisions actées (2026-07-29)

| # | Décision | Justification | Conséquence sur le plan |
|---|---|---|---|
| D1 | **Pas d'overbooking aujourd'hui** — remplissage des navires < 50 % | Le scénario « confirmer 1 500 palettes sur 978 » est non atteignable en exploitation actuelle | Le lot capacité **descend de P0 en P2**. Libère ~2 jours réinvestis dans la note d'escale. |
| D2 | **Un seul rail documentaire** : les documents sont générés depuis les **packing lists** | Le rail packing list porte les mentions obligatoires (consignee, notify party, marks & numbers, HS code) et est alimenté par le portail expéditeur ; le rail booking produit un BL dégradé | Retirer/rediriger les boutons du rail booking plutôt que maintenir deux qualités de BL. **ADR à écrire.** |
| D3 | **Process MRV manuel maintenu** (comme 2025 — Noon Reports + Carbon Reports, scripts Python + tâches planifiées) | Le process fonctionne et la déclaration 2025 est faite | Aucun développement MRV dans ce plan. |
| D4 | **La capture MRV v2 reste ACTIVE** (pas d'opt-out `mrv_v2_capture.audience.vessels_off`) | Yasmin veut pouvoir tester la saisie événementielle elle-même ; aucun autre utilisateur n'a d'accès en écriture | ⚠️ Vigilance : un événement **finalisé** entre dans le grand livre et peut alimenter un certificat Anemos. Rester en brouillon en exploration, ou dédier un navire de test. |
| D5 | **Filet CI : option A** — corriger les tests périmés puis activer `tests/integration` + `tests/regression` en CI | Un filet fiable vaut mieux qu'un filet bruyant ; 29 `xfail` risqueraient de noyer l'écart cascade à investiguer | J1 étendu de 0,5 j à ~1 j. |
| D6 | **Décision du 2026-07-27 sur la CI rouverte** | Elle était raisonnable tant qu'on ne développait pas ; elle ne l'est plus dès lors qu'on touche capacité, horodatage et émission de connaissements | J1 = prérequis de tout le reste. |

---

## 3. Vérification des hypothèses de départ

### 3.1 QHSE — hypothèse validée, recommandation renforcée

**Hypothèse** : outil d'analyse, partiellement couvert par Marad, non prioritaire.

**Verdict** : ✅ validée — avec une reformulation plus tranchée. Ce n'est pas
« QHSE peut attendre » mais **« la branche `feature/qhse-foundation` ne doit
pas être mergée en l'état »**, car l'audit y a trouvé deux défauts qui
détruisent des données (cf. `PROJECT_CONTEXT.md` §14.7) :

- un filtre par mot-clé (`test|essai|demo`) quarantaine et **n'importe jamais**
  des non-conformités ISM légitimes (« Fire pump **test** not carried out »,
  « **essai** de l'appareil à gouverner non effectué »), et la perte n'est
  **jamais persistée** ;
- un `rollback()` **dans la boucle** d'import annule les lignes déjà insérées
  tout en les comptant comme importées (l'écran affiche « 120 importés »
  quand 40 subsistent).

**Une branche non mergée est sans risque ; un import à moitié cassé en
production détruit des enregistrements ISM silencieusement.** Correctif :
quelques heures, à faire **seulement si** un merge avant septembre est voulu.

### 3.2 MRV — hypothèse validée sur le fond, mais elle passait à côté du sujet

**Hypothèse** : déclaration 2025 faite manuellement, process satisfaisant,
module à développer dans un second temps.

**Verdict** : ✅ validée pour le développement — mais **le module MRV v2 n'est
pas « à développer », il est déjà construit et mergé** (14 lots, migrations
0096-0105). La vraie question n'était pas « développer ou non » mais **quelle
posture pour le feature flag** (`mrv_v2_capture` est ON par défaut, fail-open).
Tranché en D4 : capture laissée active, usage limité aux tests de Yasmin.

**Correction d'une hypothèse erronée de l'assistant** : il avait suggéré que
la flotte, probablement sous 5 000 GT, était hors périmètre MRV UE et que le
module relevait de la sur-ingénierie. **C'est faux.** Le règlement (UE)
2023/957 a étendu le MRV aux navires de charge général de **400 à 5 000 GT
depuis le 01/01/2025**, et `docs/strategy/CDC_VERIFICATION_TIERCE_ANEMOS.md:18-21`
indique que les émissions sont **déjà surveillées, déclarées et vérifiées par
un organisme accrédité** (références THETIS-MRV, datasets déposés chez DNV).
Le niveau d'exigence du module est justifié.

**Un bug MRV-adjacent ne peut pas attendre** (indépendant de la priorité du
module) : l'heure « sous voile pure » du Carnet de Bord est **surévaluée d'un
facteur 6** — document client, claim commercial central, échéance ECGT au
27/09/2026. Traité en J2.

### 3.3 Crew — hypothèse contestée

**Hypothèse** : Marad gère l'essentiel, MyTOWT récupère par API, les
développements complémentaires ne sont pas bloquants.

**Verdict** : ⚠️ **la prémisse est juste, la conclusion ne suit pas.** Le
problème n'est pas ce que MyTOWT ne fait pas — c'est ce qu'il **affiche** :

- l'écran de conformité Schengen calcule les jours de présence sur
  `CrewAssignment` (alimentée par la seule saisie d'escale) alors que les
  marins viennent exclusivement de Marad ⇒ **zéro jour compté = statut
  « conforme », en vert, persisté et horodaté** (`crew_compliance.py:229-257`) ;
- le garde-fou de conformité à l'embarquement existe **en fonction, sans aucun
  appelant** (`passport_blocking_reason`) ⇒ on peut embarquer un marin au
  passeport périmé, médical expiré et en dépassement Schengen sans un
  avertissement. Une spec du repo affirme pourtant que ce garde-fou est
  « PRÉSERVÉ — un gain V3 à ne pas casser » (`SPEC-CREW-reprise-P0.md:226`).

**Argument** : c'est **précisément parce que Crew n'est pas une priorité de
développement** qu'il ne doit pas mentir. Un écran vide invite à la vigilance
manuelle ; un écran vert la supprime. Le correctif n'est pas de développer le
module — c'est d'afficher « indéterminé ». Quelques lignes. Traité en J3.

---

## 4. Analyse d'écart — retour des équipes Opérations

Vérification systématique dans le code de chaque demande. **Constat
structurant : il existe deux rails cargo, et les Opérations utilisent le
moins bon.** 4 demandes sur 8 sont des problèmes d'affichage ou de plomberie
sur des fonctionnalités existantes, pas des développements.

| Demande Ops | Réalité vérifiée | Verdict | Lot |
|---|---|---|---|
| Bookings : « n'affiche pas le nom du client ni le voyage » | Les colonnes **existent** (`staff/cargo/index.html:17-18`) mais affichent `#{{ b.client_account_id }}` et `{{ b.leg_id }}` — soit `#42` et `17` | ✅ Plainte valide, **diagnostic différent** : bug d'affichage, ~10 lignes | J2 |
| BL : « pas de template standardisé, pas connecté au portail » | **Deux templates coexistent.** Celui du rail packing list est correct et **alimenté par le portail expéditeur** ; celui du rail booking (le bouton utilisé, `:35`) n'a **ni consignee ni notify party** | 🔴 **La fonctionnalité demandée existe déjà, sur l'autre rail.** Fix = rediriger, pas construire | J2 (D2) |
| PL : « pas de détail par lot/batch » | `PackingListBatch` existe avec toute la structure (référence, quantité, poids, colisage, parties, HS code, n° BL) | ✅ **Donnée déjà en base**, pas affichée sur le bon écran | J2 (D2) |
| Facture commerciale client absente | Un bouton « Facture » existe (`:37`) mais c'est **la facture de fret NEWTOWT**, pas la facture commerciale du client pour le dédouanement | ⚠️ Vrai manque — attention au piège de nommage | J7 (option) |
| Onboard : « absence de Manifeste, BL, Mate's Receipt » | **Mate's Receipt existe** (`MATES_RECEIPT`, template dédié, création par leg, signature commandant, pièce jointe). **Manifeste n'existe pas.** BL vit dans Cargo | ⚠️ Mixte : problème de **découvrabilité** pour le Mate's Receipt ; Manifeste = vrai manque | J2 / P2 |
| Note d'escale à générer automatiquement | `grep` : **aucune trace**. Vrai manque. Un flux de clôture existe (`/captain/legs/{id}/closure.pdf`, ONB-05) pouvant servir de base | ⚠️ Vrai manque, template reçu le 2026-07-29 | **J5-J8** |
| Quai & Opérations : « pas d'info réelle sur le navire à quai » | L'ATD/ATA écrit par le bord est **l'heure de saisie**, pas l'heure de l'événement ; et **aucune alerte** quand un navire en mer dépasse son ETA | 🔴 Plainte valide, **cause plus profonde** : la donnée sous-jacente est fausse | J2 / J9 |
| Escale, Tracking : « fonctionnel, aucune action requise » | Cascade ignorant le verrou d'escale, rotations de 0 h auto-générées, inversion ATD > ATA possible ; aucune détection de trou de position | ⚠️ **Les Opérations ne voient pas ces bugs parce qu'ils sont silencieux.** « Aucune action requise » ≠ « sain » | J9 / P2 |

### 4.1 Template de note d'escale — cartographie

Fichier reçu : `2026_S29_ Port Call Preparation-ARTEMIS-Voyage 2BGPFR6_20.07.2026.xlsx`.
C'est un **document de préparation hebdomadaire multi-services** (« must be
prepared before the weekly meeting »), une feuille par département avec code
couleur : Operations, Crewing, Technical, Commercial, Master & C/O.

| Feuille | Déjà en base | Manque |
|---|---|---|
| **Operations** | Navire, `leg_code`, ports précédent/suivant, ETA/ETD, agent (nom/tél/mail via `PortConfig`), soutage complet (`BunkerOperation` : type, port, date, quantité, fournisseur), opérations commerciales (client, quantité, start/end, durée, **shifts et dockers** via `EscaleOperation` + `DockerShift`), marchandises dangereuses, remarques | **Berth**, **Garbage (MARPOL Annexe V)**, Tug, Expert, Lashing responsibility, adresse agent, statut Customs |
| **Crewing** | Matrice `POSITION × on/off signers` — **c'est le tableau d'armement** (MASTER, CHENG, CHOFF, MATE, BOSUN, AB 1/2, FITTER, COOK, CADET) ; nationalité ; billets (`CrewTicket` : vol/train, heures, lieux) ; PAF (`passage_paf`) | **Residence Permit** |
| **Technical** | Météo (`vessel_weather`) | Structure de **déviation** (Impact ETA/ETD, cause, responsable d'action) |
| **Commercial** | Contraintes ETA/ETD, client + quantité du prochain voyage (dérivable des commandes) | Volet Com & Marketing (événements médias, journalistes, visites) — surtout du texte |
| **Master & C/O** | Pilote (VHF/tél via `PortConfig`), **CTM = module Caisse de bord** | **Tide schedule**, avitaillement quantifié, matériel d'arrimage (airbags, dunnage, forklift) |

**Convergence remarquable** : ce document réel, rempli à la main chaque
semaine, confirme indépendamment **six** manques identifiés par l'audit sans
le connaître — `berth`, registre MARPOL Annexe V, **titre de séjour**
(exactement le champ manquant pour l'exemption Schengen), horaire de marée,
avitaillement quantifié, tableau d'armement par poste.

⇒ Ce ne sont pas des lacunes théoriques : **ce sont des champs que les
Opérations remplissent déjà à la main**. Les ajouter sert deux objectifs à la
fois (générer le document *et* combler des trous de conformité).

**Architecture retenue** — l'intention de Yasmin (event-driven : générer
depuis les données déclarées) est la bonne et a déjà un patron dans la maison
(MRV : on déclare des événements, tout le reste est dérivé). Réalisation en
**deux étapes** :

- **Étape 1 — le document devient une compilation.** Générer depuis ce qui
  existe déjà (~70 % des champs), champs manquants en saisie libre. Les
  Opérations arrêtent de recopier navire/leg/ETA/agent/soutage/dockers/client.
  Livrable : **XLSX au format actuel** (leur réunion tourne autour de ce
  fichier — changer le support *et* la source de données en même temps ferait
  deux risques d'adoption au lieu d'un).
- **Étape 2 — les trous se comblent, le document se dérive.** Ajouter `berth`,
  registre MARPOL, `residence_permit`, marée, avitaillement quantifié. Chaque
  champ ajouté sort de la saisie manuelle. Le « doc plus propre » (PDF) vient
  ici, quand les données sont fiables.

---

## 5. Matrice de priorisation

### P0 — Critique

| # | Sujet | Valeur métier | Complexité | Dépendances | Risque si non fait | Effort |
|---|---|---|---|---|---|---|
| 0 | Filet CI (integration + regression) | Habilitante | Faible | — | On modifie horodatage et BL sans filet ; 114 fichiers de tests dormants | 1 j |
| 1 | Horodatage ATD/ATA = heure de l'événement + contrainte `atd < ata` + « pilote départ » n'écrit plus l'ATD | 🔥 Élevée | Moyenne | #0 | Pollue **définitivement** le taux de service publié, les KPI, la fenêtre GPS, la finance. **Données non rattrapables** | 1,5 j |
| 2 | Schengen : plus de « conforme » par défaut + garde-fou d'embarquement recâblé | 🔥 Élevée | Faible | — | Faux vert = suppression de la vigilance manuelle. Interdiction Schengen 1-3 ans, navire immobilisé | 1 j |
| 3 | Carnet de bord : heures voile ×6 | 🔥 Élevée (document client) | Faible | — | Chiffre faux sur le claim central, ECGT 27/09 | 0,5 j |
| 4 | Marge : formule unique incluant `claims_cost` | Élevée (pilotage) | Faible | #0 | Marge surévaluée du montant exact du sinistre ⇒ arbitrage orienté vers les routes les plus sinistrées | 0,5 j |
| 5 | Alerte ETA dépassée en mer | 🔥 Élevée (quotidien Ops) | Triviale | — | La question posée chaque matin n'a pas de réponse dans l'outil | 0,25 j |

### P1 — Important

| # | Sujet | Valeur métier | Complexité | Dépendances | Effort |
|---|---|---|---|---|---|
| 6 | **Note d'escale — étape 1** (générateur XLSX depuis données existantes) | 🔥 Très élevée (demande Ops n°1, gain récurrent chaque escale) | Moyenne | Template ✅ reçu | 3-4 j |
| 7 | Bookings : nom du client + `leg_code` | 🔥 Élevée (demande Ops) | Triviale | — | 0,25 j |
| 8 | Rail unique : rediriger BL/PL vers le rail packing list (D2) | 🔥 Élevée (résout 2 demandes Ops) | Faible | — | 0,5 j |
| 9 | ~~Micro-gardes BL~~ **REVU 2026-07-29** — le workflow BL demandé (draft → validation client → signature commandant → final) **invalide le gel à l'émission** : le client doit pouvoir modifier la PL au stade draft. Le point de gel devient la **signature commandant**. Reste livrable au J2 sans migration : **journalisation des mutations du portail client** (0,5 j, comble aussi un trou d'audit §14.2). Le reste part dans le lot workflow BL — voir `SPEC_WORKFLOW_BILL_OF_LADING.md` | Élevée | Faible | — | 0,5 j |
| 10 | Découvrabilité documents de bord (Mate's Receipt, NOR, LOP depuis l'escale) | Élevée (**ça existe déjà**) | Faible | — | 0,5 j |
| 11 | Upload facture commerciale client (nommage explicite) | Moyenne (demande Ops) | Faible | — | 0,5 j |
| 12 | Note d'escale — étape 2 (berth, MARPOL, residence permit, marée) | Élevée (double bénéfice) | Moyenne | #6 | 2-3 j |

### P2 — Secondaire (après septembre)

Capacité : contrôle sur `order_confirm` + verrou à `submit()` + plafond de
poids *(descendu de P0 — cf. D1)* · Registre BL complet (originaux, remise,
révisions numérotées, hash PDF) · `Port.max_draft_m` + avertissement · Alertes
calendaires (prescription sinistre 1 an, expiration de polices, brevets
J-90/60/30) · Notifications non branchées (`notify_new_cargo_message`) ·
Correction QHSE Phase 0 (2 bugs) · Ouverture `commercial:claims/tickets` en
lecture · Manifeste · Frais de quai sur durée réelle · Alerte silence satcom ·
Nettoyage `services/pricing.py` (code mort contradictoire) · Traçabilité du
prix (`BookingPriceLine`) · Frais d'annulation rendus visibles

### P3 — Évolution future (décision manager requise)

Moteur de laytime/surestaries (**plan écrit existant** : `SOF_UPGRADE_PLAN.md`
S1→S8 — à exécuter, pas à improviser) · États `rolled`/`waitlisted`/
`part_shipped` · Certificat Anemos versionné/révocable · Modélisation MLC
(SEA, rapatriement, 11 mois, garantie d'abandon) · Ségrégation IMDG +
multi-POD/overstowage · Densité par lot de carburant + correction VCF ·
Jonction maintenance ↔ planning · Écran d'audience des feature flags

---

## 6. Ce qui est délibérément écarté

| À écarter | Pourquoi |
|---|---|
| États booking `rolled`/`waitlisted`/`part_shipped` | Très pertinent métier (les 3 événements les plus fréquents en liner) mais modifie le contrat de **tous** les consommateurs de `Booking.status` — dont `emission_ledger`, `kpi`, `anemos`, `carnet_bord`, donc le reporting MRV et les certificats. **Refactor à haut risque → validation manager.** |
| Moteur de laytime | Vraie valeur (surestaries = marge non captée) mais c'est un chantier avec un plan écrit. L'improviser produirait une demi-implémentation inexploitable juridiquement. |
| Développement MRV / QHSE Phase 1 | Le process manuel fonctionne (D3). Zéro valeur Opérations immédiate. |
| Ségrégation IMDG, overstowage, multi-POD | Modélisation lourde, et la priorité dépend d'une réponse métier absente (NEWTOWT accepte-t-il réellement du dangereux ?). |
| Modélisation MLC | Décision d'architecture, précédée d'une question factuelle non tranchée (Marad porte-t-il déjà les contrats d'engagement maritime ?). |
| Refonte du certificat Anemos | Exposition réelle, mais touche la chaîne MRV **et** le positionnement commercial ⇒ arbitrage manager obligatoire. |

---

## 7. Planning 10 jours

| Jour | Lot | Contenu | Livrable | Definition of Done |
|---|---|---|---|---|
| **J1** ✅ | Filet CI | État de la suite établi (781/29/1) ; option A retenue | `chore/ci-integration-tests` | Tests périmés corrigés ; integration+regression en CI ; écart cascade isolé en investigation ; tag `pre-upgrade-2026-08` ; `pg_dump` testé |
| **J2** | **Quick wins** | #5 alerte ETA · #7 client + `leg_code` · #3 heures voile · #8 rail unique · #9 micro-gardes BL | `feat/ops-quickwins` | **3 demandes Ops + 1 bug document client** ; captures avant/après pour validation Ops |
| **J3** | Faux verts | #2 Schengen `indéterminé` + garde-fou recâblé avec override tracé | `fix/no-false-green` | Tests sur les 2 ; aucun statut vert sans donnée |
| **J4** | Marge | #4 formule unique, `claims_cost_eur` non-éditable | `fix/margin-single-source` | Formule testée sur les deux chemins d'écriture |
| **J5-J8** | **Note d'escale étape 1** | #6 générateur XLSX Port Call Preparation | `feat/port-call-preparation` | XLSX généré au format actuel depuis les données existantes ; champs manquants en saisie libre ; validé par une personne des Opérations |
| **J9** | Horodatage | #1 ATD/ATA + contrainte `atd < ata` | `fix/atd-ata-occurred-at` | 3 tests de non-régression (SOF antidaté, inversion refusée, idempotence) ; migration en 2 temps (rapport puis contrainte) |
| **J10** | Livraison | Quality Gate + audit de compatibilité + journal | Rapport complet | Tous items du Quality Gate passés ou justifiés ; audit de divergence vs `main` ; **aucune PR sans accord explicite** |

**Note sur #1 (horodatage)** : ce bug ne se déclenche que par le chemin SOF du
module `/captain`. Le test à bord étant décalable (D4) et les masters sans
identifiants, il est **dormant** aujourd'hui. Placé en J9 pour cette raison —
à remonter en semaine 1 si le test à bord se confirme.

---

## 8. Analyse des risques

### Risques techniques

| Risque | Prob. | Impact | Mitigation |
|---|---|---|---|
| Les tests d'intégration révèlent des échecs préexistants nombreux | **Réalisé (J1)** | Maîtrisé | 29 échecs triés : ~15 environnement (PDF/WeasyPrint sur Windows), ~13 tests périmés, 1 à investiguer. Le filet protège le **code neuf**, il n'assainit pas le passé |
| La migration `atd < ata` échoue sur des données déjà incohérentes | **Élevée** | Migration bloquée | Migration en **deux temps** : rapport des lignes en infraction → correction → puis contrainte. Jamais une contrainte sur des données non auditées |
| L'écart cascade (1 jour) est un vrai off-by-one | Moyenne | Planning faux | Investigation dédiée ; **ne pas ajuster l'attente du test pour obtenir du vert** |
| Rediriger le bouton BL change le document reçu par les Ops | Moyenne | Confusion | Prévenir avant, montrer avant/après. Le nouveau BL est plus **complet**, pas différent en nature |
| Régression sur l'horodatage touchant le taux de service public | Faible | Visible publiquement | Tests dédiés + vérification manuelle sur un leg de démo avant merge |

### Risques métier

| Risque | Mitigation |
|---|---|
| **Test à bord avant les correctifs de bord** | Décalable (D4) ; si confirmé, remonter #1 en semaine 1 — les autres lots attendent |
| **Données de test MRV v2 polluant les calculs** (D4) | Rester en brouillon en exploration ; les brouillons sont exclus de tout calcul par construction. Ou dédier un navire de test |
| **Adoption : les Ops valident sans avoir vraiment testé** | Session de validation avec captures avant/après sur les lots J2 et J5-J8, plutôt qu'un « c'est livré ». Les équipes reviennent progressivement de congés — bon moment |
| **Attente implicite « tout sera prêt en septembre »** | Le plan livre ~12 lots sur ~60 constats d'audit. À dire explicitement dans le journal : **on livre la valeur, pas l'exhaustivité** |

### Dépendances critiques

| Dépendance | Bloquant pour | Statut |
|---|---|---|
| Template note d'escale | #6, #12 | ✅ Reçu 2026-07-29 |
| Décision overbooking | Capacité | ✅ Tranché (D1) |
| Posture flag MRV v2 | Test à bord | ✅ Tranché (D4) |
| **Admin GitHub (protection de branche `main`)** | Sécurité de tous les commits | ⛔ **Non résolu** — Yasmin n'est pas admin. À escalader (un incident de merge direct a déjà cassé `main`) |
| Rubriques Silae (cabinet de paie) | Pont paie marin (P3) | ⛔ Non lancé — long délai, mail à envoyer |
| Hydrostatiques / capacités cuves (Q11) | Cargo MRV auto, R23 bloquant | ⛔ Non fourni |

### Quick wins (à faire même si tout dérape)

Par ratio valeur/effort décroissant : **alerte ETA** (une ligne, question
quotidienne des Ops) · **nom client + `leg_code`** (~10 lignes, demande Ops
n°1) · **heures voile** (une ligne, document client) · **redirection du bouton
BL** (résout une demande Ops en pointant vers l'existant).

**Moins d'une journée cumulée, trois demandes Opérations sur huit adressées.**
Si un seul paquet devait être livré, c'est celui-là.

---

## 9. Méthode de développement sécurisée

**Avant chaque lot** — analyse d'impact écrite dans le journal *avant* la
première ligne de code : modules touchés, consommateurs de la donnée
modifiée (par `grep` exhaustif), migrations, contrats d'API.

**Sauvegarde** — `pg_dump` horodaté avant chaque migration ; tag git de
référence avant le premier lot (`pre-upgrade-2026-08`). Procédure de
restauration **testée une fois**, pas supposée fonctionner.

**Une branche par lot, jamais `main`.** Chaque lot doit être mergeable et
**révocable indépendamment** — pas de lot dépendant d'un autre non mergé.

**Rollback** — pour chaque migration, `downgrade()` écrite **et exécutée une
fois** en local. Pour les changements de comportement visibles par les
Opérations, préférer un **feature flag** à un déploiement irréversible
(l'infrastructure `feature_flags` existe déjà).

**Validation continue** — après chaque lot : suite complète en local
(unit + integration + regression), lancement réel de l'app avec les données
de démo, vérification manuelle du parcours touché. **Un test qui passe ne
prouve pas qu'un écran fonctionne.**

**Quand une modification paraît trop risquée** — proposer une alternative
plus petite plutôt que de la tenter. Exemples déjà appliqués : micro-gardes
BL (#9) au lieu du registre complet ; note d'escale en deux étapes.

---

## 10. Quality Gate (obligatoire avant toute recommandation de PR)

Compilation · lint (`ruff`, `black --check`) · **`mypy app` comparé au plafond
`MYPY_MAX` de la CI** · tests **unit + integration + regression** · absence de
régression · documentation à jour · cohérence des migrations (`upgrade` **et**
`downgrade`) · compatibilité des contrats d'API · `bandit` · `pip-audit` ·
`gitleaks` · absence de secrets · fichiers temporaires/debug retirés · pas de
dégradation de performance.

**Tout item en échec est expliqué avec une action corrective proposée, jamais
passé sous silence.**

### Règles issues d'échecs réels de ce Quality Gate

1. **`mypy app` fait partie du gate** (ajouté 2026-07-30). Le lot J1 a annoncé
   « dette introduite : aucune » alors qu'il ajoutait une erreur — parce que les
   contrôles locaux ne lançaient pas mypy et que l'étape CI est
   `continue-on-error`. Comparer au plafond, pas au vert de l'étape.
2. **Ne jamais se fier au vert d'une étape `continue-on-error`.** Trois gardes
   de CI affichaient vert sans rien garder (suites non exécutées, mypy en
   dérive de 3×, gitleaks en échec permanent). **Un contrôle qu'on ne fait pas
   échouer volontairement au moins une fois n'est pas un contrôle.**
3. **Rejouer la suite COMPLÈTE, jamais ciblée**, après toute modification d'un
   helper partagé (règle née d'une régression J1 sur `voyage_track`, revérifiée
   au J3 sur `ensure_utc` et ses 57 appelants).
4. **Un correctif de typage se fait à la racine, pas au point d'appel** quand la
   signature du helper est en cause : `ensure_utc` correctement typée a résorbé
   64 erreurs au lieu d'en masquer 1.

## 11. Audit de compatibilité (obligatoire avant toute PR)

1. **Divergence de branche** vs `main` : commits ahead/behind, fichiers
   modifiés, recouvrements, conflits potentiels, fichiers renommés/supprimés,
   changements de dépendances, différences de config, conflits de migration,
   changements d'API.
2. **Analyse d'impact** : frontend, backend, base, APIs, auth, permissions,
   workflows métier, intégrations, CI/CD, déploiement, documentation, tests
   — et impacts indirects.
3. **Niveau de risque unique** : 🟢 Faible / 🟡 Modéré / 🟠 Élevé / 🔴 Critique,
   avec justification.
4. **Rapport de compatibilité** : statut, risques, points bloquants, dette
   introduite, recommandations avant fusion.
5. **Plan d'action** si problèmes : étapes, ordre d'exécution, complexité,
   risque d'implémentation, bénéfice attendu.
6. **Recommandations d'ingénierie** : scinder la branche, PR multiples,
   rebase, squash, refactor préalable, report de fonctionnalités risquées…

### ⚠️ Méthode de détection des recouvrements (erreur commise le 2026-07-30)

**Toujours partir de la BASE COMMUNE, jamais de `main..branche`.**

```bash
# FAUX — pour une branche en retard, remonte tout ce que `main` a fait évoluer
# depuis, et non ce que la branche modifie.
git diff --name-only main..feature/x

# JUSTE — ce que la branche modifie RÉELLEMENT depuis son point de départ.
git diff --name-only $(git merge-base main feature/x)..feature/x
```

La méthode fautive a produit **deux constats faux** le même jour :
`feature/qhse-foundation` (39 commits **behind**) semblait toucher
`crew_router.py`, `crew/index.html` et `CLAUDE.md` — en réalité **aucun** : ses
2 commits ne touchent que les fichiers QHSE, i18n, `permissions.py`, `main.py`
et `validation_engine.py`. Et elle donnait l'illusion qu'une fusion de `qhse`
**supprimerait le trombinoscope** (149 lignes) : faux, une fusion ne rejoue que
les commits de la branche depuis la base commune — vérifié par `git merge-tree`,
le trombinoscope survit et il n'y a aucun conflit.

Corollaire : `feature/crewing-monthly-yearbook` est à **0 commit d'avance** —
elle est **déjà entièrement fusionnée** dans `main` (PR #148). Une branche qui
existe encore n'est pas une branche en attente.

**Toujours confirmer un recouvrement supposé par une fusion à blanc**
(`git merge-tree <base> <a> <b>`) avant de le présenter comme un risque.

**Aucune PR proposée avant cet audit. Aucune PR créée sans demande explicite
de Yasmin. Jamais de merge, jamais d'approbation.**

---

## 12. RAF — reste à faire ouvert (suivi)

Éléments identifiés, **non traités**, avec leur portée réelle. Mis à jour au fil
de l'eau. Un item ne bloquant rien aujourd'hui peut bloquer un lot futur : la
colonne « bloque quoi » est la seule qui compte pour l'ordonnancement.

| # | Item | Bloque le J2 ? | Bloque quoi réellement | Échéance | Qui |
|---|---|---|---|---|---|
| R1 | ✅ **CORRIGÉ 2026-07-30, en attente de validation de Julien** — migration de fusion **pure** `20260730_0113_merge_heads.py` (aucun DDL) sur `fix/alembic-merge-heads`, issue de `main`. Écrite à la main plutôt que par `alembic merge` (`migrations/` n'est pas monté dans le conteneur). Vérifié : **une seule tête**, `(mergepoint)` présent dans l'historique, `upgrade()`/`downgrade()` exécutables. Fusion sûre : les deux chaînes touchent des tables disjointes (`nav_event_noon` / `generated_reports`), leur ordre est indifférent | — | **Débloque le lot workflow BL, le J9, le lot relèves et tout déploiement.** 🔴 **Julien est le seul à pouvoir valider une fusion** et il est absent jusqu'au **2026-08-17** ⇒ stratégie « tout préparer, ne rien fusionner », les lots à migration se branchant sur celui-ci (cf. `07-ordre-pr-et-merge.md` §1 bis) | Retour de Julien, 2026-08-17 | **Julien** |
| R1b | 🟡 **`alembic upgrade head --sql` inutilisable** — découvert lors de R1, **préexistant et sans lien** : `20260703_0094_planning_rules_hardening.py` fait un `fetchall()`, impossible en mode offline (pas de connexion). On ne peut donc **pas prévisualiser le DDL** d'un déploiement | **Non** | Un déploiement en deux temps (revue du SQL, puis application). Le déploiement normal n'est pas affecté | Opportuniste | — |
| R2 | ✅ **RÉSOLU 2026-07-29** — lot J1 rebasé sur `main`. La dépendance venait d'un seul commit amendant `PROJECT_CONTEXT.md` (document du lot découverte) : la correction du §7 a été déplacée vers le lot découverte (`e48847d`), rendant les deux lots indépendants. Vérifié : `main` est ancêtre direct des deux, ils fusionnent proprement (`CLAUDE.md` s'auto-fusionne), suite revalidée 2000/15 | — | — | — |
| R3 | 🟠 **Protection de branche absente sur `main`** — Yasmin n'est pas admin du dépôt. Un incident de merge direct a déjà cassé `main` par le passé | **Non** | Rien techniquement — **contrôle de risque pur**. D'autant plus pertinent qu'on produit beaucoup de commits sur cette période | Dès que possible | À escalader auprès de la personne admin |
| R4 | ✅ **RÉSOLU 2026-07-30** — PR #149 créée, CI exécutée : **2015 passés · 1 ignoré** sur Ubuntu. Les 15 échecs locaux étaient bien un artefact WeasyPrint/GTK sous Windows : hypothèse **confirmée, plus supposée**. Le filet fonctionne réellement | — | — | — | — |
| R4b | 🟠 **Deux autres gardes de CI mentaient** — découvert au 1er run. **Gitleaks n'a jamais scanné une seule PR** (`GITHUB_TOKEN` devenu obligatoire ; `continue-on-error` masquait l'échec — vérifié identique sur le run du 23/07, donc préexistant). Et l'étape mypy annonçait « baseline 142 » pour **434** réelles. **Corrigés dans la PR #149** : token ajouté + cliquet bloquant anti-dérive du typage (plafond 371) | — | Plus rien. La 1re exécution réelle de gitleaks est le run suivant — **à surveiller**, il peut révéler des secrets historiques jamais détectés | Vérifier au prochain run | — |
| R5 | 🟡 **Filet Postgres-free** — toute la suite tourne sur SQLite en mémoire ; ni `TIMESTAMP WITH TIME ZONE`, ni types `Numeric`, ni migrations Alembic ne sont couverts. Le service Postgres du job CI est de la config morte | **Non** | La **fiabilité du filet** sur les lots touchant les dates (J3 Schengen) et le schéma (J9). Piste : `testcontainers[postgres]`, déjà dans `requirements-dev.txt` | Avant J9 | — |
| R6 | 🟠 **Embarquement hors leg (A4) non saisissable** — cas métier confirmé (changement d'équipage en arrêt technique) mais aucun chemin applicatif ne crée d'affectation sans leg (`EscaleOperation.leg_id` est NOT NULL, et c'est le **seul** point de création de l'app). **Et** le calcul Schengen saute ces affectations (`crew_compliance.py:231-233`) ⇒ leurs jours ne seraient pas comptés. **Même angle mort** sur `vessel_readiness` (l.311) | **Non** | Rien pour le J2. À traiter **avec** le J3 (faux verts Schengen) : restaurer la saisie sans corriger le calcul ne servirait à rien | J3 | — |
| R9 | 🟡 **Deux registres d'embarquement parallèles** (analyse d'impact J3, 2026-07-30). `marad_crew_schedules` porte les relèves décidées par l'Armement (lecture seule, **source de vérité**) ; `crew_assignments` est alimenté **uniquement** par la saisie d'escale. **Conséquences traitées le 2026-07-30** : le double comptage des jours en mer est corrigé (union d'ensembles de jours), et le statut Schengen dit désormais `indetermine` au lieu d'affirmer « conforme » sans données. **Invariant documenté dans `CLAUDE.md`** (§Équipage — deux registres) | — | **Plus la justesse des indicateurs** (corrigée). Reste : `vessel_readiness` et `crew_border_police_pdf` ne lisent toujours que les affectations rattachées à un leg ⇒ **la liste PAF est probablement incomplète en production** | Avec le lot relèves | — |
| R10 | ✅ **TRANCHÉ 2026-07-30 — ce n'était pas un problème de permissions.** Le processus réel (recueilli auprès de l'Armement) : la décision de relève se prend **dans Excel**, pas dans MyTOWT ni dans Marad ; l'agent d'escale **ne décide rien**, il organise les RDV PAF à partir de ce que l'Armement lui transmet. La matrice est donc **cohérente avec l'organisation** : `armement` n'a pas besoin d'écrire dans `escale`. Ce qui manque n'est pas un droit, c'est **le processus de relève lui-même**, absent du logiciel | — | — | — | — |
| R11 | 🟠 **Le processus de relèves d'équipage est hors du logiciel** — simulation Excel, décision Excel, puis transmission PAF à l'agent d'escale → note d'escale. **C'est le vrai manque fonctionnel**, la conformité Schengen n'en étant qu'un sous-produit déjà couvert par Marad. ✅ **Excel analysés le 2026-08-03** → `REFERENCE_METIER_RELEVES_EQUIPAGE.md` | **Non** | Le lot « relèves d'équipage ». **Bloqué sur 3 questions à l'Armement** (§7 de la référence métier) : les coefficients d'acquisition font-ils foi et alimentent-ils la paie · élèves 60 ou 90 j · les overrides de durée sont-ils volontaires. Elles déterminent si le lot est un outil de planification ou un **compteur de droits** — deux périmètres très différents | Dès réponses de l'Armement | Yasmin (relance Armement) |
| R12 | 🔑 **Le moteur de la simulation est un grand livre d'acquisition de congés**, pas un `début + 60` (découverte du 2026-08-03, feuille `data`) : chaque jour embarqué crédite 0,9 j (1,0 pour le personnel PMS), chaque jour à terre en débite 1, avec des coefficients par statut (formation 0,6 · AT 0,2 · cadet 0,3 · télétravail 0,9). MyTOWT n'a **aucune** logique d'acquisition — `CrewLeave` gère la demande/approbation, `services/leaves.py` ne fait que lister et compter | **Non** | Le dimensionnement du lot relèves. La feuille porte les **matricules** ⇒ adjacence paie à clarifier **avant** de coder | Avec R11 | Armement + RH/paie |
| R13 | ⚠️ **Deux conventions de comptage de jours, légitimement différentes** — Excel compte les jours à bord en **exclusif** (`AUJOURDHUI()−début`, jour d'embarquement = 0) car cohérent avec « contrat 60 j ⇒ fin = début + 60 » ; la règle **Schengen** 90/180 est **inclusive** (jour d'entrée et de sortie comptés). Écart mesuré : 62 vs 63 jours sur un cas réel | **Non** | Toute fonction de comptage future. **Ne pas unifier — nommer** : confondre les deux casse soit un contrat, soit une conformité réglementaire | À appliquer dès le lot relèves | — |
| R7 | 🟡 **Dérive de schéma de la base de dev** rattrapée à la main le 2026-07-29 (8 colonnes ajoutées). La procédure §7 de `PROJECT_CONTEXT.md` est corrigée, mais **aucun garde-fou** n'empêche la dérive de réapparaître | **Non** | Rien. Confort et fiabilité des validations locales. Piste : un script de diagnostic `Base.metadata` ↔ `information_schema` à lancer au démarrage en dev | Opportuniste | — |
| R8 | 🟡 **Hook du harnais lançant `alembic` depuis l'hôte** — échoue à chaque commit (`getaddrinfo failed`, le nom `db` n'est résoluble que dans Docker). Bruit permanent, aucun impact fonctionnel | **Non** | Rien | Opportuniste | Yasmin (config `settings.json`) |

### Ordre de priorité imposé par Yasmin (2026-07-30) — à appliquer à toute analyse

1. Comprendre les processus réels de l'entreprise.
2. Les **reproduire fidèlement** dans MyTOWT.
3. Valider que les équipes peuvent travailler efficacement avec le logiciel.
4. **Ensuite** seulement : contrôles, conformité, qualité de données.

> « La valeur métier doit toujours passer avant les contrôles de conformité ou les
> fonctionnalités *nice to have*. »

Elle demande **explicitement à être challengée** quand un risque opérationnel ou
réglementaire majeur justifierait l'inverse. Deux endroits où la ligne se tient :
le **MRV** (organisme accrédité, échéance réglementaire dure) et le **workflow
BL** (titre de propriété, prescription Hague-Visby d'un an) — tous deux déjà
priorisés.

**Distinction retenue, qui a servi le 2026-07-30** : un indicateur qui **affirme
quelque chose de faux** n'est pas un contrôle manquant, c'est un **défaut**. Le
corriger peut consister à le faire **taire** plutôt qu'à le rendre intelligent —
quelques heures au lieu de plusieurs jours. Et un chiffre dont dépendra une
fonctionnalité métier future (les jours en mer pour la planification des relèves)
n'est pas un « contrôle de phase 4 » : il est **absorbé** par la valeur métier.

**Lecture d'ensemble (mise à jour 2026-07-30)** : **R1, R2 et R4 sont soldés** —
la fusion Alembic débloque le lot workflow BL, le J9 et tout déploiement (reste
la validation manager), et la CI a fait la preuve du filet.

**Le J3 a été repriorisé en cours de route** par le recueil du processus réel
auprès de l'Armement : **R10 est tranché** (ce n'était pas un problème de
permissions — la décision se prend dans Excel, la matrice est cohérente), **R9 est
traité** sur ses deux conséquences mesurables, et le vrai manque est **R11** : le
processus de relèves lui-même, absent du logiciel, en attente des fichiers Excel.

**Abandonné au J3** : le recâblage du garde-fou passeport. Il aurait contraint
l'agent d'escale, qui ne décide pas les embarquements — il transcrit une décision
prise par l'Armement après vérification via les alertes Marad. Recommandation
initiale erronée, corrigée dès réception du processus réel. R6 reste ouvert (le
chemin de saisie hors leg n'existe toujours pas) mais n'est plus urgent : il sera
traité par le lot relèves ou pas du tout.

Restent sans échéance forte : R3 (escalade externe), R5 (avant le J9), R1b, R7,
R8 (opportunistes).

⚠️ **Leçon de méthode du 2026-07-30** : trois gardes de CI affichaient vert sans
rien garder, et mon propre Quality Gate a affirmé « dette introduite : aucune »
alors que le lot ajoutait une erreur mypy. Cause commune : **un contrôle qu'on
ne fait pas échouer volontairement au moins une fois n'est pas un contrôle**.
Ajouté au Quality Gate (§10) : exécuter `mypy app` et **comparer au plafond**,
ne jamais se fier au vert d'une étape `continue-on-error`.

## 13. Questions ouvertes

1. **Écart cascade** (J1, catégorie ③) : attente de test périmée après un
   changement délibéré non documenté, ou off-by-one réel ? → investigation.
2. **NEWTOWT accepte-t-il réellement du fret dangereux ?** Détermine la
   priorité de la ségrégation IMDG (aujourd'hui inexistante : toutes classes
   confondues dans les mêmes zones, café/cacao = denrées alimentaires).
3. **Marad porte-t-il les contrats d'engagement maritime (SEA), certificats
   de rapatriement et garantie financière d'abandon ?** Si oui → import
   lecture seule ; si non → module. À répondre **avant** de coder.
4. **Un leg peut-il avoir plusieurs ports de déchargement ?** Prérequis d'un
   vrai contrôle d'overstowage.
5. **Le FMS reste-t-il la source de vérité QHSE ?** Détermine tout le design
   de la Phase 1 (miroir idempotent vs seconde source d'écriture).
