# Vision produit — `mynewtowt`

> Statut : document fondateur, version 1.0, daté du 18 mai 2026.

## 1. Énoncé de vision

Faire de `mynewtowt` la **plateforme unique** qui pilote la compagnie
NEWTOWT et permet à ses clients de **réserver, suivre, mesurer** le
transport vélique de leurs marchandises avec la même exigence que les
acteurs de la grande conteneurisation (CMA-CGM, MSC, Maersk) tout en
gardant l'ADN décarboné de la marque.

## 2. Différenciation par rapport à la version précédente

| Capacité | V2 (TOWT) | V3 (mynewtowt) |
|----------|----------|----------------|
| ERP collaborateurs | ✅ | ✅ enrichi |
| Portail client par token | ✅ (packing list seule) | ✅ (compte authentifié + dashboard) |
| **Réservation d'espace en cale** | ❌ | ✅ **NOUVEAU** (planning + booking) |
| **Compte client persistant** | ❌ | ✅ avec espace dédié |
| **Catalogue routes/legs publics** | ❌ partiel | ✅ moteur de recherche |
| Rapports CO₂ par client | ✅ certificat unique | ✅ + dashboard cumulé |
| Suivi claims côté client | ❌ | ✅ |
| Documentation auto-remplie | ✅ packing list | ✅ packing + BL + facture |
| Chatbot Kairos AI | 🟡 planifié | ✅ intégré jour 1 |
| Sécurité (2FA, audit, DLP) | 🟡 | ✅ renforcée |

## 3. Audiences cibles

### 3.1 Collaborateurs (8 rôles)

| Rôle | Quotidien outillé |
|------|------------------|
| Commandant de bord | Pilotage à bord (escale, navigation, cargo, équipage) |
| Agent d'escale | Opérations portuaires (import/export, ticketing, dockers) |
| Responsable RH | Équipage, congés, paie, compliance Schengen |
| Superintendant | Maintenance, technique, certifs ISM/ISPS |
| Commercial | Pipeline, cotations, suivi commandes, CRM |
| Manager maritime | Vue flotte, KPI, décisions, escalades |
| Data Analyst | KPI, finance, MRV, exploration |
| Administrateur | Gouvernance, sécurité, exports |

### 3.2 Prospects / Clients

| Profil | Besoin |
|--------|--------|
| Prospect curieux | Comprendre l'offre, simuler un trajet, voir le calendrier public |
| Client occasionnel | Réserver une palette ponctuelle, suivre, télécharger BL |
| Client récurrent (B2B) | Dashboard commandes, factures, rapports CO₂ cumulés |
| Chargeur grand compte | API de réservation, intégration ERP, SLA reporting |

## 4. Boussole produit (North Star)

> **Le score North Star** : *nombre de palettes réservées par mois,
> par clients récurrents, avec rapport CO₂ téléchargé.*

Mesure l'adoption simultanée des 3 capacités fondatrices : réservation,
fidélisation, transparence environnementale.

## 5. Promesses utilisateur

1. **Transparence radicale** : tout client voit où est sa marchandise, qui
   l'a chargée, combien de CO₂ a été économisé.
2. **Simplicité maritime** : un quai, un navire, une cale, une réservation.
   Pas de container, pas d'EDI baroque — un parcours utilisateur de 4 écrans.
3. **Fiabilité commerciale** : ce qui est promis (capacité, ETA, prix)
   est ce qui est livré. Toute déviation est tracée et notifiée.
4. **Sécurité de l'information** : données salariales, contractuelles,
   sinistres traités au niveau d'un grand compte (RGPD, audit, MFA).
5. **Continuité opérationnelle** : aucune perte de données possible,
   chaque action est journalisée et rejouable.

## 6. Indicateurs de succès (KPI produit)

| KPI | Cible 6 mois | Cible 12 mois |
|-----|--------------|---------------|
| Taux de remplissage moyen flotte | +5 pts | +10 pts |
| Délai moyen confirmation réservation | < 4 h | < 1 h |
| NPS clients | ≥ 35 | ≥ 50 |
| % réservations B2B en self-service | 30 % | 60 % |
| Tickets P1 résolus dans SLA | ≥ 95 % | ≥ 99 % |
| Volume mensuel rapports CO₂ téléchargés | 100 | 400 |
| Cycle commande → BL signé | 5 j | 3 j |

## 7. Anti-objectifs explicites

- Ne pas devenir un EDI conteneur (pas de SI-CTPL, pas d'INTTRA).
- Ne pas remplacer Pipedrive — on s'y connecte, on ne le concurrence pas.
- Pas de framework JS lourd (React/Vue). HTMX + Alpine couvrent les
  besoins et préservent l'accessibilité, la performance et la
  maintenabilité.
- Pas de mobile app native — PWA installable suffit (touch-first,
  offline-tolerant).

## 8. Couplages externes maîtrisés

| Externe | Usage | Risque si indispo |
|---------|-------|-------------------|
| Pipedrive | CRM commercial | Mode dégradé, sync différée |
| Windy API | Météo navigation | Affichage masqué, pas de blocage |
| Anthropic Claude | Chatbot Kairos AI | Widget masqué |
| OSM Nominatim | Géocodage ports | Cache local des ports connus |
| Mapbox | Tiles cartes | Fallback raster OSM |
| Stripe | Paiement client | Mode "facture papier" fallback |
| Microsoft O365 | SSO interne | Login local conservé |

## 9. Conformité

- **RGPD** (UE 2016/679) : registre des traitements, DPO référencé,
  droits d'accès / portabilité / oubli implémentés.
- **EU MRV** (UE 2015/757) : reporting émissions maritimes annuel.
- **SOLAS / ISM / ISPS** : check-lists numérisées, traçabilité audit.
- **eIDAS** : signature électronique pour BL et contrats (V3.1).
- **PCI-DSS SAQ-A** : si paiement en ligne via Stripe Checkout (Stripe
  hosted, scope minimal).

## 10. Évolution prévue (12 trimestres)

| Trimestre | Thème principal |
|-----------|-----------------|
| T0 (auj.) | Refonte design system Kairos + bootstrap |
| T+1 | Plateforme de réservation publique |
| T+2 | Refonte Onboard 4 espaces + PWA offline |
| T+3 | Analytics dashboard cumul + IA conversationnelle |
| T+4 | Multi-langue, multi-devise, partenaires API |
| T+5 | Marketplace (chargeurs ↔ co-chargement) |
| T+6 | Émission de certificats blockchain CO₂ |
