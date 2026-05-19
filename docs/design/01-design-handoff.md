# Design Handoff — Ajustement & complétude

> Étape 2 — `/design-handoff`. Document d'ajustement des propositions de
> design héritées de la V2 (cf. `Versions TOWT/docs/ux/`) pour répondre
> aux attentes utilisateurs et prospects identifiées dans
> `docs/personas/01-personas.md`.

## 1. Audit des propositions héritées

### 1.1 Ce qu'on conserve

| Élément | Origine | Raison |
|---------|---------|--------|
| Design tokens Kairos | `design-system-v2.md` | Solide, dark-first, conforme WCAG AA |
| Architecture sidebar regroupée | `design-system-v2.md` | Regroupement par domaine reste pertinent |
| Bento dashboard | `mockups.md` §1 | Adapté au pilotage flotte |
| Onboard 4 espaces | `onboard-v2-spec.md` | Validation commandant favorable |
| Split Import / Export escale | `mockups.md` §3 | Demande terrain forte |
| Chatbot widget | `mockups.md` §6 | Économise les clics |
| Command palette Cmd+K | `mockups.md` §5 | Productivité utilisateurs avancés |
| Ticketing Kanban | `mockups.md` §7 | Standard métier |

### 1.2 Ce qu'on ajuste

| Décision V2 | Ajustement V3 | Justification |
|-------------|---------------|---------------|
| Manrope (UI) + DM Serif Display | **Conservé** + ajout `JetBrains Mono` pour codes | Lisibilité maritime (codes leg, MMSI, IMO) |
| Police Inter (mentionnée dans `tokens`) | **Remplacée par Manrope** | Aligné sur charte NEWTOWT historique |
| Sidebar 240 px (collapsible 60) | Sidebar **256 px** (collapsible 72) | Plus de respiration, touch-friendly |
| Toggle dark/light implicite via `prefers-color-scheme` | **Toggle explicite + persisté par user** | Conditions mer/quai variables |
| Couleurs accentuées `#7CFFB2`, `#8BA7FF` | **Réharmonisées sur charte NEWTOWT** (`#87BD29` vert + `#0D5966` teal) | Cohérence marque historique |

### 1.3 Ce qu'on ajoute (NOUVEAU)

| Élément | Cible utilisateur | Description courte |
|---------|------------------|-------------------|
| **Layout client public** | Prospects, clients | Header simplifié, footer marketing, palette plus claire |
| **Booking wizard 4 étapes** | Clients B2B | Itinéraire → marchandise → confirmation → paiement |
| **Carte de calendrier de capacité** | Prospects | Carte interactive monde + table calendrier |
| **Dashboard client** | Clients | Vue mensuelle de leurs commandes, factures, CO₂ |
| **PDF templates Kairos** | Tous | BL, packing list, facture, certificat CO₂ unifiés |
| **Empty states illustrés** | Tous | SVG line-art (vagues + voile) en `--accent` |
| **Animations de transition** | Tous | Glissement carte 250ms easing out-expo |
| **Mode haute lisibilité** | Marins (pont) | Texte +20 %, contraste AAA, large touch |

## 2. Système de palette consolidé

### 2.1 Palette unifiée NEWTOWT + Kairos

```css
:root {
  /* Charte historique NEWTOWT (anchor) */
  --brand-teal: #0D5966;       /* primary surface, navbar interne */
  --brand-vert: #87BD29;       /* primary action, success */
  --brand-cuivre: #B47148;     /* accent secondaire */
  --brand-sable: #EFE6D6;      /* surface light */
  --brand-marine: #0A2540;     /* texte highlight clair */

  /* Surfaces sombres (Kairos dark-first) */
  --bg-0: #08121A;             /* canvas */
  --bg-1: #0F1E27;             /* cards */
  --bg-2: #1A2D38;             /* hover */
  --bg-3: #28404E;             /* borders */

  /* Textes */
  --text-0: #F4F7F9;
  --text-1: #B8C5CE;
  --text-2: #7C8B95;

  /* Actions & sémantique */
  --accent: var(--brand-vert);
  --accent-hover: #9AD13B;
  --accent-alt: #4FA6B5;       /* dérivé teal pour focus rings */
  --warn: #E89132;
  --error: #E14B5A;
  --ok: var(--brand-vert);

  /* Status leg */
  --status-planned: #4FA6B5;
  --status-inprogress: #E89132;
  --status-completed: var(--brand-vert);
  --status-cancelled: #7C8B95;

  /* Cargo direction */
  --cargo-import: #4FA6B5;
  --cargo-export: #E89132;
}
```

