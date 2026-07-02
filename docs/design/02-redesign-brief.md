# Brief Refonte Design — Plateforme NEWTOWT (mynewtowt)

> **Usage** : copier ce document tel quel comme prompt initial à Claude
> (ou autre outil de design) pour piloter une refonte design de la
> plateforme. Auto-portant — toute la matière nécessaire est dedans.

---

## 1. Mission & contexte produit

**NEWTOWT** (TransOceanic Wind Transport) est le pionnier français du
**transport maritime décarboné à la voile** depuis 2011. La compagnie
opère une flotte de voiliers-cargo entre l'Europe (Le Havre, Fécamp),
les Amériques (New York, Boston, São Sebastião) et les Açores
(Ponta Delgada).

`mynewtowt` est sa plateforme unifiée qui réunit **3 univers** dans une
seule application web :

- **ERP interne** — outil opérationnel des collaborateurs (planning de
  flotte, commercial, escale, cargo, équipage, finance, KPI, MRV
  réglementaire, claims, dashboard commandant à bord).
- **Plateforme client public** — recherche de routes, réservation de
  cale, compte client B2B (MFA + Passkey), factures, Labels Anemos
  (bilan carbone évité).
- **Portail expéditeur par token** (`/p/{token}`) — packing list,
  messagerie sécurisée, documents, suivi.

**Identité** : NEWTOWT est sérieux (B2B, réglementaire MRV/IMO),
écologique (raison d'être : décarboner le shipping), maritime (codes,
horaires UTC, météo), pédagogue (montrer le CO₂ évité). **Pas startup
gadget**, pas non plus banque corporate froide.

---

## 2. Charte graphique « Nouvelle Étoile » — source de vérité

### 2.1 Palette de couleurs

Source : `docs/design/newtowt-design-tokens.json` (format W3C Design
Tokens). Ratio 60/20/10/10 :

| Couleur | Hex | Variable CSS | Rôle | Ratio |
|---|---|---|---|---|
| **Teal NEWTOWT** | `#0D5966` | `--teal` | Lettrage logo, titres, structures, liens, fond premium | 60 % |
| Teal dark | `#093F48` | `--teal-dark` | Fonds premium intenses | — |
| Teal light | `#1A7C8C` | `--teal-light` | Hover, états actifs | — |
| **Vert NEWTOWT** | `#87BD29` | `--vert` | Accent secondaire, succès, baseline, soulignements | 20 % |
| Vert light | `#BEDB92` | `--vert-light` | Chiffres clés sur fond foncé | — |
| **Cuivre NEWTOWT** | `#B47148` | `--cuivre` | Accent transition, préfixe NEW-, filets, warnings | 10 % |
| Cuivre light | `#D49975` | `--cuivre-light` | Hover sur fond foncé | — |
| **Sable NEWTOWT** | `#EFE6D6` | `--sable` | Fond éditorial, encadrés, citations | 10 % |
| Sable light | `#F8F2E6` | `--sable-light` | Variante fond page | — |
| Bleu marine | `#0051A0` | `--bleu-marine` | Branches Est-Ouest rose des vents (signalétique stricte logo) | — |
| Bleu horizon | `#9EC6D2` | `--bleu-horizon` | Textes secondaires sur fond foncé | — |
| Anthracite | `#2A2A2A` | `--text` | Texte courant | — |
| Gris | `#6E6E6E` | `--text-muted` | Légendes | — |
| Gris clair | `#D9D9D9` | `--border` | Filets, séparateurs | — |
| Blanc cassé | `#FAF7F2` | `--bg` | Fond de page warm-neutral | — |
| Nuit | `#0B2A33` | `--bg-premium` | Fond supports premium | — |
| Danger | `#C44536` | `--danger` | Erreurs |

**Sémantique** : success = vert, warning = cuivre, danger = `#C44536`,
info = bleu horizon.

### 2.2 Typographie

| Famille | Web import | Usage |
|---|---|---|
| **Manrope** | Google Fonts (woff2) | UI + corps de texte (titres + body en sans-serif unique) |
| **DM Serif Display** | Google Fonts | Accents éditoriaux, citations, mises en exergue (sparingly) |
| **JetBrains Mono** | Google Fonts | Codes leg (`1CFRBR6`), MMSI, IMO, LOCODE, hashes, heures UTC |

Poids : 300 / 400 / 500 / 600 / 700 / 800.

