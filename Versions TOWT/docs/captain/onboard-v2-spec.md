# Refonte Onboard V2 — Spec

## Architecture cible : 4 espaces

```
        /onboard  (landing)
        ┌────────────────────────────┐
        │  KPI strip                 │
        │  ┌──┐ ┌──┐ ┌──┐ ┌──┐       │
        │  │1 │ │2 │ │3 │ │4 │       │
        │  └──┘ └──┘ └──┘ └──┘       │
        │   ↓     ↓     ↓     ↓      │
        └────┴─────┴─────┴─────┴─────┘
             │     │     │     │
        ┌────▼┐ ┌──▼──┐ ┌▼────┐ ┌▼─────┐
        │Esc. │ │Nav. │ │Cargo│ │Crew  │
        └─────┘ └─────┘ └─────┘ └──────┘
```

## Landing `/onboard`

### KPI strip (top)
4 chiffres-clés en haut de page :
- ETA prochain port (countdown DD:HH:MM live).
- Distance restante (NM).
- Vent au point actuel (kn + flèche).
- Tickets ouverts (compteur, badge rouge si P1).

### 4 tuiles tap-friendly (centre)

```
┌─────────────────────┬─────────────────────┐
│  ⚓ ESCALE          │  🌊 NAVIGATION      │
│  3 ops en cours     │  9.2 kn · ETA 14h   │
│                     │                     │
├─────────────────────┼─────────────────────┤
│  📦 CARGO & DOC     │  👥 ÉQUIPAGE        │
│  Closure 60 %       │  6 à bord           │
│                     │                     │
└─────────────────────┴─────────────────────┘
```

Chaque tuile = card cliquable, fond `--bg-1`, hover `--bg-2`, vers une sous-page.

### Footer landing
Activité récente du leg (dernières 5 entrées) — SOF events, tickets, notifications mélangés.

## `/onboard/escale`

Vue active uniquement quand vessel est à quai (`leg.ata && !leg.atd`).

### Contenu
- **Header** : port, navire, ATA → ATD prévu (timer live escale).
- **Split Import / Export** (cf. `../ux/mockups.md` §3) : palettes en cours de débarquement / embarquement, docker shifts.
- **Pilotage** : EOSP, SOSP, pilot on/off, tug, gangway up/down (sous-set des SOF events filtrés).
- **Documents portuaires** : NOR, mate's receipt, letters of protest — checklist avec badges signés/en attente.
- **Tickets escale** : kanban compact + lien vers vue ticketing complète.

### Routes existantes à recombiner
- Une partie vit dans `/escale` (opérations, dockers).
- Une partie vit dans `/onboard` (SOF, attachments, cargo docs).
- V2 : `/onboard/escale` agrège tout pour le commandant ; `/escale/...` reste pour l'opérateur shore-side.

## `/onboard/navigation` (NOUVEAU)

Vue active quand vessel est en mer (`leg.atd && !next_leg.ata`).

### Contenu
- **Position live** : carte centrée sur dernière `VesselPosition` + trace du leg.
- **Vitesse / cap** : SOG, COG, vent (Windy API).
- **ETA** : projection depuis position + speed actuelle + distance restante.
- **Noon Reports** :
  - Bouton "Saisir noon report" en haut.
  - Liste des reports précédents (date, lat, lon, sog moy, vent, mer, fuel).
  - Détail expandable.
- **Journal de quart** :
  - Bouton "Nouvelle entrée" (auto-pré-rempli avec officier de quart actuel).
  - Liste chronologique des entrées (text + timestamp + signataire).
- **Météo prévue** : 5 jours, intégration Windy ou similar.
- **Routage voile** (V3) : polaires + waypoints recommandés.

### Modèles à créer

```python
class NoonReport(Base):
    __tablename__ = "noon_reports"
    id = Column(Integer, primary_key=True)
    leg_id = Column(Integer, ForeignKey("legs.id"), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    sog_avg = Column(Float)         # noeuds, moyenne sur 24 h
    cog_avg = Column(Float)         # degrés
    wind_speed = Column(Float)      # noeuds
    wind_direction = Column(Float)  # degrés
    sea_state = Column(Integer)     # échelle Beaufort 0-12
    visibility_nm = Column(Float)
    barometric_pressure = Column(Float)  # hPa
    fuel_consumed_24h = Column(Float)    # litres
    distance_24h = Column(Float)         # NM
    remarks = Column(Text)
    recorded_by_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WatchLog(Base):
    __tablename__ = "watch_logs"
    id = Column(Integer, primary_key=True)
    leg_id = Column(Integer, ForeignKey("legs.id"), nullable=False, index=True)
    watch_period = Column(String(20))  # "00-04", "04-08", "08-12", "12-16", "16-20", "20-24"
    watch_date = Column(Date, nullable=False)
    officer_on_watch_id = Column(Integer, ForeignKey("crew_members.id"))
    entry = Column(Text, nullable=False)
    signed_at = Column(DateTime(timezone=True), server_default=func.now())
```

