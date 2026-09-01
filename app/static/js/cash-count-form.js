/*
 * Contrôle de caisse — comptage vivant et confirmation avant enregistrement.
 *
 * Deux besoins remontés du bord (Cdt ANEMOS, 2026-08-29) :
 *
 *  1. voir le total de sa déclaration **pendant** la saisie, et son écart au
 *     solde théorique, pour vérifier ses chiffres avant de les enregistrer ;
 *  2. une confirmation explicite, une erreur de manipulation ayant enregistré
 *     une déclaration involontaire — un état de caisse est définitif, le
 *     registre n'a ni route de modification ni route de suppression.
 *
 * Le total affiché ici est un **secours de saisie**, jamais la valeur retenue :
 * le serveur recalcule tout depuis les quantités (cf. services/cash_count.py).
 * Rien n'est pré-rempli à partir du théorique — un comptage qu'on aligne sur
 * l'attendu ne contrôle rien.
 *
 * Les montants sont sommés en centimes (entiers) : 0.1 + 0.2 en virgule
 * flottante ne fait pas 0.3, et une caisse qui affiche 1988,34 au lieu de
 * 1988,35 ruine la confiance dans l'outil.
 */
(function () {
  "use strict";

  /** Convertit une saisie décimale ("31,47" ou "31.47") en centimes entiers. */
  function toCents(raw) {
    if (raw === null || raw === undefined) return 0;
    var text = String(raw).trim().replace(",", ".").replace(/\s/g, "");
    if (!text) return 0;
    var value = Number(text);
    if (!isFinite(value)) return NaN;
    return Math.round(value * 100);
  }

  function formatCents(cents) {
    var sign = cents < 0 ? "-" : "";
    var abs = Math.abs(cents);
    return sign + Math.floor(abs / 100) + "," + String(abs % 100).padStart(2, "0");
  }

  function signedCents(cents) {
    return (cents > 0 ? "+" : "") + formatCents(cents);
  }

  /** Total compté d'un bloc devise, en centimes. NaN si une saisie est illisible. */
  function blockTotal(block) {
    var total = 0;
    block.querySelectorAll("[data-denom]").forEach(function (input) {
      var qty = input.value.trim();
      if (!qty) return;
      var count = Number(qty);
      // Le serveur lit un entier (`int(raw)`) : arrondir ici afficherait un
      // total que l'enregistrement refuserait.
      if (!isFinite(count) || count < 0 || count % 1 !== 0) {
        total = NaN;
        return;
      }
      total += toCents(input.getAttribute("data-denom")) * count;
    });
    var bulk = block.querySelector("[data-bulk]");
    if (bulk) total += toCents(bulk.value);
    return total;
  }

  function refresh(block) {
    var declared = block.querySelector("[data-declare]");
    var isDeclared = !declared || declared.checked;
    var theoretical = toCents(block.getAttribute("data-computed"));
    var counted = blockTotal(block);
    var totalEl = block.querySelector("[data-counted-total]");
    var varianceEl = block.querySelector("[data-variance]");
    var hintEl = block.querySelector("[data-variance-hint]");

    if (!isDeclared) {
      if (totalEl) totalEl.textContent = "—";
      if (varianceEl) {
        varianceEl.textContent = "—";
        varianceEl.className = "value mono";
      }
      if (hintEl) hintEl.textContent = "Devise non déclarée : cochez-la si la caisse en contient.";
      return { declared: false };
    }

    if (isNaN(counted)) {
      if (totalEl) totalEl.textContent = "?";
      if (varianceEl) {
        varianceEl.textContent = "?";
        varianceEl.className = "value mono text-error";
      }
      if (hintEl) hintEl.textContent = "Une quantité saisie n'est pas un nombre entier positif.";
      return { declared: true, invalid: true };
    }

    var variance = counted - theoretical;
    if (totalEl) totalEl.textContent = formatCents(counted);
    if (varianceEl) {
      varianceEl.textContent = signedCents(variance);
      varianceEl.className =
        "value mono " + (variance < 0 ? "text-error" : variance > 0 ? "text-warning" : "text-success");
    }
    if (hintEl) {
      hintEl.textContent =
        variance === 0
          ? "Le comptage correspond au solde théorique."
          : variance > 0
            ? "Excédent constaté : la caisse contient plus que le solde théorique."
            : "Manquant constaté : la caisse contient moins que le solde théorique.";
    }
    return {
      declared: true,
      currency: block.getAttribute("data-currency"),
      counted: counted,
      theoretical: theoretical,
      variance: variance,
    };
  }

  /** Message de confirmation : le récapitulatif exact de ce qui va être écrit. */
  function confirmMessage(form, blocks) {
    var lines = [];
    blocks.forEach(function (state) {
      if (!state.declared || state.invalid) return;
      lines.push(
        "· " +
          state.currency +
          " : " +
          formatCents(state.counted) +
          " compté / " +
          formatCents(state.theoretical) +
          " théorique → écart " +
          signedCents(state.variance)
      );
    });
    var trigger = form.querySelector("[name=trigger]");
    var when = form.querySelector("[name=counted_on]");
    var header =
      "Enregistrer cet état de caisse ?\n\n" +
      (trigger ? "Motif : " + trigger.options[trigger.selectedIndex].text + "\n" : "") +
      (when ? "Date du comptage : " + when.value + "\n" : "");
    if (!lines.length) {
      return header + "\nAucune devise déclarée — l'enregistrement sera refusé.";
    }
    return (
      header +
      "\n" +
      lines.join("\n") +
      "\n\nUn état de caisse est définitif : il ne peut être ni modifié ni supprimé." +
      (trigger && trigger.value === "fin_embarquement"
        ? "\nCe motif fige en outre la comptabilité jusqu'à la date du comptage."
        : "")
    );
  }

  function bind() {
    document.querySelectorAll("[data-cash-count-form]").forEach(function (form) {
      var blocks = Array.prototype.slice.call(form.querySelectorAll("[data-cash-block]"));
      if (!blocks.length) return;

      function recompute() {
        var states = blocks.map(refresh);
        // forms.js (chargé globalement) porte la confirmation : on lui fournit
        // un message à jour plutôt que d'ajouter un second window.confirm, qui
        // ferait s'ouvrir deux boîtes de dialogue à la suite.
        form.dataset.confirm = confirmMessage(form, states);
      }

      form.addEventListener("input", recompute);
      form.addEventListener("change", recompute);
      recompute();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
