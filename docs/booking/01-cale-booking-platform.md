# Plateforme de réservation d'espace en cale

> Étape 5 — `/vendor-review` puis spécification fonctionnelle et
> technique de la plateforme inspirée de CMA-CGM eBusiness.

## 1. Benchmark de la profession

### 1.1 Référence : CMA-CGM eBusiness

L'analyse de `https://www.cma-cgm.fr/ebusiness` met en évidence
9 capacités exposées au client :

| # | Capacité CMA-CGM | Équivalent NEWTOWT (V3) | Statut |
|---|------------------|------------------------|--------|
| 1 | Consultation des plannings de navigation | `/routes` + carte interactive | ✅ V3 |
| 2 | Recherche de routes et legs | Moteur de recherche `/routes` | ✅ V3 |
| 3 | Réservation d'emplacements (booking) | `/booking/new` wizard | ✅ V3 |
| 4 | Espace client (mes commandes) | `/me` dashboard | ✅ V3 |
| 5 | Factures | `/me/invoices` | ✅ V3 |
| 6 | Rapports d'émissions | `/me/co2` certificats | ✅ V3 |
| 7 | Documentation (BL, packing list) | `/me/documents` | ✅ V3 |
| 8 | Suivi des claims | `/me/claims` | ✅ V3 |
| 9 | Navigation / tracking | `/me/track/{booking_ref}` | ✅ V3 |

### 1.2 Autres acteurs benchmarkés

| Acteur | Apport spécifique | Reprise |
|--------|------------------|---------|
| **Maersk Spot** | Tarif instantané + capacité ferme garantie | Booking instantané pour clients récurrents |
| **MSC myMSC** | Suivi container en temps réel | Tracking palette via plan d'arrimage NEWTOWT |
| **Hapag-Lloyd Quick Quotes** | Devis sans inscription | Cotation invité sans compte (limitée) |
| **ZIM ZIMonitor** | Alertes proactives | Notifications push pour ETA shift |

### 1.3 Spécificités NEWTOWT (non-container)

| Particularité | Impact sur l'UX |
|---------------|----------------|
| Unité = **palette** (pas TEU) | Sélecteur en palettes EPAL / USPAL / etc. |
| Pas de transbordement | Pas de "shipment leg" multi-navires |
| Décarboné = différenciateur | CO₂ mis en avant à chaque étape |
| Capacité limitée (850 EPAL/navire) | Sentiment de rareté assumé |
| Marchandises dangereuses → SUP_AV | Workflow IMDG dédié |
| Pas de container 20'/40' | Sélecteur format unique (palette) |

## 2. Modèle de données

### 2.1 Tables nouvelles

