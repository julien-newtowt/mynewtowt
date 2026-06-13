// Déclenche l'impression navigateur (export A4 / PDF) sur clic d'un
// élément [data-print]. Fichier externe — CSP stricte, pas d'inline.
(function () {
  "use strict";
  function bind() {
    document.querySelectorAll("[data-print]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        window.print();
      });
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
