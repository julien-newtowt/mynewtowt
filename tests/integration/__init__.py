"""Integration tests package — route-level coverage with a real DB session.

Contrairement aux tests unitaires (fonctions pures), ces tests montent une
base SQLite asynchrone **avec contraintes FK activées** (`PRAGMA
foreign_keys=ON`) et appellent directement les coroutines de route, afin de
valider les comportements qui dépendent réellement de la base : gardes de
suppression (FK), persistance, scoping self-service.
"""
