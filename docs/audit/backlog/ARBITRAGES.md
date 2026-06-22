# Arbitrages métier — à trancher avant le Lot 1 des modules concernés

Ces décisions conditionnent le périmètre de certains tickets P0. Tant qu'elles ne sont pas
prises, les tickets « bloqués » ci‑dessous ne doivent pas démarrer.

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
- **Module passengers** définitivement supprimé (v3.0.0) — ne pas réintroduire.
