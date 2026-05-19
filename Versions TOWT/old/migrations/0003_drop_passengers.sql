-- ════════════════════════════════════════════════════════════════
-- v3.0.0 — Drop passenger module tables
-- Suite à la restructuration juridique (NEWTOWT post-TOWT liquidation),
-- l'activité passager n'est plus opérée. Cette migration supprime les
-- tables et colonnes liées.
--
-- ⚠️  IRRÉVERSIBLE — exécuter UNIQUEMENT après :
--   1. Backup complet de la base (`./backup.sh`)
--   2. Export RGPD préalable des données passagers actives
--      (cf. archives RGPD du 2026-Q1 avant exécution)
--   3. Validation du déploiement applicatif v3.0.0 sur staging
--
-- Exécution :
--   docker exec -i towt-app-v2-db psql -U towt_admin -d towt_planning < migrations/0003_drop_passengers.sql
-- ════════════════════════════════════════════════════════════════

BEGIN;

-- 1. Drop FK columns on related tables -------------------------------
ALTER TABLE IF EXISTS claims              DROP COLUMN IF EXISTS passenger_id;
ALTER TABLE IF EXISTS notifications       DROP COLUMN IF EXISTS booking_id;
ALTER TABLE IF EXISTS portal_messages     DROP COLUMN IF EXISTS booking_id;
ALTER TABLE IF EXISTS portal_access_logs  DROP COLUMN IF EXISTS booking_id;

-- 2. Drop passenger SOF event types ----------------------------------
DELETE FROM sof_events WHERE event_type IN
    ('PAX_EMBARK', 'PAX_DISEMBARK', 'PAX_SAFETY_DRILL', 'PAX_MUSTER');

-- 3. Drop passenger notifications (legacy types) ---------------------
DELETE FROM notifications WHERE type IN
    ('new_passenger_message', 'new_passenger_booking');

-- 4. Drop the tables themselves (CASCADE pour les FK reverse) --------
DROP TABLE IF EXISTS satisfaction_responses    CASCADE;
DROP TABLE IF EXISTS preboarding_forms         CASCADE;
DROP TABLE IF EXISTS passenger_audit_logs      CASCADE;
DROP TABLE IF EXISTS passenger_documents       CASCADE;
DROP TABLE IF EXISTS passenger_payments        CASCADE;
DROP TABLE IF EXISTS passengers                CASCADE;
DROP TABLE IF EXISTS passenger_bookings        CASCADE;
DROP TABLE IF EXISTS cabin_price_grid          CASCADE;

-- 5. Cleanup uploads directory (run separately as root on the host) --
-- rm -rf /app/uploads/passenger_docs/

COMMIT;

-- Sanity check
SELECT 'Tables passagers restantes :' AS check_label,
       COUNT(*) AS remaining_count
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name LIKE 'passenger%' OR table_name = 'cabin_price_grid';
