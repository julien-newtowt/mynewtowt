# Audit 5 — Lecture par personas métiers internes (cycle 2)

> **Objet** : étendre l'audit 360 aux personas métiers de l'entreprise, après
> la mise en œuvre des correctifs du 2026-06-12 (commits `f568f41` → `e53c717`).
> Pour chaque persona : ce que les correctifs changent dans son quotidien, ce
> qu'il doit faire pour en tirer parti (« mode d'emploi »), et ce qui reste
> douloureux — c'est la matière première des **évolutions en profondeur par
> métier** annoncées pour le cycle 3.
> Personas de référence : [`docs/personas/01-personas.md`](../../personas/01-personas.md).
> Conventions : voir [README](README.md).

---

## 1. Inès — commerciale

**Ce qui change pour elle (cycle 2)**
- Les **grilles tarifaires portent désormais sa logique métier** : 1 route
  POL/POD + 1 période, grille par défaut générée pour chaque route du planning
  (formule OPEX), grille client prioritaire quand le compte plateforme est
  relié au client commercial.
- Les **options de grille** lui permettent de tarifer les à-côtés sans
  bricolage : manutention à la palette, contribution à la tonne chargée,
  forfait réservation, frais de booking note.
- Les **devis arrivent tout seuls** : `/commercial/devis` liste les demandes
  publiques (avec coordonnées) ; chaque lead `/contact` et chaque devis invité
  avec email part dans Pipedrive et lui est notifié.
- La **capacité est enfin une vérité unique** : ses commandes classiques
  décomptent la même cale que les bookings (FLX-01) — plus de survente
  silencieuse entre les deux canaux.

**Son mode d'emploi immédiat**
1. Cliquer « Générer les grilles par défaut » (`/commercial/grids`) puis
   **relire chaque taux généré** : la formule OPEX donne un plancher
   économique, pas un prix de marché [J].
2. Activer/ajuster les options par route (la THC est livrée désactivée à
   12 €/palette à titre d'exemple).
3. Relier les comptes plateforme de ses clients négociés (fiche client →
   « Comptes plateforme reliés ») — sinon ils seront cotés sur la grille
   par défaut.
4. Renseigner `COMMERCIAL_INBOX_EMAIL` (et le token Pipedrive) en production.

**Ce qui lui manque encore** : conversion devis → réservation pré-remplie ;
politique d'annulation publiée et outillée (COM-08) ; funnel mesuré (COM-13) ;
relances sur devis non transformés.

## 2. Mathilde — capitaine

**Ce qui change pour elle**
- **Ses saisies pilotent désormais l'aval** : un SOF de départ/arrivée signé
  pose ATD/ATA et fait avancer les réservations clients (emails « en mer » /
  « débarquée » automatiques) ; son noon report **génère l'événement MRV**
  (fini la triple saisie du fuel) ; et ce sont **ses consommations déclarées**
  qui font foi sur les certificats clients (méthode « réel déclaré »).
- **L'outil devient embarquable** : PWA installable sur la tablette, noon
  report et journal de quart **saisissables hors ligne** (file locale,
  synchronisation au retour réseau, aucune perte ni doublon).

**Vigilances nouvelles** [J] : son exactitude (fuel, distances 24 h) porte
désormais des effets clients et réglementaires directs — c'est voulu (le bord
est la référence n°1), mais cela mérite une consigne de bord écrite : *noon
report quotidien signé = engagement de données*.