### 2.2 Tokens W3C (extrait `design/tokens.json`)

Voir fichier `app/static/css/tokens.css` pour l'expression complète des
tokens et leur publication runtime à toutes les pages.

## 3. Composants ajoutés / spécifiés

### 3.1 `BookingCard`

Card cliquable pour un leg réservable.

```
┌──────────────────────────────────────────────────┐
│ 1A FR→US  · Anemos · S22                         │
│ ──────────────────────────────────────────────── │
│ Fécamp 🇫🇷 ──[8j]──► New York 🇺🇸                  │
│ ETD 2026-06-04 · ETA 2026-06-12                  │
│                                                  │
│ Capacité restante : ████████░░░░░░ 612 / 850 EPAL │
│ Tarif indicatif : 38 EUR / palette                │
│                                                  │
│            [Voir détails]  [Réserver →]          │
└──────────────────────────────────────────────────┘
```

### 3.2 `CapacityGauge`

Indicateur de remplissage d'un leg (utilisé sur dashboard et booking).

- 0–60 % : `--brand-vert` (calme)
- 60–85 % : `--warn` (à booker rapidement)
- 85–100 % : `--error` + label "Complet" si 100 %

### 3.3 `CO2Badge`

```
┌──────────────────┐
│ -2.7 t CO₂       │
│ vs conventionnel │
└──────────────────┘
```

Toujours en `--brand-vert` sur fond `--bg-1`, font-mono pour le chiffre.

### 3.4 `RouteMap`

Carte interactive MapLibre + tracé orthodromique animé entre POL et POD.
Affiche les escales intermédiaires (waypoints) si le leg en a. Utilise
`MapLibre.GL` + tiles MapTiler/OSM (CSP `*.maptiler.com`).

### 3.5 `StatusBar`

Bandeau horizontal des 5 statuts d'un voyage (sailing) :

```
[reserved]─[loaded]─[at-sea]─[discharged]─[delivered]
   ●          ●         ○         ○            ○
```

État courant en `--accent`, étapes franchies opaques, étapes à venir
semi-transparentes.

## 4. Maquettes textuelles des nouvelles pages

### 4.1 Page d'accueil publique `/`

```
┌────────────────────────────────────────────────────────────────┐
│  [NEWTOWT logo]                          [Connexion] [Réserver]│
├────────────────────────────────────────────────────────────────┤
│                                                                │
│           ┌─────────────────────────────────────┐              │
│           │   Trouvez votre prochaine route     │              │
│           │ ┌────────┐ ┌────────┐ ┌──────────┐  │              │
│           │ │ Départ │ │Arrivée │ │  Dates   │  │              │
│           │ └────────┘ └────────┘ └──────────┘  │              │
│           │              [Rechercher →]          │              │
│           └─────────────────────────────────────┘              │
│                                                                │
│   ┌─── Pourquoi NEWTOWT ──────────────────────────────────┐   │
│   │  1. Décarboné -90% CO₂                                │   │
│   │  2. Capacités prévisibles                             │   │
│   │  3. Suivi de bout en bout                             │   │
│   └────────────────────────────────────────────────────────┘   │
│                                                                │
│   [Carte des routes actuelles + animation navires]            │
│                                                                │
│   [Témoignages clients · 3 cards]                              │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Page recherche `/routes`

```
┌────────────────────────────────────────────────────────────────┐
│ Filtres : POL · POD · ETD entre A et B · type marchandise      │
├────────────────────────────────────────────────────────────────┤
│ ┌─ BookingCard 1A FR→US ────────────────────────┐              │
│ ├─ BookingCard 2B FR→BR ────────────────────────┤              │
│ ├─ BookingCard 1C FR→US ────────────────────────┤              │
│ └─ ... (pagination)                              ┘              │
└────────────────────────────────────────────────────────────────┘
```

### 4.3 Booking wizard `/booking/new`

4 étapes, progression visible en haut :

```
●━━━━━○━━━━━○━━━━━○
 Trajet  Cargo  Confirm  Paiement
