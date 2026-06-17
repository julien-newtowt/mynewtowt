# Audit de couverture i18n — `mynewtowt`

> Date : 2026-06-17 · Périmètre : audit (lecture seule, aucune modification de
> catalogue ni de template). Livrable unique de ce passage.

## 1. Synthèse exécutive

- Le moteur i18n (`app/i18n` + helper `t()`) fonctionne, mais le **catalogue est
  minuscule** : **67 clés** au total, dédiées presque exclusivement au *chrome*
  (navigation, boutons, statuts, login). Aucune clé ne couvre le **contenu
  éditorial** des pages.
- Conséquence : `t()` n'est appelé **que dans les deux layouts publics**
  (`_layout_v2.html`, `_layout.html`). **Aucun corps de page** (public, staff,
  client, portail, PDF, email) n'utilise `t()`.
- Les pages vitrine publiques affichent donc leur **contenu 100 % en français
  codé en dur**, malgré un sélecteur de langue 4 langues (FR/EN/ES/PT-BR) bien
  visible dans l'en-tête. Un *bandeau « traduction en cours »*
  (`_translation_notice.html`) sert de cache-misère sur ~10 pages.
- L'espace **client** (`/me`) utilise un **3ᵉ anti-pattern** : des conditions
  inline `{% if lang == 'en' %}…{% else %}…{% endif %}` (FR/EN uniquement,
  **270 occurrences**), qui contournent totalement les catalogues.
- Couverture chiffrée des catalogues vs FR : **en/es/pt-br = 100 % des clés
  présentes** (mais en partie non traduites : 14 clés EN identiques au FR) ;
  **vi = 22 % seulement (15/67, 52 clés manquantes)**.

**Verdict** : le problème n'est pas tant des « clés manquantes dans les
catalogues » que **l'absence d'extraction du contenu en clés i18n**. Le travail
principal est d'extraire le texte des templates publics vers des clés, puis de
remplir les 4 langues.

---

## 2. Système i18n — comment ça marche

### 2.1 Fichiers

| Fichier | Rôle |
|---|---|
| `app/i18n/__init__.py` | dispatcher `t(key, lang, **fmt)`, `get_lang_from_request()`, constantes `SUPPORTED`, `DEFAULT` |
| `app/i18n/fr.py` | catalogue de **référence** (67 clés) — `CATALOG: dict[str,str]` |
| `app/i18n/en.py`, `es.py`, `pt_br.py`, `vi.py` | catalogues par langue |
| `app/templating.py` | enregistre `t` comme global Jinja, injecte `lang`/`brand`/`lang_options` via `_i18n_context_processor`, et expose `public_langs`, `lang_country`, `lang_name`, `hreflang_map` |

### 2.2 Langues supportées — écart à clarifier

- `app/i18n/__init__.py` : `SUPPORTED = ("fr", "en", "es", "pt-br", "vi")` →
  **5 langues** acceptées par le détecteur (`?lang=`, cookie, Accept-Language).
- `app/templating.py:228` : `public_langs = ["fr", "en", "es", "pt-br"]` →
  **4 langues** seulement proposées dans le **sélecteur de la vitrine** (le `vi`
  est volontairement absent du switcher public).
- `CLAUDE.md` annonce « 5 catalogues (fr, en, es, pt-br, vi) ».

**Conclusion sur l'écart** : `vi` est techniquement *sélectionnable* (si on force
`?lang=vi` ou via `user.language`), donc son catalogue doit exister, mais il
**n'est pas exposé** dans l'UI publique. Le vietnamien sert vraisemblablement à
des comptes staff/marins. C'est cohérent : on priorise FR/EN/ES/PT-BR pour la
vitrine, `vi` pour l'ERP marins.

### 2.3 Mécanique de repli (fallback)

`t()` résout dans l'ordre : **langue demandée → FR (DEFAULT) → clé brute**. Une
clé absente dans `es`/`pt-br`/`vi` **retombe donc silencieusement sur le
français** sans jamais afficher l'identifiant brut. C'est robuste, mais cela
**masque** les trous de traduction (l'utilisateur voit du FR au lieu d'une
erreur visible).

### 2.4 Sélection de langue

- Cookie `towt_lang` (posé par `GET /lang/{lang}`, défini dans
  `app/routers/public_router.py:57`) — priorité 1.
- puis `?lang=`, puis `user.language`, puis `Accept-Language`, puis `fr`.

---

## 3. Couverture des catalogues (chiffrée, vs FR référence = 67 clés)

