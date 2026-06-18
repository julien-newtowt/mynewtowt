#!/bin/bash
# NEWTOWT Git Cleanup Script
# Nettoie les branches mergées et abandonnées
# Usage: ./scripts/git-cleanup.sh [--dry-run] [--force]

set -e

# Couleurs pour les messages
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Variables
DRY_RUN=false
FORCE=false
BRANCHES_TO_DELETE=()

# Fonction pour afficher l'aide
usage() {
    echo "Usage: $0 [--dry-run] [--force]"
    echo ""
    echo "Options:"
    echo "  --dry-run    Affiche les branches à supprimer sans les supprimer"
    echo "  --force      Supprime sans confirmation"
    echo ""
    echo "Exemples:"
    echo "  $0 --dry-run          # Simulation"
    echo "  $0                    # Mode interactif"
    echo "  $0 --force            # Suppression automatique"
    exit 1
}

# Parser les arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Option inconnue: $1"
            usage
            ;;
    esac
done

# Fonction pour confirmer
confirm() {
    if [ "$FORCE" = true ]; then
        return 0
    fi
    
    read -p "$1 [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi
    return 1
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  NEWTOWT Git Cleanup Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. Vérifier qu'on est sur main
echo -e "${YELLOW}✓ Vérification de la branche actuelle...${NC}"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo -e "${RED}ERREUR: Vous devez être sur la branche 'main' pour exécuter ce script.${NC}"
    echo ""
    echo "Pour basculer sur main:"
    echo "  git checkout main"
    exit 1
fi
echo -e "  Branche actuelle: ${GREEN}$CURRENT_BRANCH${NC}"
echo ""

# 2. Récupérer les dernières informations
echo -e "${YELLOW}✓ Récupération des informations depuis origin...${NC}"
git fetch --all --prune
echo ""

# 3. Trouver les branches mergées (sauf main et staging)
echo -e "${YELLOW}✓ Recherche des branches mergées sur main...${NC}"
MERGED_BRANCHES=$(git branch --merged main | grep -v "main\|staging" | sed 's/^[ *]*//')

if [ -z "$MERGED_BRANCHES" ]; then
    echo -e "  ${GREEN}Aucune branche mergée à nettoyer.${NC}"
else
    echo -e "  Branches mergées trouvées (${#MERGED_BRANCHES[@]}):"
    for branch in $MERGED_BRANCHES; do
        # Vérifier la date du dernier commit
        LAST_COMMIT_DATE=$(git log -1 --format="%cd" --date=iso $branch 2>/dev/null || echo "inconnu")
        echo -e "    - ${branch} (dernier commit: $LAST_COMMIT_DATE)"
        BRANCHES_TO_DELETE+=("$branch")
    done
fi
echo ""

# 4. Trouver les branches abandonnées (>30 jours sans commit)
echo -e "${YELLOW}✓ Recherche des branches abandonnées (>30 jours)...${NC}"
ABANDONED_BRANCHES=()

# Pour chaque branche locale (sauf main et staging)
for branch in $(git branch | grep -v "main\|staging" | sed 's/^[ *]*//'); do
    # Vérifier si la branche existe toujours sur remote
    if ! git ls-remote --exit-code --heads origin $branch > /dev/null 2>&1; then
        # Branche locale sans remote
        LAST_COMMIT_DATE=$(git log -1 --format="%cd" --date=iso $branch 2>/dev/null || echo "inconnu")
        LAST_COMMIT_EPOCH=$(git log -1 --format="%ct" $branch 2>/dev/null || echo "0")
        
        # Calculer l'âge en jours
        NOW_EPOCH=$(date +%s)
        AGE_DAYS=$(( (NOW_EPOCH - LAST_COMMIT_EPOCH) / 86400 ))
        
        if [ "$AGE_DAYS" -gt 30 ]; then
            echo -e "    - ${branch} (abandonnée, dernier commit il y a ${AGE_DAYS} jours)"
            ABANDONED_BRANCHES+=("$branch")
        fi
    fi
done

# Ajouter les branches abandonnées à la liste de suppression
BRANCHES_TO_DELETE+=("${ABANDONED_BRANCHES[@]}")

# 5. Trouver les branches distantes mergées
echo -e "${YELLOW}✓ Recherche des branches distantes mergées...${NC}"
REMOTE_MERGED_BRANCHES=()

for branch in $(git branch -r | grep -v "main\|staging" | sed 's/origin\///'); do
    if git merge-base --is-ancestor origin/main origin/$branch > /dev/null 2>&1; then
        echo -e "    - origin/${branch}"
        REMOTE_MERGED_BRANCHES+=("$branch")
    fi
done

# 6. Résumé
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  RÉSUMÉ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "  Branches locales à supprimer: ${#BRANCHES_TO_DELETE[@]}"
echo -e "  Branches distantes à supprimer: ${#REMOTE_MERGED_BRANCHES[@]}"
echo ""

# 7. Suppression
if [ ${#BRANCHES_TO_DELETE[@]} -eq 0 ] && [ ${#REMOTE_MERGED_BRANCHES[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ Aucune branche à supprimer.${NC}"
    exit 0
fi

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}Mode --dry-run: Aucune suppression effectuée.${NC}"
    exit 0
fi

# Supprimer les branches locales
if [ ${#BRANCHES_TO_DELETE[@]} -gt 0 ]; then
    echo -e "${RED}⚠ Suppression des branches locales:${NC}"
    for branch in "${BRANCHES_TO_DELETE[@]}"; do
        if [ -n "$branch" ]; then
            echo -e "  - Suppression de: ${branch}"
            if confirm "Supprimer la branche locale '$branch' ?"; then
                git branch -D "$branch"
                echo -e "    ${GREEN}✓ Supprimée${NC}"
            else
                echo -e "    ${YELLOW}⊘ Annulée${NC}"
            fi
        fi
    done
fi

# Supprimer les branches distantes
if [ ${#REMOTE_MERGED_BRANCHES[@]} -gt 0 ]; then
    echo -e "${RED}⚠ Suppression des branches distantes:${NC}"
    for branch in "${REMOTE_MERGED_BRANCHES[@]}"; do
        if [ -n "$branch" ]; then
            echo -e "  - Suppression de: origin/${branch}"
            if confirm "Supprimer la branche distante 'origin/${branch}' ?"; then
                git push origin --delete "$branch"
                echo -e "    ${GREEN}✓ Supprimée${NC}"
            else
                echo -e "    ${YELLOW}⊘ Annulée${NC}"
            fi
        fi
    done
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Nettoyage terminé!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 8. Statistiques finales
echo -e "${YELLOW}Statistiques:${NC}"
echo -e "  Branches locales: $(git branch | wc -l)"
echo -e "  Branches distantes: $(git branch -r | wc -l)"
echo ""
