# Audit — Documents commerciaux sortants

**Cible** : tous les documents générés par l'app et envoyés à l'extérieur (clients, agents, autorités).

## Périmètre

| Document | Source actuelle | Format | Cible client |
|----------|-----------------|--------|--------------|
| Planning partagé | `/planning/share/{token}` (HTML) | HTML | Clients, partenaires |
| Bill of Lading (BL) | `app/static/BILL_OF_LADING_TEMPLATE.docx` | Word → PDF | Shipper, consignee |
| Arrival Notice | **N/A — non implémenté** | — | Consignee à l'arrivée |
| Portail client cargo | `/p/{token}` (HTML) | HTML | Shipper |
| Cargo manifest | `cargo_router.py` (Excel) | Excel | Manutention port |
| Crew list | `crew_router.py` (PDF) | PDF | Police aux frontières |
| BL signé scan | Upload manuel | PDF | Archive |
| Mate's Receipt | À vérifier dans onboard | — | Loading port |

## Constats

### C-01 — Branding TOWT obsolète partout
Tous les exports portent l'identité TOWT (logo, signature, conditions générales). À refondre intégralement avec la charte Kairos (cf. `../ux/design-system-v2.md`).

### C-02 — Planning partagé peu lisible
- Rendu HTML basique, pas de mise en page adaptée à l'impression.
- Pas de export PDF natif.
- Pas de filtres côté client (ex. uniquement legs Anemos).
- Pas d'auto-refresh pour les modifications après envoi.

### C-03 — Bill of Lading via Word
- Template `.docx` édité manuellement, fragile.
- Pas de versionning des modèles.
- Format `TUAW_{voyage_id}_{bl_no}` planifié mais non implémenté (CLAUDE.md backlog #5).
- Number of OBL: 3 attendu, à vérifier.

### C-04 — Arrival Notice manquant
Backlog #6 du CLAUDE.md. Document standard maritime obligatoire (notification consignee dès l'arrivée). À implémenter.

### C-05 — Portail client cargo : navigation pauvre
- Pas de dark mode.
- Pas de téléchargement direct BL/Arrival/Invoice.
- Pas de statut cargo en temps réel.
- Messagerie portail existe mais peu mise en avant.

### C-06 — Pas de signature électronique
Tous les BL signés sont scannés et uploadés à la main. Solution : SES (Simple Electronic Signature) directement dans l'app, sinon DocuSign integration.

### C-07 — Multi-langue non automatique
Les exports sont FR uniquement. Le portail client connaît la langue du destinataire — les exports devraient suivre.

### C-08 — Crew list : format figé
Format actuel = standard police aux frontières FR. Pour les escales BR, il faut un format différent (Receita Federal). À paramétrer.

## Plan d'action

### Sprint 1 — Visuel / branding

1. Nouveau template PDF unifié (ReportLab ou WeasyPrint) avec charte Kairos :
   - Header logo + couleurs `--gradient-ocean`.
   - Footer mention légale + numéro de version doc.
   - Watermark "DRAFT" / "FINAL" selon statut.
2. Refonte du portail `/p/{token}` :
   - Dark mode par défaut.
   - Cards modulaires (`design-system-v2.md`).
   - Téléchargements groupés ZIP (BL + Arrival + Invoice).

### Sprint 2 — Nouveaux documents

3. **Arrival Notice automatisée** :
   - Trigger : leg passe en status `arrived` (ATA posée).
   - Template PDF : références BL, palettes, poids, dimensions, notify party, agent.
   - Envoi auto par email au consignee + portail.
4. **BL Format `TUAW_{voyage_id}_{bl_no}`** :
   - Refonte du nommage et du modèle.
   - Number of OBL: 3 (3 originaux + copies).
   - Signature électronique (SES).

### Sprint 3 — Multi-langue + intégrations

5. Multi-langue auto : tous les PDF exports détectent `client.language` (FR/EN/ES/PT) et appliquent les traductions.
6. Crew list : variants FR (PAF) et BR (Receita Federal).
7. Cargo manifest Excel : refonte avec palette Kairos + ajout colonnes manquantes (POL/POD, type goods).

### Sprint 4 — Signature & archivage

8. Intégration DocuSign ou solution SES native (open-source : DocuSeal self-hosted).
9. Archivage automatique post-signature dans `documents/legs/{leg_code}/`.
10. Audit trail (qui a signé, IP, timestamp).

## KPIs cibles

- 100 % des documents en charte Kairos fin Sprint 1.
- Génération Arrival Notice automatisée < 5 s post-ATA.
- Taux de signature électronique > 80 % à 6 mois.
- Réduction des allers-retours mail clients de 50 % (via portail enrichi).