| Langue | Clés présentes | Manquantes | Vides | **Identiques au FR** (présentes mais non traduites) | Couverture utile |
|---|---|---|---|---|---|
| **fr** (réf.) | 67 | — | 0 | — | 100 % |
| **en** | 67 | 0 | 0 | **14** | ~79 % |
| **es** | 67 | 0 | 0 | 3 | ~96 % |
| **pt-br** | 67 | 0 | 0 | 3 | ~96 % |
| **vi** | 15 | **52** | 0 | 2 | ~19 % |

### Détail des clés non traduites / manquantes

- **EN — 14 clés identiques au FR** (cognats ou oublis, à vérifier) :
  `dash_notifications`, `footer_navigation`, `nav_admin`, `nav_captain`,
  `nav_cargo`, `nav_claims`, `nav_commercial`, `nav_contact`, `nav_finance`,
  `nav_impact`, `nav_kpi`, `nav_mrv`, `nav_navigation`, `nav_planning`.
  (La plupart sont des cognats légitimes — `Commercial`, `Impact`, `Navigation`,
  `Finance`, `Contact`, `Cargo`, `Claims`, `KPI`, `MRV`, `Admin` —, mais
  `dash_notifications`, `footer_navigation`, `nav_captain` méritent vérif.)
- **ES / PT-BR — 3 clés identiques au FR** : `nav_admin`, `nav_kpi`, `nav_mrv`
  (sigles/abréviations légitimes → non bloquant).