```sql
-- Comptes clients (distincts des users staff)
CREATE TABLE client_accounts (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  hashed_password VARCHAR(255) NOT NULL,
  company_name VARCHAR(200) NOT NULL,
  contact_name VARCHAR(200),
  phone VARCHAR(50),
  vat_number VARCHAR(50),
  country VARCHAR(2),
  billing_address TEXT,
  language VARCHAR(5) DEFAULT 'fr',
  is_verified BOOLEAN DEFAULT FALSE,
  mfa_secret VARCHAR(64),
  must_change_password BOOLEAN DEFAULT FALSE,
  segment VARCHAR(20) DEFAULT 'occasional',  -- 'occasional', 'recurring', 'key_account'
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);

CREATE INDEX idx_client_accounts_email ON client_accounts(email);
CREATE INDEX idx_client_accounts_segment ON client_accounts(segment);

-- Réservations
CREATE TABLE bookings (
  id SERIAL PRIMARY KEY,
  reference VARCHAR(20) UNIQUE NOT NULL,        -- ex. BK-2026-0042
  client_account_id INTEGER NOT NULL REFERENCES client_accounts(id),
  leg_id INTEGER NOT NULL REFERENCES legs(id),
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
    -- workflow : draft → submitted → confirmed → loaded → at_sea → discharged → delivered → cancelled
  total_palettes INTEGER NOT NULL DEFAULT 0,
  total_weight_kg NUMERIC(10,2) NOT NULL DEFAULT 0,
  total_cubage_m3 NUMERIC(10,3) NOT NULL DEFAULT 0,
  hazardous BOOLEAN DEFAULT FALSE,
  oversize BOOLEAN DEFAULT FALSE,
  estimated_price_eur NUMERIC(10,2),
  confirmed_price_eur NUMERIC(10,2),
  pickup_address TEXT,
  delivery_address TEXT,
  shipper_reference VARCHAR(100),
  notes TEXT,
  signed_terms_version VARCHAR(20),
  signed_terms_at TIMESTAMPTZ,
  submitted_at TIMESTAMPTZ,
  confirmed_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  cancelled_reason TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_bookings_client ON bookings(client_account_id);
CREATE INDEX idx_bookings_leg ON bookings(leg_id);
CREATE INDEX idx_bookings_status ON bookings(status);
CREATE INDEX idx_bookings_ref ON bookings(reference);

-- Détail des palettes par réservation
CREATE TABLE booking_items (
  id SERIAL PRIMARY KEY,
  booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  pallet_format VARCHAR(20) NOT NULL,           -- 'EPAL', 'USPAL', 'PORTPAL', 'IBC', 'BIGBAG', 'BARRIQUE120', 'BARRIQUE140'
  pallet_count INTEGER NOT NULL,
  cargo_description VARCHAR(500) NOT NULL,
  unit_weight_kg NUMERIC(10,2),
  total_weight_kg NUMERIC(10,2),
  stackable BOOLEAN DEFAULT TRUE,
  hazardous BOOLEAN DEFAULT FALSE,
  imdg_class VARCHAR(20),
  un_number VARCHAR(10),
  hs_code VARCHAR(20),
  temperature_min INTEGER,
  temperature_max INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_booking_items_booking ON booking_items(booking_id);

-- Factures clients
CREATE TABLE client_invoices (
  id SERIAL PRIMARY KEY,
  reference VARCHAR(30) UNIQUE NOT NULL,        -- INV-2026-0042
  booking_id INTEGER REFERENCES bookings(id),
  client_account_id INTEGER NOT NULL REFERENCES client_accounts(id),
  issued_at TIMESTAMPTZ DEFAULT NOW(),
  due_at TIMESTAMPTZ,
  amount_excl_vat_eur NUMERIC(10,2) NOT NULL,
  vat_amount_eur NUMERIC(10,2) NOT NULL,
  amount_incl_vat_eur NUMERIC(10,2) NOT NULL,
  currency CHAR(3) DEFAULT 'EUR',
  status VARCHAR(20) DEFAULT 'draft',           -- draft, issued, paid, overdue, cancelled
  stripe_payment_intent_id VARCHAR(100),
  pdf_url VARCHAR(500),
  paid_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_invoices_client ON client_invoices(client_account_id);
CREATE INDEX idx_invoices_status ON client_invoices(status);

-- Certificats CO2 par expédition
CREATE TABLE co2_certificates (
  id SERIAL PRIMARY KEY,
  reference VARCHAR(30) UNIQUE NOT NULL,        -- CO2-2026-0042
  booking_id INTEGER REFERENCES bookings(id),
  client_account_id INTEGER NOT NULL REFERENCES client_accounts(id),
  leg_id INTEGER REFERENCES legs(id),
  tonnage_transported_t NUMERIC(8,3),
  distance_nm NUMERIC(8,2),
  co2_emitted_kg NUMERIC(10,3),
  co2_conventional_kg NUMERIC(10,3),
  co2_avoided_kg NUMERIC(10,3),
  pdf_url VARCHAR(500),
  issued_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_certificates_client ON co2_certificates(client_account_id);

-- Snapshot capacité par leg (recalculé à chaque réservation)
CREATE TABLE leg_capacity_snapshots (
  id SERIAL PRIMARY KEY,
  leg_id INTEGER NOT NULL REFERENCES legs(id),
  snapshot_at TIMESTAMPTZ DEFAULT NOW(),
  capacity_palettes INTEGER NOT NULL,
  reserved_palettes INTEGER NOT NULL,
  available_palettes INTEGER NOT NULL,
  occupancy_pct NUMERIC(5,2)
);

CREATE INDEX idx_capacity_leg ON leg_capacity_snapshots(leg_id, snapshot_at DESC);
```

