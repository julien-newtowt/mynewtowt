// COM-11 — ventilation multi-legs : lignes (leg × palettes) dynamiques + garde
// de réconciliation capacité (somme ventilée == palettes réservées).
(function () {
  "use strict";
  var form = document.getElementById("split-form");
  if (!form) return;
  var rowsEl = document.getElementById("split-rows");
  var addBtn = document.getElementById("split-add");
  var sumEl = document.getElementById("split-sum");
  var submitBtn = document.getElementById("split-submit");
  var optionsTpl = document.getElementById("split-leg-options");
  if (!rowsEl || !addBtn || !sumEl || !submitBtn || !optionsTpl) return;
  var booked = parseInt(form.getAttribute("data-booked"), 10) || 0;
  var optionsHtml = optionsTpl.innerHTML;

  function recompute() {
    var nums = rowsEl.querySelectorAll('input[name="palettes"]');
    var sum = 0;
    nums.forEach(function (i) {
      sum += parseInt(i.value, 10) || 0;
    });
    sumEl.textContent = sum;
    var legs = rowsEl.querySelectorAll('select[name="leg_ids"]');
    var seen = {};
    var dup = false;
    legs.forEach(function (s) {
      if (seen[s.value]) dup = true;
      seen[s.value] = true;
    });
    var valid = legs.length >= 1 && sum === booked && !dup;
    submitBtn.disabled = !valid;
    sumEl.style.color = sum === booked ? "var(--vert, #87bd29)" : "var(--cuivre, #b47148)";
  }

  function addRow() {
    var row = document.createElement("div");
    row.className = "flex items-center gap-2 mb-2";
    var sel = document.createElement("select");
    sel.name = "leg_ids";
    sel.innerHTML = optionsHtml;
    sel.addEventListener("change", recompute);
    var num = document.createElement("input");
    num.type = "number";
    num.name = "palettes";
    num.min = "1";
    num.placeholder = "palettes";
    num.style.maxWidth = "120px";
    num.addEventListener("input", recompute);
    var rm = document.createElement("button");
    rm.type = "button";
    rm.className = "btn btn-ghost btn-sm";
    rm.setAttribute("aria-label", "Retirer cette ligne");
    rm.textContent = "×";
    rm.addEventListener("click", function () {
      row.remove();
      recompute();
    });
    row.appendChild(sel);
    row.appendChild(num);
    row.appendChild(rm);
    rowsEl.appendChild(row);
    recompute();
  }

  addBtn.addEventListener("click", addRow);
  // Démarre avec deux lignes (une ventilation suppose ≥ 2 legs).
  addRow();
  addRow();
})();
