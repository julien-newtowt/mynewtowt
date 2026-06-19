// Formulaire « Nouvelle réservation (opérateur) » : recherche client + batches.
(function () {
  "use strict";

  // ── Moteur de recherche client : filtre les <option> du select ──
  function initClientSearch() {
    var search = document.getElementById("client-search");
    var select = document.getElementById("client_account_id");
    if (!search || !select) return;
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      var firstVisible = null;
      Array.prototype.forEach.call(select.options, function (opt) {
        if (!opt.value) {
          opt.hidden = false; // garde le placeholder
          return;
        }
        var match = opt.textContent.toLowerCase().indexOf(q) !== -1;
        opt.hidden = !match;
        if (match && firstVisible === null) firstVisible = opt;
      });
      // Si l'option sélectionnée est masquée, bascule sur la 1re visible.
      if (select.selectedOptions[0] && select.selectedOptions[0].hidden) {
        select.value = firstVisible ? firstVisible.value : "";
      }
    });
  }

  // ── Batches cargo : n'afficher que Batch 1, révéler les suivants ──
  function initBatches() {
    var addBtn = document.getElementById("add-batch");
    if (!addBtn) return;
    function nextHidden() {
      return document.querySelector(".cargo-batch[hidden]");
    }
    function refresh() {
      if (!nextHidden()) {
        addBtn.setAttribute("hidden", "");
      }
    }
    addBtn.addEventListener("click", function () {
      var b = nextHidden();
      if (b) {
        b.removeAttribute("hidden");
        var f = b.querySelector("input, select, textarea");
        if (f) f.focus();
      }
      refresh();
    });
    refresh();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initClientSearch();
    initBatches();
  });
})();