### 2.2 Extensions des tables existantes

```sql
-- Légère extension du modèle Leg pour le booking
ALTER TABLE legs ADD COLUMN is_bookable BOOLEAN DEFAULT FALSE;
ALTER TABLE legs ADD COLUMN public_price_per_palette_eur NUMERIC(8,2);
ALTER TABLE legs ADD COLUMN booking_open_at TIMESTAMPTZ;
ALTER TABLE legs ADD COLUMN booking_close_at TIMESTAMPTZ;
ALTER TABLE legs ADD COLUMN public_capacity_palettes INTEGER;
ALTER TABLE legs ADD COLUMN public_capacity_overrides JSONB;
  -- ex. { "max_hazardous_palettes": 30, "max_oversize_palettes": 10 }

-- Lien entre Booking et l'ancien Order pour la conversion
ALTER TABLE orders ADD COLUMN booking_id INTEGER REFERENCES bookings(id);
```

## 3. Parcours utilisateur

### 3.1 Prospect non connecté

```
1. Atterrissage sur /  (landing)
2. Recherche d'une route → /routes?from=FR&to=US
3. Sélection d'un leg → /routes/{leg_code}
4. Bouton "Réserver" → /booking/new/step-1-route
5. Étape 1 cargo (palettes, marchandise)
6. Étape 2 récap + prix indicatif
7. Étape 3 création de compte (email + password + entreprise)
8. Étape 4 confirmation → email + lien d'activation
9. Booking en statut "submitted", équipe NEWTOWT confirme
```

### 3.2 Client connecté récurrent

```
1. /me → "Nouvelle réservation"
2. /booking/new → étape 1 cargo (route + cargo)
3. Étape 2 confirmation + prix (instantané, basé sur grille tarifaire)
4. Étape 3 paiement Stripe (carte ou facture)
5. Booking en statut "confirmed", BL généré automatiquement
6. Email confirmation + lien dashboard
```

### 3.3 Grand compte API

```
1. POST /api/v1/bookings  (avec API key)
2. Réponse 201 avec booking_ref + status="submitted"
3. Webhook NEWTOWT → SI client : status changes
4. POST /api/v1/bookings/{id}/confirm via OPS NEWTOWT
5. GET /api/v1/bookings/{id}/documents/bl pour télécharger BL
```

Cf. `docs/api/01-public-api.md` pour la spécification OpenAPI.

## 4. Règles de gestion

### 4.1 Recherche de capacité

```python
def get_available_capacity(leg_id: int) -> CapacityInfo:
    leg = await get_leg(leg_id)
    if not leg.is_bookable:
        raise NotBookable
    if leg.booking_close_at and leg.booking_close_at < now():
        raise BookingClosed
    if leg.atd:  # parti
        raise BookingClosed
    
    reserved = await db.scalar(
        select(func.coalesce(func.sum(Booking.total_palettes), 0))
        .where(Booking.leg_id == leg_id)
        .where(Booking.status.in_(['submitted', 'confirmed', 'loaded', 'at_sea']))
    )
    
    capacity = leg.public_capacity_palettes or leg.vessel.capacity_palettes
    available = capacity - reserved
    return CapacityInfo(capacity=capacity, reserved=reserved, available=available)
```

### 4.2 Tarification

Source de vérité : `rate_grids` + `rate_grid_lines` (existant V2)
étendu d'une colonne `public_price` (booléen).

Calcul indicatif (sans engagement) :

```
total_eur = sum( pallet_count_i × format_coef_i × base_price )
          × surcharge_dangerous (1.25 si hazardous)
          × surcharge_oversize  (1.40 si oversize)
          + frais_documentaires (50 EUR)
```

Prix ferme :
- Pour clients `key_account` : application de leur grille négociée.
- Pour autres : prix public + cotation manuelle si > 50 palettes.

