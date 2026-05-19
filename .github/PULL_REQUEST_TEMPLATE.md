## Quoi

<!-- Résumé en 1-2 lignes -->

## Pourquoi

<!-- Ticket / besoin métier / lien vers une persona ou un use case -->

## Golden path testé

- [ ] <persona> : <chemin>

## Chemins adjacents testés

- [ ] <persona> : <chemin amont>
- [ ] <persona> : <chemin aval>
- [ ] <persona> : <chemin contournement>

## Migration / breaking changes

- [ ] Aucune migration
- [ ] Migration backward-compatible
- [ ] Migration non-backward-compatible (justification ci-dessous)

## Feature flag

- [ ] Pas nécessaire (trivialement réversible)
- [ ] Flag `<key>` créé, default OFF
- [ ] Flag `<key>` modifié

## Screenshots (UI)

<!-- avant / après si UI -->

## Monitoring

- [ ] Métriques exposées (Prometheus)
- [ ] Alerte ajoutée si erreur fonctionnelle critique
- [ ] Log structuré ajouté

## Rollback

<!-- Procédure spécifique si non standard -->

---

### Checklist final

- [ ] CI verte
- [ ] Couverture > 80 % sur les fichiers touchés
- [ ] `/security-review` lancé, sans 🔴
- [ ] `/review` rendu, suggestions appliquées ou justifiées
- [ ] Doc utilisateur mise à jour si UI publique
- [ ] Migrations + rollback testés
- [ ] Aucun TODO sans ticket associé