Échelle responsive :
- `xs` 12 px (légendes), `sm` 14 px, `base` 16 px, `md` 18 px,
  `lg` 24 px (h3), `xl` 32 px (h2), `2xl` 44 px (h1),
  `3xl` `clamp(3rem, 7vw, 5rem)` (hero).

Line-height : tight 1.15 / snug 1.35 / normal 1.6 / loose 1.8.

### 2.3 Espacements & rayons

Échelle 4 px : `--space-1` = 4 px → `--space-12` = 48 px.
Rayons : `--radius-sm` 4 px, `--radius-md` 8 px, `--radius-lg` 16 px,
`--radius-full` 9999.

### 2.4 Logos disponibles

`/static/img/` : `logo_NEWTOWT_web.png` (clair), `_web_dark.png`,
`_web_white.png`, `_email.png`. Toujours respecter le préfixe **NEW**
en cuivre `#B47148` à gauche du wordmark teal.

### 2.5 Principes visuels NEWTOWT

- **Maritime** : rose des vents en élément graphique (déjà dans le
  logo), nuancier qui évoque mer + horizon + transition.
- **Décarboné** : le vert NEWTOWT signale les économies d'émissions,
  pas un éco-blanchiment générique.
- **Lisibilité réglementaire** : codes leg / heures UTC / dates MRV
  doivent être *mono-spaced* et copy-pasteables (`user-select: all`).
- **Pas d'effets gratuits** : pas de gradient pour faire joli, pas
  d'ombres exagérées. Le sérieux B2B prime.
- **Sable comme respiration** : utilisé pour les encadrés calmes
  (alertes douces, notes commandant), pas le fond principal.

---

## 3. Architecture de l'application

### 3.1 Trois audiences, trois layouts

1. **`/` public** (sans login) — landing, recherche routes, page route
   détail, page flotte, méthodologie Label Anemos, mentions légales.
   Public B2B prospects. Layout simple, header + footer marketing,
   palette plus claire que le staff.

2. **`/me/...` client** (cookie session client, 30 j) — dashboard,
   wizard de réservation 3 étapes, liste bookings, détail booking,
   factures, Labels Anemos, compte (MFA TOTP + Passkeys WebAuthn).
   Cible : FF / shippers B2B. i18n 5 langues (FR/EN/ES/PT-BR/VI).

3. **`/dashboard /planning /escale /captain /admin/...` staff** (cookie
   8 h, ou 14 j pour rôles `marins` et `manager_maritime`). Sidebar
   ERP riche groupée par domaine, topbar avec horloges port/local,
   badge notifications. Cible : 8 rôles (administrateur, operation,
   armement, technique, data_analyst, marins, commercial,
   manager_maritime).

4. **`/p/{token}` portail expéditeur** (token UUID 24c, 90 j) — sans
   compte. Packing list, messagerie sécurisée, documents.
   Audit append-only des accès (token SHA-256 jamais en clair).

### 3.2 Modules ERP staff (16)

| Module | Route | Rôle |
|---|---|---|
| Planning | `/planning` | Gantt + table legs + lien partage public |
| Commercial | `/commercial/...` | Clients, grilles tarifaires, offres, commandes |
| Cargo / Packing list | `/cargo` + `/p/{token}` | Batches, audit, lock, messagerie |
| Escale (port call) | `/escale` | Opérations Import/Export + shifts dockers + lock |
| Onboard / Captain | `/captain`, `/onboard/*` | SOF, ETA shifts, messagerie, docs, écran "Prochaine escale" |
| Crew | `/crew` | Bordées, compliance Schengen 90/180 j, calendar |
| Stowage | `/stowage` | 18 zones, algo glouton |
| Claims | `/claims` | Workflow 6 statuts, timeline |
| MRV | `/mrv` | Events fuel, exports DNV CSV, Carbon Report |
| Finance | `/finance` | LegFinance, OpexParameter |
| KPI | `/kpi` | Indicateurs |
| Booking (staff) | `/staff/bookings` | Confirmation manuelle des bookings client |
| Tickets escale | `/tickets` | Kanban SLA P1/P2/P3 |
| Cashbox | `/cashbox` | EUR/USD/VND |
| Chat Kairos AI | `/chat` | Assistant Claude Sonnet 4.6 |
| Admin | `/admin/*` | Users, OPEX, insurance, maintenance, activity-logs, ports config, security dashboard |

### 3.3 Pages client public (essentielles)

