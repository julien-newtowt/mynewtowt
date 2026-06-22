/*
 * Form helpers — double-submit prevention.
 *
 * Disables the submit button for 5 s after the form is submitted, so a
 * double-click doesn't create two records. Re-enabled after 5 s or on
 * page navigation.
 *
 * Opt out with: <form data-no-double-submit>
 *
 * Also handles <form data-confirm="message"> : asks for confirmation before
 * submitting (destructive actions). If declined, the submit is cancelled.
 */
(function () {
  "use strict";

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form) return;

    // Confirmation guard for destructive forms (data-confirm="…").
    var confirmMsg = form.dataset ? form.dataset.confirm : null;
    if (confirmMsg && !window.confirm(confirmMsg)) {
      e.preventDefault();
      return;
    }

    if (form.dataset.noDoubleSubmit !== undefined) return;

    var btns = form.querySelectorAll('button[type="submit"], input[type="submit"]');
    btns.forEach(function (btn) {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.dataset.prevOpacity = btn.style.opacity || "";
      btn.style.opacity = "0.6";
      btn.style.cursor = "wait";
      setTimeout(function () {
        btn.disabled = false;
        btn.style.opacity = btn.dataset.prevOpacity;
        btn.style.cursor = "";
      }, 5000);
    });
  });
})();
