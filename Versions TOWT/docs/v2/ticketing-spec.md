# Spec — Ticketing escale

## Objectif

Permettre aux équipages, agents, opérateurs de **déclarer**, **suivre** et **clôturer** les demandes d'intervention pendant une escale (avarie, avitaillement urgent, douane, réparation, incident cargo…).

## Modèle

```sql
CREATE TABLE tickets (
  id SERIAL PRIMARY KEY,
  reference VARCHAR(20) UNIQUE NOT NULL,        -- ex. "TKT-2604-A3F2"
  leg_id INTEGER REFERENCES legs(id),
  port_id INTEGER REFERENCES ports(id),
  category VARCHAR(40) NOT NULL,                -- enum (cf. ci-dessous)
  priority VARCHAR(4) NOT NULL,                 -- 'P1', 'P2', 'P3'
  title VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'open',   -- workflow ci-dessous
  created_by INTEGER REFERENCES users(id),
  assigned_to INTEGER REFERENCES users(id),     -- nullable
  external_contact VARCHAR(200),                -- agent portuaire, ship chandler...
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  resolved_at TIMESTAMPTZ,
  closed_at TIMESTAMPTZ,
  sla_target_at TIMESTAMPTZ,                    -- calculé selon priority
  sla_breached BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_tickets_leg ON tickets(leg_id);
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_priority_status ON tickets(priority, status);

CREATE TABLE ticket_comments (
  id SERIAL PRIMARY KEY,
  ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
  author_id INTEGER REFERENCES users(id),
  body TEXT NOT NULL,
  is_internal BOOLEAN DEFAULT FALSE,            -- vs visible-au-port-agent
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ticket_attachments (
  id SERIAL PRIMARY KEY,
  ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  file_path VARCHAR(500) NOT NULL,
  size_bytes INTEGER,
  mime_type VARCHAR(100),
  uploaded_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ticket_audit (
  id SERIAL PRIMARY KEY,
  ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
  action VARCHAR(40),                           -- 'created', 'status_changed', 'assigned', ...
  detail JSONB,
  user_id INTEGER REFERENCES users(id),
  at TIMESTAMPTZ DEFAULT NOW()
);
```

## Catégories

| Code | Label | SLA cible |
|------|-------|-----------|
| `avarie` | Avarie cargo / matériel | P1 ou P2 selon impact |
| `avitaillement_urgent` | Avitaillement (eau, vivres) | P2 |
| `formalite_douane` | Formalité douanière bloquante | P1 |
| `reparation` | Réparation à quai | P2 ou P3 |
| `incident_cargo` | Incident manutention / dommage | P1 ou P2 |
| `medical` | Urgence médicale équipage | P1 |
| `securite` | Incident sécurité (ISPS) | P1 |
| `meteo` | Décision routage météo | P3 |
| `documentation` | Document portuaire manquant | P2 |
| `autre` | Divers | P3 |

## Priorités

| Code | Définition | SLA résolution |
|------|-----------|----------------|
| `P1` | Bloque le départ ou met en danger | 2 h |
| `P2` | À traiter avant le départ < 24 h | 8 h |
| `P3` | Informatif, à suivre | 72 h |

`sla_target_at = created_at + sla_duration[priority]`.

## Workflow status

```
        ┌──────┐
        │ open │
        └──┬───┘
           │ assignation
           ▼
   ┌──────────────┐
   │ in_progress  │
   └─┬────────┬───┘
     │        │ attente externe
     │        ▼
     │  ┌──────────────────┐
     │  │ pending_external │
     │  └────────┬─────────┘
     │           │
     ▼           ▼
   ┌──────────┐
   │ resolved │
   └────┬─────┘
        │ validation utilisateur
        ▼
   ┌────────┐
   │ closed │
   └────────┘
```

Status `cancelled` accessible à tout moment (avec raison).

## Endpoints

| Méthode | Path | Rôle |
|---------|------|------|
| GET | `/escale/{leg_id}/tickets` | Kanban tickets de l'escale |
| GET | `/tickets` | Liste globale, filtres |
| POST | `/tickets/create` | Création (depuis escale ou onboard) |
| GET | `/tickets/{id}` | Détail + commentaires + pièces |
| POST | `/tickets/{id}/comment` | Ajout commentaire |
| POST | `/tickets/{id}/status` | Changement status |
| POST | `/tickets/{id}/assign` | Affectation |
| POST | `/tickets/{id}/upload` | Pièce jointe |
| DELETE | `/tickets/{id}` | Suppression (admin only) |

## UI

Cf. `../ux/mockups.md` §7 — kanban 4 colonnes (open / in_progress / pending_external / resolved), drag-and-drop entre colonnes pour changer status.

Filtres : navire, port, catégorie, priorité, auteur, dates.

## SLA & escalade

Cron toutes les 5 minutes :
- Tickets `P1` non résolus + `now() > sla_target_at` → notification email manager + cloche app.
- Tickets `P2` à 80 % du SLA → notification soft à l'assignee.
- Marquer `sla_breached = true` au passage.

## Notifications

- À la création : email + push à l'utilisateur assigné (s'il existe) + à tous les `manager_maritime`.
- À chaque commentaire externe : email à l'assignee.
- À la résolution : email au créateur pour validation/clôture.
- Intégration sidebar : badge count des tickets P1 ouverts, cloche `notifications`.

## Permissions

| Rôle | C | M | S | Notes |
|------|---|---|---|-------|
| administrateur | ✓ | ✓ | ✓ | Full |
| operation | ✓ | ✓ | ✓ | Full sur tickets de leur opération |
| armement | ✓ | ✓ | — | Création + édition |
| technique | ✓ | ✓ | — | Création + édition |
| marins | ✓ | ✓ | — | Création depuis bord |
| commercial | ✓ | — | — | Lecture seule |
| manager_maritime | ✓ | ✓ | ✓ | Full + reporting |
| data_analyst | ✓ | — | — | Lecture pour analytics |

## KPI

Dashboard admin :
- Nombre tickets ouverts par priorité.
- SLA respect % (P1, P2, P3) sur 30/90 jours.
- Top catégories.
- MTTR (mean time to resolve) par catégorie.
- Top ports / navires en termes de volume tickets.
