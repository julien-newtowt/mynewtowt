"""Design system — reprise UX Phase 3 (docs/design/03-reprise-ux-legacy.md §4).

Couvre : skeleton loaders + densité de tableau dans kairos.css (et purge du
sélecteur legacy mort `.app-shell`), graisses Manrope 300/800 chargées,
bouton de bascule de densité dans la topbar, compilation + contenu des pages
d'erreur enrichies, et `density.js` sans handler inline (CSP stricte).
"""

from __future__ import annotations

from pathlib import Path

KAIROS_CSS = Path(__file__).resolve().parents[2] / "app" / "static" / "css" / "kairos.css"
BASE_HTML = Path(__file__).resolve().parents[2] / "app" / "templates" / "base.html"
TOPBAR_HTML = Path(__file__).resolve().parents[2] / "app" / "templates" / "staff" / "_topbar.html"
DENSITY_JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "density.js"
TOAST_JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "toast.js"


def _kairos_css() -> str:
    return KAIROS_CSS.read_text(encoding="utf-8")


# ───────────────────────── kairos.css ─────────────────────────


def test_kairos_has_skeleton_loader():
    src = _kairos_css()
    assert ".skeleton {" in src
    assert "@keyframes skeleton-pulse" in src


def test_kairos_has_htmx_indicator_pattern():
    src = _kairos_css()
    assert ".htmx-indicator {" in src
    assert ".htmx-request .htmx-indicator" in src


def test_kairos_has_density_cosy_variant():
    src = _kairos_css()
    assert "density-cosy" in src
    assert "body.density-cosy .data-table th" in src


def test_kairos_has_empty_state_cta_and_card_variant():
    src = _kairos_css()
    assert ".empty-state .btn" in src
    assert ".empty-state--card" in src


def test_kairos_completes_focus_visible_on_interactive_components():
    src = _kairos_css()
    for selector in (
        ".topbar-action:focus-visible",
        ".sidebar-toggle:focus-visible",
        ".nav-group-toggle:focus-visible",
        ".vessel-tab:focus-visible",
        ".year-btn:focus-visible",
        ".view-btn:focus-visible",
        ".leg-chip:focus-visible",
        ".btn-icon-only:focus-visible",
    ):
        assert selector in src, f"règle focus-visible manquante : {selector}"


def test_kairos_no_longer_has_the_dead_app_shell_grid_selector():
    """`.app-shell` (conteneur grid legacy) était mort — remplacé par
    `.app-shell-v2` — et a été purgé. `.sidebar` (partagée, cf. commentaire
    dans kairos.css) est volontairement conservée : ce test ne la vise pas."""
    src = _kairos_css()
    assert "display: grid;\n  grid-template-columns: 256px 1fr;" not in src


def test_kairos_still_has_app_shell_v2_and_shared_sidebar():
    # Non-régression : on n'a pas supprimé plus que le bloc mort.
    src = _kairos_css()
    assert ".app-shell-v2 {" in src
    assert ".sidebar {" in src


# ───────────────────────── base.html — Manrope ─────────────────────────


def test_base_html_loads_manrope_300_and_800():
    src = BASE_HTML.read_text(encoding="utf-8")
    assert "family=Manrope:wght@" in src
    weights_line = next(line for line in src.splitlines() if "family=Manrope:wght@" in line)
    weights = weights_line.split("family=Manrope:wght@", 1)[1].split("&", 1)[0]
    weight_set = set(weights.split(";"))
    assert "300" in weight_set, "graisse --fw-light (300) non chargée"
    assert "800" in weight_set, "graisse --fw-extrabold (800) non chargée"


# ───────────────────────── Topbar — densité ─────────────────────────


def test_topbar_has_density_toggle_button():
    src = TOPBAR_HTML.read_text(encoding="utf-8")
    assert 'id="density-toggle"' in src
    assert "topbar-action" in src


def test_staff_layout_loads_density_js():
    layout = Path(__file__).resolve().parents[2] / "app" / "templates" / "staff" / "_layout.html"
    src = layout.read_text(encoding="utf-8")
    # Passe par ``asset()`` depuis le 2026-09-03 : le chemin nu privait le
    # fichier du cache-busting ``?v=<mtime>``, et le navigateur servait une
    # version périmée après déploiement (cf. test_static_cache_busting).
    assert "asset('js/density.js')" in src


# ───────────────────────── density.js — CSP-safe ─────────────────────────


def test_density_js_exists_and_has_no_inline_handlers():
    assert DENSITY_JS.exists()
    src = DENSITY_JS.read_text(encoding="utf-8")
    assert "onclick" not in src
    assert "addEventListener" in src
    assert "towt-density" in src


# ───────────────────────── toast.js — erreurs 4xx/5xx sous HTMX ─────────────────────────


def test_toast_js_handles_htmx_response_error():
    """Une mutation ``hx-post`` + ``hx-swap="none"`` ne swape jamais sur un
    non-2xx : sans handler dédié, un 409 (verrou/gel) ou 400 (métier)
    n'affichait RIEN. ``htmx:responseError`` doit toaster un message, en
    lisant ``detail`` du corps JSON FastAPI quand présent.
    """
    src = TOAST_JS.read_text(encoding="utf-8")
    assert 'addEventListener("htmx:responseError"' in src
    assert "detail" in src
    assert "error" in src


def test_toast_js_loaded_globally_via_base_html():
    base_src = BASE_HTML.read_text(encoding="utf-8")
    assert "js/toast.js" in base_src


# ───────────────────────── Pages d'erreur enrichies ─────────────────────────


def test_error_pages_compile_and_use_empty_state_card():
    from app.templating import templates

    for name in ("errors/404.html", "errors/403.html"):
        # Lève une TemplateSyntaxError/TemplateNotFound si le template ne
        # compile pas — imite le pattern déjà utilisé pour la fiche cockpit.
        templates.env.get_template(name)
        src = templates.env.loader.get_source(templates.env, name)[0]
        assert "empty-state" in src
        assert "empty-state--card" in src
        assert 'href="/"' in src
        # Pas de lien "page précédente" en javascript: (interdit par la CSP).
        assert "javascript:" not in src


def test_error_pages_have_clear_french_copy_and_no_history_back_link():
    from app.templating import templates

    src_404 = templates.env.loader.get_source(templates.env, "errors/404.html")[0]
    src_403 = templates.env.loader.get_source(templates.env, "errors/403.html")[0]
    assert "introuvable" in src_404.lower()
    assert "refus" in src_403.lower()
    for src in (src_404, src_403):
        assert "history.back" not in src
