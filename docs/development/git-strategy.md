# Stratégie Git - NEWTOWT

> **Version** : 1.0.0  
> **Date** : 18 Juin 2026  
> **Responsable** : Équipe DevOps  
> **Statut** : ✅ Implémentée

---

## 📌 **Principe de Base**

**Toute modification doit passer par une Pull Request (PR) avant d'être mergée sur `main`.**

---

## 🌿 **Structure des Branches**

### **Branches Protégées**

| Branche | Description | Protection | Déploiement |
|---------|-------------|------------|-------------|
| `main` | Code de production | ✅ **Protected** | Production |
| `staging` | Code de pré-production | ✅ **Protected** | Staging |

**Règles de protection** :
- ❌ Pas de push direct (sauf pour `dependabot`)
- ✅ Requiert **2 approbations** minimum
- ✅ Requiert **PR template** rempli
- ✅ Requiert **CI/CD passing**
- ✅ Requiert **review de sécurité** pour les changements sensibles

---

## 🏗️ **Nommage des Branches**

### **Format Général**

```
<type>/<scope>-<description>
```

| Type | Description | Exemple |
|------|-------------|---------|
| `feature/` | Nouvelle fonctionnalité | `feature/booking-multi-currency` |
| `fix/` | Correction de bug | `fix/permission-matrix-bug` |
| `hotfix/` | Correction critique (production) | `hotfix/security-patch-cve-2026` |
| `chore/` | Tâches de maintenance | `chore/cleanup-branches` |
| `docs/` | Documentation | `docs/update-architecture-diagrams` |
| `refactor/` | Refactoring de code | `refactor/split-commercial-router` |
| `test/` | Ajout/modification de tests | `test/add-booking-coverage` |

### **Règles de Nommage**

1. **Utiliser des tirets** (`-`) pour séparer les mots
2. **Éviter les majuscules** (sauf acronymes : `mrv`, `co2`)
3. **Être descriptif** : la description doit expliquer le **quoi** et le **pourquoi**
4. **Limiter à 50 caractères** maximum
5. **Pas de préfixes numériques** (ex: `1-feature/...`)

### **Exemples Valides**

```bash
# ✅ Bon
feature/planning-gantt-improvements
fix/booking-capacity-calculation
hotfix/csrf-vulnerability-patch
chore/update-dependencies
refactor/split-admin-router
test/add-kpi-service-coverage

# ❌ Mauvais
feature/planning  # Trop vague
Fix/booking_bug   # Majuscule + underscore
1-feature/add-booking  # Préfixe numérique
very-long-branch-name-that-exceeds-the-50-characters-limit  # Trop long
```

---

## 🔄 **Workflow de Développement**

### **1. Créer une Nouvelle Fonctionnalité**

```bash
# Se mettre à jour avec main
git checkout main
git pull origin main

# Créer une nouvelle branche
git checkout -b feature/booking-multi-currency

# Développer, commiter, pusher
git add .
git commit -m "feat(booking): add multi-currency support"
git push -u origin feature/booking-multi-currency

# Créer une PR sur GitHub
# → Remplir le template PR
# → Attendre les reviews
# → Merge après approbations
```

### **2. Corriger un Bug**

```bash
# Créer une branche de fix
git checkout -b fix/permission-matrix-bug

# Corriger le bug, tester
git add .
git commit -m "fix(permissions): correct matrix override logic"
git push -u origin fix/permission-matrix-bug

# Créer PR avec label "bug"
```

### **3. Hotfix en Production**

```bash
# Créer une branche hotfix depuis main
git checkout main
git pull origin main
git checkout -b hotfix/security-patch-cve-2026

# Appliquer le fix, tester
git add .
git commit -m "fix(security): patch CVE-2026-XXXX vulnerability"
git push -u origin hotfix/security-patch-cve-2026

# Créer PR avec label "hotfix"
# → Requiert approbation immédiate
# → Déploiement rapide après merge
```

---

## 🤖 **Git Hooks**

### **pre-commit** (`.git/hooks/pre-commit`)

Exécuté avant chaque commit :

1. ✅ **Ruff** : Linting du code (E, F, W, I, UP, B, C4, SIM, RUF)
2. ✅ **Black** : Vérification du formatting (line-length 100)
3. ✅ **bandit** : Analyse de sécurité (vulnérabilités Python)
4. ✅ **gitleaks** : Détection de secrets (API keys, passwords)

**Si un hook échoue, le commit est bloqué.**

### **pre-push** (`.git/hooks/pre-push`)

Exécuté avant chaque push :

1. ✅ **pytest** : Exécution des tests unitaires
2. ✅ **coverage** : Vérification du seuil de couverture (25% minimum)
3. ⚠️ **TODO/FIXME** : Avertissement (non bloquant)

**Si un hook échoue, le push est bloqué.**

---

## 🧹 **Nettoyage des Branches**

### **Quand Supprimer une Branche ?**

| Condition | Action |
|-----------|--------|
| Branche **mergée** sur `main` | Supprimer après **7 jours** |
| Branche **abandonnée** (pas de commit depuis >30 jours) | Supprimer immédiatement |
| Branche **hotfix** mergée | Supprimer après **24h** |
| Branche **feature** en cours | Conserver |

### **Commandes de Nettoyage**

```bash
# Lister les branches mergées (sauf main et staging)
git branch --merged main | grep -v "main\|staging"

# Supprimer une branche locale
git branch -d feature/old-feature

# Supprimer une branche distante
git push origin --delete feature/old-feature

# Nettoyer toutes les branches mergées (attention !)
git branch --merged main | grep -v "main\|staging" | xargs git branch -d
```

