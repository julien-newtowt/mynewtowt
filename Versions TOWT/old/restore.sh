#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# my_TOWT — Script de restauration de la base de données
# ═══════════════════════════════════════════════════════════════
#
# Usage: ./restore.sh <fichier_backup.sql.gz>
# ═══════════════════════════════════════════════════════════════

set -e

DB_CONTAINER="towt-db"
DB_USER="towt_admin"
DB_NAME="towt_planning"

if [ -z "$1" ]; then
    echo "Usage: $0 <fichier_backup.sql.gz>"
    echo ""
    echo "Sauvegardes disponibles:"
    ls -1t backups/towt_backup_*.sql.gz 2>/dev/null || echo "  Aucune sauvegarde trouvée dans backups/"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Fichier non trouvé: $BACKUP_FILE"
    exit 1
fi

echo "⚠ ATTENTION: Cette opération va REMPLACER toutes les données actuelles !"
echo "   Fichier: $BACKUP_FILE"
echo ""
read -p "Êtes-vous sûr ? (oui/NON) " REPLY
if [ "$REPLY" != "oui" ]; then
    echo "Restauration annulée."
    exit 0
fi

echo ""
echo "📦 Restauration en cours..."

# Décompresser si .gz
if [[ "$BACKUP_FILE" == *.gz ]]; then
    echo "   Décompression..."
    TMP_FILE="/tmp/towt_restore_$$.sql"
    gunzip -c "$BACKUP_FILE" > "$TMP_FILE"
else
    TMP_FILE="$BACKUP_FILE"
fi

# Copier dans le container et restaurer
docker cp "$TMP_FILE" "$DB_CONTAINER":/tmp/restore.sql

echo "   Suppression de la base existante..."
docker exec "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$DB_NAME"
docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$DB_NAME"

echo "   Import des données..."
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -f /tmp/restore.sql

# Nettoyage
docker exec "$DB_CONTAINER" rm -f /tmp/restore.sql
[[ "$BACKUP_FILE" == *.gz ]] && rm -f "$TMP_FILE"

echo ""
echo "✅ Restauration terminée !"
echo "   Redémarrage de l'application..."
docker restart towt-app-v2
echo "   ✓ Application redémarrée"
