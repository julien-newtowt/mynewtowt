# Reprise UX legacy — Audit & plan d'action

> **Date** : 2026-08-30 · **Branche** : `claude/mytowt-legacy-ux-recovery-79gjf0`
> **Demande** (Julien) : retrouver l'ancien UX adapté aux nouvelles
> fonctionnalités — (1) l'écran de gestion des escales, (2) le système de
> gestion des documents et des événements au cours d'une escale, (3) le
> design et le format des pages — **sans perdre les nouvelles
> fonctionnalités**, pour ne pas dérouter les utilisateurs historiques.
> Déploiement en plusieurs phases.

---

## TL;DR

- **Il n'existe aucune archive de code de l'ancienne application** dans ce
  dépôt (historique git tronqué au 2026-06-30, déjà en V3). L'« ancien
  site » se reconstitue depuis trois corpus documentaires de fiabilité
  inégale (§1).
- **La parité fonctionnelle V2→V3 est déjà soldée** : les 12 ruptures P0 et
  les lots P1 ont été repris et sont verrouillés par
  `tests/regression/test_v2_parity.py` (`_PENDING` vide). Ce qui reste à
  reprendre n'est **pas du fonctionnel, c'est de l'ergonomie** : le
  « cockpit » que les utilisateurs avaient, contre l'empilement d'écrans
  actuel.
- Le plan (§4) tient en **3 phases** : cockpit escale → journal
  documents/événements d'escale → format des pages & design system. Chaque
  phase est autonome, sans migration de données, réversible.

---

## 1. Sources et méthode — ce qu'on appelle « l'ancien site »

Trois corpus, à ne **jamais** confondre (l'erreur classique est de prendre
le 2ᵉ pour le 1ᵉʳ) :

| Corpus | Nature | Fiabilité |
|---|---|---|
| `docs/audit/AUDIT_V2_V3_RAPPORT_ECARTS_ET_PLAN.md` (2026-06-22) + `docs/audit/specs/SPEC-*-reprise-P0.md` + `tests/regression/test_v2_parity.py` | Audit rétrospectif du **code réel** de l'archive `mytowt-main` (mai 2025) | **FAITS** — c'est le témoignage du site que les utilisateurs ont connu |
| `docs/legacy/ux/*`, `docs/legacy/v2/*`, `docs/legacy/captain/*` | Specs **prospectives** d'une refonte « Kairos » rédigées pendant le développement | **VISION** — jamais livrée telle quelle en V2 (preuve : la spec propose d'ajouter la direction Import/Export… que l'audit rétrospectif liste comme un *gain de la V3*) |
| Code V3 actuel (`app/`) | L'existant | FAITS |

Portrait du **V2 réel** (source : audit rétrospectif) : un ERP staff pur,
écrans **« cockpit » denses** (tout sous la main, une page par métier),
police Poppins, styles/JS inline — réécrit en V3 sur la charte « Nouvelle
Étoile » (Manrope, CSP strict, décision assumée : les couleurs de marque
sont restées byte-identiques, la typo a changé). **L'habitude utilisateur à
préserver n'est donc pas une esthétique, c'est une organisation du
travail** : l'agent d'escale pilotait toute son escale depuis un seul
écran ; le capitaine tenait ses documents et son SOF depuis un seul écran.

## 2. Audit

### 2.1 Ce que faisait l'écran escale V2 (habitudes réelles)

Toutes ces capacités ont déjà été **restaurées en V3** (tests
`test_v2_escale_*`), mais dispersées ou banalisées dans la page :

- Pilotage du statut portuaire en 3 temps (pilote arrivée → à quai →
  pilote départ), pose ATA/ATD → cascade finance + notifications.
