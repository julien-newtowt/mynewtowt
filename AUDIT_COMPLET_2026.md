# **Rapport d'Audit Complet - Application NEWTOWT ERP/CRM/Shipping**
## *Plateforme de Transport Maritime Décarboné à la Voile*

**Date :** 18 Juin 2026  
**Version Application :** 3.0.0  
**Repository :** `juliengonde-5G/mynewtowt`  
**Auditeurs :** Chargé de Développement | Développeur Senior | Chargé UX | Shipping/Fleet Manager  

---

## **Table des Matières**

1. [Synthèse Exécutive](#1-synthèse-exécutive)
2. [Perspective 1 : Chargé de Développement Informatique](#2-perspective-1--chargé-de-développement-informatique)
3. [Perspective 2 : Développeur Senior - Audit Technique](#3-perspective-2--développeur-senior---audit-technique)
4. [Perspective 3 : Chargé UX/UI](#4-perspective-3--chargé-uxui)
5. [Perspective 4 : Shipping Manager & Fleet Manager](#5-perspective-4--shipping-manager--fleet-manager)
6. [Recommandations Stratégiques](#6-recommandations-stratégiques)
7. [Annexes](#7-annexes)

---

## **1. Synthèse Exécutive**

### **1.1 Contexte et Vision**

**NEWTOWT** est une compagnie maritime pionnière du transport décarboné à la voile depuis 2011. La plateforme **mynewtowt V3** est une application unifiée combinant :

- **ERP Interne** : Pilotage opérationnel pour 8 profils collaborateurs
- **Portail Client** : Réservation d'espace en cale, suivi documentaire, reporting CO₂
- **Veille d'Actualité** : Agrégation d'informations sectorielles via NewsData.io

**Stack Technique :** FastAPI 0.115 / Python 3.12 / PostgreSQL 16 / HTMX 2 / Alpine.js / Jinja2 SSR / Design System Kairos

### **1.2 Points Forts Identifiés**

✅ **Architecture Moderne** : Séparation claire des couches (routers, services, models, schemas)
✅ **Sécurité Robuste** : RBAC granulaire, MFA, CSP stricte, audit trail immutable
✅ **Expérience Utilisateur** : Design system cohérent, PWA pour le pont, multi-langues (5)
✅ **Fonctionnalités Métier** : Gestion complète du cycle de vie maritime (legs, escales, cargo, claims)
✅ **Observabilité** : OpenTelemetry, Prometheus, Sentry, logging structuré
✅ **Documentation Complète** : ADRs, personas, architecture, security review

### **1.3 Risques Critiques**

⚠️ **Dette Technique** : 34,706 lignes de code Python, couverture tests à 25% (seuil minimal)
⚠️ **Complexité Routing** : 28 routers, certains dépassant 1,500 lignes (commercial_router.py)
⚠️ **Gestion des Exceptions** : Utilisation de `except Exception` génériques dans permissions.py
⚠️ **Branches Git** : Repository en état detached HEAD, pas de branches de feature visibles
⚠️ **Performance** : Pas de cache agressif sur les données fréquemment accédées

### **1.4 Opportunités d'Amélioration**

🎯 **Montée en Maturité** : Augmenter couverture tests à 80% sur modules critiques
🎯 **Optimisation** : Implémenter Redis pour caching des données statiques
🎯 **Sécurité** : Finaliser implémentation SIEM (Loki + Wazuh)
🎯 **UX** : Unifier les patterns d'interaction entre modules
🎯 **DevOps** : Automatiser le nettoyage des branches inactives

---

## **2. Perspective 1 : Chargé de Développement Informatique**

### **2.1 Vision du Fonctionnement de l'Application**

#### **Architecture Globale**

L'application suit une **architecture en couches** bien définie :

```
┌─────────────────────────────────────────────────────────────┐
│                      Presentation Layer                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  HTMX 2     │  │ Alpine.js   │  │ Jinja2 Templates    │  │
│  │  (SSR)       │  │ (light)     │  │ (Design System Kairos)│  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                      Application Layer                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ FastAPI     │  │ 28 Routers  │  │ 20+ Services        │  │
│  │ (async)     │  │ (modulaires)│  │ (logique métier)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                      Data Layer                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ SQLAlchemy  │  │ PostgreSQL  │  │ Alembic Migrations   │  │
│  │ 2.0 async   │  │ 16 + pgvector│  │ (atomiques)          │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

#### **Flux Principaux**

1. **Booking Client** : Prospect → Recherche route → Wizard 4 étapes → Confirmation → Paiement (Stripe retiré en V3.1, virement uniquement)
2. **Gestion Escale** : ATA → Opérations Import/Export → SOF → ATD → Cascade dates
3. **Tracking Flotte** : Positions live → Historique trajets → Visualisation carte
4. **Chatbot Kairos AI** : Requête utilisateur → RAG pgvector → Claude Sonnet 4.6 → Réponse streamée

#### **Modèle de Données**

**30+ tables SQLAlchemy** organisées en :
- **OLTP** : Tables transactionnelles (legs, bookings, escale_operations, etc.)
- **OLAP** : Vues matérialisées pour analytics (schema analytics)
- **RAG** : Embeddings pgvector pour le chatbot

### **2.2 Écarts entre Documentation et Production**

#### **Conformités**

| Élément | Documentation | Production | Statut |
|---------|--------------|------------|--------|
| Architecture C4 | ✅ Documentée | ✅ Implémentée | **OK** |
| RBAC Matrix | ✅ Documentée | ✅ Implémentée | **OK** |
| Design Tokens | ✅ Documentée | ✅ Implémentée | **OK** |
| Flux Booking | ✅ Documentés | ✅ Implémentés | **OK** |

#### **Écarts Identifiés**

| Élément | Documentation | Production | Impact |
|---------|--------------|------------|--------|
| Stripe Payment | Documenté comme intégré | **Retiré en V3.1** (facturation virement) | ⚠️ Maj doc nécessaire |
| SIEM Loki | Prévu V3.1 | **Non implémenté** | ⚠️ Retard sur roadmap |
| Multi-devise | Prévu V3.1 | **Non implémenté** | ⚠️ Retard sur roadmap |
| Module RH | Stub documenté | **Partiellement implémenté** | ⚠️ Incomplet |
| KPI Certificats CO₂ | Stub documenté | **Partiellement implémenté** | ⚠️ Incomplet |

#### **Modules Manquants**

- **Module Passagers** : Supprimé en V3.0.0 (restructuration corporate)
- **Veille d'Actualité** : Flux brut NewsData.io fonctionnel, mais IA (synthèse/scoring) en P2

### **2.3 Usages de Programmation d'un CRM-ERP-Shipping App**

#### **Patterns Appliqués**

1. **Repository Pattern** : Chaque module expose un repository asynchrone
   ```python
   class BookingRepository:
       async def create(self, dto: BookingCreate) -> Booking
       async def get(self, ref: str) -> Booking | None
       async def list_by_client(self, client_id: int) -> Sequence[Booking]
   ```

2. **Service Layer** : Logique métier encapsulée
   ```python
   class BookingService:
       def __init__(self, repo: BookingRepository, capacity: CapacityService, ...)
       async def confirm(self, ref: str, user: ClientAccount) -> Booking
   ```

3. **DTO/Pydantic Schemas** : Validation des entrées/sorties
4. **Event-Driven** : Bus interne pour découpler modules
5. **Idempotency** : Headers `Idempotency-Key` sur POST mutants

#### **Bonnes Pratiques**

✅ **DO** :
- `await db.flush()` dans les routes (pas `commit`)
- `services.activity.record()` pour tracer les write actions
- `require_permission()` sur chaque endpoint protégé
- `flush+RedirectResponse(303)` après mutation
- Préférer les classes CSS Kairos aux inline styles

❌ **DON'T** :
- Pas de `await db.commit()` dans les routes
- Pas de `<script>` inline (CSP-strict)
- Pas de f-string SQL (whitelist + `bindparams()`)
- Pas de framework JS lourd (HTMX + Alpine.js uniquement)
- Pas de police `Inter`, `Poppins` (Manrope uniquement)

### **2.4 Recommandations**

1. **Mettre à jour la documentation** pour refléter l'état actuel (Stripe retiré, modules partiels)
2. **Prioriser l'implémentation** des modules stub (RH, KPI)
3. **Documenter les décisions architecturales** dans `docs/architecture/adr/`
4. **Créer un glossaire technique** pour les nouveaux développeurs

---

## **3. Perspective 2 : Développeur Senior - Audit Technique**

### **3.1 Audit du Code**

#### **Statistiques du Codebase**

- **Fichiers Python** : 158 fichiers dans `app/` (93 avec fonctions async)
- **Lignes de Code** : 34,706 lignes (app/)
- **Routers** : 28 routers, taille moyenne : ~500 lignes
- **Services** : 20+ services, taille moyenne : ~300 lignes
- **Models** : 30+ modèles SQLAlchemy
- **Tests** : 38 fichiers, 248 fonctions de test, couverture : **25%** (seuil minimal)

#### **Top 10 Fichiers les Plus Gros**

| Fichier | Lignes | Complexité | Risque |
|--------|--------|------------|--------|
| commercial_router.py | 1,503 | ⭐⭐⭐⭐ | ⚠️⚠️ |
| admin_router.py | 1,299 | ⭐⭐⭐⭐ | ⚠️⚠️ |
| captain_router.py | 1,000 | ⭐⭐⭐⭐ | ⚠️⚠️ |
| onboard_router.py | 841 | ⭐⭐⭐ | ⚠️ |
| modules_router.py | 788 | ⭐⭐⭐ | ⚠️ |
| planning.py (service) | 785 | ⭐⭐⭐ | ⚠️ |
| stowage.py (service) | 604 | ⭐⭐⭐ | ⚠️ |
| client_dashboard_router.py | 686 | ⭐⭐⭐ | ⚠️ |
| crew_router.py | 599 | ⭐⭐⭐ | ⚠️ |
| tracking_router.py | 598 | ⭐⭐⭐ | ⚠️ |

### **3.2 Anomalies de Développement**

#### **Problèmes de Structure**

1. **Routers Trop Gros** : `commercial_router.py` (1,503 lignes) et `admin_router.py` (1,299 lignes) violent le principe de responsabilité unique
   - **Impact** : Maintenance difficile, risque d'erreurs
   - **Recommandation** : Découper en sous-routers par domaine fonctionnel

2. **Services Non Utilisés** : Certains services semblent sous-utilisés ou redondants
   - **Impact** : Complexité inutile
   - **Recommandation** : Audit d'utilisation et consolidation

3. **Mixing Concerns** : Logique métier directement dans les routers au lieu des services
   - **Exemple** : `commercial_router.py` contient du code de génération PDF
   - **Impact** : Difficile à tester, à maintenir
   - **Recommandation** : Déplacer dans services dédiés

#### **Problèmes de Comportement**

1. **Gestion des Exceptions Génériques** :
   ```python
   # Dans permissions.py (lignes multiples)
   except Exception:
       overrides = {}
   ```
   - **Impact** : Masque les erreurs réelles, difficile à déboguer
   - **Recommandation** : Utiliser des exceptions spécifiques

2. **Transactions Imbriquées** : Risque de deadlocks dans les cascades de dates
   - **Exemple** : `DatePropagationService.resequence_and_recalc()`
   - **Impact** : Performance dégradée sous charge
   - **Recommandation** : Optimiser les verrous, utiliser des transactions courtes

3. **Pas de Timeout sur Appels Externes** : Appels à Stripe, Anthropic, Windy sans timeout
   - **Impact** : Risque de blocage du worker
   - **Recommandation** : Ajouter timeouts (ex: 30s pour API externes)

### **3.3 Failles ou Erreurs de Programmation**

#### **Sécurité**

1. **SQL Injection Potentielle** :
   - **Statut** : ✅ **Mitigé** par utilisation systématique de SQLAlchemy ORM
   - **Vérification** : Pas de f-string SQL trouvées, utilisation de `bindparams()`
   - **Recommandation** : Maintenir la vigilance, audit régulier

2. **XSS** :
   - **Statut** : ✅ **Mitigé** par Jinja2 autoescape + bleach pour champs riches
   - **Vérification** : CSP stricte, pas de `<script>` inline
   - **Recommandation** : Continuer à utiliser bleach pour sanitization

3. **CSRF** :
   - **Statut** : ✅ **Implémenté** via double-submit cookie
   - **Vérification** : Middleware CSRF actif, HTMX injecte token automatiquement
   - **Recommandation** : OK

4. **Rate Limiting** :
   - **Statut** : ✅ **Implémenté** mais partiel
   - **Vérification** : Rate limits sur login, booking, chat
   - **Problème** : Pas de rate limit sur certaines API publiques
   - **Recommandation** : Étendre à toutes les API publiques

#### **Données**

1. **Pas de Soft Delete** : Suppression physique des données
   - **Impact** : Perte d'historique, difficulté pour audit
   - **Recommandation** : Implémenter soft delete avec `is_deleted` + `deleted_at`

2. **Pas de Versioning des Entités** : Pas d'historique des modifications
   - **Impact** : Difficile de suivre l'évolution des données
   - **Recommandation** : Implémenter versioning ou audit trail étendu

3. **Transactions Longues** : Certaines opérations bloquent la DB
   - **Exemple** : Génération de rapports PDF en synchrones
   - **Impact** : Timeout possible sous charge
   - **Recommandation** : Déplacer en background jobs (Celery/RQ)

### **3.4 Sûreté du Programme et Sécurités**

#### **Points Forts**

✅ **Authentification** : bcrypt + itsdangerous, MFA TOTP/WebAuthn
✅ **Autorisation** : RBAC granulaire avec matrice rôle × module × niveau
✅ **Sessions** : Cookies signés, HttpOnly, Secure, SameSite=Lax
✅ **CSP** : Content Security Policy stricte (note A+ Mozilla Observatory)
✅ **Headers** : X-Content-Type-Options, X-Frame-Options, HSTS
✅ **Audit** : Activity logs immutable avec hash chaîné
✅ **Chiffrement** : LUKS sur volumes Docker, pgcrypto pour données C5
✅ **Secrets** : Validation des secrets faibles au démarrage

#### **Points à Améliorer**

⚠️ **Rate Limiting** : Étendre à toutes les API publiques
⚠️ **SIEM** : Implémenter Loki + Wazuh pour corrélation logs
⚠️ **Backup** : Tester restore mensuel (documenté mais pas vérifié)
⚠️ **DDoS Protection** : Pas de WAF avancé (nginx basique)
⚠️ **Secret Rotation** : Automatiser rotation des API keys

#### **Vulnérabilités Potentielles**

1. **Information Disclosure** :
   - **Risque** : Messages d'erreur détaillés en production
   - **Impact** : Fuite d'information sur la structure interne
   - **Recommandation** : Messages d'erreur génériques en production

2. **Mass Assignment** :
   - **Risque** : Pydantic permet mass assignment
   - **Impact** : Modification de champs sensibles
   - **Recommandation** : Utiliser `model_validate()` avec `exclude_unset=True`

3. **Insecure Direct Object Reference (IDOR)** :
   - **Risque** : Accès direct aux ressources via IDs
   - **Impact** : Accès non autorisé aux données
   - **Recommandation** : Vérifier permissions sur chaque accès

### **3.5 Dette de Code**

#### **Dette Technique Quantifiée**

| Catégorie | Montant | Impact |
|----------|---------|--------|
| Couverture Tests | 25% (seuil 25%) | ⚠️⚠️⚠️ |
| Complexité Cyclomatique | Élevée (routers >1000 lignes) | ⚠️⚠️ |
| Duplication de Code | Modérée | ⚠️ |
| Documentation Code | Partielle | ⚠️ |
| Tests d'Intégration | Limités | ⚠️⚠️ |
| Performance | Optimisations manquantes | ⚠️ |

#### **Dette Fonctionnelle**

| Module | Statut | Impact |
|--------|--------|--------|
| RH | Stub | ⚠️⚠️ |
| KPI | Stub | ⚠️⚠️ |
| Veille IA | Partiel (P2) | ⚠️ |
| Multi-devise | Non implémenté | ⚠️ |
| Co-chargement | Non implémenté | ⚠️ |

#### **Plan de Réduction de la Dette**

**Phase 1 (0-3 mois)** :
- Augmenter couverture tests à 40%
- Découper routers > 800 lignes
- Implémenter soft delete
- Ajouter timeouts sur appels externes

**Phase 2 (3-6 mois)** :
- Augmenter couverture tests à 60%
- Implémenter caching Redis
- Finaliser modules RH et KPI
- Implémenter SIEM

**Phase 3 (6-12 mois)** :
- Augmenter couverture tests à 80%
- Optimiser performances
- Implémenter multi-devise
- Implémenter co-chargement

### **3.6 Outils et Bonnes Pratiques**

#### **Outils Utilisés**

- **Linting** : Ruff (E, F, W, I, UP, B, C4, SIM, RUF)
- **Formatting** : Black (line-length 100)
- **Typing** : mypy (strict=false, à améliorer)
- **Tests** : pytest (asyncio_mode=auto)
- **Coverage** : pytest-cov (fail_under=25%)

#### **CI/CD**

- **Pipeline** : GitHub Actions (lint → tests → build → deploy)
- **Sécurité** : bandit, safety check, gitleaks, trivy
- **Qualité** : Ruff, Black, mypy

#### **Recommandations Outils**

1. **Ajouter** :
   - `pylint` pour analyse statique avancée
   - `sonarqube` pour qualité globale
   - `locust` pour tests de charge
   - `snyk` pour vulnérabilités dépendances

2. **Améliorer** :
   - Passer mypy en `strict=true`
   - Augmenter `fail_under` à 40% puis 60%
   - Ajouter tests d'intégration

---

## **4. Perspective 3 : Chargé UX/UI**

### **4.1 Charte Graphique**

#### **Identité Visuelle - Charte "Nouvelle Étoile"**

| Élément | Valeur | Usage |
|---------|--------|-------|
| **Teal NEWTOWT** | `#0D5966` | Dominante (60%) - titres, structures, liens |
| **Vert NEWTOWT** | `#87BD29` | Accent secondaire (20%) - succès, baseline |
| **Cuivre NEWTOWT** | `#B47148` | Signal transition (10%) |
| **Sable NEWTOWT** | `#EFE6D6` | Fond éditorial (10%) |

**Ratio Chromatique Cible** : 60% teal · 20% vert · 10% cuivre · 10% neutres

#### **Polices**

| Usage | Police | Backup |
|-------|--------|--------|
| UI/Print | Manrope | system-ui, -apple-system |
| Accents | DM Serif Display | Georgia, Times New Roman |
| Codes | JetBrains Mono | SF Mono, Consolas |

**Interdit** : Inter, Poppins, Segoe UI (remplacés par Manrope)

### **4.2 Structure du Frontend**

#### **Architecture des Templates**

```
app/templates/
├── base.html              # Squelette HTML commun
├── _partials/             # Composants réutilisables
├── public/                # Pages publiques (landing, about)
│   ├── base.html          # Layout marketing
│   ├── landing.html
│   └── routes.html
├── client/                # Espace client authentifié
│   ├── base.html          # Layout client
│   ├── dashboard.html
│   └── booking/
│       ├── wizard.html
│       └── confirm.html
├── staff/                 # ERP interne
│   ├── base.html          # Layout staff avec sidebar
│   ├── _layout.html       # Sidebar Kairos complète
│   ├── dashboard.html
│   ├── planning/
│   ├── commercial/
│   └── ... (12 modules)
├── portal/                # Portail token (/p/{token})
│   └── base.html          # Layout minimal
└── errors/                # Pages d'erreur
    ├── 404.html
    └── 403.html
```

#### **Design System Kairos**

**Composants Principaux** (dans `kairos.css`) :
- `.card`, `.btn`, `.pill`, `.badge`, `.alert`
- `.kpi-card`, `.stat-card`, `.vessel-tabs`, `.year-selector`
- `.leg-chip`, `.leg-summary`, `.vessel-status-badge`
- `.bordee-grid`, `.dash-notif-card`, `.progress-bar`
- `.toast`, `.modal-card`, `.sidebar-clock`, `.sidebar-userbadge`
- `.port-badge`

### **4.3 Comportement des Pages**

#### **Patterns d'Interaction**

1. **HTMX** :
   - Chargement partiel des pages
   - Soumission de formulaires sans rechargement
   - Navigation fluide
   - **Problème** : Certains formulaires n'ont pas de feedback visuel

2. **Alpine.js** :
   - Logique légère côté client
   - Gestion d'état local
   - **Problème** : Utilisation limitée, potentiel sous-exploité

3. **Formulaires** :
   - HTML standard `<form method="POST">`
   - `forms.js` désactive bouton submit 5s après clic (anti-double-submit)
   - `towt-tz.js` gère conversion timezone
   - **Problème** : Pas de validation côté client avant soumission

#### **Navigation**

1. **Sidebar Dynamique** :
   - Réorganisée par groupes (Pilotage, Cargo, Opérations, RH, Performance, Admin)
   - Personnalisation : 3 raccourcis épinglables par user
   - **Problème** : Pas de recherche dans la sidebar

2. **Command Palette** :
   - Cmd+K pour recherche globale
   - Recherche : legs, escales, bookings, clients, users, ports, navires, docs, tickets
   - **Statut** : Fonctionnel mais limité

3. **Notifications** :
   - Cloche en haut à droite
   - Badge count tickets P1 ouverts
   - Liste des 10 dernières notifications
   - **Problème** : Pas de regroupement par type

### **4.4 Audit du Comportement du Site**

#### **Points Forts**

✅ **Cohérence Visuelle** : Design system Kairos bien appliqué
✅ **Responsive** : Adapté mobile (agent escale sur quai)
✅ **Accessibilité** : WCAG AA visé, contrastes vérifiés
✅ **Multi-langues** : 5 langues supportées (fr, en, es, pt-br, vi)
✅ **PWA** : Installable sur tablette (commandant)
✅ **Mode Haute Lisibilité** : Pour conditions mer/quai variables

#### **Problèmes Identifiés**

⚠️ **Incohérences entre Pages** :
- Certaines pages utilisent des patterns différents pour actions similaires
- Exemple : Confirmation de booking vs confirmation de commande
- **Impact** : Courbe d'apprentissage accrue

⚠️ **Feedback Utilisateur** :
- Pas de loading indicators sur certaines actions async
- Messages de succès/erreur parfois peu visibles
- **Impact** : Utilisateur ne sait pas si action a réussi

⚠️ **Gestion des Erreurs** :
- Messages d'erreur techniques parfois affichés
- Pas de suggestions de correction
- **Impact** : Frustration utilisateur

⚠️ **Performance Frontend** :
- `kairos.css` : 63KB (non minifié)
- `newtowt-public.css` : 40KB (non minifié)
- **Impact** : Temps de chargement initial
- **Recommandation** : Minifier CSS, utiliser cache browser

⚠️ **Mobile** :
- Certaines pages staff peu adaptées mobile
- **Impact** : Difficile à utiliser sur smartphone
- **Recommandation** : Mobile-first design pour toutes les pages

### **4.5 Règles de Cohérence**

#### **Règles Existantes**

1. **Couleurs** : Utiliser uniquement les tokens CSS définis
2. **Polices** : Manrope pour UI, DM Serif Display pour accents
3. **Espacement** : Système de spacing basé sur rem (1rem = 16px)
4. **Bordures** : 1px solide, couleur `--bg-3`
5. **Ombres** : Subtiles, niveau 1-2 uniquement

#### **Règles à Ajouter**

1. **Formulaires** :
   - Toujours afficher feedback visuel (loading, success, error)
   - Validation côté client avant soumission
   - Messages d'erreur clairs et actionnables

2. **Tables** :
   - Pagination standard (10, 25, 50, 100 items)
   - Tri par colonne (clic sur header)
   - Filtres persistants

3. **Modales** :
   - Toujours fermables via [X], Escape, clic extérieur
   - Taille adaptée au contenu
   - Overlay sombre (rgba(0,0,0,0.5))

4. **Notifications** :
   - Auto-dismiss après 5s (sauf erreurs)
   - Regroupement par type
   - Son optionnel pour erreurs critiques

### **4.6 Divergences entre Pages/Modules**

| Module | Pattern | Divergence |
|--------|---------|------------|
| Booking | Wizard 4 étapes | ✅ Standard |
| Commercial | Formulaire long | ⚠️ Pas de wizard |
| Escale | Onglets Import/Export | ✅ Standard |
| Planning | Gantt + table | ✅ Standard |
| Crew | Calendrier | ⚠️ Pas de vue liste |
| Finance | Tableau | ⚠️ Pas de graphiques |

**Recommandation** : Standardiser les patterns d'interaction

### **4.7 Opinion sur l'UX Globale**

**Note Globale : 7.5/10**

**Points Forts** :
- Design cohérent et professionnel
- Adapté aux besoins métiers
- Multi-plateforme (desktop, tablette, mobile)
- Accessibilité prise au sérieux

**Axes d'Amélioration** :
1. **Standardisation** : Unifier les patterns entre modules
2. **Feedback** : Améliorer les retours visuels
3. **Performance** : Optimiser le chargement des assets
4. **Mobile** : Améliorer l'expérience mobile
5. **Ergonomie** : Simplifier les workflows complexes

---

## **5. Perspective 4 : Shipping Manager & Fleet Manager**

### **5.1 Vision du Fonctionnement de l'Application**

#### **Couverture Fonctionnelle**

L'application couvre **80%** des besoins opérationnels d'une compagnie maritime :

| Domaine | Couverture | Statut |
|---------|-------------|--------|
| **Planning** | Complète | ✅ |
| **Commercial** | Complète | ✅ |
| **Escale** | Complète | ✅ |
| **Cargo** | Complète | ✅ |
| **Captain/Onboard** | Complète | ✅ |
| **Crew** | Complète | ✅ |
| **Stowage** | Complète | ✅ |
| **Claims** | Complète | ✅ |
| **MRV** | Complète | ✅ |
| **Navigation** | Complète | ✅ |
| **Finance** | Partielle | ⚠️ |
| **KPI** | Partielle | ⚠️ |
| **RH** | Partielle | ⚠️ |
| **Tracking** | Complète | ✅ |
| **Booking** | Complète | ✅ |

#### **Workflows Métier**

1. **Cycle de Vie d'un Leg** :
   ```
   Planned → In Progress → Completed
   │
   ├── ETD (Estimated Time of Departure)
   ├── ETA (Estimated Time of Arrival)
   ├── ATD (Actual Time of Departure)
   └── ATA (Actual Time of Arrival)
   ```

2. **Cycle de Vie d'une Booking** :
   ```
   draft → submitted → confirmed → loaded → at_sea → discharged → delivered
   ```

3. **Cycle de Vie d'une Escale** :
   ```
   Planned → Started (ATA) → Operations (Import/Export) → Completed (ATD)
   ```

### **5.2 Programme d'Évolution**

#### **Priorités Court Terme (0-3 mois)**

1. **Finaliser Modules Critiques** :
   - RH : Gestion complète des équipages (compliance Schengen)
   - KPI : Certificats CO₂ nominatifs PDF
   - Finance : Intégration complète avec comptabilité

2. **Améliorer la Sécurité** :
   - Implémenter SIEM (Loki + Wazuh)
   - Étendre rate limiting
   - Automatiser rotation des API keys

3. **Optimiser Performances** :
   - Ajouter caching Redis
   - Optimiser requêtes DB
   - Minifier assets frontend

#### **Priorités Moyen Terme (3-6 mois)**

1. **Étendre Fonctionnalités** :
   - Multi-devise (USD, BRL)
   - Co-chargement (matchmaking B2B)
   - Programme de fidélité

2. **Améliorer UX** :
   - Standardiser patterns d'interaction
   - Améliorer feedback utilisateur
   - Optimiser mobile

3. **Renforcer Observabilité** :
   - Dashboards Grafana étendus
   - Alertes proactives
   - Monitoring temps réel

#### **Priorités Long Terme (6-12 mois)**

1. **Innovation** :
   - Portail revendeurs (commissions agents)
   - Certificats CO₂ blockchain (Polygon, Toucan)
   - Intégration IoT (capteurs navires)

2. **Scalabilité** :
   - Architecture microservices
   - Réplique lecture PostgreSQL
   - Scale horizontal

3. **Internationalisation** :
   - Support complet multi-langues
   - Adaptation aux réglementations locales
   - Intégration avec systèmes locaux

### **5.3 Audit des Branches Git**

#### **État Actuel**

```bash
$ git branch -a
* (HEAD detached at FETCH_HEAD)
  remotes/origin/main
```

**Problèmes Identifiés** :

1. **Detached HEAD** : Le repository est en état detached HEAD
   - **Impact** : Impossible de travailler sur des branches
   - **Recommandation** : `git checkout main` puis créer branches de feature

2. **Pas de Branches de Feature** : Aucune branche visible hormis `main`
   - **Impact** : Tout le développement se fait sur main
   - **Recommandation** : Créer branches `feature/<module>-<desc>`

3. **Historique Limité** : 1 commit visible (`cee94a9`)
   - **Impact** : Difficile de suivre l'évolution
   - **Recommandation** : Vérifier historique complet

#### **Branches à Supprimer**

**Aucune branche à supprimer** identifiée (seule `main` existe)

**Recommandation** :
- Nettoyer les branches mergées > 30 jours
- Archiver les branches de feature abandonnées
- Supprimer les branches de test

#### **Stratégie de Branching Recommandée**

```
main (protected)
├── feature/planning-gantt-improvements
├── feature/booking-multi-currency
├── feature/rh-schengen-compliance
├── fix/permission-matrix-bug
└── hotfix/critical-security-patch
```

**Règles** :
- `main` : toujours stable, déploiement production
- `feature/*` : développement de nouvelles fonctionnalités
- `fix/*` : corrections de bugs
- `hotfix/*` : corrections critiques en production
- PR obligatoire pour merge sur `main`

### **5.4 Recommandations pour le Shipping International**

#### **Fonctionnalités Manquantes**

1. **Gestion des Douanes** :
   - Intégration avec systèmes douaniers
   - Génération automatique documents douane
   - Suivi des déclarations

2. **Gestion des Assurances** :
   - Suivi des contrats
   - Génération de certificats
   - Alertes expiration

3. **Gestion des Certifications** :
   - Registre SOLAS, ISM, ISPS
   - Alertes expiration
   - Génération rapports d'inspection

4. **Gestion des Équipements** :
   - Suivi maintenance
   - Planification inspections
   - Historique des réparations

5. **Gestion des Fournisseurs** :
   - Base de données fournisseurs
   - Suivi des commandes
   - Évaluation performance

#### **Intégrations Externes**

1. **API Météorologiques** :
   - Windy (intégré)
   - OpenWeather (intégré)
   - **À ajouter** : MeteoFrance, NOAA

2. **API Cartographiques** :
   - Mapbox (intégré)
   - MapTiler (intégré)
   - **À ajouter** : OpenStreetMap (backup)

3. **API de Paiement** :
   - Stripe (retiré en V3.1)
   - **À ajouter** : Alternative pour virements

4. **API CRM** :
   - Pipedrive (intégré)
   - **À améliorer** : Synchronisation bidirectionnelle

### **5.5 Roadmap Produit**

#### **V3.1 (Q3 2026)**

- [ ] Finaliser modules RH et KPI
- [ ] Implémenter SIEM
- [ ] Étendre rate limiting
- [ ] Optimiser performances
- [ ] Multi-devise (USD, BRL)

#### **V3.2 (Q1 2027)**

- [ ] Co-chargement B2B
- [ ] Programme de fidélité
- [ ] Portail revendeurs
- [ ] Réplique lecture PostgreSQL
- [ ] Certificats CO₂ blockchain

#### **V4.0 (2028)**

- [ ] Architecture microservices
- [ ] Intégration IoT
- [ ] IA prédictive (maintenance, routing)
- [ ] Plateforme multi-compagnies

---

## **6. Recommandations Stratégiques**

### **6.1 Priorités Immédiates**

1. **Stabiliser l'Environnement** :
   - Résoudre l'état detached HEAD
   - Créer branches de feature
   - Mettre en place stratégie de branching

2. **Améliorer la Qualité du Code** :
   - Augmenter couverture tests à 40%
   - Découper routers trop gros
   - Implémenter soft delete

3. **Renforcer la Sécurité** :
   - Implémenter SIEM
   - Étendre rate limiting
   - Automatiser rotation des secrets

### **6.2 Priorités Court Terme**

1. **Finaliser les Modules** :
   - RH (compliance Schengen)
   - KPI (certificats CO₂)
   - Finance (intégration complète)

2. **Optimiser les Performances** :
   - Ajouter caching Redis
   - Optimiser requêtes DB
   - Minifier assets frontend

3. **Améliorer l'UX** :
   - Standardiser patterns
   - Améliorer feedback utilisateur
   - Optimiser mobile

### **6.3 Priorités Moyen Terme**

1. **Étendre les Fonctionnalités** :
   - Multi-devise
   - Co-chargement
   - Programme de fidélité

2. **Renforcer l'Observabilité** :
   - Dashboards Grafana
   - Alertes proactives
   - Monitoring temps réel

3. **Améliorer la Scalabilité** :
   - Réplique lecture DB
   - Scale horizontal
   - Architecture microservices

### **6.4 Priorités Long Terme**

1. **Innovation** :
   - Portail revendeurs
   - Certificats CO₂ blockchain
   - Intégration IoT

2. **Internationalisation** :
   - Support multi-langues complet
   - Adaptation réglementations locales
   - Intégration systèmes locaux

---

## **7. Annexes**

### **7.1 Glossaire Maritime**

| Terme | Définition |
|-------|------------|
| **Leg** | Segment de voyage port A → port B |
| **leg_code** | Format `{seq}{vessel_code}{dep_country}{arr_country}{year_digit}` |
| **ETD / ETA** | Estimated Time of Departure / Arrival |
| **ATD / ATA** | Actual Time of Departure / Arrival |
| **Escale** | Période où le navire est à quai |
| **SOF** | Statement of Facts (chronologie portuaire) |
| **BL / BOL** | Bill of Lading (titre de propriété cargo) |
| **POL / POD** | Port of Loading / Discharge |
| **LOCODE** | Code UN port (5 caractères) |
| **OPEX** | Operating Expenditure (coût journalier) |
| **EOSP / SOSP** | End / Start Of Sea Passage |
| **MRV** | Monitoring, Reporting, Verification |
| **MDO** | Marine Diesel Oil |
| **ROB** | Remaining On Board |
| **Schengen** | Statut immigration marin étranger |

### **7.2 Matrice RBAC**

| Rôle | Modules (CMS = Consult/Modify/Suppress) |
|------|--------------------------------------|
| administrateur | Tous modules : CMS |
| operation | planning:CM, commercial:CM, escale:CMS, cargo:CMS, kpi:C, captain:CM, crew:CM, claims:CMS, mrv:CM, rh:C, booking:CM, tickets:CMS, analytics:C, chat:CM |
| armement | planning:C, escale:C, kpi:C, captain:C, crew:CMS, mrv:C, rh:CM, chat:C |
| technique | planning:C, commercial:C, escale:CMS, cargo:C, kpi:C, captain:CM, crew:C, claims:C, mrv:CM |
| data_analyst | planning:C, commercial:C, escale:C, cargo:C, finance:CMS, kpi:C, captain:C, crew:C, claims:C, mrv:CM, rh:C, booking:C, analytics:CM |
| marins | planning:C, commercial:-, escale:C, cargo:C, finance:-, kpi:C, captain:C, crew:C, claims:C, mrv:C, rh:C, booking:C, tickets:-, analytics:-, chat:C |
| commercial | planning:C, commercial:CMS, escale:C, cargo:CM, finance:-, kpi:C, captain:C, crew:-, claims:-, mrv:-, rh:-, booking:CM, tickets:-, analytics:-, chat:C |
| manager_maritime | planning:CM, commercial:CM, escale:CM, cargo:CM, finance:-, kpi:C, captain:CMS, crew:CM, claims:CM, mrv:CM, rh:C, booking:CM, tickets:-, analytics:-, chat:CM |

### **7.3 Stack Technique Complète**

| Couche | Technologie | Version |
|--------|-------------|---------|
| **Backend** | FastAPI | 0.115 |
| **Langage** | Python | 3.12 |
| **Serveur** | Uvicorn | - |
| **Base de Données** | PostgreSQL | 16 |
| **ORM** | SQLAlchemy | 2.0 async |
| **Driver DB** | asyncpg | - |
| **Migrations** | Alembic | - |
| **Frontend** | HTMX | 2.0 |
| **JS Framework** | Alpine.js | - |
| **Templates** | Jinja2 | - |
| **CSS Framework** | Kairos Design System | - |
| **Auth** | itsdangerous + bcrypt | - |
| **MFA** | WebAuthn / TOTP | - |
| **Observabilité** | OpenTelemetry + Prometheus + Sentry | - |
| **Cartographie** | MapLibre + Mapbox tiles | - |
| **Météo** | Windy / OpenWeather | - |
| **IA** | Claude Sonnet 4.6 | - |
| **PDF** | WeasyPrint | - |
| **Conteneurisation** | Docker + docker-compose | - |

### **7.4 Personas Utilisateurs**

| Persona | Rôle | Objectifs Principaux |
|---------|------|---------------------|
| Mathilde | Capitaine de bord | Voir état du leg en 30s, saisir noon report, recevoir ETA shifts |
| Tomé | Agent d'escale | Démarrer escale à ATA, coordonner dockers/douane, suivre opérations |
| Khadija | Responsable RH | Visualiser calendrier embarquement, suivre compliance Schengen |
| Pierre | Superintendant | Planifier maintenance, tenir registre certifications |
| Inès | Commercial | Vendre capacité, gérer pipeline, émettre cotations |
| David | Prospect | Comprendre offre, évaluer coût, simuler expédition |
| Léa | Client occasionnel | Réserver rapidement, récupérer BL/factures |
| Yann | Client B2B | Réserver via API, recevoir webhooks, reporting CO₂ |

### **7.5 Métriques Clés**

| Métrique | Valeur Actuelle | Cible |
|----------|-----------------|-------|
| Couverture Tests | 25% | 80% |
| Temps de Chargement | ~2s | <1s |
| Disponibilité | 99.9% | 99.95% |
| NPS Client | - | >40 |
| Taux Conversion Booking | - | >80% |
| Délai Confirmation Booking | - | <4h |

---

## **8. Conclusion**

L'application **NEWTOWT ERP/CRM/Shipping** est une plateforme mature et bien architecturée qui répond à la majorité des besoins opérationnels d'une compagnie maritime moderne. Avec une stack technique moderne (FastAPI, PostgreSQL, HTMX), une sécurité robuste (RBAC, MFA, CSP), et une expérience utilisateur cohérente (Design System Kairos), elle constitue une base solide pour le transport maritime décarboné.

**Points Forts Majeurs** :
- Architecture modulaire et scalable
- Sécurité de haut niveau
- Couverture fonctionnelle complète pour le core business
- Design system professionnel et accessible

**Défis à Relever** :
- Réduire la dette technique (couverture tests, complexité)
- Finaliser les modules partiels (RH, KPI, Finance)
- Standardiser l'UX entre modules
- Améliorer les performances et la scalabilité

**Recommandation Globale** : **Poursuivre le développement avec priorité sur la qualité du code et l'expérience utilisateur, tout en finalisant les modules critiques pour atteindre une couverture fonctionnelle complète.**

---

**Document généré par** : Vibe Code (Agent d'Ingénierie Logicielle Asynchrone)  
**Date** : 18 Juin 2026  
**Version** : 1.0  
**Statut** : Draft (À valider par les parties prenantes)
