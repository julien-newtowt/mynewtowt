# Design System V2 — Kairos

**Nom de produit candidat** : `Kairos` (du grec ancien καιρός — « moment opportun », terme cardinal de la navigation à voile pour saisir le bon moment de virer ou de larguer).

> Variantes à valider commercialement : `Helm`, `Plani`, `Stella`, `Bridge`, `MarLine`.

## Direction artistique

**Positionnement** : outil de pilotage maritime moderne, dark-first, pensé pour des équipages mobiles, jeunes, multi-écran.

**Mots-clés** : précision, fluidité, calme, confiance, instantanéité.

**Influences** : Linear, Arc Browser, Figma, Notion 2026, Apple Weather.

## Tokens — Couleurs

### Palette principale (dark mode par défaut)

| Token | Hex | Rôle |
|-------|-----|------|
| `--bg-0` | `#0B0E14` | Fond app (canvas) |
| `--bg-1` | `#141925` | Cartes, panels |
| `--bg-2` | `#1E2537` | Hover, sélection, surfaces actives |
| `--bg-3` | `#2A3347` | Borders subtiles, dividers |
| `--text-0` | `#F7F9FC` | Texte principal |
| `--text-1` | `#A3ADC2` | Texte secondaire, labels |
| `--text-2` | `#6B7591` | Texte tertiaire, placeholders |
| `--accent` | `#7CFFB2` | Vert électrique — primary, succès, CTA |
| `--accent-alt` | `#8BA7FF` | Lavande — liens, info, focus rings |
| `--warn` | `#FFB547` | Ambre — alertes non-bloquantes |
| `--error` | `#FF5A6A` | Corail — erreurs |
| `--ok` | `#7CFFB2` | (alias `--accent`) |

### Gradients

| Token | Valeur | Usage |
|-------|--------|-------|
| `--gradient-ocean` | `linear-gradient(135deg, #7CFFB2 0%, #8BA7FF 100%)` | CTA principal, mockups héro |
| `--gradient-night` | `linear-gradient(180deg, #0B0E14 0%, #141925 100%)` | Fond hero / loaders |

### Light mode (bascule via `prefers-color-scheme` + toggle)

Inversion des `bg-*` / `text-*` :
- `--bg-0`: `#FAFBFC`, `--bg-1`: `#FFFFFF`, `--bg-2`: `#F0F2F5`, `--bg-3`: `#E1E5EC`.
- `--text-0`: `#0B0E14`, `--text-1`: `#3F4659`, `--text-2`: `#7B8395`.
- Accents inchangés (assez contrastés).

### Couleurs métier (sémantiques)

| Token | Usage |
|-------|-------|
| `--status-planned` | `#8BA7FF` (lavande) — leg planifié |
| `--status-inprogress` | `#FFB547` (ambre) — leg en cours |
| `--status-completed` | `#7CFFB2` (vert) — leg terminé |
| `--status-cancelled` | `#6B7591` (gris) — annulé |
| `--cargo-import` | `#8BA7FF` |
| `--cargo-export` | `#FFB547` |

## Tokens — Typographie

| Famille | Police | Usage |
|---------|--------|-------|
| `--font-display` | `Space Grotesk`, sans-serif | Titres H1-H3, métriques, leg codes |
| `--font-body` | `Inter`, sans-serif | Corps, formulaires, navigation |
| `--font-mono` | `JetBrains Mono`, monospace | Codes leg, timestamps, SOF, coordonnées |

### Échelle (modular scale 1.25 — minor third)

| Token | Px | Usage |
|-------|----|----|
| `--text-xs` | 12 | Labels, captions |
| `--text-sm` | 14 | Corps secondaire, navigation |
| `--text-base` | 16 | Corps |
| `--text-lg` | 20 | H4, sous-titres |
| `--text-xl` | 25 | H3 |
| `--text-2xl` | 31 | H2 |
| `--text-3xl` | 39 | H1, métriques héros |

### Graisses

- 400 (Regular), 500 (Medium), 600 (Semibold), 700 (Bold).
- Ne jamais mélanger plus de 3 graisses dans un même écran.

## Tokens — Espacement (4 px base)

| Token | Px |
|-------|----|
| `--space-1` | 4 |
| `--space-2` | 8 |
| `--space-3` | 12 |
| `--space-4` | 16 |
| `--space-5` | 24 |
| `--space-6` | 32 |
| `--space-8` | 48 |
| `--space-10` | 64 |

## Tokens — Radius

| Token | Px |
|-------|----|
| `--radius-sm` | 6 |
| `--radius-md` | 10 |
| `--radius-lg` | 14 |
| `--radius-xl` | 20 |
| `--radius-pill` | 999 |

## Tokens — Élévation

