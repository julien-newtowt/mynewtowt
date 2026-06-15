# Module de filtrage standard — navire × année × leg

> Composant transverse **réutilisable** sur toute page de l'ERP qui demande un
> filtre par navire / année / leg (escale, KPI, plan de chargement, MRV…).
> Standard imposé : **ne pas réimplémenter** ce filtre page par page.

## Aperçu

Le module reproduit le bandeau de filtrage : onglets navires + sélecteur
d'années + chips de legs, et (optionnel) un bandeau récapitulatif du leg
sélectionné (route, statut à quai/en mer, ETD/ETA/ATA/ATD, actions).

## Composants

| Élément | Emplacement |
|---|---|
| Contexte (données) | `app/services/leg_filter.py` → `build_leg_filter(db, vessel, year, leg_id)` |
| Rendu (macros) | `app/templates/staff/_leg_filter.html` → `leg_filter(...)`, `leg_summary(...)` |
| Styles | `app/static/css/kairos.css` (`.vessel-tabs`, `.year-selector`, `.leg-chip`, `.leg-summary`, `.vessel-status-badge`) |

## Utilisation

### 1. Router — construire le contexte

```python
from app.services.leg_filter import build_leg_filter

@router.get("/ma-page")
async def ma_page(request, vessel=None, year=None, leg_id=None, db=Depends(get_db), ...):
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id)
    # f["legs"] = legs du navire pour l'année ; f["selected_leg"] = leg choisi
    return templates.TemplateResponse("staff/ma_page.html", {"leg_filter_ctx": f, ...})
```

### 2. Template — rendre le filtre

```jinja
{% from "staff/_leg_filter.html" import leg_filter, leg_summary %}

{{ leg_filter(leg_filter_ctx, "/ma-page") }}      {# base_path = racine de la page #}

{% if leg_filter_ctx.selected_leg %}
{% call leg_summary(leg_filter_ctx.selected_leg, pol, pod, vessel_status, locked) %}
  {# boutons d'action spécifiques à la page (verrouiller, etc.) #}
{% endcall %}
{% endif %}
```

Paramètres utiles :
- `leg_filter(f, base_path, show_legs=True)` — `show_legs=False` masque les chips
  (filtre navire + année seulement).
- `leg_summary(..., locked=False)` — bandeau récap ; le bloc `{% call %}` injecte
  les actions propres à la page (slot `caller()`).

## Contrat de données (`build_leg_filter`)

```
{
  "vessels": [Vessel],         # onglets navires
  "selected_vessel": str|None, # code navire actif
  "years": [int],              # plage (année-1 → année+2)
  "current_year": int,         # année active
  "legs": [Leg],               # legs du navire pour l'année (chips)
  "leg_id": int|None,          # leg sélectionné (query ?leg_id=)
  "selected_leg": Leg|None,    # objet leg sélectionné
}
```

## Héritage de la sélection (cookie)

La sélection (navire | année | leg) est persistée dans le cookie
`towt_leg_filter` (12 h, httponly, SameSite=Lax) :

- `set_leg_filter_cookie(response, f)` — à appeler sur la réponse de la page.
- `build_leg_filter(..., request=request)` — complète les paramètres absents
  depuis le cookie.

Ainsi, **le leg choisi sur `/onboard` est hérité** par les autres pages du
module opérations (navigation, escale, plan de chargement…) sans repasser les
query-params. Un changement de sélection sur n'importe quelle page met le
cookie à jour.

## Conventions

- Liens **GET** uniquement (`base_path?vessel=&year=&leg_id=`) — CSP-safe,
  aucun JS inline.
- L'année par défaut = année courante ; le navire par défaut = premier navire
  (ou cookie). Une page RBAC-navire (ex. navigation avec `assigned_vessel_id`)
  force son navire.
- `leg_href` / `leg_href_suffix` : faire pointer les chips vers une page de
  détail (ex. `leg_href="/stowage/legs/"`, ou `"/mrv/legs/"` +
  `leg_href_suffix="/carbon"`) plutôt que vers `base_path?leg_id=`.

## Pages adoptantes

| Page | Base path | Sélection leg |
|---|---|---|
| Onboard (landing) | `/onboard` | ✅ **point d'entrée** — pose le cookie |
| Onboard › Navigation | `/onboard/navigation` | ✅ hérite (cookie), RBAC navire |
| Escale | `/escale` | ✅ hérite (cookie) |
| Plan de chargement | `/stowage` | ✅ chips → plan du leg |
| MRV | `/mrv` | ✅ chips → carbon report du leg |
| KPI | `/kpi` | ✅ filtre navire × année |
| Claims | `/claims` | ✅ filtre la liste par leg |
| Finance | `/finance` | ✅ filtre navire × année |
