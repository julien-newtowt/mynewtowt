/* FDS upload — confort de saisie (Vague 1) : aperçu du nom de fichier choisi
   et dépôt par glisser-déposer. Progressif : l'<input type=file> reste
   pleinement fonctionnel sans ce script. Externe → compatible CSP stricte. */
(function () {
  "use strict";

  function init() {
    var input = document.querySelector("[data-fds-input]");
    if (!input) return;
    var zone = input.closest("[data-fds-zone]");
    var label = document.querySelector("[data-fds-filename]");

    function show() {
      if (!label) return;
      label.textContent = input.files && input.files.length ? input.files[0].name : "";
    }
    input.addEventListener("change", show);

    if (!zone) return;
    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.remove("is-dragover");
      });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        try {
          input.files = e.dataTransfer.files;
        } catch (err) {
          return; // navigateur n'autorisant pas l'assignation : l'input clic reste OK
        }
        show();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