- `/` landing — hero, prochaines traversées, USP décarboné, CTA Réserver
- `/routes?from=FR&to=US&from_date=...` — search + résultats
- `/routes/{leg_code}` — détail itinéraire, capacité, météo POD@ETA,
  conditions transport, bouton Réserver
- `/fleet` — carte tracker positions GPS publiques
- `/about`, `/about/anemos` (méthodologie CO₂), `/about/legal`,
  `/about/privacy`, `/about/terms`

### 3.4 Wizard réservation client (3 étapes)

1. `/booking/new` — choix d'un leg (legs bookables, capacité, prix)
2. `/booking/new/{leg_code}` — détails cargo : palettes (EPAL/USPAL/
   IBC/BARRIQUE140), poids, hazardous, oversize, addresses pickup/livraison
3. `/booking/new/{leg_code}/confirm` — review + accept CGV
   → `/booking/{ref}/done` "L'équipe confirme sous 4 h, facturation
   par virement bancaire". **Pas de paiement en ligne**.

### 3.5 Compte client `/me/*`

- `/me` dashboard — résumé bookings, CO₂ cumulé, prochaines traversées
- `/me/bookings` liste, `/me/bookings/{ref}` détail
- `/me/invoices` — table factures HT/TVA/TTC + PDFs
- `/me/anemos` — Labels Anemos (anciennement certificats CO₂) + PDFs
- `/me/account` — profil + sécurité (password, MFA TOTP, Passkeys
  WebAuthn, recovery codes 10× single-use, GDPR export/delete)

### 3.6 Espaces commandant (`/captain`, `/onboard/*`)

- `/onboard` landing — 4 tuiles : Escale, Navigation, Cargo, Équipage
  + bandeau sticky **"Prochaine escale"** en tête
- `/captain/next-port` — synthèse port arrivée : nom, ETA, météo
  Open-Meteo @ ETA, agent maritime, VHF pilote, docs requis,
  restrictions, SOF récents avec bouton "Signer & figer"
- `/onboard/navigation` — saisir noon report (lat/lon **auto-rempli
  depuis satcom <6h** + vent **auto-rempli depuis météo**) + journal
  de quart
- `/captain?leg_id=X` — SOF events (24 types EOSP/SOSP/NOR/PILOT_ON…),
  ETA shifts avec motif, messagerie onboard, cargo documents

---

## 4. Design system existant (Kairos) — à faire évoluer, pas remplacer

Fichier source : `app/static/css/kairos.css` (~1750 lignes).
Composants déjà disponibles :

### 4.1 Containers & layouts
`.card`, `.card-elevated`, `.card-interactive`, `.app-shell-v2`,
`.sidebar`, `.sidebar.expanded`, `.sidebar.collapsed`,
`.section-header`.

### 4.2 Données & métriques
`.kpi-card`, `.stat-card`, `.kpi-strip`, `.data-table`,
`.capacity-gauge`, `.year-selector`, `.vessel-tabs`,
`.vessel-status-badge`, `.bordee-grid`, `.dash-notif-card`,
`.progress-bar`.

### 4.3 Maritime spécifique
`.leg-code` (mono cuivre), `.leg-chip`, `.leg-summary`,
`.port-badge`, `.sidebar-clock`, `.sidebar-userbadge`,
`.flag-icon`.

### 4.4 Atomes
`.btn` + variantes (`btn-primary`, `btn-secondary`, `btn-ghost`,
`btn-danger`, `btn-cuivre`, `btn-block`, `btn-xs/sm/lg`),
`.pill` (info/ok/warn/neutral), `.badge` (planned/inprogress/success),
`.alert` (alert-error / alert-warn / alert-info),
`.field`, `.kicker` (label en majuscule cuivre).

### 4.5 Overlays
`.toast` (notif éphémère), `.modal-card`, `.dropdown`.

### 4.6 Filtres Jinja disponibles
`|money` (Decimal → "1 234,56 EUR"), `|date`, `|datetime`,
`|flag` (FR → 🇫🇷), `|tojson`. Global `t(key, lang)` pour i18n.

---

## 5. Stack technique & contraintes (IMPORTANT)

**La refonte ne peut pas être React/Vue/Next**. Stack figée :

