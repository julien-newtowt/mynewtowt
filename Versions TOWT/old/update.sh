#!/bin/bash
# ============================================================
# my_TOWT — Script de mise à jour
# Usage: chmod +x update.sh && ./update.sh
# ============================================================

set -e

# Couleurs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Détecter docker compose (plugin) vs $DC (standalone)
if docker compose version > /dev/null 2>&1; then
    DC="docker compose"
elif command -v $DC > /dev/null 2>&1; then
    DC="$DC"
else
    error "Ni 'docker compose' ni '$DC' trouvé"
fi

echo ""
echo "════════════════════════════════════════════════"
echo "   my_TOWT — Mise à jour"
echo "════════════════════════════════════════════════"
echo ""

# ─── 1. Vérifications préalables ─────────────────────
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)
log "Répertoire : $PROJECT_DIR"

# Vérifier que Docker tourne
docker info > /dev/null 2>&1 || error "Docker n'est pas démarré"
log "Docker OK"

# Vérifier les conteneurs
if ! docker ps --format '{{.Names}}' | grep -q "towt-app-v2"; then
    warn "Le conteneur towt-app-v2 n'est pas en cours d'exécution"
    warn "Lancement initial ? Utilisez: $DC up -d --build"
fi

# ─── 2. Pull du code ─────────────────────────────────
echo ""
warn "Récupération du code..."

# Essayer de pull depuis la branche de dev, sinon main
BRANCH="claude/develop-chat-app-275SR"
git fetch origin "$BRANCH" 2>/dev/null && {
    git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
    git pull origin "$BRANCH"
    log "Code récupéré depuis $BRANCH"
} || {
    git pull origin main 2>/dev/null && log "Code récupéré depuis main" || warn "Pas de remote configuré, utilisation du code local"
}

# ─── 3. Rebuild de l'image Docker ────────────────────
echo ""
warn "Rebuild de l'image Docker (httpx ajouté aux dépendances)..."
$DC build --no-cache app
log "Image reconstruite"

# ─── 4. Redémarrage des services ─────────────────────
echo ""
warn "Redémarrage des services..."
$DC up -d
log "Services redémarrés"

# Attente que l'app soit prête
echo -n "   Attente de l'application"
for i in $(seq 1 30); do
    if docker exec towt-app-v2 python3 -c "print('ok')" > /dev/null 2>&1; then
        echo ""
        log "Application prête"
        break
    fi
    echo -n "."
    sleep 2
done

# ─── 5. Migration de la base de données ──────────────
echo ""
warn "Exécution des migrations SQL..."

docker exec towt-db psql -U "${POSTGRES_USER:-towt_admin}" -d "${POSTGRES_DB:-towt_planning}" -c "
-- 1. Table activity_log (journal d'activité)
CREATE TABLE IF NOT EXISTS activity_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    username VARCHAR(100),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    module VARCHAR(50) NOT NULL,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_activity_log_module ON activity_log(module);
CREATE INDEX IF NOT EXISTS ix_activity_log_id ON activity_log(id);

-- 2. Adresses structurées sur packing_list_batches
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS shipper_postal VARCHAR(20);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS shipper_city VARCHAR(100);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS shipper_country VARCHAR(100);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS notify_name VARCHAR(200);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS notify_postal VARCHAR(20);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS notify_city VARCHAR(100);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS notify_country VARCHAR(100);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS consignee_name VARCHAR(200);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS consignee_postal VARCHAR(20);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS consignee_city VARCHAR(100);
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS consignee_country VARCHAR(100);

-- 3. Description of goods pour Bill of Lading
ALTER TABLE packing_list_batches ADD COLUMN IF NOT EXISTS description_of_goods TEXT;
" && log "Migrations SQL appliquées" || warn "Erreur SQL (peut-être déjà appliquées)"

# ─── 6. Redémarrage final de l'app ───────────────────
echo ""
warn "Redémarrage final de l'application..."
docker restart towt-app-v2
sleep 3

# ─── 7. Vérification santé ───────────────────────────
echo ""
if curl -s -o /dev/null -w "%{http_code}" http://localhost/login | grep -q "200"; then
    log "Application accessible sur http://localhost"
else
    # Essai avec le port interne
    if docker exec towt-app-v2 curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login 2>/dev/null | grep -q "200"; then
        log "Application accessible (vérification interne OK)"
    else
        warn "L'application ne répond pas encore — vérifiez les logs :"
        warn "  docker logs towt-app-v2 --tail 20"
    fi
fi

# ─── Résumé ──────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "   Mise à jour terminée !"
echo "════════════════════════════════════════════════"
echo ""
echo "  Nouveautés :"
echo "  • Journal d'activité (Paramètres → Journal)"
echo "  • Adresses structurées cargo (shipper/notify/consignee)"
echo "  • Génération Arrival Notice (DOCX)"
echo "  • Timeline escale améliorée"
echo "  • CRUD navires complet (créer/modifier/supprimer)"
echo "  • Import ports UN/LOCODE"
echo ""
echo "  Accès : http://$(hostname -I | awk '{print $1}')"
echo "  Logs  : docker logs towt-app-v2 --tail 50"
echo ""
