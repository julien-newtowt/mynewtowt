# Audit Onboard — Vue commandant

**Auteur** : agent commandant (revue critique de l'expérience utilisateur capitaine).

## Questions auxquelles je veux pouvoir répondre depuis le pont

1. **Où en suis-je dans la traversée ?** (position, vitesse, vent, ETA)
2. **Quel est le manifest cargo de cette traversée ?**
3. **Qui est à bord ?** (équipage actif, passagers — désactivé V2)
4. **Quels sont les documents portuaires en attente / signés ?**
5. **Quelle est la météo prévue ?**
6. **Quels incidents ouverts (claims, tickets) ?**
7. **Quel est le journal de quart ?**
8. **Quelles check-lists ISM/ISPS à valider ?**
9. **Qui est monté à bord (visiteurs) ?**

## Constat : la moitié des questions n'a pas de réponse

| # | Question | Réponse actuelle |
|---|----------|------------------|
| 1 | Position/vitesse/vent/ETA | ❌ pas centralisé. Position dans `/tracking`, ETA dans `/planning`, vent inexistant, vitesse instantanée nulle part. |
| 2 | Manifest | 🟡 partiellement — section dans `/onboard` page unique, infos dispersées avec cargo, passagers, crew. |
| 3 | Qui à bord | ✅ section crew_onboard dans `/onboard`. |
| 4 | Documents portuaires | 🟡 section `cargo_documents` + `attachments` dans `/onboard` mais sans workflow de validation clair. |
| 5 | Météo prévue | ❌ aucune intégration. |
| 6 | Incidents ouverts | 🟡 claims accessibles via `/claims` mais pas filtrés au leg actif. Pas de ticketing escale (à venir V2). |
| 7 | Journal de quart | ❌ inexistant. SOF events sont des moments ponctuels (EOSP, SOSP, pilot on/off) pas des entrées de quart structurées. |
| 8 | Check-lists ISM/ISPS | ❌ inexistant. |
| 9 | Visiteurs à bord | ❌ inexistant (obligation ISPS). |

## Audit page `/onboard` actuelle

**Routeur** : `app/routers/onboard_router.py:35-242`.
**Template** : `app/templates/onboard/index.html`.
**Sections** :
1. Crew on board
2. Cargo summary (Import current leg)
3. Cargo Export (next leg)
4. Passagers (désactivé Phase 1)
5. SOF events
6. Notifications
7. ETA shifts history
8. Attachments
9. Cargo documents

**Verdict** : page **scrollable unique de 8 sections**, sans hiérarchie claire entre :
- ce qui se passe **maintenant à quai** (escale),
- ce qui s'est passé **pendant la traversée** (navigation),
- ce qui concerne **uniquement le cargo** (manifest, BL),
- ce qui concerne **uniquement l'équipage** (rotation, ISM).

## Refonte ?

**OUI, indispensable**. La page actuelle est un débouché de toutes les données du leg sans logique d'organisation pour le commandant.

**Cible** : 4 espaces distincts (cf. [`onboard-v2-spec.md`](onboard-v2-spec.md)) avec landing synthétique 4 tuiles.

## Sujets non traités à intégrer en V2

| Sujet | Modèle proposé | Espace |
|-------|----------------|--------|
| Noon report | `NoonReport(leg_id, recorded_at, lat, lon, sog, cog, wind_speed, wind_dir, sea_state, visibility, barometric, fuel_consumed, distance_24h, remarks)` | Navigation |
| Journal de quart | `WatchLog(leg_id, watch_period, officer_on_watch, entries[])` | Navigation |
| Check-lists ISM/ISPS | `OnboardChecklist(leg_id, kind, items[], signed_by, signed_at)` | Équipage |
| Registre visiteurs | `VisitorLog(leg_id, full_name, company, time_in, time_out, purpose, escorted_by)` | Équipage |
| Météo embarquée | API Windy (lecture only) | Navigation |
| Routage voile | `VesselPolar(vessel_id, twa, tws, sog)` + algo routage | Navigation (V3) |

## Recommandations transverses

- **Mode hors-ligne** : la passerelle a parfois une connectivité satellite intermittente. Les saisies (noon report, journal de quart, tickets) doivent être saveables localement et resyncer.
- **PWA installable** : éviter de devoir aller au navigateur, icône d'app sur tablette.
- **Mode "nuit"** : dark + désaturation (déjà la palette Kairos est dark-first, nice).
- **Touch-first** : boutons gros, formulaires verticaux, éviter les hover-only states.