| Couche | Choix |
|---|---|
| Templating | **Jinja2 SSR** uniquement |
| Interactivité | **HTMX 2** (server-driven) + **Alpine.js** *light* pour les widgets isolés |
| CSS | **CSS variables W3C** + classes Kairos. Pas de Tailwind, pas de CSS-in-JS, pas de Sass |
| Pas de bundler | Pas de Webpack/Vite. Tous les JS sont des fichiers servis tels quels dans `/static/js/` |
| Icons | **Lucide** via CDN (`<i data-lucide="...">`) |
| Maps | **MapLibre GL** + MapTiler/Mapbox |
| Charts | À choisir (compatible CSP — pas d'inline) |

### 5.1 CSP strict (à respecter absolument)

```
script-src 'self' https://unpkg.com
style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com
font-src 'self' https://fonts.gstatic.com
img-src 'self' data: blob: <map tiles whitelisted>
connect-src 'self' <map APIs + nominatim>
```

**Interdit** :
- `<script>` inline (utiliser `<script src>` externe)
- `onclick="..."` et autres event handlers inline
- Domaines externes non whitelistés (pas de CDN tiers)

**Toléré** :
- `style="..."` attribut (inline styles autorisés via `'unsafe-inline'`)
- `data:` URIs en `img-src` (QR codes inline SVG par exemple)

### 5.2 Multilingue (5 langues)

Tous les libellés en `templates/**` doivent être inclus en branchements
`{% if lang == 'en' %}…{% else %}…{% endif %}` (FR par défaut).
Langues supportées : `fr`, `en`, `es`, `pt-br`, `vi`. Switch via
`/lang/{code}` (cookie `towt_lang`).

### 5.3 Accessibilité

WCAG AA minimum sur les couleurs (contraste teal `#0D5966` sur sable
`#EFE6D6` = OK ≥ 4.5:1). `<label for>` sur tous les inputs. `aria-*`
sur les composants overlay. Focus visible explicite (pas
`outline: none` sans alternative).

### 5.4 Responsive

- **Desktop** (≥ 1280 px) — cible primaire staff. Sidebar 256 px + main.
- **Tablet portrait** (768-1279 px) — cible commandants à bord. Tester
  spécifiquement en 7"/10" landscape. Sidebar collapsible 72 px.
- **Mobile** (< 768 px) — cible client occasionnel. Bottom-nav
  envisageable. Booking wizard doit marcher au pouce.

### 5.5 Print-friendly

Les PDFs (`templates/pdf/*.html`) sont rendus par **WeasyPrint** —
règles `@page` + `@media print`. BL, packing list, facture,
Label Anemos. Format A4, marges 20 mm, logo en tête.

---

## 6. État actuel : ce qui marche, ce qui pince

### 6.1 Forces

- Charte palette/typo bien définie et appliquée
- ~115 templates Jinja cohérents
- Composants Kairos riches (.kpi-card, .vessel-tabs, .leg-chip…)
- i18n FR/EN cohérent sur les pages publiques + client
- Print PDFs Weasy OK
- Topbar staff avec horloges port/local + badge notif global

### 6.2 Pain points connus (à traiter)

- **Hiérarchie visuelle** parfois faible : trop de cards de même
  importance sur la même page (dashboard staff, leg detail).
- **Densité ERP** : certaines pages staff (planning Gantt, escale
  detail) saturées en infos sans hiérarchie.
- **Inconstance des libellés** : "leg", "voyage", "traversée"
  utilisés indifféremment.
- **Mobile B2B** : wizard de booking pas optimisé pouce, sidebar staff
  illisible <768 px.
- **Onboard tablette** : pas de breakpoint dédié. Le commandant en
  iPad-durci voit le layout desktop compressé.
- **Empty states** pauvres (juste "Aucun X" en texte muted).
- **Marketing public** (`/`, `/routes`) ne pousse pas assez fort
  l'argument décarboné / Label Anemos.
- **Map flotte** (`/fleet`) — visual basique, on ne ressent pas le
  "voilier qui traverse l'Atlantique".

### 6.3 Composants à ajouter / améliorer

- **Empty state** robuste (illustration légère + CTA explicite)
- **Skeleton loaders** sur les listes (HTMX peut renvoyer un skeleton
  HTML)
- **Filtres dropdowns** unifiés (planning, cargo, escale les ont en
  inconsistant)
- **Timeline component** pour SOF events, claim timeline, activity log
- **Wind/wave rose** visualisation météo dans Label Anemos PDF +
  écran prochaine escale
- **Carte "à bord"** : le commandant en mer veut une vue tablet
  optimisée — proximité doigt-bouton, contraste élevé pour soleil
- **Print labels** : Label Anemos doit "claquer" comme un certificat,
  pas un document banal

---

## 7. Objectifs de la refonte

### 7.1 Primaires

1. **Renforcer l'identité maritime décarboné** dans les écrans publics
   et le Label Anemos. La rose des vents doit être présente comme motif
   subtil (background, séparateurs), pas seulement dans le logo.
2. **Améliorer la hiérarchie visuelle ERP** — un seul focal point par
   page, le reste en supporting layers.
3. **Refondre la marketing landing** (`/`) pour vendre la promesse
   décarboné en 5 secondes (above-fold).
4. **Tablette commandant** : variant layout dédié (≥ 768 px portrait,
   contraste +, tap targets ≥ 48 px).
5. **Mobile booking** : le wizard 3 étapes doit être impeccable au
   pouce sur smartphone (forms compacts, CTA flottant).

### 7.2 Secondaires

6. **Empty states** systématisés avec illustration vectorielle légère.
7. **Skeleton loaders** sur listes HTMX-rechargées.
8. **Iconographie maritime** custom (les `<i data-lucide>` génériques
   peuvent rester pour ERP, mais le public mérite un set custom :
   voilier, container, palette EPAL, vent, houle, port).
9. **Label Anemos PDF** : refonte typo + intégration d'un dataviz
   "votre CO₂ évité = X arbres = Y trajets Paris-NY en avion".
10. **Dark mode staff** (déjà toggle prévu, finir l'implémentation).

### 7.3 Non-objectifs (volontairement exclus)

- ❌ Remplacer la stack technique (HTMX/Alpine reste, pas de React).
- ❌ Refondre l'IA (Kairos AI chat reste tel quel, c'est le backend).
- ❌ Casser les URLs existantes (les 18 commits récents ont posé des
  redirects 301 — ne pas régresser).
- ❌ Migrer vers Tailwind / Bootstrap. Le système de tokens CSS-vars
  + classes Kairos reste.

---

## 8. Livrables attendus

Pour chaque écran ou composant retravaillé :

1. **Maquette Figma** (ou équivalent) sur breakpoints 1440 / 1024 / 768
   / 375 selon pertinence.
2. **Spec interactive** : états (default, hover, focus, active,
   disabled, loading, error, empty).
3. **Tokens utilisés** : référencer explicitement les variables CSS
   (`--teal`, `--vert-light`, `--space-3`) plutôt que des hex hardcodés.
4. **Notes d'implémentation** : composants Kairos réutilisés vs
   nouveaux à créer ; classes CSS à ajouter dans `kairos.css`.
5. **Accessibilité** : contraste WCAG AA vérifié, ordre tab logique,
   labels ARIA si custom widget.
6. **Print spec** pour les PDFs : A4 portrait, marges 20 mm, où placer
   le logo / cachet / signature.

### 8.1 Ordre de priorité suggéré

1. **Landing public + recherche routes** (`/`, `/routes`,
   `/routes/{code}`) — premier point de contact prospects.
2. **Wizard booking 3 étapes** mobile-first — friction directe sur
   conversion.
3. **Label Anemos PDF + page** — argument différenciant majeur.
4. **Écran "Prochaine escale" commandant** + mode tablette.
5. **Dashboard staff** (`/dashboard`) — saturation à réduire.
6. **Module Planning Gantt** — densité info à arbitrer.
7. **Page flotte publique** (`/fleet`) — émotionnel maritime à pousser.
8. **Système d'empty states + skeletons** transverses.

---

## 9. Annexes — quick references

### 9.1 Variables CSS critiques

```css
:root {
  --teal: #0D5966;
  --teal-dark: #093F48;
  --teal-light: #1A7C8C;
  --vert: #87BD29;
  --vert-light: #BEDB92;
  --cuivre: #B47148;
  --cuivre-light: #D49975;
  --sable: #EFE6D6;
  --sable-light: #F8F2E6;
  --bleu-marine: #0051A0;
  --bleu-horizon: #9EC6D2;
  --text: #2A2A2A;
  --text-muted: #6E6E6E;
  --border: #D9D9D9;
  --bg: #FAF7F2;
  --bg-premium: #0B2A33;
  --danger: #C44536;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-5: 24px; --space-6: 32px; --space-8: 48px;
  --radius-sm: 4px; --radius-md: 8px; --radius-lg: 16px;
  --text-xs: 0.75rem; --text-sm: 0.875rem; --text-base: 1rem;
  --text-md: 1.125rem; --text-lg: 1.5rem; --text-xl: 2rem;
  --text-2xl: 2.75rem;
}
```

### 9.2 Vocabulaire métier (à utiliser exact)

- **Leg** (pas "trajet" ni "voyage") — segment port A → port B
- **leg_code** — format `{seq}{vessel_code}{dep_country}{arr_country}{year_digit}` (ex. `1CFRBR6`)
- **ETD / ETA / ATD / ATA** — Estimated/Actual Time of Departure/Arrival
- **POL / POD** — Port of Loading / Discharge
- **LOCODE** — 5 caractères (ex. `FRFEC` Fécamp)
- **Escale** — période quai
- **SOF** — Statement of Facts (chronologie portuaire)
- **BL / BOL** — Bill of Lading
- **MRV** — Monitoring, Reporting, Verification (régl. UE émissions)
- **Bordée** — équipe de quart à bord
- **Schengen** — statut immigration 90 j/180 j marins étrangers
- **Label Anemos** — programme bilan carbone évité (anciennement
  "Certificat CO₂"). Le terme « CO₂ » reste pour la **métrique
  scientifique** (kg CO₂ évité, g CO₂/t·km).

### 9.3 Personae à servir

| Persona | Rôle | Device | Priorité |
|---|---|---|---|
| **Sophie (client B2B)** | Acheteur transport chez un FF (freight forwarder). 35 ans, mobile + desktop, FR/EN. | Smartphone Android + laptop | P1 |
| **Marc (gestionnaire logistique NEWTOWT)** | Opérations Le Havre. Prépare escales, valide bookings. | Desktop 27" | P1 |
| **Capt. Dupont (commandant)** | Voilier-cargo, 15+ j en mer. Saisit noon report, signe SOF, lit météo. | iPad durci 12.9" + parfois MacBook port | P1 |
| **Julien (administrateur)** | Direction NEWTOWT. KPI, MRV, security dashboard. | Laptop + grand écran externe | P2 |
| **Tom (prospect curieux)** | Vient découvrir NEWTOWT après un article presse. Veut comprendre en 30 s. | Smartphone surtout | P1 |

### 9.4 Données factuelles à respecter dans les mockups

- ⚠️ OBSOLÈTE (corrigé P4) — chiffres périmés d'un brief de refonte
  antérieur. Vérité courante : **6 sisterships classe TSC 80** (2 en
  opération — *Anemos*, *Artemis* ; 4 en construction — *Atlantis*,
  *Astérias*, *Archimedes*, *Atlas*) ; capacité commerciale **978 palettes
  EPAL** par navire (« Aphrodite »/« Pélican » et « 80-200 palettes » ne sont
  plus valides).
- Vitesse moyenne : 7-9 nœuds (transit Atlantique ~15 j).
- Émissions évitées : ~85-90 % vs cargo conventionnel (1.5 vs 13.7 g
  CO₂/t·km).
- Routes commerciales actives : Le Havre/Fécamp ↔ New York / Boston /
  São Sebastião / Ponta Delgada.
- Clients exemples : Maison Brisset (vins), Belco (café), Vivescia
  (céréales), Camif (mobilier).

---

## 10. Question de validation à poser avant de produire

Avant de lancer la refonte, valider avec le PO :

1. **Toggle dark mode staff** : on finit l'implémentation ou on
   reporte ?
2. **Niveau d'illustration custom** : on commande un set d'icônes
   maritimes propriétaires, ou on reste sur Lucide ?
3. **Carte flotte `/fleet`** : on garde MapLibre ou on envisage une
   "carte stylisée" plus marketing (style isometric, voilier qui
   navigue) ?
4. **Print Label Anemos** : on signe avec le DG (signature scannée) ou
   tampon corporate ?
5. **Dataviz "votre CO₂ évité ≈ X arbres / Y vols Paris-NY"** : on
   creuse les références (1 arbre absorbe 25 kg CO₂/an, 1 vol Paris-NY
   économique = 770 kg CO₂) ou on reste sur les chiffres bruts ?

---

**Fin du brief.**

Toutes les sources sont dans le repo :
- `docs/design/newtowt-design-tokens.json` (tokens W3C complets)
- `app/static/css/kairos.css` (~1750 lignes, design system actuel)
- `app/static/css/tokens.css` (exposition des variables au DOM)
- `app/templates/**/*.html` (~115 templates à auditer)
- `docs/design/01-design-handoff.md` (handoff initial V2 → V3)
- `docs/personas/01-personas.md` (personas détaillés)