### 4.3 Workflow `bookings.status`

```
draft (panier client)
  └─> submitted (client a confirmé, NEWTOWT doit valider)
        └─> confirmed (OPS NEWTOWT a confirmé + facturation possible)
              ├─> loaded (chargement effectué, lié à PackingListBatch)
              ├─> at_sea
              ├─> discharged
              └─> delivered (livré, archivable)
        └─> cancelled (par client avant confirmed, sans frais)
  └─> cancelled (panier abandonné > 7 j, purge auto)
```

Frais d'annulation post-confirmed :
- > 30 j avant ETD : 0 %
- 30-7 j : 25 %
- 7-2 j : 50 %
- < 2 j : 100 %

### 4.4 Marchandises dangereuses (IMDG)

- Bouton "marchandise dangereuse" dans wizard étape 2.
- Champs obligatoires : classe IMDG, UN number, fiche FDS PDF à
  uploader.
- Flag `hazardous = TRUE` propage à `PackingListBatch.dangerous`.
- Plan d'arrimage : assignation automatique en zones `SUP_AV_*`.
- Validation manuelle par opération avant `confirmed`.

### 4.5 Hors-format

- Si une palette dépasse 380×150×220 cm ou 5,1 t, flag `oversize = TRUE`.
- Demande "co-chargement" : NEWTOWT contacte le client pour discuter
  d'une solution sur-mesure.

## 5. Endpoints

### 5.1 Public (sans auth)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/` | Landing page |
| GET | `/routes` | Recherche de legs réservables |
| GET | `/routes/{leg_code}` | Détail d'un leg |
| GET | `/capacity/{leg_id}` | API JSON capacité d'un leg |
| GET | `/about` | Présentation compagnie |
| GET | `/about/co2` | Méthodologie calcul CO₂ |
| GET | `/about/legal` | Mentions légales |
| GET | `/about/terms` | CGU/CGV |
| GET | `/about/privacy` | Politique de confidentialité |
| GET | `/api/v1/spec.json` | OpenAPI |

### 5.2 Client authentifié

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/me` | Dashboard |
| GET | `/me/bookings` | Mes réservations |
| GET | `/me/bookings/{ref}` | Détail réservation |
| GET | `/me/invoices` | Mes factures |
| GET | `/me/co2` | Mes certificats CO₂ |
| GET | `/me/documents` | Tous mes documents |
| GET | `/me/claims` | Mes sinistres |
| GET | `/me/track/{ref}` | Tracking expédition |
| GET | `/me/account` | Profil + sécurité |
| POST | `/me/account/password` | Changer mot de passe |
| POST | `/me/account/mfa` | Activer 2FA |
| GET | `/booking/new` | Démarrer wizard |
| POST | `/booking/new/step-1-route` | Sauver étape 1 |
| POST | `/booking/new/step-2-cargo` | Sauver étape 2 |
| POST | `/booking/new/step-3-confirm` | Sauver étape 3 |
| POST | `/booking/new/step-4-pay` | Sauver étape 4 + paiement |
| POST | `/booking/{ref}/cancel` | Annuler |
| GET | `/booking/{ref}/pdf` | Confirmation PDF |

### 5.3 API (clé API)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/v1/health` | Santé |
| GET | `/api/v1/routes` | Recherche |
| GET | `/api/v1/legs/{leg_id}` | Détail leg |
| GET | `/api/v1/legs/{leg_id}/capacity` | Capacité |
| POST | `/api/v1/bookings` | Créer |
| GET | `/api/v1/bookings/{ref}` | Lire |
| POST | `/api/v1/bookings/{ref}/cancel` | Annuler |
| GET | `/api/v1/bookings/{ref}/documents/{kind}` | BL/Facture/CO₂ |
| POST | `/api/v1/webhooks/subscribe` | S'abonner |

### 5.4 OPS NEWTOWT (collaborateurs)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/booking` | Liste backoffice de toutes les bookings |
| GET | `/booking/{id}` | Détail backoffice |
| POST | `/booking/{id}/confirm` | Confirmer manuellement |
| POST | `/booking/{id}/reject` | Refuser |
| POST | `/booking/{id}/note` | Ajouter note interne |

