// Booking wizard — lignes cargo multiples (ajout/retrait). Fichier externe,
// CSP stricte (pas d'inline). Le backend tolère les trous d'index, donc
// pas besoin de renuméroter à la suppression : on incrémente un compteur.
(function () {
  "use strict";

  function bind() {
    var rows = document.getElementById("cargo-rows");
    var tpl = document.getElementById("cargo-row-tpl");
    var addBtn = document.querySelector("[data-add-row]");
    if (!rows || !tpl || !addBtn) return;

    var next = parseInt(rows.getAttribute("data-next-index") || "1", 10);
    if (!isFinite(next) || next < 1) next = 1;

    function refreshRemoveButtons() {
      var present = rows.querySelectorAll("[data-cargo-row]");
      present.forEach(function (row) {
        var btn = row.querySelector("[data-remove-row]");
        if (btn) btn.style.display = present.length > 1 ? "" : "none";
      });
    }

    addBtn.addEventListener("click", function () {
      var html = tpl.innerHTML.replace(/__I__/g, String(next));
      next += 1;
      var holder = document.createElement("div");
      holder.innerHTML = html.trim();
      var row = holder.firstElementChild;
      rows.appendChild(row);
      refreshRemoveButtons();
    });

    rows.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-remove-row]") : null;
      if (!btn) return;
      var row = btn.closest("[data-cargo-row]");
      if (row && rows.querySelectorAll("[data-cargo-row]").length > 1) {
        row.remove();
        refreshRemoveButtons();
      }
    });

    refreshRemoveButtons();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