### **Automatisation**

Un script de nettoyage automatique est disponible :

```bash
# À exécuter mensuellement
./scripts/git-cleanup.sh
```

---

## 📝 **Conventions de Commit**

### **Format**

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### **Types de Commit**

| Type | Description | Exemple |
|------|-------------|---------|
| `feat` | Nouvelle fonctionnalité | `feat(booking): add multi-currency support` |
| `fix` | Correction de bug | `fix(permissions): correct matrix override` |
| `chore` | Tâche de maintenance | `chore: update dependencies` |
| `docs` | Documentation | `docs: add git strategy guide` |
| `test` | Tests | `test: add booking workflow tests` |
| `refactor` | Refactoring | `refactor: split commercial router` |
| `perf` | Optimisation performance | `perf: add Redis caching` |
| `security` | Correction de sécurité | `security: patch XSS vulnerability` |
| `ci` | CI/CD | `ci: add pre-commit hooks` |

### **Scopes**

Utiliser le nom du module ou du composant :
- `booking`, `planning`, `commercial`, `cargo`, `escale`
- `auth`, `permissions`, `database`, `api`
- `frontend`, `backend`, `infra`

### **Exemples**

```bash
# ✅ Bon
git commit -m "feat(booking): add multi-currency support"
git commit -m "fix(permissions): correct matrix override logic"
git commit -m "chore: update Python dependencies"
git commit -m "docs: add architecture diagrams"
git commit -m "test(booking): add workflow transition tests"

# ❌ Mauvais
git commit -m "added multi-currency"  # Pas de type/scope
git commit -m "fix"  # Trop vague
git commit -m "WIP: booking stuff"  # Pas descriptif
```

---

## 🔒 **Gestion des Conflits**

### **Résolution des Conflits**

1. **Mettre à jour sa branche** avec `main` :
   ```bash
   git fetch origin
   git rebase origin/main
   ```

2. **Résoudre les conflits** manuellement (marqueurs `<<<<<<<`, `=======`, `>>>>>>>`)

3. **Continuer le rebase** :
   ```bash
   git add <fichiers-resolus>
   git rebase --continue
   ```

4. **Si abandon** :
   ```bash
   git rebase --abort
   ```

### **Bonnes Pratiques**

- ✅ **Rebaser souvent** : `git rebase origin/main` quotidiennement
- ✅ **Commits atomiques** : Un commit = une modification logique
- ✅ **Messages clairs** : Expliquer le **quoi** et le **pourquoi**
- ❌ **Éviter les merges** : Préférer `rebase` à `merge`

---

## 🛡️ **Sécurité Git**

### **Protection des Secrets**

- ❌ **Ne jamais commiter** :
  - Mots de passe
  - Clés API (Stripe, Anthropic, etc.)
  - Tokens (JWT, session)
  - Certificats privés
  - Fichiers `.env`

- ✅ **Utiliser** :
  - `.gitignore` pour exclure les fichiers sensibles
  - **gitleaks** (intégré dans pre-commit)
  - **GitHub Secret Scanning**

### **Rotation des Clés**

- **API Keys** : Rotation tous les **90 jours**
- **Secrets** : Rotation après **départ d'un employé**
- **Certificats** : Rotation **annuelle**

---

## 📊 **Métriques et Reporting**

### **Tableau de Bord Git**

| Métrique | Cible | Actuel |
|----------|-------|--------|
| Nombre de branches actives | < 20 | - |
| Branches mergées non supprimées | 0 | - |
| Branches abandonnées (>30j) | 0 | - |
| Taux de PR mergées | > 80% | - |
| Temps moyen de review | < 24h | - |

### **Rapports Automatiques**

- **Mensuel** : Statistiques d'activité Git (via GitHub Insights)
- **Trimestriel** : Audit des branches et nettoyage
- **Annuel** : Revue complète de la stratégie Git

---

## 🆘 **Dépannage**

### **Problèmes Courants**

| Problème | Solution |
|----------|----------|
| `detached HEAD` | `git checkout main` puis recréer la branche |
| Conflits de merge | `git rebase origin/main` + résoudre conflits |
| Hooks bloquants | Vérifier les erreurs et corriger le code |
| Push rejeté | `git pull --rebase` puis retry |
| Branche protégée | Créer une PR au lieu de pusher directement |

### **Contacts**

| Rôle | Contact | Responsabilités |
|------|---------|------------------|
| **Git Admin** | @git-admin | Gestion des branches, hooks, protections |
| **DevOps** | @devops-team | CI/CD, déploiements |
| **Sécurité** | @security-team | Review des PR sensibles |

---

## 📚 **Ressources**

- [Conventional Commits](https://www.conventionalcommits.org/)
- [GitHub Flow](https://docs.github.com/en/get-started/quickstart/github-flow)
- [Git Best Practices](https://git-scm.com/book/en/v2/Distributed-Git-Distributed-Workflows)
- [NEWTOWT Contributing Guide](CONTRIBUTING.md)

---

## 📝 **Historique des Versions**

| Version | Date | Auteur | Changements |
|---------|------|--------|-------------|
| 1.0.0 | 18 Juin 2026 | Vibe Code | Création initiale |

---

**Document maintenu par** : Équipe DevOps NEWTOWT  
**Dernière mise à jour** : 18 Juin 2026  
**Prochaine révision** : 18 Juillet 2026
