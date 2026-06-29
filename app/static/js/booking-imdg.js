// Booking wizard — révèle la section IMDG / FDS quand au moins une ligne cargo
// est marquée « marchandises dangereuses ». Fichier externe (CSP stricte).
// La validation « FDS obligatoire si dangereux » reste appliquée côté serveur.
(function () {
  "use strict";

  function bind() {
    var section = document.querySelector("[data-imdg-section]");
    var rowsHolder = document.getElementById("cargo-rows");
    if (!section || !rowsHolder) return;

    var classInput = section.querySelector('input[name="imdg_class"]');
    var fdsInput = section.querySelector('input[name="fds_file"]');

    function anyHazardous() {
      var boxes = document.querySelectorAll("[data-hazardous]");
      for (var i = 0; i < boxes.length; i++) {
        if (boxes[i].checked) return true;
      }
      return false;
    }

    function refresh() {
      var on = anyHazardous();
      section.hidden = !on;
      // Champs requis seulement quand la section est visible (sinon le navigateur
      // bloquerait la soumission sur un champ caché).
      if (classInput) classInput.required = on;
      if (fdsInput) fdsInput.required = on;
    }

    // Délégation : couvre aussi les lignes ajoutées dynamiquement.
    document.addEventListener("change", function (e) {
      if (e.target && e.target.matches && e.target.matches("[data-hazardous]")) {
        refresh();
      }
    });

    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
