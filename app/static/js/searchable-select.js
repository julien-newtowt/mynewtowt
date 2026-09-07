/*
 * Select filtrable — recherche au clavier dans une longue liste d'options.
 *
 * Cible : tout `<select data-searchable>`. Le script insère un champ de
 * recherche au-dessus et masque les options qui ne correspondent pas.
 *
 * Amélioration progressive assumée : sans JavaScript (ou si le script échoue),
 * le `<select>` natif reste intégralement utilisable — c'est lui qui porte le
 * `name` et la valeur postée. Aucun composant maison ne remplace le contrôle
 * natif : le clavier, le lecteur d'écran et la saisie mobile continuent de
 * fonctionner comme le navigateur l'entend.
 *
 * Pattern IIFE identique à density.js / sidebar.js — aucun handler inline
 * (CSP stricte), aucune dépendance.
 */
(function () {
  "use strict";

  /* Normalise pour comparer sans accents ni casse : « Sénégal » doit remonter
     sur « senegal », faute de quoi la recherche rate la moitié du portefeuille. */
  function fold(value) {
    var s = String(value || "").toLowerCase();
    if (s.normalize) {
      s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
    return s;
  }

  function optionText(option) {
    return fold(option.textContent) + " " + fold(option.value);
  }

  function enhance(select) {
    if (select.dataset.searchableBound === "1") return;
    select.dataset.searchableBound = "1";

    var wrap = document.createElement("div");
    wrap.className = "searchable-select-wrap";
    var input = document.createElement("input");
    input.type = "search";
    input.className = "searchable-select-input";
    input.placeholder = select.dataset.searchablePlaceholder || "Filtrer la liste…";
    input.setAttribute("aria-label", input.placeholder);
    /* Le champ ne doit jamais partir dans le POST : il ne porte aucun nom. */
    input.autocomplete = "off";

    var count = document.createElement("span");
    count.className = "searchable-select-count";

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(input);
    wrap.appendChild(select);
    wrap.appendChild(count);

    var options = Array.prototype.slice.call(select.options);
    var haystack = options.map(optionText);

    function apply() {
      var needle = fold(input.value).trim();
      var shown = 0;
      for (var i = 0; i < options.length; i++) {
        var option = options[i];
        /* Une option vide (« — Aucun — ») reste toujours atteignable : la
           masquer empêcherait de revenir à « pas de valeur ». */
        var always = option.value === "";
        var match = always || !needle || haystack[i].indexOf(needle) !== -1;
        option.hidden = !match;
        option.disabled = !match && !always;
        if (match && !always) shown++;
      }
      count.textContent = needle ? shown + " résultat(s)" : "";
      /* Si la sélection courante vient d'être masquée, on bascule sur la
         première option visible — sinon le formulaire posterait une valeur
         que l'utilisateur ne voit plus. */
      var current = select.options[select.selectedIndex];
      if (current && current.hidden) {
        for (var j = 0; j < options.length; j++) {
          if (!options[j].hidden) {
            select.selectedIndex = j;
            break;
          }
        }
      }
    }

    input.addEventListener("input", apply);
    /* Entrée dans le champ de filtre ne doit pas soumettre le formulaire. */
    input.addEventListener("keydown", function (evt) {
      if (evt.key === "Enter") evt.preventDefault();
    });
  }

  function init() {
    var selects = document.querySelectorAll("select[data-searchable]");
    for (var i = 0; i < selects.length; i++) enhance(selects[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
