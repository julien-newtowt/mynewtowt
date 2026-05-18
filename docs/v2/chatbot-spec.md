# Spec — Agent conversationnel Kairos AI

## Objectif

Permettre aux utilisateurs de :
- **Retrouver** une information (leg, escale, client, document) en langage naturel.
- **Assister** la prise en main de l'app (questions FAQ, tutoriels contextuels).
- **Répondre** aux questions sur le fonctionnement de la compagnie / des modules.

## Architecture

```
┌──────────────┐         ┌──────────────────┐         ┌──────────────┐
│ Widget chat  │  HTTP   │ /chat router     │  SDK    │ Anthropic    │
│ (HTMX)       │ ──────> │ FastAPI          │ ──────> │ Claude API   │
└──────────────┘         │                  │ <────── │ sonnet-4-6   │
                         │ - prompt cache   │         └──────────────┘
                         │ - tool dispatch  │                ▲
                         │ - RAG retrieval  │ tool_use       │
                         └──────────────────┘                │
                                  │                          │
                                  ▼                          │
                         ┌──────────────────┐                │
                         │ pgvector store   │                │
                         │ (docs/, models)  │                │
                         └──────────────────┘                │
                                  │                          │
                                  ▼                          │
                         ┌──────────────────┐                │
                         │ Postgres         │ ───────────────┘
                         │ (search tools)   │   tool_result
                         └──────────────────┘
```

## Modèle & paramètres

- **Modèle** : `claude-sonnet-4-6`.
- **Prompt caching** : system prompt (5-8k tokens, doc Kairos + schéma BDD) cached → 90 % d'économie sur tours suivants.
- **Tool use** : 5 outils lecture-seule (cf. ci-dessous).
- **Max tokens output** : 1024 par réponse.
- **Streaming** : oui, pour UX réactive.

## System prompt (extrait)

```
Tu es Kairos AI, l'assistant de l'application de gestion maritime Kairos.

Tu aides les opérateurs, marins et managers à :
- retrouver des informations sur les voyages (legs), escales, commandes, équipages
- comprendre le fonctionnement des modules de l'application
- résoudre des questions opérationnelles courantes

Tu disposes d'outils pour interroger la base de données en lecture seule.
Réponds toujours en français sauf si l'utilisateur écrit dans une autre langue.
Cite tes sources (numéro de leg, référence commande, lien vers la page).
Si tu ne sais pas, dis-le. N'invente jamais de données.

[Glossaire maritime intégré ici — extrait de docs/v2/glossary.md]
[Description schéma BDD — relations clés Vessel/Leg/Order/Escale/...]
```

## Outils (Anthropic tool_use)

### `search_leg(query: str)` 
Recherche un leg par code, navire+date, port. Retourne max 5 résultats : `{leg_code, vessel, departure_port, arrival_port, etd, eta, status}`.

### `search_escale(port: str, date_from: str, date_to: str)`
Liste les escales sur un port + plage. Retourne `{leg_code, vessel, ata, atd, ops_count}`.

### `search_order(reference: str)`
Recherche une commande par ref ou client. Retourne `{order_ref, client, leg, palettes, weight, status}`.

### `get_vessel_position(vessel_name: str)`
Dernière position connue d'un navire. Retourne `{lat, lon, sog, cog, recorded_at}`.

### `get_user_activity(user_id: int | None, since: str)`
Activité récente d'un user (ou de tous). Retourne liste `{action, module, entity, at, ip}`.

**Sécurité** : chaque tool wrappe `require_permission(user, module, 'C')`. Si l'utilisateur n'a pas le droit, l'outil retourne `{"error": "permission_denied"}` plutôt que les données.

## RAG (pgvector)

### Sources indexées
- `docs/` (toute la documentation Markdown).
- Schéma BDD : pour chaque modèle, embed la docstring + nom des colonnes.
- FAQ utilisateurs : à constituer (Q/R d'usage courant).
- Onboarding : "comment créer un leg ?", "comment ajouter un docker shift ?", etc.

### Pipeline d'indexation
1. Cron quotidien `scripts/reindex_chatbot.py`.
2. Chunking : 800 tokens, overlap 100.
3. Embeddings : `voyage-3-lite` (qualité/prix optimal pour FR/EN) ou `text-embedding-3-large` (OpenAI) — selon budget.
4. Insert dans `chatbot_chunks(id, source, chunk, embedding VECTOR(1024))`.

### Retrieval
À chaque tour utilisateur : embed query → top-5 chunks (cosine) → injecter dans le system prompt avant l'appel Claude.

## Stockage conversations

```sql
CREATE TABLE chat_conversation (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  started_at TIMESTAMPTZ DEFAULT NOW(),
  title VARCHAR(200) -- auto-générée à partir du premier message
);

CREATE TABLE chat_message (
  id SERIAL PRIMARY KEY,
  conversation_id INTEGER REFERENCES chat_conversation(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL, -- 'user', 'assistant', 'tool'
  content TEXT NOT NULL,
  tool_calls JSONB,
  tool_results JSONB,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd NUMERIC(8,4),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Endpoints

| Endpoint | Méthode | Rôle |
|----------|---------|------|
| `/chat` | GET | Page widget (rendu HTMX) |
| `/chat/conversations` | GET | Liste des conversations user |
| `/chat/conversations/{id}` | GET | Charge l'historique d'une conv |
| `/chat/messages` | POST | Envoie un message + stream réponse (SSE) |
| `/chat/conversations/{id}` | DELETE | Supprime conversation |

## UI

Cf. `../ux/mockups.md` §6 — widget flottant bottom-right + `Cmd+K`.

## Quotas & coûts

- Cap user : 50 messages/jour (paramétrable rôle-based).
- Cap entreprise : 200 EUR/mois Anthropic — alerte à 80 %.
- Tracking dans `chat_message.cost_usd` agrégé hebdo dans le dashboard admin.

## Sécurité

- Chaque tool_use re-vérifie les permissions du user (pas confiance dans le LLM).
- Pas de tool d'écriture dans le V1 (uniquement lecture).
- Logging exhaustif (audit RGPD, prompt + réponse, sans masquer les outils utilisés).
- Détection prompt injection : refus si patterns suspects (`ignore previous`, `system:`, etc.).
- Pas de remontée des données sensibles non-autorisées : si tool retourne `permission_denied`, l'agent doit le signaler à l'utilisateur sans contourner.

## Extension future (V3)

- Tools d'écriture (créer SOF, marquer ATA) — derrière confirm utilisateur explicite.
- Vocal in/out (Whisper + ElevenLabs) — utile en passerelle.
- Mémoire long-terme par user (préférences, contexte récurrent).