- Édition/suppression des opérations et shifts, **saisie rétroactive** des
  heures réelles (consolidation de fin d'escale).
- Cadence dockers (pal/h planifié vs réel, delta %), `intervenant`, durées.
- Couplage embarquement/débarquement → équipage + billets + PAF auto (FR).
- Saisie horaire systématique avec sélecteur de fuseau (UTC/Paris/port).

### 2.2 Ce que faisait le bord V2 (documents & événements d'escale)

Restauré aussi (tests `test_v2_onboard_*`, `test_v2_cargo_docs_*`) :
SOF éditable tant que non signé, documents cargo **guidés par type**
(13 types V2 → 12 repris, `data_json`, mentions légales pré-remplies,
signataire choisi parmi l'équipage embarqué), pièces jointes catégorisées
avec zone « documents de l'agent d'escale », clôture avec checklist + PDF +
réouverture, messagerie de bord avec mentions et messages système.

Écart résiduel notable : la messagerie V2 avait un **fil à l'échelle du
navire** (continuité entre traversées) ; la V3 est scopée au leg.

### 2.3 Ce que la vision « Kairos legacy » apporte encore

De `docs/legacy/ux/` + `docs/design/01-design-handoff.md` (qui a déjà
arbitré conserve/ajuste/abandonne), reste **non livré** à ce jour :

| Pattern legacy | Statut code actuel |
|---|---|
| Split visuel Import / Export de l'escale | Perdu — l'ex-template `staff/escale/detail.html` (2 colonnes I/E) est orphelin ; l'écran actuel mélange tout avec un badge `direction` |
| Command palette `Cmd+K` | Absent |
| Skeleton loaders / loading states | Absents |
| Empty states illustrés avec CTA | Composant pauvre, usage incohérent (`text-muted` vs `.empty-state`) |
| Densité de tableau togglable | Une seule densité |
| Focus rings systématiques | Quasi absents hors `.btn`/`a` |
| Toggle dark/light persisté | Bloc `[data-theme="dark"]` = 6 alias jamais câblés, aucun toggle |
| Dashboard bento réordonnable | Absent (le dashboard actuel est fixe) |

À l'inverse, **déjà absorbé par la V3** (ne pas recompter) : sidebar
regroupée par domaines (7 groupes repliables persistés), onboard multi-
espaces, kanban tickets SLA, chatbot, tokens W3C, horloge sidebar, cloche
notifications.

### 2.4 Frictions UX de l'existant (audit code du 2026-08-30)

Écran `/escale` (`app/templates/staff/escale/index.html`, 351 l.) :

1. **Monolithe** : 7+ sections empilées sans navigation interne ; scroll
   long dès qu'une escale est active ; formulaires permanents en bas de
   card (pas de saisie rapide).
2. **Aucune interaction HTMX** : chaque action (démarrer/terminer une
   opération, progresser un shift…) recharge la page entière
   (`RedirectResponse 303`) → perte de position et de contexte, des
   dizaines de fois par escale. C'est l'écart le plus douloureux vs le
   « cockpit » V2.
3. **Import/Export mélangés** dans une table unique (badge seul).
4. **Deux écrans pour une même escale, zéro lien croisé** : `/escale`
   (opérations, dockers, commercial) et `/captain` (SOF, documents,
   clôture) s'ignorent ; deux PDF « SOF » de sources différentes peuvent
   diverger sans qu'aucune UI ne les rapproche.
5. **Deux verrous indépendants non synchronisés** : lock d'escale
   (`escale_locked_at`) vs clôture de voyage (3 étapes) — aucune alerte
   croisée.
6. PAF réglementaire noyé dans la table générique ; pas d'indicateur de
   retard sur les opérations ; marins non cliquables ; kanban tickets sans
   filtre par leg (le service `list_for_kanban(leg_id)` le permet déjà) ;
   lien mort `/onboard/escale` → `/escale/{leg.id}` (404) ; template
   orphelin `staff/escale/detail.html` ; `closure_notes` en texte concaténé.

Design system (`kairos.css`, 1923 l.) : `.app-shell` legacy = code mort ;
deux systèmes de cartes KPI concurrents (`.kpi-card` vs `.stat-card`) ;
double système de tokens ERP (`tokens.css`) / vitrine
(`colors_and_type.css`) volontairement disjoints ; finitions UX-P2 de
l'audit V2↔V3 toujours ouvertes (pages d'erreur, empty-states, graisses
Manrope 300/800, CSS mort `.tz-*`).

## 3. Cible UX — principes

1. **Le cockpit d'abord.** L'agent d'escale et le capitaine retrouvent
   chacun UN écran où tout se fait — comme en V2 — mais organisé :
   sous-navigation interne collante (ancres), sections dépliées par
   défaut, formulaires en modal, fragments HTMX pour toute action
   répétitive (zéro rechargement de page pendant une escale active).
2. **Import / Export redevient structurant** (réintroduction du pattern
   legacy plébiscité par le terrain) : deux volets colorés
   (`--cargo-import` / `--cargo-export`, tokens déjà en place) + volet
   commun, sur les opérations ET les shifts dockers.
3. **Une escale = un dossier.** Les documents et événements produits
   pendant l'escale (SOF, documents cargo, pièces jointes agent, tickets,
   verrous) deviennent visibles depuis `/escale` en lecture, avec liens
   d'action vers `/captain` — et réciproquement. Le « journal d'escale »
   (timeline unifiée) rapproche ce que les deux écrans savent.
4. **Zéro perte fonctionnelle** : la matrice §6 est contractuelle ; la
   parité est déjà verrouillée par `test_v2_parity.py`, qui doit rester
   vert à chaque phase.
5. **La charte ne bouge pas** : Nouvelle Étoile (teal/vert/cuivre/sable,
   Manrope), CSP strict, HTMX — on reprend des *patterns* legacy, pas la
   palette dark abandonnée ni Poppins.