| Token | Valeur |
|-------|--------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.4)` |
| `--shadow-md` | `0 4px 12px rgba(0,0,0,0.5)` |
| `--shadow-lg` | `0 12px 32px rgba(0,0,0,0.6)` |
| `--shadow-glow` | `0 0 24px rgba(124,255,178,0.25)` (accent glow) |

## Composants

### Layout

- **App shell** : `grid` 2 colonnes (sidebar 240 px + main fluid). Mobile : sidebar overlay slide-from-left + bottom-nav 4 items.
- **Bento grid** sur dashboard : 12 cols × ratio variable, cards drag-to-reorder (persisté en `user_preferences`).

### Navigation

- **Sidebar collapsible** : icons-only mode (60 px) togglable.
- **Sections regroupées** :
  1. *Pilotage* : Dashboard, Planning, Tracking
  2. *Cargo* : Commercial, Cargo, Stowage, Claims
  3. *Opérations* : Escale, Onboard, Crew
  4. *Performance* : Finance, KPI, MRV
- **Command palette** `Cmd+K` : recherche universelle (legs, escales, clients, users, docs).
- **Bottom-nav mobile** : Dashboard, Planning, Onboard, Plus.

### Cards

- `.card` standard : `--bg-1`, `--radius-lg`, `--space-4` padding, border `1px solid --bg-3`.
- `.card-elevated` : ajoute `--shadow-md`.
- `.card-interactive` : hover bascule à `--bg-2` + scale(1.01) en 150 ms.

### Boutons

- `.btn-primary` : fond `--gradient-ocean`, texte `--bg-0`, hover `--shadow-glow`.
- `.btn-secondary` : fond `--bg-2`, border `--bg-3`.
- `.btn-ghost` : transparent, hover `--bg-2`.
- `.btn-danger` : texte `--error`, hover background `--error`/0.1.
- Tailles : `sm` (28), `md` (36), `lg` (44).

### Inputs

- Padding `--space-3` `--space-4`, radius `--radius-md`, border `1px solid --bg-3`.
- Focus : border `--accent-alt` + ring `0 0 0 3px rgba(139,167,255,0.2)`.
- Label flottant ou top-aligned (jamais inline).
- Error state : border `--error`, message helper texte `--error` `--text-xs`.

### Badges & status pills

- `.pill` : `--radius-pill`, `--space-1` `--space-3`, font-mono, `--text-xs` uppercase.
- Variants : `.pill-ok`, `.pill-warn`, `.pill-error`, `.pill-info`, `.pill-neutral`.

### Tableaux

- `--bg-1` background, header sticky avec `--bg-2`.
- Zebra `--bg-1` / `--bg-2`/0.5.
- Row hover `--bg-2`.
- Tri par clic header (icons up/down).
- Pagination en footer (Linear-style).
- Densité togglable (compact / cosy / comfortable).

### Toasts

- Position bottom-right desktop, top-center mobile.
- Variants : success (`--accent`), warn (`--warn`), error (`--error`), info (`--accent-alt`).
- Auto-dismiss 4 s, swipe-to-dismiss mobile.
- Lib : `htmx-notify` ou simple JS custom.

### Loading states

- **Skeleton** : pulse animation `--bg-2` ↔ `--bg-3` 1.5 s linear infinite.
- **Spinner** inline : SVG circle, stroke `--accent`, 1 s rotation.
- **Progress bar** : pour uploads, fond `--bg-2`, fill `--gradient-ocean`.
- Trigger sur HTMX requests > 200 ms.

### Empty states

- Illustration SVG (custom set, style line-art accent), titre `--text-lg`, sous-titre `--text-1`, CTA `--btn-primary`.
- Toujours offrir 1 action (créer, importer, voir tutoriel).

## Iconographie

- **Lucide** uniquement (déjà en place via CDN). Bannir tous les emojis (sauf `|flag` filter pour drapeaux pays — c'est sémantique).
- Taille standard 18 px, 24 px sur mobile.

## Animations

- **Durée** : 150 ms (micro), 250 ms (transition vue), 400 ms (hero).
- **Easing** : `cubic-bezier(0.16, 1, 0.3, 1)` (out-expo) par défaut.
- **Page transitions** : fade + slide 8 px.
- **Hover** : scale(1.01) + shadow +1 niveau.

## Accessibilité

- WCAG AA minimum, AAA cible :
  - Contraste texte/fond ≥ 4.5:1 (déjà le cas avec la palette ci-dessus).
  - Focus visible : `outline: 2px solid --accent-alt; outline-offset: 2px;` partout.
  - Touch targets ≥ 44 px sur mobile.
  - Navigation clavier complète, aria-labels exhaustifs.
  - `prefers-reduced-motion` respecté (anims désactivées).

## Stack technique

- CSS variables (déjà en place) — migration progressive `--towt-*` → tokens Kairos.
- HTMX 2.x (en place).
- Optionnel : Alpine.js pour les micro-interactions complexes (command palette, drag bento).
- Pas de framework JS lourd (React/Vue) — pas nécessaire.

## Migration depuis V1

1. Créer `app/static/css/tokens.css` avec tous les tokens ci-dessus.
2. Créer `app/static/css/kairos.css` qui consomme les tokens et redéfinit `.card`, `.btn`, `.alert`, etc.
3. Bascule via class root `<html data-theme="kairos">` — coexistence temporaire des deux DS.
4. Migrer template par template (commencer par dashboard + planning).
5. Suppression `app.css` legacy en fin de migration.