**Ce qui lui manque encore** : check-lists ISM/ISPS et registre visiteurs
(modèles en base, toujours pas d'écrans — FLX-11) ; météo intégrée à la
saisie ; mode haute lisibilité passerelle ; étendre la file hors-ligne aux SOF.

## 3. Tomé — agent d'escale

**Ce qui change pour lui** : peu, ce cycle — et c'est le principal constat.
Les jalons clients sont désormais déclenchés par le bord (SOF), pas par lui,
ce qui clarifie les rôles ; le rollup finance valorise enfin ses saisies de
coûts dockers à la clôture.

**Ce qui reste douloureux (priorités cycle 3)** : la **double saisie
escale ↔ SOF** (FLX-04) est intacte ; pas d'escalade automatique des tickets
P1 (SLA 2 h calculé mais silencieux — FLX-08) ; pas de verrouillage d'escale ;
mobile sur quai toujours non optimisé (le traitement PWA a ciblé `/onboard`,
pas `/escale`).

## 4. Khadija — armement / RH

**Ce qui change pour elle**
- La conformité Schengen est **persistée** (statut, jours consommés, fenêtre)
  et surtout **opposable au planning** : l'affectation d'un marin non conforme
  ou au passeport expiré est bloquée, l'éventuel passage en force est explicite
  et audité.
- Le **panneau d'armement réglementaire** lui montre par navire les rôles clés
  manquants (capitaine, second, chef mécanicien, cuisinier, lieutenant, bosco).

**Ce qui lui manque encore** : liste PAF imprimable ; alertes proactives
J-30/J-7 (certificats, visas) ; historique daté des statuts Schengen
(snapshots) ; congés et paie variable en profondeur (module RH toujours
minimal).

## 5. Pierre — superintendant (technique)

**Ce qui change pour lui** : rien de spécifique ce cycle. Le registre des
certifications navire, les jauges d'expiration et le ticketing technique
restent les manques identifiés au volet 3. Candidat naturel du cycle 3, avec
le métier escale [J].

## 6. Le manager maritime & la data analyst — pilotage

**Ce qui change pour eux**
- La **marge par voyage existe enfin sans saisie** : à l'approbation de
  clôture, revenus (bookings + commandes), coûts dockers et part OPEX (jours
  de mer réels) sont consolidés ; bouton de recalcul à la demande.
- Les **droits sont gouvernables** : matrice 8 rôles × 17 modules ajustable
  dans l'admin (overrides surlignés, garde-fou administrateur), appliquée
  sous 60 s, repli sûr sur la matrice codée.
- Les **variables CO₂ sont versionnées** dans l'admin avec source et date
  d'effet — KPI et certificats les consomment.

**Ce qui leur manque encore** : alertes sur seuils (remplissage, marge,
SLA) ; écart prévisionnel/réalisé présenté en variance ; exploitation du
tracking (ETA dynamique, geofence d'arrivée) ; funnel commercial.

## 7. Le client / l'acheteur RSE (Léa)

**Ce qui change pour lui**
- Il obtient un **devis sans créer de compte**, sur une grille traçable
  (référence de grille imprimée sur le devis), options comprises.
- Son **certificat Anemos dit la vérité de sa méthode** : « consommations
  déclarées à bord (noon reports signés) » quand la traversée a des données,
  forfait assumé sinon — et plus aucun pourcentage marketing nulle part.
- Sa **booking note** remplace la facture dans l'espace client (la
  facturation est gérée par la comptabilité, hors plateforme).

**Ce qui lui manque encore** : rapport CO₂ annuel consolidé (ENV-06),
vérificateur tiers nommé + QR de vérification (ENV-04), méthodologie au
format ISO 14083 (ENV-05), parcours EN/PT-BR (COM-07).

## 8. Synthèse — carnet d'ordres du cycle 3 (« en profondeur des métiers »)

| Métier | Chantier prioritaire | Constats portés |
|---|---|---|
| Escale (Tomé) | Saisie unique escale↔SOF + escalade SLA + mobile quai | FLX-04, FLX-08 |
| Bord (Mathilde) | Check-lists ISM/ISPS + visiteurs + offline étendu aux SOF | FLX-11 |
| Technique (Pierre) | Registre certifications navire + ticketing technique | volet 3 §6 |
| RH (Khadija) | PAF + alertes expirations + snapshots Schengen | FLX-06 (suite) |
| Commercial (Inès) | Conversion devis→booking + annulation + funnel | COM-08, COM-13 |
| Pilotage | Variance prévisionnel/réalisé + alertes seuils + tracking exploité | FLX-07 |
| Client RSE | Rapport annuel + vérificateur nommé + ISO 14083 | ENV-04/05/06 |
| Socle | Poursuite des domaines (RH/tracking/analytics hors modules_router) | ARC-03 (suite) |

---

*Retour au [cadre & synthèse](README.md) — registre post-correctifs au §7.*