## 6. Vues UI clés

### 6.1 `/` Landing

Cf. `docs/design/01-design-handoff.md` §4.1.

### 6.2 `/routes` Recherche

Filtres latéraux : POL, POD, ETD, format palette, marchandise
dangereuse oui/non. Résultats triés par ETD croissant, capacité
décroissante. Pagination 10 par page.

### 6.3 Booking wizard

```
Étape 1 — Route :
  ▢ Origine [Fécamp]  ▢ Destination [New York]
  ▢ Date départ souhaitée [04/06/2026 ± 7 j]
  → [Voir les legs disponibles]
  ─── 3 legs proposés en cards ───

Étape 2 — Cargo :
  Ajouter une ligne palette :
  [+] Format | Nb | Description | Poids unitaire | Stackable | Dangereux
  Total : 12 palettes EPAL · 3200 kg · 4.8 m³
  Prix indicatif : 456 EUR HT

Étape 3 — Confirmation :
  Récap route, cargo, prix
  ☐ J'accepte les CGV (v2026.1)
  → [Confirmer]

Étape 4 — Paiement :
  ▢ Carte (Stripe)
  ▢ Virement (facture émise sous 24h)
  → [Payer]
```

### 6.4 Dashboard client `/me`

Cf. `docs/design/01-design-handoff.md` §4.4.

### 6.5 Backoffice booking (collaborateurs)

```
┌──────────────────────────────────────────────────────────────┐
│ Bookings  · 47 ouvertes · 12 à confirmer aujourd'hui         │
├──────────────────────────────────────────────────────────────┤
│ Filtres : status · navire · client · date · segment          │
├──────────────────────────────────────────────────────────────┤
│ BK-2026-0042 · Acme SAS · 1A FR-US · 12 EPAL · submitted     │
│ BK-2026-0041 · BioFruits · 1C FR-US · 25 USPAL · confirmed   │
│ ...                                                          │
└──────────────────────────────────────────────────────────────┘
```

## 7. Sécurité du booking

- **Anti-double booking** : verrou pessimiste `SELECT ... FOR UPDATE` sur
  le leg lors de la confirmation, avec re-check de capacité.
- **Rate limit** : 10 bookings/jour/IP non-authentifié, 100/jour pour
  client authentifié.
- **Vérification email** : compte créé → email avec lien
  d'activation. Le booking reste `submitted` même sans activation, mais
  ne peut pas être payé.
- **Détection fraude** : règle Stripe Radar + check VAT/SIRET côté
  serveur.
- **Audit** : chaque transition de status est tracée dans
  `activity_logs` avec `entity_type='booking'`.

## 8. Notifications

| Événement | Canal | Cible |
|----------|-------|-------|
| Booking submitted | Email | Client + OPS |
| Booking confirmed | Email | Client |
| Facture émise | Email | Client |
| Loading effectif | Email + push PWA | Client |
| ETA shift > 12 h | Email + SMS optionnel | Client |
| At sea | Email | Client |
| Discharged | Email + lien BL | Client |
| Delivered | Email + certificat CO₂ | Client |
| Claim opened | Email | Client + OPS + Manager |

## 9. KPI booking

Mesurés en continu sur dashboard analytics :

- Funnel : visite landing → recherche → wizard step 1/2/3/4 → payé.
- Taux de conversion par segment (occasional / recurring / key_account).
- Délai moyen submitted → confirmed (target < 4h).
- Taux d'annulation par fenêtre (J-30, J-7, J-2).
- Top 10 routes les plus réservées.
- Revenu mensuel + marge prévisionnelle.

## 10. Plan d'évolution (post-V3)

- V3.1 : multi-devise (USD, BRL).
- V3.2 : co-chargement (matchmaking entre chargeurs B2B).
- V3.3 : programme de fidélité (palettes offertes après N réservations).
- V3.4 : portail revendeurs (commissions agents).
- V4 : émission de certificats CO₂ blockchain (Polygon, Toucan).