## `/onboard/cargo`

### Contenu
- **Manifest courant** : table compact orders + batches + palettes + poids + types.
- **SOF events filtrés cargo** : loading, discharging, claims, hold inspection.
- **Documents cargo** : BL (status signature), mate's receipts, photos chargement/déchargement.
- **Plan d'arrimage** (lien vers `/stowage/{leg_id}`).
- **Closure checklist** : barre de progression % docs complets.

## `/onboard/crew`

### Contenu
- **Rotation à bord** : crew assignments actifs (qui, depuis quand, jusqu'à quand).
- **Embarquements/débarquements à venir** sur le leg.
- **Check-lists ISM/ISPS** :
  - Modèles prédéfinis (drill incendie, abandon navire, sûreté ISPS, contrôle FSC).
  - Items à cocher, signature requise.
  - Historique des check-lists complétées.
- **Incidents équipage** : claims liés à l'équipage + tickets médicaux.
- **Registre visiteurs** (ISPS) :
  - Saisie : nom, société, motif, escorté par.
  - Time in / time out.
  - Liste filtrable + export PDF.

### Modèles à créer

```python
class OnboardChecklist(Base):
    __tablename__ = "onboard_checklists"
    id = Column(Integer, primary_key=True)
    leg_id = Column(Integer, ForeignKey("legs.id"), nullable=False, index=True)
    kind = Column(String(40), nullable=False)  # 'fire_drill', 'abandon_drill', 'isps_audit', 'fsc_inspection'
    title = Column(String(200), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    signed_by_id = Column(Integer, ForeignKey("users.id"))
    items = relationship("OnboardChecklistItem", back_populates="checklist", cascade="all, delete-orphan")


class OnboardChecklistItem(Base):
    __tablename__ = "onboard_checklist_items"
    id = Column(Integer, primary_key=True)
    checklist_id = Column(Integer, ForeignKey("onboard_checklists.id"), nullable=False)
    label = Column(String(300), nullable=False)
    is_completed = Column(Boolean, default=False)
    note = Column(Text)


class VisitorLog(Base):
    __tablename__ = "visitor_logs"
    id = Column(Integer, primary_key=True)
    leg_id = Column(Integer, ForeignKey("legs.id"), nullable=False, index=True)
    full_name = Column(String(200), nullable=False)
    company = Column(String(200))
    purpose = Column(String(200))
    time_in = Column(DateTime(timezone=True))
    time_out = Column(DateTime(timezone=True))
    escorted_by_id = Column(Integer, ForeignKey("crew_members.id"))
```

## Migration depuis V1

1. Créer les nouveaux modèles + migration Alembic.
2. Refactorer `onboard_router.py` (1840 l.) en 5 routeurs :
   - `onboard_landing_router.py` (`/onboard`)
   - `onboard_escale_router.py` (`/onboard/escale`)
   - `onboard_navigation_router.py` (`/onboard/navigation`)
   - `onboard_cargo_router.py` (`/onboard/cargo`)
   - `onboard_crew_router.py` (`/onboard/crew`)
3. Migrer les sections existantes vers leurs nouvelles pages.
4. Ajouter les nouveaux formulaires (noon report, watch log, checklists, visiteurs).
5. PWA manifest + service worker (offline + installable).
6. Tester sur tablette en condition pont (touch-first, gros boutons).

## Permissions

| Espace | C | M | S |
|--------|---|---|---|
| `/onboard/escale` | captain, operation, marins | captain, operation | manager_maritime |
| `/onboard/navigation` | captain, marins | captain, marins | manager_maritime |
| `/onboard/cargo` | captain, operation, marins | captain, operation | manager_maritime |
| `/onboard/crew` | captain, armement, marins | captain, armement | manager_maritime |
