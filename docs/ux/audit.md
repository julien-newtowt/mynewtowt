# Audit UX — my_TOWT V1

**Date** : 2026-04-17
**Périmètre** : interfaces web (`app/templates/`, `app/static/css/app.css`), navigation, formulaires, états vides, mobile.
**Cible utilisateurs V2** : Gen Z (équipages, opérateurs jeunes — 22-30 ans).

## Méthode

Revue heuristique (Nielsen 10), inspection visuelle des templates principaux (dashboard, planning, escale, finance, kpi, onboard, crew, cargo, claims, mrv), test responsive en émulation mobile.

## Constats (13)

### U-01 — Identité graphique TOWT obsolète

**Évidence** : `app.css` utilise `--towt-blue`, `--towt-green` etc. Logo `logo_mytowt_white.svg` brandé TOWT.

**Impact** : la cession en liquidation impose un désancrage. Aucune raison de conserver l'identité visuelle de la société liquidée.

### U-02 — Typographie incohérente

**Évidence** : Poppins en 5 graisses, pas d'échelle modulaire. Les titres mélangent `1.2rem`, `1.4rem`, `1.5rem` au gré des templates.

**Impact** : hiérarchie de lecture floue, fatigue visuelle.

### U-03 — Sidebar fixe + responsive cassé mobile

**Évidence** : `base.html` sidebar permanente, pas de hamburger, pas de breakpoint. `app.css` a peu de media queries.

**Impact** : utilisation mobile = parcours impraticable. Or les marins/officiers sont mobile-first (escale, tablette de pont).

### U-04 — Densité tableau écrasante

**Évidence** : `templates/finance/`, `templates/kpi/`, `templates/escale/` — tableaux à 10+ colonnes sans regroupement, sans scroll horizontal sticky, sans surlignage zebra moderne.

**Impact** : scan visuel difficile, erreurs de lecture (mauvaise ligne).

### U-05 — Feedback action quasi-absent

**Évidence** : la majorité des actions retourne un `RedirectResponse(303)` — l'utilisateur ne sait pas si l'action a réussi.

**Impact** : sentiment d'incertitude, parfois double-soumission par doute.

### U-06 — Pas de dark mode

**Évidence** : aucun `prefers-color-scheme`, pas de toggle.

**Impact** : Gen Z attend dark-first. Confort visuel sur écran de pont la nuit.

### U-07 — Iconographie hétérogène

**Évidence** : `lucide-react` (via CDN) pour la sidebar, mais émojis (`|flag` filter, 🚢, ⚓, 📦) dans les templates et titres.

**Impact** : brisure visuelle, perception non-pro.

### U-08 — Formulaires bruts

**Évidence** : `<input>` sans focus state stylé, sans messages d'aide contextuels, sans validation inline (HTMX permet pourtant des partials).

**Impact** : accessibilité (WCAG AA non garanti), erreurs utilisateur fréquentes.

### U-09 — Sidebar 11 items sans regroupement

**Évidence** : `base.html:54-112` enchaîne 11 entrées de niveau 1 (Dashboard, Planning, Commercial, Escale, Finance, KPI, On Board, Passagers, Crew, Cargo, Claims, MRV).

**Impact** : surcharge cognitive, recherche d'item lente.

### U-10 — Zéro micro-interaction

**Évidence** : aucun `transition`, `transform`, `opacity` animation dans `app.css`. HTMX swap brut.

**Impact** : perception "app de gestion 2010". Frein adoption Gen Z.

### U-11 — Loading states muets

**Évidence** : HTMX charge en blanc, pas de spinner / skeleton.

**Impact** : utilisateur clique 2× pensant que ça n'a pas marché.

### U-12 — Empty states pauvres

**Évidence** : page sans données = tableau vide ou message texte unique. Aucune illustration, aucun CTA d'amorce.

**Impact** : engagement zéro à l'onboarding.

### U-13 — Mobile gestures inexistants

**Évidence** : pas de swipe, pas de bottom-nav, pas de pull-to-refresh, pas de FAB.

**Impact** : UX 2015. À l'usage mobile, app perçue comme version réduite du desktop.

## Synthèse

| Zone | Constats | Priorité |
|------|----------|----------|
| Identité | U-01 | 🔴 (bloquant rebrand) |
| Cohérence visuelle | U-02, U-07 | 🟠 |
| Layout / responsive | U-03, U-13 | 🔴 |
| Densité d'info | U-04 | 🟠 |
| Feedback / micro-UX | U-05, U-10, U-11 | 🟠 |
| Accessibilité | U-08 | 🟡 |
| Navigation | U-09 | 🟠 |
| Onboarding | U-12 | 🟡 |
| Confort | U-06 | 🟡 |

**Recommandation** : refonte complète du design system (cf. [`design-system-v2.md`](design-system-v2.md)) plutôt que correctifs ponctuels — l'effort cumulé serait similaire pour un résultat moins cohérent.
