# Arbitrages métier — décisions actées

Ces décisions conditionnent le périmètre de certains tickets P0. **Statut : tranchées le
2026‑06‑22.** Le tableau de synthèse en bas reste pour mémoire ; les conséquences ci‑dessous
font foi pour le périmètre des tickets.

## ✅ Décisions actées (2026‑06‑22)

### A1 — MRV : **Hybride** (noon auto + compteurs DO de contrôle)
- **Tickets en périmètre :** MRV‑04, MRV‑05, MRV‑06 (tous **GO**).
- **Conséquence :** conserver la **synchro auto** noon/SOF → `MRVEvent` (gain V3) **ET** réintroduire
  les **4 compteurs DO** + `compute_consumption` (ME/AE) + `compute_rob` + **contrôle qualité
  multi‑règles** (statut `error` bloquant) appliqué à **tous** les events (auto et manuels).
  Source primaire machine = compteurs ; noon report = complément + cross‑check. L'export DNV
  18 colonnes (MRV‑01) et la position DMS (MRV‑07) restent indispensables.

### A2 — Finance : **Prévisionnel/réalisé complet**
- **Tickets en périmètre :** FIN‑01 (modèle 5 postes × 2 colonnes), FIN‑02 (export CSV).
- **Conséquence :** restaurer sur `LegFinance` le couple **forecast/actual** des 5 postes
  (CA, portuaire, **quai**, OPEX mer, opérations) + résultat + marge prév/réel + **écarts** +
  ligne **TOTAL** ; export CSV 18 colonnes.
- **Migration :** recréer les colonnes forecast/actual + `quay_cost` (sans cible V3) ; définir
  la reprise de l'historique V2 avant tout écrasement.

### A3 — Stowage/Escale : **Configurable par zone**
- **Ticket en périmètre :** STO‑05.
- **Conséquence :** `evaluate_plan` garde l'**avertissement par défaut** (gain V3) ; ajouter un
  **flag de blocage dur par zone** dans `StowageZoneSpec` (ex. zones DG, résistance de pont
  critique) ; rejet HTTP 400 uniquement sur les zones marquées « strictes ».

### A4 — Crew : **Autoriser l'embarquement hors leg** (comportement V2)
- **Ticket en périmètre :** CREW‑04 (+ CREW‑08 anti‑overlap).
- **Conséquence :** rendre `CrewAssignment.leg_id` **nullable** et permettre l'affectation
  directe à un **navire** sans leg planifié (embarquement anticipé) ; le rattachement à un leg
  reste possible (optionnel). Réintroduire l'**anti‑overlap** d'embarquement.
- **Migration :** `leg_id` nullable ; conserver/dériver le navire d'affectation.

### A5 — Cargo : **Facturation hors plateforme + nettoyer le code dormant**
- **Ticket en périmètre :** EVO‑01.
- **Conséquence :** **retirer** `client_invoice`/`invoicing` et la redirection **301** silencieuse
  de `/me/invoices` (ou la remplacer par une page explicite « facturation gérée hors
  plateforme »). Documenter la décision dans `CLAUDE.md`.

### A6 — Portail expéditeur : **Portail token riche + espace `/me`**
- **Tickets en périmètre :** CARGO‑06, CARGO‑10, CARGO‑11, CARGO‑12 (tous **GO**, périmètre complet).
- **Conséquence :** restaurer le **portail token complet** (saisie/correction de batches, dépôt
  de documents, import Excel, suivi voyage, guide + fiche navire, multilingue) **en plus** de
  l'espace client authentifié `/me`. **SEC‑02** (rate‑limit token) devient **obligatoire**.

### A7 — Droits `data_analyst` : **Restriction V3 + accès ciblé aux réglages**
- **Tickets en périmètre :** ADM‑06, FIN‑03, MRV‑06.
- **Conséquence :** **ne pas** remettre `data_analyst` dans un module `admin` global. Exposer les
  réglages **CO₂ / émissions (NOx/SOx) / MRV** via une **permission ciblée** (écran de
  paramètres accessible aux rôles `data_analyst` **et** `administrateur`), en s'appuyant sur
  l'éditeur de matrice de permissions V3 (overrides DB).