```

Chaque étape est sa propre URL pour bookmarking :

- `/booking/new/step-1-route`
- `/booking/new/step-2-cargo`
- `/booking/new/step-3-confirm`
- `/booking/new/step-4-pay`

### 4.4 Dashboard client `/me`

```
┌─────────────────────────────────────────────────────────────────┐
│  Bonjour, [Société]                            [Nouvelle résa]  │
├─────────────────────────────────────────────────────────────────┤
│  [CO₂ évité ce mois : -2.4 t]   [3 expéd. en cours]            │
│                                                                 │
│  Mes réservations                                               │
│  ─────────────────────────────────────────                      │
│  ● Réf  · ROUTE · ETD · Statut · Action                         │
│  ● BK-2026-0042 · 1A FR-US · 04/06 · Confirmée · [Détails]      │
│  ● BK-2026-0041 · 1C FR-US · 02/05 · Embarquée · [Suivre]       │
│  ● BK-2026-0033 · 2B FR-BR · 12/04 · Livrée · [BL][Facture]     │
│                                                                 │
│  Mes documents                                                  │
│  ─────────────────────────────────────────                      │
│  [BL] [Packing List] [Facture] [Certificat CO₂]                 │
└─────────────────────────────────────────────────────────────────┘
```

## 5. Accessibilité — Standards à appliquer

- **WCAG 2.2 AA** sur l'ensemble. Cible AAA pour le pont (mode haute
  lisibilité).
- **Focus visible** : `outline: 2px solid var(--accent-alt); outline-offset: 2px;`.
- **Touch targets** : minimum 44×44 px (recommandé 48 px sur mobile).
- **`prefers-reduced-motion`** : `transition: none` global si actif.
- **`prefers-contrast: more`** : durcissement contraste textes ↔ fonds.
- **Lecteurs d'écran** : `aria-label`, `aria-live="polite"` pour les
  toasts, `aria-current` sur la navigation active.
- **Skip-link** "Aller au contenu" en haut de chaque page.

## 6. Internationalisation

Langues supportées dès le lancement public :

- 🇫🇷 Français (référence)
- 🇬🇧 Anglais (toutes interfaces)
- 🇪🇸 Espagnol
- 🇵🇹 Portugais (Brésil) — clients sud-américains
- 🇻🇳 Vietnamien — équipages partenaires

Mécanique : Babel + Jinja2 `{% trans %}`. Clés stockées dans
`app/i18n/locale/{lang}/LC_MESSAGES/messages.po`.

Mode RTL non prioritaire (V3.2).

## 7. Compatibilité navigateurs

| Navigateur | Version min | Notes |
|------------|-------------|-------|
| Chrome | 120 | référence |
| Firefox | 120 | référence |
| Safari | 17 | iOS pont |
| Edge | 120 | bureau O365 |
| Mobile Safari | iOS 17 | tablettes pont |
| Chrome Android | 120 | mobiles dockers |

Pas de support IE / Edge legacy.

## 8. Performance budget

| Métrique | Budget | Outil de mesure |
|----------|--------|----------------|
| LCP | < 1,5 s sur 4G | Lighthouse CI |
| FID / INP | < 100 ms | Field RUM |
| CLS | < 0,05 | Lighthouse CI |
| TTI | < 2 s | Lighthouse CI |
| Payload page initiale | < 200 KB compressé | webpack-bundle-analyzer |
| Requêtes HTMX | < 300 ms p95 | Sentry perf |

## 9. Livrables design attendus en parallèle

| Livrable | Format | Owner | Statut |
|----------|--------|-------|--------|
| Tokens W3C | JSON | Design Lead | ✅ dans `app/static/css/tokens.css` |
| Maquettes Figma desktop | Figma | Designer UX | ⏳ branche `design/v3-screens` |
| Maquettes Figma mobile | Figma | Designer UX | ⏳ branche `design/v3-screens` |
| Library de composants | Figma | Designer UX | ⏳ |
| Iconographie (Lucide) | SVG | Designer UX | ✅ via CDN |
| Illustrations empty-states | SVG | Designer UX | ⏳ |
| PDF templates | HTML + WeasyPrint | Dev | ✅ `app/templates/pdf/` |

## 10. Critères d'acceptation du design

- [x] Tokens publiés et utilisés dans `app/static/css/tokens.css`.
- [x] Composant `BookingCard` implémenté + testé en Storybook simulé.
- [x] Mode dark/light togglable et persisté.
- [x] Page d'accueil publique livrée (`/`).
- [x] Wizard de réservation en 4 étapes navigables.
- [x] Dashboard client minimal opérationnel (`/me`).
- [x] Skip-link et focus visibles.
- [x] Lighthouse > 90 sur les 4 pages publiques.
