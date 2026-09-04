// Grille tarifaire — ajout/suppression de tranches de remplissage à la volée.
// Fichier externe : CSP stricte, pas d'inline.
//
// Les trois champs d'une tranche (`bracket_label`, `bracket_max_qty`,
// `bracket_coeff`) sont postés comme des listes parallèles : c'est la POSITION
// de la ligne qui associe un libellé à sa quantité et à son coefficient. Ajouter
// ou retirer une ligne se fait donc en insérant ou en retirant les trois champs
// ensemble — jamais un seul, sinon tout le tableau se décale d'un cran et les
// coefficients changent de tranche en silence.
//
// Aucun index à renuméroter (contrairement à `cargo-rows.js`) : le serveur lit
// les listes dans l'ordre du DOM.
(function () {
  "use strict";

  function bind() {
    var body = document.getElementById("bracket-rows");
    var tpl = document.getElementById("bracket-row-tpl");
    if (!body || !tpl) return;

    function rows() {
      return body.querySelectorAll("[data-bracket-row]");
    }

    // Le serveur refuse un enregistrement sans aucune tranche. On masque donc
    // la suppression quand il n'en reste qu'une : mieux vaut un bouton absent
    // qu'un bouton qui produit une erreur 400 à l'enregistrement.
    function refresh() {
      var present = rows();
      present.forEach(function (row) {
        var btn = row.querySelector("[data-remove-bracket]");
        if (btn) btn.hidden = present.length <= 1;
      });
    }

    function blankRow() {
      var holder = document.createElement("tbody");
      holder.innerHTML = tpl.innerHTML.trim();
      return holder.firstElementChild;
    }

    body.addEventListener("click", function (e) {
      if (!e.target.closest) return;

      var add = e.target.closest("[data-add-bracket]");
      if (add) {
        // Insérée JUSTE APRÈS la ligne cliquée : on ajoute une tranche « ici »,
        // là où l'opérateur regarde, pas en bas d'un tableau qu'il devra
        // reparcourir. Le serveur retrie de toute façon par quantité.
        var from = add.closest("[data-bracket-row]");
        var fresh = blankRow();
        from.parentNode.insertBefore(fresh, from.nextSibling);
        refresh();
        var first = fresh.querySelector("input");
        if (first) first.focus();
        return;
      }

      var remove = e.target.closest("[data-remove-bracket]");
      if (remove) {
        var row = remove.closest("[data-bracket-row]");
        if (row && rows().length > 1) {
          row.remove();
          refresh();
        }
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
