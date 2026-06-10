# Vitrine `newtowt.eu` — plan de construction

Référence : *NewTowt — Dossier de conception et prompt de construction*
(section 10). Source de vérité des faits : section 2 du dossier.

La vitrine est la façade publique de la plateforme `mynewtowt`. Une base
publique existe déjà (`app/routers/public_router.py`, layout
`templates/public/_layout_v2.html`, `static/css/newtowt-public.css`). Ce
plan **étend** cette base ; il ne repart pas de zéro et ne dégrade pas
l'existant.

## Séquencement (vagues du dossier, Phase 2/4)

| Vague | Contenu | État |
|---|---|---|
| **1 — transactionnel + accueil** | sélecteur d'horaires (`/routes` ✅ existant), **Notre flotte**, **logistique/Impact**, **Contact + demande de cotation** | **CE PR** |
| **+ Socle SEO/IA + légal** | `robots.txt`, `llms.txt`, `sitemap.xml`, hreflang, JSON-LD Schema.org, alignement mentions légales / confidentialité (§6), correction du récit (§5/§2) | **CE PR** |
| 2 — conviction | Navigation (courants, propulsion vélique, carte) | **CE PR** (amorce) |
| 3 — engagement | carnet de construction (blog éditable), recrutement, kit presse, base visuelle, actualités | PR ultérieur |
| 4 — futur | service passagers 2027 | PR ultérieur |

## Périmètre de ce PR

Langues : **FR complet** (texte du dossier verbatim) ; EN/ES/PT-BR =
page + nav + meta câblés, corps de texte en **emplacement traduction
réservé** clairement étiqueté (cf. dossier : « là où une traduction
manque, pose un emplacement clairement identifié »).

### Backend
- `app/models/contact_request.py` — table `contact_requests` (demandes de
  cotation/contact ; aucun paiement, relais vers le commercial).
- `migrations/versions/20260602_0021_contact_requests.py`.
- `app/services/contact.py` — validation **pure** (testable) + persistance.
- `app/services/seo.py` — génération **pure** de `robots.txt`, `llms.txt`,
  `sitemap.xml`, JSON-LD Organisation/Service/FAQ (testable).
- `app/routers/vitrine_router.py` — `/flotte`, `/impact`, `/navigation`,
  `/contact` (GET formulaire + POST), `/contact/merci`.
- `app/routers/seo_router.py` — `/robots.txt`, `/llms.txt`, `/sitemap.xml`.
- Enregistrement des routers dans `app/main.py`.

### Templates (`templates/public/`)
- `flotte.html`, `impact.html`, `navigation.html`, `contact.html`,
  `contact_merci.html`.
- `_layout_v2.html` : nav + footer 4 langues, sélecteur de langue complet,
  hreflang + canonical + JSON-LD Organisation dans `<head>`.
- Alignement **récit** (§2/§5) sur `landing.html` et `about.html`
  (Europe→Brésil/Amérique latine, café/cacao/fret industriel, 6
  sisterships TSC 80, équipage 9–10, 12 passagers) — suppression du
  récit obsolète New York/luxe.
- Alignement **légal** (§6) sur `about_legal.html` / `about_privacy.html`
  (siège Paris 128 bd Raspail, RCS Paris 994 529 873, OVH, `dpo@newtowt.eu`).

### i18n
- Clés de navigation/footer ajoutées à `fr/en/es/pt_br`.

### SEO / lisibilité IA
- `robots.txt` autorisant explicitement les robots d'IA (GPTBot,
  ClaudeBot, PerplexityBot, Google-Extended, etc.) + lien sitemap.
- `llms.txt` pointant vers les pages clés.
- `sitemap.xml` avec alternates hreflang par langue.
- JSON-LD `Organization` (liens d'identité), `Service`, `FAQPage`,
  `BreadcrumbList`. Inline `application/ld+json` : non bloqué par la CSP
  stricte (bloc de données, non exécuté).

### Tests
- `tests/unit/test_contact_service.py` — validation (honeypot, consentement,
  champs requis, normalisation).
- `tests/unit/test_seo_service.py` — robots/llms/sitemap/JSON-LD.

## Décisions pragmatiques
- **hreflang via `?lang=`** : l'architecture i18n existante repose sur un
  cookie + query `?lang=` (pas d'URL par langue). On expose donc les
  alternates en `?lang=xx` + `x-default`, valide et honnête, sans
  refactor du routage. Migration vers URL-par-langue = travail ultérieur.
- **Pas de paiement** : le formulaire de cotation valide, journalise et
  prépare le relais commercial ; la réservation/paiement reste dans
  l'extranet.
- **Redirection `towt.eu` → `newtowt.eu`** : relève de la couche
  hébergement/DNS (OVH/Caddy), hors application ; documentée mais non
  codée ici.
