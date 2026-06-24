// ONB-04 — autocomplete des @mentions du fil de bord.
// Détecte un token "@prefix" devant le curseur, interroge
// /captain/messages/users?q=prefix et propose les utilisateurs staff.
(function () {
  "use strict";
  var ta = document.querySelector("textarea[data-mention-input]");
  if (!ta) return;

  var wrap = ta.parentNode;
  wrap.style.position = "relative";
  var box = document.createElement("div");
  box.className = "mention-suggest";
  box.style.position = "absolute";
  box.style.zIndex = "50";
  box.style.left = "0";
  box.style.right = "0";
  box.style.background = "var(--blanc, #fff)";
  box.style.border = "1px solid var(--cuivre, #b47148)";
  box.style.borderRadius = "6px";
  box.style.maxHeight = "180px";
  box.style.overflowY = "auto";
  box.style.display = "none";
  wrap.appendChild(box);

  var items = [];
  var active = -1;

  function currentToken() {
    var pos = ta.selectionStart;
    var upto = ta.value.slice(0, pos);
    var m = upto.match(/@([A-Za-z0-9_]*)$/);
    return m ? { q: m[1], start: pos - m[1].length } : null;
  }

  function hide() {
    box.style.display = "none";
    active = -1;
    items = [];
  }

  function render() {
    if (!items.length) {
      hide();
      return;
    }
    box.innerHTML = "";
    items.forEach(function (u, i) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "mention-suggest-item";
      el.style.display = "block";
      el.style.width = "100%";
      el.style.textAlign = "left";
      el.style.padding = "4px 8px";
      el.style.border = "none";
      el.style.background = i === active ? "var(--sable, #efe6d6)" : "transparent";
      el.style.cursor = "pointer";
      el.textContent = "@" + u.username + " — " + u.full_name;
      el.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
        pick(u);
      });
      box.appendChild(el);
    });
    box.style.display = "block";
  }

  function pick(u) {
    var tok = currentToken();
    if (!tok) return;
    var before = ta.value.slice(0, tok.start - 1); // retire le "@"
    var after = ta.value.slice(ta.selectionStart);
    var insert = "@" + u.username + " ";
    ta.value = before + insert + after;
    var caret = (before + insert).length;
    ta.setSelectionRange(caret, caret);
    ta.focus();
    hide();
  }

  function refresh() {
    var tok = currentToken();
    if (!tok || tok.q.length < 1) {
      hide();
      return;
    }
    fetch("/captain/messages/users?q=" + encodeURIComponent(tok.q), {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        return r.ok ? r.json() : [];
      })
      .then(function (data) {
        items = data || [];
        active = -1;
        render();
      })
      .catch(function () {
        hide();
      });
  }

  ta.addEventListener("input", refresh);
  ta.addEventListener("keydown", function (e) {
    if (box.style.display === "none") return;
    if (e.key === "ArrowDown") {
      active = Math.min(active + 1, items.length - 1);
      render();
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      active = Math.max(active - 1, 0);
      render();
      e.preventDefault();
    } else if (e.key === "Enter" && active >= 0) {
      pick(items[active]);
      e.preventDefault();
    } else if (e.key === "Escape") {
      hide();
    }
  });
  document.addEventListener("click", function (e) {
    if (e.target !== ta) hide();
  });
})();
