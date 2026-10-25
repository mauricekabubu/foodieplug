/**
 * FoodiePlug — Budget page script
 * static/js/budget.js
 *
 * Owns Budget-page behaviour only (dashboard.js stays global):
 *   - quick-add / ask / savings modals (open, close, backdrop, Escape, focus, scroll lock)
 *   - client-side expense search
 *   - weekly spending bar chart (from the json_script payload)
 *   - "Ask FoodiePlug" prompts (explains instead of navigating while the AI route is "#")
 *   - double-submit protection on every form
 *
 * All forms stay normal Django POSTs. Nothing here calls preventDefault on a real submit.
 */
(function () {
  'use strict';

  var page = document.getElementById('bd-page');
  if (!page) return;

  function $(id) { return document.getElementById(id); }

  function toast(message, level) {
    level = level || 'success';
    if (typeof window.showToast === 'function') { window.showToast(message, level); return; }
    var container = $('toast-container');
    if (!container) return;
    var t = document.createElement('div');
    t.className = 'toast toast-' + level;
    t.textContent = message;
    container.appendChild(t);
    setTimeout(function () { t.remove(); }, 4000);
  }

  /* ------------------------------------------------------------------ modals */
  var modals = [].slice.call(document.querySelectorAll('.bd-modal'));
  var origin = null;

  function openModals() { return modals.filter(function (m) { return m.classList.contains('is-open'); }); }

  function openModal(modal, trigger) {
    openModals().forEach(function (m) { m.classList.remove('is-open'); m.setAttribute('aria-hidden', 'true'); });
    origin = trigger;
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    document.documentElement.classList.add('modal-open');
    window.requestAnimationFrame(function () {
      var first = modal.querySelector('input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])');
      if (first) {
        try { first.focus({ preventScroll: true }); } catch (error) { first.focus(); }
      }
    });
  }

  function closeModal(modal) {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    if (!openModals().length) {
      document.body.classList.remove('modal-open');
      document.documentElement.classList.remove('modal-open');
      if (origin && document.body.contains(origin)) origin.focus();
      origin = null;
    }
  }

  // Capture phase: this file is the sole owner of these modals.
  document.addEventListener('click', function (e) {
    var opener = e.target.closest('[data-open-modal]');
    if (opener) {
      var modal = $(opener.dataset.openModal);
      if (modal && modal.classList.contains('bd-modal')) {
        e.preventDefault();
        e.stopPropagation();
        var today = modal.querySelector('[data-default-today]');
        if (today && !today.value) today.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
        openModal(modal, opener);
        return;
      }
    }

    var top = openModals().pop();
    if (!top) return;

    var closer = e.target.closest('[data-close-modal]');
    if (closer && top.contains(closer)) {
      e.preventDefault();
      e.stopPropagation();
      closeModal(top);
      return;
    }
    if (e.target === top) { e.stopPropagation(); closeModal(top); }
  }, true);

  document.addEventListener('keydown', function (e) {
    var top = openModals().pop();
    if (!top) return;

    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeModal(top); return; }

    if (e.key === 'Tab') {                                  // keep focus inside the dialog
      var f = top.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  /* ------------------------------------------------------------------ expense search */
  var search = $('expense-search');
  var list = $('expense-list');
  var emptyMsg = $('search-empty');

  if (search && list) {
    var rows = [].slice.call(list.querySelectorAll('.bd-expense'));
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      var visible = 0;
      rows.forEach(function (row) {
        var show = !q || (row.getAttribute('data-search') || '').indexOf(q) > -1;
        row.hidden = !show;
        if (show) visible++;
      });
      if (emptyMsg) emptyMsg.hidden = visible > 0;
    });
  }

  /* ------------------------------------------------------------------ weekly spending chart */
  var dataEl = $('spending-data');
  var chart = $('spending-chart');

  if (dataEl && chart) {
    var points = [];
    try { points = JSON.parse(dataEl.textContent) || []; } catch (e) { points = []; }

    if (points.length) {
      var max = Math.max.apply(null, points.map(function (p) { return Number(p.amount) || 0; })) || 1;

      points.forEach(function (p, i) {
        var col = document.createElement('div');
        col.className = 'bd-chart-col';

        var bar = document.createElement('div');
        bar.className = 'bd-chart-bar' + (i === points.length - 1 ? ' is-last' : '');
        bar.style.height = Math.max(4, Math.round((Number(p.amount) / max) * 100)) + '%';
        bar.title = 'Week of ' + p.label + ': KES ' + Number(p.amount).toLocaleString('en-KE');

        var label = document.createElement('span');
        label.className = 'bd-chart-label';
        label.textContent = p.label;

        col.appendChild(bar);
        col.appendChild(label);
        chart.appendChild(col);
      });
    }
  }

  /* ------------------------------------------------------------------ Ask FoodiePlug */
  var config = $('bd-config') ? $('bd-config').dataset : {};
  var aiUrl = config.aiUrl;
  var aiReady = !!aiUrl && aiUrl !== '#';

  function ask(question) {
    if (aiReady) {
      window.location.href = aiUrl + (aiUrl.indexOf('?') > -1 ? '&' : '?') + 'q=' + encodeURIComponent(question);
    } else {
      toast("Ask FoodiePlug isn't connected on this page yet.", 'info');
    }
  }

  document.addEventListener('click', function (e) {
    var chip = e.target.closest('[data-ai-prompt]');
    if (!chip) return;
    e.preventDefault();
    ask(chip.dataset.aiPrompt);
  });

  var aiForm = $('ai-form');
  if (aiForm) {
    aiForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var q = ($('ai-question').value || '').trim();
      if (q) ask(q); else $('ai-question').focus();
    });
  }

  /* ------------------------------------------------------------------ double-submit guard (real POSTs) */
  [].slice.call(document.querySelectorAll('form[method="post"]')).forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (form.dataset.submitting === '1') { e.preventDefault(); return; }
      form.dataset.submitting = '1';
      var btn = form.querySelector('button[type="submit"]');
      if (btn) { btn.dataset.label = btn.textContent; btn.disabled = true; btn.textContent = 'Saving\u2026'; }
    });
  });

  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    [].slice.call(document.querySelectorAll('form[method="post"]')).forEach(function (form) {
      form.dataset.submitting = '';
      var btn = form.querySelector('button[type="submit"]');
      if (btn) { btn.disabled = false; if (btn.dataset.label) btn.textContent = btn.dataset.label; }
    });
    openModals().forEach(closeModal);
  });

})();
