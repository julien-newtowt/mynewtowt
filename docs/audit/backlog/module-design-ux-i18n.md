# Module Design system / UX / i18n (transverse)

Persona : **utilisateur staff au quotidien** + **designer/intégrateur**.
Réf V2 : `app/templates/{base.html,includes/time_input.html,403.html,404.html}`, `app/static/css/app.css`, `app/i18n/__init__.py`.
Cible V3 : `app/templates/{base.html,staff/_layout.html,staff/_topbar.html,errors/*}`,
`app/static/css/{tokens.css,kairos.css}`, `app/static/js/*`, `app/i18n/*.py`.

> La charte « Nouvelle Étoile » est préservée (tokens byte‑identiques). Les régressions sont
> du **câblage perdu**, réparables sans refonte.

---

## Lot 1 — P0

### [UX-01] Recâbler la saisie de fuseau horaire dans les formulaires
- **Persona :** Tous opérateurs portuaires · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `includes/time_input.html` (input time/datetime + `tz-select` UTC/Paris/Port local + hint UTC), utilisé dans 9 templates
- **Cible V3 :** créer `app/templates/staff/_time_input.html` ; recâbler escale/onboard/planning/claims/mrv ; exposer `data-port-tz` dans `staff/_layout.html` ; `static/js/towt-tz.js` (livré mais mort) ; `static/css/kairos.css` (`.tz-*`)
- **Objectif :** aucun template V3 n'utilise le sélecteur de fuseau (JS+CSS livrés mais morts) → saisie d'heures portuaires (SOF, escale, ETA) sans UTC/Paris/Port local ni aperçu UTC.
- **Critères d'acceptation :** partial réutilisable ; `port_local` résolu via `data-port-tz` ; appliqué aux forms concernés.
- **Test de non‑régression :** P4 #6.
- **Effort :** M

### [UX-02] Restaurer le catalogue i18n vietnamien
- **Persona :** Utilisateur VN · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `i18n/__init__.py` (≈ 442 valeurs `vi`)
- **Cible V3 :** `app/i18n/vi.py` (15 clés seulement)
- **Objectif :** un utilisateur VN voit ~97 % de l'UI en français (fallback). Régénérer le catalogue complet (parité avec fr/en/es/pt‑br ≈ 509 clés).
- **Critères d'acceptation :** `vi.py` à parité de clés ; portail (CARGO‑12) couvert.
- **Effort :** M

> SEC‑03 (filtrage sidebar par permission) est en **Lot 0**.

## Lot 2 — P1

### [UX-03] Horloge « prochain port » de la sidebar
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `base.html` + `fetch('/api/ports/next-clocks')`
- **Cible V3 :** `static/js/clock.js` (endpoint vivant mais plus appelé), `staff/_layout.html`
- **Objectif :** repère « heure au port de destination » perdu ; CSS `.sidebar-clock` orphelin. Rebrancher ou retirer proprement (endpoint + CSS).
- **Effort :** S

### [UX-04] Brancher la cloche de notifications du topbar
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** centre de notifications (dashboard)
- **Cible V3 :** `staff/_topbar.html` (menu placeholder statique), `notifications_router.py`
- **Objectif :** la cloche affiche un menu statique « aucune notification » non branché au vrai flux.
- **Critères d'acceptation :** menu alimenté par les notifications réelles (toggle‑read/archive).
- **Effort :** S

### [UX-05] Sélecteur de langue en UI staff
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** —
- **Cible V3 :** `staff/_topbar.html` (menu user), `/lang/{lang}` existe
- **Objectif :** aucun switcher de langue exposé en UI staff (mécanisme dispo).
- **Effort :** S

## Lot 3 — P2

### [UX-06] Polish charte & dette
- Enrichir `errors/403.html`/`404.html` (icône Lucide + carte `.auth-card`) ; réintroduire
  `.empty-state` dans `kairos.css` ; charger les graisses Manrope 300/800 si `--fw-light`/
  `--fw-extrabold` utilisés ; nettoyer le CSS mort (`.tz-*`, `.sidebar-clock` si non rebranchés) ;
  vérifier qu'aucun template n'appelle l'ancien filtre `|eur`/`|eur_int`.
- **Priorité :** P2 · **Effort :** S (groupé)

---

## Gains V3 à préserver
Tokens W3C → CSS + dark‑mode · architecture templates en couches · JS 100 % externe (CSP‑strict) +
toast/modal server‑driven (`HX-Trigger`) · topbar riche · navigation groupée repliable (tous
modules présents) · accessibilité (skip‑link, aria) · JetBrains Mono · `templating.py` enrichi.