## 4. Plan d'action phasé

### Phase 1 — Cockpit escale (`/escale`) · risque 🟡 Modéré · ✅ **LIVRÉE le 2026-08-30**

Refonte de `staff/escale/index.html` + `escale_router.py`, **sans
changement de modèle de données**. Suite complète : 2905 tests verts.

- Sous-navigation collante d'ancres : Statut · Opérations · Dockers ·
  Équipage · Documents & SOF · Tickets · Commercial. ✅
- **Split Import / Export / Commun** sur les opérations (regroupement par
  `direction`, déjà en base), code couleur tokens existants ; badges
  directionnels sur les shifts. ✅
- Formulaires « Nouvelle opération » / « Nouveau shift » **repliés par
  défaut** (`<details>` natif). *Écart assumé vs le plan initial (« en
  modal ») : la CSP interdit les handlers inline dont dépendait
  `loadModal`, et le disclosure natif fait le même travail sans JS.* ✅
- Actions rapides **sans rechargement** : start/end opération, pointage
  palettes (nouveau mini-formulaire), pose ATA/ATD, créations — POST HTMX
  → `204` + `HX-Trigger` (`toast` + `escaleRefresh`), le conteneur
  `#escale-sections` se re-remplit par `hx-get` + `hx-select` sur la page
  elle-même (aucun refactor du contexte, repli 303 sans JS intact). ✅
- KPI d'escale (en cours / en retard / palettes / cadence moyenne),
  indicateur **retard** (`_cockpit_late_op_ids`), ligne **PAF** mise en
  évidence, marins cliquables vers `/crew/members/{id}`. ✅
- Carte **Documents & SOF** (compteurs SOF/docs/PJ, 3 derniers SOF, les
  deux PDF SOF côte à côte, **alerte croisée** clôture engagée ↔ escale
  non verrouillée) + lien « Espace bord ». ✅
- Encart tickets de l'escale + kanban filtré `leg_id` (routeur + bandeau),
  pré-sélection du leg sur `/tickets/new` ; lien réciproque « Escale
  (terre) » depuis `/captain`. ✅
- Nettoyage : template orphelin `staff/escale/detail.html` supprimé, lien
  mort `staff/onboard/escale.html` corrigé. ✅
- Tests ajoutés : `test_escale_cockpit.py` (5), `test_tickets_leg_filter.py`
  (2). Limite connue : une erreur 400 sous HTMX (escale verrouillée entre
  deux gestes) n'affiche pas encore de toast d'erreur — repli : recharger.

### Phase 2 — Journal documents & événements d'escale · risque 🟡 Modéré

- **Timeline unifiée** de l'escale (lecture) : événements SOF + opérations
  réelles + documents cargo signés + pièces jointes agent + poses ATA/ATD
  + verrous/clôture, ordonnée chronologiquement — visible depuis `/escale`
  (section Documents & SOF) et `/captain` (même fragment).
- **Rapprochement des deux SOF** : encart de contrôle signalant les écarts
  entre le SOF « escale » (opérations/dockers) et le SOF « commandant »
  (`SofEvent`) avant génération PDF.
- **Alerte croisée verrous** : clôture soumise avec escale déverrouillée
  (et inverse) → bandeau d'avertissement, sans blocage (les deux
  mécanismes restent indépendants — pas de changement de règle métier).
- Reliquats ONB-08 dont le coût est faible : pièces jointes multiples par
  document cargo, contexte/lieu sur les claims. (Export Word des docs
  cargo : déjà couvert partiellement par `docx_generator` — à évaluer.)

### Phase 3 — Format des pages & design system · risque 🟢 Faible

- **Loading states** : classe `.skeleton` + indicateur HTMX global.
- **Empty states** normalisés (icône Lucide + titre + CTA) et généralisés.
- **Focus rings systématiques** (`:focus-visible`) sur tabs, boutons
  icône, nav — dette accessibilité.
- **Densité de tableau** togglable (compacte par défaut, confortable).
- Harmonisation `.kpi-card` / `.stat-card` (un seul composant).
- Finitions UX-P2 héritées de l'audit V2↔V3 : pages d'erreur enrichies,
  graisses Manrope 300/800, purge CSS mort (`.app-shell` legacy).

### Backlog optionnel (hors phases — à arbitrer séparément)

| Sujet | Pourquoi pas maintenant |
|---|---|
| Command palette `Cmd+K` | Valeur réelle mais nouveau composant JS global (CSP, i18n, permissions) — chantier propre |
| Dark mode câblé + toggle persisté | Le bloc `[data-theme]` ne couvre que 6 alias ; câbler = repasser sur ~1900 l. de kairos.css. Gros chantier cosmétique (P2 au sens des priorités opérationnelles) |
| Messagerie de bord scope navire (habitude V2) | Changement de modèle de données — à spécifier avec les Opérations |
| Dashboard bento réordonnable | P2, nécessite `user_preferences` |
| Unification tokens ERP/vitrine | Séparation actuellement volontaire (collisions `.btn`/`.card`) — ne toucher qu'avec un plan de non-régression vitrine |

