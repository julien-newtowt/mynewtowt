/*
 * Vente rapide (espèces) — panier client, encaissable hors connexion.
 *
 * Pourquoi un panier côté navigateur : le parcours écran par écran enchaîne
 * trois requêtes dépendantes (créer la vente, ajouter chaque ligne, encaisser),
 * la deuxième ayant besoin de la référence renvoyée par la première. Une file
 * d'attente ne peut pas rejouer cela. Ici le panier se construit localement et
 * part en **un seul POST atomique et idempotent** (`client_uuid`), que
 * `onboard-offline.js` sait conserver et rejouer au retour du réseau.
 *
 * Le panier est sérialisé dans un champ unique `cart` ("id:qté,id:qté") : la
 * file ne conserve qu'une valeur par nom de champ, des lignes répétées y
 * seraient écrasées.
 *
 * Les prix affichés ici ne servent qu'au total indicatif — le serveur relit
 * toujours le catalogue, il ne fait jamais confiance au client.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-quick-sale]");
  if (!root) return;

  var picker = root.querySelector("[data-qs-product]");
  var qtyInput = root.querySelector("[data-qs-qty]");
  var addBtn = root.querySelector("[data-qs-add]");
  var linesEl = root.querySelector("[data-qs-lines]");
  var totalEl = root.querySelector("[data-qs-total]");
  var cartField = root.querySelector("[data-qs-cart]");
  var uuidField = root.querySelector("[data-qs-uuid]");
  var submitBtn = root.querySelector("[data-qs-submit]");
  var currency = root.getAttribute("data-currency") || "EUR";

  var cart = []; // [{id, label, price, qty}]

  function makeUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "qs-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2, 14);
  }

  function money(value) {
    return value.toFixed(2).replace(".", ",");
  }

  function total() {
    return cart.reduce(function (sum, line) {
      return sum + line.price * line.qty;
    }, 0);
  }

  function render() {
    linesEl.innerHTML = "";
    cart.forEach(function (line, index) {
      var row = document.createElement("tr");

      var label = document.createElement("td");
      label.textContent = line.label;
      row.appendChild(label);

      var qty = document.createElement("td");
      qty.className = "mono";
      qty.textContent = String(line.qty);
      row.appendChild(qty);

      var amount = document.createElement("td");
      amount.className = "mono";
      amount.textContent = money(line.price * line.qty) + " " + currency;
      row.appendChild(amount);

      var actions = document.createElement("td");
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-ghost btn-sm";
      remove.textContent = "Retirer";
      remove.addEventListener("click", function () {
        cart.splice(index, 1);
        render();
      });
      actions.appendChild(remove);
      row.appendChild(actions);

      linesEl.appendChild(row);
    });

    totalEl.textContent = money(total()) + " " + currency;
    cartField.value = cart
      .map(function (line) {
        return line.id + ":" + line.qty;
      })
      .join(",");
    submitBtn.disabled = cart.length === 0;
  }

  function addSelected() {
    var option = picker.options[picker.selectedIndex];
    if (!option || !option.value) return;
    var qty = parseFloat((qtyInput.value || "1").replace(",", "."));
    if (!isFinite(qty) || qty <= 0) {
      qtyInput.focus();
      return;
    }
    var id = option.value;
    var found = cart.filter(function (line) {
      return line.id === id;
    })[0];
    if (found) {
      found.qty = Math.round((found.qty + qty) * 1000) / 1000;
    } else {
      cart.push({
        id: id,
        label: option.getAttribute("data-label") || option.textContent.trim(),
        price: parseFloat(option.getAttribute("data-price") || "0"),
        qty: qty
      });
    }
    qtyInput.value = "1";
    render();
  }

  addBtn.addEventListener("click", addSelected);
  qtyInput.addEventListener("keydown", function (evt) {
    if (evt.key === "Enter") {
      evt.preventDefault();
      addSelected();
    }
  });

  // Un identifiant par vente : renouvelé après chaque envoi réussi, pour que
  // deux ventes successives ne se confondent pas.
  uuidField.value = makeUuid();
  root.addEventListener("submit", function () {
    window.setTimeout(function () {
      uuidField.value = makeUuid();
      cart = [];
      render();
    }, 0);
  });

  render();
})();
