// app.js — global UI behaviors for AutoApply.
//
// Two features:
//   1. .file-drop — styled wrapper around <input type=file>. Mirrors the
//      filename + adds drag-and-drop. Wired on DOMContentLoaded (file-drops
//      are static template fragments, not HTMX-swapped today).
//   2. Themed confirm-modal — replaces window.confirm() for any HTMX
//      element with hx-confirm. Native dialogs ignore the dark theme.

(function wireFileDrops() {
  function wireFileDrop(drop) {
    var input = drop.querySelector('input[type=file]');
    if (!input) return;
    var fname = drop.querySelector('.file-drop__filename');
    var emptyText = fname ? (fname.dataset.empty || '') : '';

    if (input.disabled) drop.classList.add('is-disabled');

    function reflect() {
      var f = input.files && input.files[0];
      if (f) {
        drop.classList.add('is-selected');
        if (fname) {
          fname.textContent = f.name;
          fname.classList.add('is-selected');
        }
      } else {
        drop.classList.remove('is-selected');
        if (fname) {
          fname.textContent = emptyText;
          fname.classList.remove('is-selected');
        }
      }
    }
    input.addEventListener('change', reflect);
    reflect();

    // Drag-and-drop — forward events from the visual .file-drop label to
    // the visually-hidden native input.
    ['dragenter', 'dragover'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        if (input.disabled) return;
        e.preventDefault();
        drop.classList.add('is-dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault();
        drop.classList.remove('is-dragover');
      });
    });
    drop.addEventListener('drop', function (e) {
      if (input.disabled) return;
      var dt = e.dataTransfer;
      if (dt && dt.files && dt.files.length) {
        input.files = dt.files;
        reflect();
      }
    });
  }
  document.querySelectorAll('.file-drop').forEach(wireFileDrop);
})();

(function wireOpenModalTriggers() {
  // Generic modal opener wired via event delegation.
  //   <button data-open-modal="my-modal-id">Open</button>
  //   <div id="my-modal-id" role="dialog" hidden>...
  //     <element data-modal-close>Cancel</element>
  //   </div>
  // Click on the backdrop with [data-modal-close] also closes. Escape closes
  // any open dialog (excluding #confirm-modal which has its own lifecycle).

  function closeAllModals() {
    document.querySelectorAll('[role="dialog"]:not([hidden])').forEach(function (m) {
      if (m.id === 'confirm-modal') return;  // htmx confirm has its own state
      m.hidden = true;
    });
  }

  document.addEventListener('click', function (e) {
    var opener = e.target.closest && e.target.closest('[data-open-modal]');
    if (opener) {
      e.preventDefault();
      var id = opener.getAttribute('data-open-modal');
      var modal = document.getElementById(id);
      if (!modal) return;
      modal.hidden = false;
      var firstInput = modal.querySelector('input:not([type=hidden]), textarea');
      setTimeout(function () { if (firstInput) firstInput.focus(); }, 0);
      return;
    }
    var closer = e.target.closest && e.target.closest('[data-modal-close]');
    if (closer) {
      var m = closer.closest('[role="dialog"]');
      if (m) m.hidden = true;
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAllModals();
  });
})();

(function wireTypeToConfirm() {
  // Live-validate forms that require the user to type a specific string
  // (e.g. the profile name) before the submit button enables.
  // Markup:
  //   <form data-confirm-name="afeef">
  //     <input data-confirm-input>
  //     <button data-confirm-submit disabled>...</button>
  //   </form>
  // Server-side validation still runs independently — this is just a UX gate.
  document.querySelectorAll('[data-confirm-name]').forEach(function (form) {
    var expected = form.dataset.confirmName;
    var input = form.querySelector('[data-confirm-input]');
    var btn = form.querySelector('[data-confirm-submit]');
    if (!input || !btn) return;
    function refresh() {
      btn.disabled = (input.value.trim() !== expected);
    }
    input.addEventListener('input', refresh);
    refresh();
  });
})();

(function wireOverflowMenus() {
  // Generic "⋯" menu wired via event delegation so it survives HTMX swaps.
  // Markup contract:
  //   <div class="row-actions">
  //     <button class="menu-trigger" aria-haspopup="menu" aria-expanded="false">⋯</button>
  //     <div class="menu-panel" role="menu">...</div>
  //   </div>
  // Click outside or Escape closes any open panel. Only one panel open at a time.

  function closeAll(except) {
    document.querySelectorAll('.menu-panel.is-open').forEach(function (p) {
      if (p === except) return;
      p.classList.remove('is-open');
      var trigger = p.parentElement && p.parentElement.querySelector('.menu-trigger');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  document.addEventListener('click', function (e) {
    var trigger = e.target.closest && e.target.closest('.menu-trigger');
    if (trigger) {
      e.stopPropagation();
      var panel = trigger.parentElement.querySelector('.menu-panel');
      var willOpen = !panel.classList.contains('is-open');
      closeAll(willOpen ? panel : null);
      panel.classList.toggle('is-open', willOpen);
      trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      return;
    }
    // Click anywhere outside an open panel closes it.
    if (!(e.target.closest && e.target.closest('.menu-panel'))) {
      closeAll(null);
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAll(null);
  });

  // After HTMX swaps the row, the panel goes away — no extra cleanup needed.
  // But if the row that owned the open panel is REPLACED (track/delete),
  // call closeAll to be safe.
  document.body.addEventListener('htmx:afterSwap', function () { closeAll(null); });
})();

(function wireConfirmModal() {
  var modal = document.getElementById('confirm-modal');
  if (!modal) return;
  var textEl = document.getElementById('confirm-modal-text');
  var okBtn  = document.getElementById('confirm-modal-ok');
  var cxBtn  = document.getElementById('confirm-modal-cancel');
  var resolver = null;

  function open(question) {
    textEl.textContent = question;
    modal.hidden = false;
    // Focus Confirm so Enter immediately commits (matches native dialog UX).
    setTimeout(function () { okBtn.focus(); }, 0);
    return new Promise(function (res) { resolver = res; });
  }
  function close(result) {
    modal.hidden = true;
    var r = resolver; resolver = null;
    if (r) r(result);
  }
  okBtn.addEventListener('click', function () { close(true); });
  cxBtn.addEventListener('click', function () { close(false); });
  modal.querySelector('.confirm-modal-backdrop')
       .addEventListener('click', function () { close(false); });
  document.addEventListener('keydown', function (e) {
    if (modal.hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); close(false); }
  });

  // Intercept HTMX's default confirm() and route through our themed modal.
  // evt.detail.question is the hx-confirm string; calling issueRequest(true)
  // re-fires the original HTMX request and skips re-confirmation.
  document.body.addEventListener('htmx:confirm', function (evt) {
    if (!evt.detail.question) return;
    evt.preventDefault();
    open(evt.detail.question).then(function (ok) {
      if (ok) evt.detail.issueRequest(true);
    });
  });
})();
