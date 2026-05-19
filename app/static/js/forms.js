/*
 * Form helpers — double-submit prevention.
 *
 * Disables the submit button for 5 s after the form is submitted, so a
 * double-click doesn't create two records. Re-enabled after 5 s or on
 * page navigation.
 *
 * Opt out with: <form data-no-double-submit>
 */
(function () {
  "use strict";

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || form.dataset.noDoubleSubmit !== undefined) return;

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
