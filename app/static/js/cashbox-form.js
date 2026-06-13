// Caisse de bord — filtrage des catégories par sens (encaissement /
// décaissement) + auto-submit du justificatif. Fichier externe (CSP stricte).
(function () {
  "use strict";

  function syncCategories(form) {
    var kindSel = form.querySelector("[data-kind-select]");
    var catSel = form.querySelector("[data-category-select]");
    if (!kindSel || !catSel) return;
    var kind = kindSel.value;
    var firstVisible = null;
    Array.prototype.forEach.call(catSel.options, function (opt) {
      var match = opt.getAttribute("data-kind") === kind;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match && firstVisible === null) firstVisible = opt;
    });
    // Si l'option sélectionnée n'appartient plus au sens, bascule sur la 1re.
    var cur = catSel.options[catSel.selectedIndex];
    if (!cur || cur.getAttribute("data-kind") !== kind) {
      if (firstVisible) firstVisible.selected = true;
    }
  }

  function bind() {
    document.querySelectorAll("[data-cashbox-form]").forEach(function (form) {
      var kindSel = form.querySelector("[data-kind-select]");
      if (kindSel) {
        kindSel.addEventListener("change", function () {
          syncCategories(form);
        });
        syncCategories(form); // état initial
      }
    });
    // Auto-submit d'un input fichier (ajout de justificatif inline).
    document.querySelectorAll("input[type=file][data-autosubmit]").forEach(function (inp) {
      inp.addEventListener("change", function () {
        if (inp.files && inp.files.length && inp.form) inp.form.submit();
      });
    });
    // Confirmation avant une action verrouillante (clôture).
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          e.preventDefault();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