- **VI — 52 clés manquantes** (repli FR à l'écran). Familles entières absentes :
  toute la navigation publique (`nav_routes`, `nav_fleet`, `nav_about`,
  `nav_book`, `nav_our_fleet`, `nav_impact`…), tout le bloc `footer_*`, tous les
  `status_*`, tous les `dash_*`, tout `auth_*`, et la quasi-totalité des `btn_*`
  (seuls `btn_login`, `btn_save`, `btn_cancel` traduits).

> Remarque : `es.py` et `pt_br.py` se déclarent « full parity with fr.py » — c'est
> exact en nombre de clés. La vraie dette est ailleurs (cf. §4).

---

## 4. Chaînes en dur (bypass i18n)

`t()` (motif réel `{{ t('…` ) n'apparaît **que** dans :
- `app/templates/public/_layout_v2.html` — 22 appels (header, footer, nav).
- `app/templates/public/_layout.html` (layout v1) — 7 appels.

**Tous les corps de page** (`{% block content %}`) sont du texte FR codé en dur.
Décompte des appels `t()` réels par fichier public : **0 partout sauf les 2
layouts**. (Les `t()` comptés naïvement dans `contact.html`/`devis_form.html`
étaient des faux positifs sur `default('')`.)

### 4.1 Pages publiques (vitrine multilingue) — À CORRIGER en priorité

Toutes étendent `public/_layout_v2.html` (sélecteur 4 langues visible). Volume =
nombre approximatif de lignes porteuses de texte visible.

| Fichier | Lignes | Volume texte | Bandeau « trad. en cours » | Exemple de chaîne en dur (fichier:ligne) |
|---|---|---|---|---|
| `public/about_anemos.html` | 408 | **~76** | non | gros contenu éditorial label ANEMOS, intégralement FR |
| `public/route_detail.html` | 349 | ~56 | non | fiche route détaillée (libellés, sections) FR |
| `public/flotte.html` | 132 | ~46 | oui | `flotte.html:26` `<h1 class="hero-title">Six sisterships, un seul standard de service</h1>` ; `:83` `<th scope="row">Longueur hors tout</th>` |
| `public/landing.html` | 105 | ~35 | non | `landing.html:9` `<h1 class="hero-title">Le fret à la voile vers le Brésil & l'Amérique latine</h1>` ; `:30` `<button…>Rechercher</button>` ; `:19` `<label for="from">Départ (pays)</label>` |
| `public/devis_form.html` | 156 | ~34 | non | formulaire de cotation : labels (`Format`, `Tonnage`, `Nom`…) en dur |
| `public/contact.html` | 148 | ~33 | non | `contact.html:9` `<h1 class="hero-title">Demandez votre cotation</h1>` ; `:65` `<label…>Nom et prénom *</label>` ; `:139` `<button…>Envoyer ma demande</button>` |
| `public/presse.html` | 93 | ~31 | oui | contenu presse FR |
| `public/about.html` | 71 | ~29 | non | page À propos FR |
| `public/about_terms.html` | 133 | ~27 | non | CGV/CGU FR (texte juridique long) |
| `public/devis_result.html` | 89 | ~27 | non | résultat de cotation FR |
| `public/planning_share.html` | 145 | ~27 | non | planning partagé FR |
| `public/impact.html` | 111 | ~26 | oui | `impact.html` sections « environnement maîtrisé », LACOE… FR |
| `public/about_privacy.html` | 82 | ~24 | oui | politique de confidentialité FR |
| `public/routes.html` | 95 | ~24 | non | `routes.html` libellés recherche/filtres FR |
| `public/about_legal.html` | 68 | ~23 | oui | mentions légales FR |
| `public/navigation.html` | 67 | ~21 | oui | page Navigation FR |
| `public/recrutement.html` | 73 | ~19 | oui | `recrutement.html:9` `Embarquez dans une aventure qui navigue déjà` ; `:39` `<h3>Naviguer à la voile</h3>` ; `:70` `Candidature spontanée` |
| `public/fleet.html` | 77 | ~12 | non | ancienne page flotte (layout v2), FR |
| `public/carnet.html` | 45 | ~9 | oui | carnet de bord FR |
| `public/actualites.html` | 39 | ~8 | oui | actualités FR |
| `public/contact_merci.html` | 22 | ~6 | non | page de remerciement FR |
| `public/carnet_post.html` | 51 | — | non | billet carnet FR |
| `public/404.html` | 13 | — | non | page 404 FR |

**Faille du bandeau** : `_translation_notice.html` ne traite que `en/es/pt-br`.
Il n'affiche **rien** pour `vi` (branche `{% else %}` = FR), et n'est inclus que
sur ~10 pages : les pages sans bandeau (landing, contact, about, devis,
route_detail, about_anemos…) présentent du FR pur **sans aucun avertissement**.

### 4.2 Espace client `/me` — 3ᵉ anti-pattern (FR/EN inline)

`app/templates/client/*` n'utilise **pas** `t()` mais des conditions inline
`{% if lang == 'en' %}…{% else %}…{% endif %}` — **270 occurrences** sur 20
fichiers, **FR/EN uniquement** (es/pt-br/vi non gérés → repli FR).

| Fichier | Occurrences `if lang` |
|---|---|
| `client/booking_detail.html` | 31 |
| `client/dashboard.html` | 29 |
| `client/account.html` | 23 |
| `client/mfa_setup.html` | 23 |
| `client/booking_step3.html` | 15 |
| `client/anemos.html`, `track.html`, `invoices.html`, `booking_step1.html`, `documents.html`, `register.html` | 12–13 chacun |
| `client/_layout.html`, `_topbar.html`, `bookings_list.html` | 11 chacun |
| autres (`login`, `mfa_*`, `messages`, `notifications`, `booking_done`) | 3–9 |

Le sélecteur client (`client/_layout.html:37`, `_topbar.html:50`) est un simple
toggle **FR↔EN** (`/lang/{{ 'en' if lang=='fr' else 'fr' }}`). L'espace client est
donc *de facto* bilingue FR/EN, pas quadrilingue.

### 4.3 ERP staff `app/templates/staff/*` — FR-only (à confirmer, NE PAS prioriser)

- **93 fichiers**, **0 appel `t()`**, contenu FR codé en dur.
- Cohérent avec la doctrine produit (ERP interne francophone). À **confirmer**
  avec le métier mais **à ne pas sur-prioriser** : pas de sélecteur de langue
  staff côté templates (le `lang` staff vient de `user.language`, surtout utile
  pour `vi` des marins → cf. catalogue `vi`).

### 4.4 Portail `/p/{token}`, PDF, emails

- `portal/` : 5 fichiers, **0 `t()`** → FR codé en dur. Le portail expéditeur
  pouvant être international, c'est un candidat de **2ᵉ vague**.
- `pdf/` : 14 fichiers (BL/PL/invoice/CO₂), **0 `t()`** → FR. Documents
  contractuels : la langue devrait suivre celle du client/destinataire ;
  internationalisation utile mais **hors vitrine** (vague ultérieure).
- `emails/` : 2 fichiers (`booking_event`, `security_event`), **0 `t()`** → FR.

---

## 5. Plan de correction priorisé

Principe directeur : **extraire le texte des templates vers des clés i18n**, puis
remplir FR (déjà écrit) + EN + ES + PT-BR. (VI hors vitrine, traité avec l'ERP.)

### Vague 0 — Quick wins catalogue (≈1 h)
1. Compléter les **52 clés `vi` manquantes** (repli FR aujourd'hui) — utile pour
   l'ERP marins.
2. Vérifier/corriger les **14 clés EN identiques au FR** (au moins
   `dash_notifications`, `footer_navigation`, `nav_captain`).
3. Décider et documenter l'écart `SUPPORTED` (5) vs `public_langs` (4) : garder
   `vi` hors vitrine, ou l'ajouter — et étendre `_translation_notice.html` au
   cas `vi`.

### Vague 1 — Pages vitrine à forte conversion (prioritaire)
Ordre par visibilité × volume :
1. `landing.html` (page d'accueil) — ~35 chaînes.
2. `flotte.html` — ~46 chaînes.
3. `contact.html` + `contact_merci.html` — ~39 chaînes (formulaire = conversion).
4. `routes.html` + `route_detail.html` — ~80 chaînes (cœur du parcours réservation).
5. `devis_form.html` + `devis_result.html` — ~61 chaînes (cotation = conversion).
6. `impact.html` — ~26 chaînes.

**Pattern de correction** (par page) :
- Extraire chaque chaîne FR en clé namespacée (ex. `landing_hero_title`,
  `contact_form_name_label`), regroupées par page dans `fr.py`.
- Remplacer dans le template par `{{ t('landing_hero_title', lang) }}` (utiliser
  `|safe` quand la chaîne contient du HTML inline `<strong>`).
- Reporter la clé dans `en.py`, `es.py`, `pt_br.py` (traduction réelle).
- Retirer le `{% include "public/_translation_notice.html" %}` une fois la page
  réellement traduite.

### Vague 2 — Reste de la vitrine + portail
- `about.html`, `about_anemos.html` (gros, ~76), `about_legal/privacy/terms`
  (juridique — exige relecture), `presse.html`, `actualites.html`, `carnet*`,
  `navigation.html`, `recrutement.html`, `fleet.html`, `planning_share.html`,
  `404.html`.
- Portail `/p/{token}` (5 fichiers) si clientèle internationale.

### Vague 3 — Espace client `/me` (refactor d'uniformisation)
- Migrer les **270 conditions inline FR/EN** vers `t()` + clés `client_*`.
- Décider si l'espace client passe en 4 langues (ES/PT-BR) ou reste FR/EN.

### Vague 4 — ERP staff / PDF / emails (sur demande métier)
- Staff (93 fichiers) : seulement si décision d'internationaliser l'ERP
  (probablement non, ou limité au `vi` marins).
- PDF & emails : i18n suivant la langue du destinataire.

---

## 6. Estimation d'effort

Hypothèses : ~1 clé / chaîne ; ~3–5 min par clé en moyenne (extraction +
remplacement template + 3 traductions EN/ES/PT-BR), texte juridique plus lent.

| Périmètre | Chaînes à extraire (approx.) | Clés à créer (×1) | Trad. à produire (×3 langues) | Effort estimé |
|---|---|---|---|---|
| Vague 0 (catalogue vi + fix EN) | — | ~55 valeurs vi + 14 EN | — | **~1 h** |
| Vague 1 (6–8 pages clés) | ~270 | ~270 | ~810 | **~3–4 j** |
| Vague 2 (reste vitrine + portail) | ~330 | ~330 | ~990 | **~4–6 j** (dont juridique relu) |
| Vague 3 (espace client) | ~270 (inline → t) | ~270 | ~540 (EN existe ; +ES/PT-BR si retenu) | **~3–4 j** |
| Vague 4 (staff/PDF/email) | très élevé (>1500) | >1500 | — | **non chiffré — décision métier** |

**Total vitrine + portail + client (vagues 0→3) : ≈ 11–15 jours-homme**, hors ERP
staff. Le poste principal n'est pas la traduction mais l'**extraction/remplacement
dans les templates** (mécanique mais nombreuse).

---

## 7. Recommandations transverses

1. **Standardiser sur `t()`** : interdire les `{% if lang == 'en' %}` inline
   (client) — source de divergence et impossible à étendre à 4 langues.
2. **Rendre les trous visibles en dev** : envisager un mode (env) où `t()`
   renvoie un marqueur (ex. `⟦key⟧`) au lieu du repli FR silencieux, pour
   détecter les clés manquantes pendant les tests.
3. **Découper `fr.py`** : avec des centaines de clés, passer d'un seul `CATALOG`
   à des sous-modules par domaine (`public.py`, `client.py`, …) fusionnés, pour
   maintenabilité.
4. **Test de parité** : ajouter un test pytest qui vérifie que `en/es/pt-br` ont
   le même jeu de clés que `fr` (échec si une clé manque ou reste vide), et que
   `vi` couvre au moins le namespace ERP.
5. **Conventions de clés** : `<page>_<section>_<element>` (ex.
   `landing_hero_title`, `contact_form_submit`). Documenter dans ce dossier.