## 5. Garde-fous — ce que la reprise ne fait PAS

- Pas de retour à Poppins ni aux styles/JS inline (CSP strict conservé).
- Pas de reprise de la palette dark V2 (`#7CFFB2`/`#8BA7FF`) ni d'Inter —
  patterns oui, palette non (déjà arbitré dans `01-design-handoff.md`).
- Pas de réouverture des sujets MRV re-décommissionnés au lot 14
  (CRUD `mrv_events`, CSV DNV) : décision produit assumée.
- Aucun changement des invariants : signature IMO immuable, lock escale,
  dérivation *shipped on board*, machine à états tickets, `activity.record`
  sur toute écriture, `require_permission` sur toute route.
- Aucune migration de données dans les 3 phases.

## 6. Matrice de non-perte fonctionnelle (contractuelle)

Toute PR de phase doit vérifier — en plus de `pytest -q` et de
`test_v2_parity.py` — que ces capacités restent accessibles depuis l'UI :

**Escale** : filtre navire→année→leg (cookie) ; bandeau leg (POL/POD,
ETD/ETA/ATA/ATD, badges à quai/verrou) ; timeline flux opérationnel 5
étapes ; KPI navigation GPS ; pose ATA/ATD idempotente (rollup finance,
notifs EOSP/SOSP) ; CRUD opérations (type→action strict, direction,
intervenant, coûts, heures rétroactives, start/end 1 clic) ; vue activités
parallèles ; sync opération→SOF ; couplage crew + billets + alertes + PAF
auto ; CRUD dockers (cale stowage, cible/réalisé, cadence delta %, coût) ;
occupation par cale + PDF plan FR/EN ; lock/unlock (M/S) bloquant ;
commandes + packing lists du leg ; PDF SOF escale ; saisie fuseau
(`towt-tz.js`) partout.

**Bord / documents** : SOF 24 types, édition/suppression si non signé
(409 sinon), signature IMO SHA-256 ; ETA shift motif obligatoire →
cascade + notifs ; messagerie @mentions, messages système intouchables ;
docs cargo guidés/libres, PDF par type, PJ scannée, signature/verrou ;
PJ leg 8 catégories dont zone agent d'escale ; clôture 3 étapes
(M/M/S) + checklist + PDF + réouverture ; signature BL unitaire/groupée
explicite ; exports SOF PDF/XLSX.

**Tickets** : kanban 4 colonnes, machine à états stricte, SLA figé à la
création, escalade idempotente, commentaires interne/public, assignation.

## 7. Critères d'acceptation & vérifications par phase

1. `pytest -q` vert, `test_v2_parity.py` vert, aucun test skippé en plus.
2. Tests d'intégration ajoutés pour : fragments HTMX (contenu partiel +
   `HX-Request`), filtre tickets par leg, timeline journal d'escale.
3. Aucune route supprimée ni renommée (compat bookmarks/formulaires) —
   uniquement des ajouts de fragments `GET`.
4. CSP inchangée ; zéro `<script>` inline ; Lucide réinitialisé après swap.
5. Docs à jour : ce fichier (statut des phases), `CLAUDE.md` (ligne
   module Escale) à la livraison de chaque phase.

## 8. Risques & compatibilité

- **Branche** : `claude/mytowt-legacy-ux-recovery-79gjf0`, partie de
  `main@391899f`. Périmètre Phase 1 = templates escale + `escale_router`
  (fragments additifs) + `tickets_router` (paramètre `leg_id`) + CSS
  additif → recouvrement faible avec les chantiers récents (caisse,
  commercial). Risque de conflit : 🟢 faible.
- **Risque utilisateur** : la refonte réorganise l'écran que les
  Opérations utilisent quotidiennement — prévoir une revue avec un agent
  d'escale sur la maquette AVANT d'implémenter la phase 2 (le navire à
  quai sous ~2 semaines est l'occasion de tester la phase 1 à bord).
- **Hypothèse à valider** (je la signale car rien ne la documente) : le
  split Import/Export vient de la *vision* legacy, pas du V2 vécu — le
  design handoff le note « demande terrain forte », je le reprends à ce
  titre. Si le terrain préfère la table unique, le split est une option
  d'affichage à conserver togglable.

---

*Rapports d'audit détaillés (routes, composants, sources) produits en
session le 2026-08-30 ; synthèses conservées avec ce document. Maquettes :
voir le canvas de design « Reprise UX escale » partagé avec ce plan.*
