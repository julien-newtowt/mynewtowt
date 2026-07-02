# Cahier des charges — vérification tierce de la méthode Anemos

> Paquet P3 (ENV-05/ENV-08) du rapport `RAPPORT_ARCHITECTURE_UNIQUE.md`.
> Objet : consultation d'organismes tiers pour la vérification du facteur
> d'émission NEWTOWT et de la méthode d'émissions évitées, et l'alignement
> ISO 14083:2023 / GLEC Framework. Statut : v1.0 — 1er juillet 2026, à
> valider par la direction avant envoi.

## 1. Contexte

- NEWTOWT émet, pour chaque expédition livrée, un **certificat Anemos**
  d'émissions de CO₂ évitées (périmètre tank-to-wake, CO₂ hors CH₄/N₂O),
  calculé selon une méthode publiée (`/preuves/methodologie.pdf`, v1.0) sur
  des facteurs **versionnés en base** (`/admin/co2`) :
  facteur NEWTOWT **1,5 g CO₂/t·km** (dérivé des émissions réelles des
  navires) vs référence conventionnelle **13,7 g CO₂/t·km** (IMO Fourth GHG
  Study).
- Les **émissions des navires** sont déjà surveillées, déclarées et vérifiées
  par un organisme accrédité au titre du règlement **EU MRV** (UE 2015/757,
  registre public THETIS-MRV). La **méthode d'évitement**, elle, est
  aujourd'hui une **auto-déclaration documentée** de NEWTOWT.
- Échéance : la directive **(UE) 2024/825 (ECGT)** s'applique au
  **27/09/2026**. La vérification tierce de la méthode ferme le dernier point
  de vigilance (auto-label) et devient un argument commercial (« opposable en
  audit ») auprès des clients CSRD/SBTi.

## 2. Objet de la consultation (3 lots)

### Lot 1 — Vérification du facteur et de la méthode (prioritaire)
- Vérifier le **facteur d'émission NEWTOWT** (g CO₂/t·km) : données sources
  (noon reports signés, consommations DO, distances GPS, tonnages
  transportés), représentativité, calcul, gestion des versions.
- Vérifier la **méthode d'émissions évitées** : choix de la référence
  conventionnelle, formule, hiérarchie mesuré/théorique (noon reports vs
  orthodromie × 1,15), traitement des cas limites (leg partiel, transbordement,
  co-chargement).
- Livrable : **déclaration de vérification** (assurance limitée acceptée en
  v1 ; viser l'assurance raisonnable en v2) + rapport détaillé + droit de
  mention (« méthode vérifiée par [organisme] », usage site/PDF/certificats).

### Lot 2 — Alignement ISO 14083:2023 / GLEC Framework
- Écart entre la méthode Anemos et **ISO 14083:2023** (quantification GES des
  opérations de transport) + **GLEC Framework** (v3) : périmètres (TtW vs WtW),
  facteurs, intensités (g CO₂e/t·km), allocation par expédition.
- Livrable : **analyse d'écarts + plan de mise en conformité** chiffré,
  incluant la trajectoire vers une déclaration **WtW/CO₂e en complément**
  (sans abandonner l'affichage CO₂/TtW actuel, cohérent EU MRV).

### Lot 3 — Avis de conformité communication (optionnel)
- Revue des supports porteurs d'allégations (site public, certificat PDF,
  rapport annuel, kit B2B2C, page publique de voyage) contre l'ECGT et la
  pratique DGCCRF, sur la base de notre grille interne
  (`AUDIT_CLAIMS_ECGT.md`).
- Livrable : avis écrit + liste de corrections éventuelles.

## 3. Données mises à disposition (data room)

- Méthodologie v1.0 (PDF + page `/about/anemos` + `/preuves`).
- Historique des facteurs versionnés (`co2_variables`, journal d'audit).
- Échantillon de certificats émis (mesuré et théorique) + registre `/verify`.
- Noon reports signés (extraits), exports MRV (format DNV, 18 colonnes),
  références THETIS-MRV des navires.
- Traces GPS des legs concernés (vessel_positions), distances.
- Rapport annuel type + réconciliation mesuré/théorique.

## 4. Candidats à consulter

| Organisme | Pertinence | Remarques |
|---|---|---|
| **Verifavia** (groupe Normec) | Vérificateur émissions transport (aviation/maritime), déjà dans l'écosystème MRV | Bon rapport coût/spécialisation maritime |
| **Bureau Veritas Certification** | Accrédité MRV, forte marque France, offres ISO 14083 | Poids de la marque dans le B2B agro |
| **DNV Business Assurance** | Référence maritime mondiale, format DNV déjà utilisé pour nos exports MRV | Continuité avec l'existant |
| **SGS** | Vérification GES + alignement GLEC | Alternative de mise en concurrence |
| Smart Freight Centre (accréditation GLEC) | Pour le lot 2 : conformité GLEC officielle | Complément possible du lot 1 |

(Consulter au minimum 3 organismes ; le vérificateur MRV actuel des navires
peut candidater mais la mission « méthode d'évitement » doit rester
distincte de sa mission MRV pour éviter l'auto-revue.)

## 5. Critères de sélection

1. Accréditation pertinente (ISO 14065 / vérificateur MRV UE) et références
   transport maritime — 30 %.
2. Compréhension de la méthode et qualité de l'approche proposée (plan de
   vérification, échantillonnage) — 25 %.
3. Délais compatibles avec le rétro-planning §6 — 20 %.
4. Droits d'usage de la mention/du logo dans notre communication — 15 %.
5. Prix — 10 %.

## 6. Rétro-planning (échéance ECGT : 27/09/2026)

| Jalon | Date cible |
|---|---|
| Envoi du CDC aux candidats | 7 juillet 2026 |
| Réponses + soutenances | 24 juillet 2026 |
| Contractualisation lot 1 (+2) | 7 août 2026 |
| Data room ouverte, vérification | août – mi-septembre 2026 |
| Déclaration de vérification lot 1 | **19 septembre 2026** |
| Publication (site /preuves + méthodologie v1.1 + communiqué) | **avant le 27 septembre 2026** |
| Lot 2 (ISO 14083/GLEC) : plan d'écarts | T4 2026 |

## 7. Budget indicatif (à valider en consultation)

Ordres de grandeur marché (estimations internes, non contractuelles) :
lot 1 : 8–18 k€ ; lot 2 : 6–12 k€ ; lot 3 : 3–6 k€. Arbitrage recommandé :
lots 1+2 fermes, lot 3 en option (notre grille interne couvre déjà
l'essentiel).

## 8. Risques et parades

- **Facteur invalidé partiellement** → le versionnage existant permet une
  correction propre (nouveau facteur, nouvelle version imprimée sur les
  certificats suivants, méthodologie révisée) ; ne jamais réécrire les
  certificats émis.
- **Délais organisme** → lancer la consultation immédiatement ; à défaut de
  déclaration au 27/09, le site reste conforme (l'audit ECGT ne repose pas
  sur la vérification tierce, elle la renforce).
- **Écart ISO 14083 (CO₂e/WtW)** → assumé et documenté publiquement sur
  /preuves ; le lot 2 fournit la trajectoire, pas une bascule immédiate.

## 9. Contact et gouvernance

Pilote : direction (validation du CDC avant envoi) ; support technique :
équipe plateforme (data room, extractions) ; suivi dans le backlog sous
ENV-05/ENV-08. Toute mention publique de la vérification n'est publiée
qu'après réception de la déclaration écrite.