---

## Tableau de synthèse (pour mémoire)

| # | Décision | Options | Tickets bloqués | Recommandation |
|---|---|---|---|---|
| A1 | **MRV — source de vérité de la consommation** | (a) revenir aux **4 compteurs DO** (méthode V2, saisie + calcul ME/AE + ROB calculé) · (b) officialiser les **noon reports** comme source unique (V3) · (c) hybride : noon par défaut, compteurs en saisie de contrôle | MRV‑04, MRV‑05, MRV‑06 | (c) hybride — conserver l'auto noon (gain V3) **et** réintroduire compteurs + contrôle qualité pour l'audit réglementaire |
| A2 | **Finance — modèle budgétaire** | (a) restaurer le **prévisionnel/réalisé complet** (5 postes × 2 colonnes, V2) · (b) modèle **budget vs réel** simplifié (1 budget + 1 réel/poste) | FIN‑01 | (a) ou (b) selon besoin contrôle de gestion ; au minimum (b). Sans l'un des deux, pas de suivi d'écart possible |
| A3 | **Stowage / Escale — politique de blocage** | (a) « avertir sans bloquer » (V3, assumé) · (b) **blocage dur** sur zones/capacités/poids critiques (V2) · (c) configurable par zone | STO‑05 | (c) : avertissement par défaut + blocage dur paramétrable sur les zones critiques (DG, résistance pont) |
| A4 | **Crew — embarquement hors leg** | (a) autoriser l'affectation **sans leg** (V2) · (b) maintenir `leg_id` **obligatoire** (V3) | CREW‑04 | À valider avec le crewing : si des embarquements anticipés existent sans leg planifié, prévoir le cas (a) |
| A5 | **Cargo — facturation client** | (a) **activer** `client_invoice`/`invoicing` (modèle prêt) · (b) confirmer la facturation **hors plateforme** et **retirer** le code dormant | EVO‑01 | Trancher pour lever la dette : aujourd'hui `/me/invoices` redirige silencieusement (301) |
| A6 | **Portail expéditeur — cible** | (a) **portail token riche** (V2) **et** espace client `/me` · (b) **convergence vers `/me`** (auth) avec portail token minimal | CARGO‑10, CARGO‑11, CARGO‑12 | (a) : l'expéditeur sans compte (transitaire ponctuel) doit pouvoir déposer documents et packing list par token |
| A7 | **Droits `data_analyst`** | (a) **restaurer** son accès admin (V2 : dans `ADMIN_ROLES`) · (b) **acter la restriction** V3 (pas de module `admin`) | ADM‑06 | Décider selon qui gère les paramètres CO₂/émissions/MRV ; sinon ces réglages deviennent inaccessibles au profil data |

## Décisions de design V3 à **documenter** (ne pas recompter comme régressions)

Ces choix V3 sont défendables ; les inscrire dans `CLAUDE.md` pour éviter qu'un futur audit
ou une reprise les considère comme des bugs :

- Suppression « dure » remplacée par **désactivation** (`is_active`) sur les utilisateurs.
- **Congés marins** migrés du périmètre crew vers **RH** (séparation des permissions `crew` ↔ `rh`).
- **Anemos** remplace le « certificat de décarbonation par client » par un **certificat par booking** + rapport RSE annuel.
- **Facteur CO₂ versionné** (`/admin/co2`) au lieu d'un paramètre éditable libre.
- **Stowage** : politique « avertir, ne jamais bloquer » (sous réserve A3).
- **Module ERP passengers** définitivement supprimé (v3.0.0) — ne pas
  réintroduire. NB (P4) : distinct du **service passagers 2027**, intention
  commerciale assumée (page vitrine `/passagers`, `Vessel.capacity_pax`), qui
  n'est **pas** un module ERP et reste publié.
