/**
 * FoodiePlug — Meal Planner script
 * static/js/meals.js
 *
 * Owns everything specific to the weekly timetable page:
 *   - Add meal / meal options (edit servings, move, duplicate) / remove modals
 *   - filling those modals from the clicked timetable cell
 *   - pantry availability preview in the Add meal modal
 *   - "add missing items to shopping list" form wiring
 *   - double-submit protection on every planner form
 *   - scrolling the timetable so today's column is visible on small screens
 *
 * dashboard.js stays global. This file's click handler runs in the capture
 * phase and stops propagation for this page's modal triggers, so a generic
 * [data-open-modal] handler elsewhere can never double-handle them.
 *
 * All forms are normal Django POSTs (no AJAX); CSRF tokens are untouched.
 */
(function () {
  'use strict';

  var OPEN = 'is-open';
  var LOCK = 'modal-open';

  var page = document.getElementById('mp-page');
  if (!page) return;

  function $(id) { return document.getElementById(id); }

  var modals = [].slice.call(document.querySelectorAll('.mp-modal'));
  var urls = $('mp-urls') ? $('mp-urls').dataset : {};
  var mealOptions = [];
  try { mealOptions = JSON.parse($('meal-options').textContent) || []; } catch (e) { mealOptions = []; }

  var current = null;     // data of the meal cell whose options are open
  var origin = null;      // element to return focus to when everything closes
  var pressOnBackdrop = false;

  function syncModalViewport() {
    var viewport = window.visualViewport;
    if (!viewport || window.innerWidth > 640) return;
    modals.forEach(function (modal) {
      if (!modal.classList.contains(OPEN)) return;
      modal.style.setProperty('--mp-viewport-height', viewport.height + 'px');
      modal.style.setProperty('--mp-viewport-offset-top', viewport.offsetTop + 'px');
    });
  }

  function clearModalViewport(modal) {
    modal.style.removeProperty('--mp-viewport-height');
    modal.style.removeProperty('--mp-viewport-offset-top');
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncModalViewport);
    window.visualViewport.addEventListener('scroll', syncModalViewport);
  }

  /* ------------------------------------------------------------------ helpers */
  
  function urlFor(template, id) {
    return (template || '').replace(
      /\/0\/$/,
      '/' + encodeURIComponent(id) + '/'
    );
  }


  function openModals() {
    return modals.filter(function (m) { return m.classList.contains(OPEN); });
  }

  function focusFirst(modal) {
    var target = modal.querySelector('select, input:not([type="hidden"]), textarea, button:not([data-close-modal])') ||
                 modal.querySelector('button');
    if (target) { try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); } }
  }

  function openModal(modal, trigger) {
    var others = openModals();
    others.forEach(function (o) { o.classList.remove(OPEN); o.setAttribute('aria-hidden', 'true'); });
    if (!others.length) origin = trigger;      // keep the original card when chaining modals

    modal.classList.add(OPEN);
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add(LOCK);
    if (window.innerWidth <= 640) document.documentElement.classList.add(LOCK);
    syncModalViewport();
    window.requestAnimationFrame(function () { focusFirst(modal); });
  }

  function closeModal(modal, restoreFocus) {
    modal.classList.remove(OPEN);
    modal.setAttribute('aria-hidden', 'true');
    clearModalViewport(modal);

    if (!openModals().length) {
      document.body.classList.remove(LOCK);
      document.documentElement.classList.remove(LOCK);
      if (restoreFocus !== false && origin && document.body.contains(origin)) {
        try { origin.focus({ preventScroll: true }); } catch (e) { origin.focus(); }
      }
      origin = null;
    }
  }

  function resetForms(scope) {
    [].slice.call((scope || document).querySelectorAll('.mp-modal form')).forEach(function (f) {
      f.dataset.submitting = '';
      var btn = f.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = btn.dataset.keepDisabled === '1';
        if (btn.dataset.label) btn.textContent = btn.dataset.label;
      }
    });
  }

  /* ------------------------------------------------------------------ add meal modal */
  function renderAvailability() {
    var box = $('add-meal-avail');
    var id = $('add-meal-select').value;
    var meal = mealOptions.filter(function (m) { return String(m.id) === String(id); })[0];

    if (!meal) { box.hidden = true; return; }

    box.hidden = false;
    box.className = 'mp-avail';

    if (!meal.total) {
      box.textContent = 'No ingredients are listed for this meal yet.';
    } else if (!meal.missing.length) {
      box.classList.add('is-ready');
      box.textContent = '\u2713 All ' + meal.total + ' ingredients are in your pantry (for 1 serving).';
    } else {
      box.classList.add(meal.available ? 'is-partial' : 'is-missing');
      box.textContent = meal.available + ' of ' + meal.total + ' ingredients available. Missing: ' +
                        meal.missing.join(', ') + '.';
    }
  }

  function fillAdd(trigger) {
    var form = $('add-meal-form');
    var today = $('add-meal-date').querySelector('option[selected]');
    form.reset();
    if (today) $('add-meal-date').value = today.value;

    if (trigger.dataset.date) $('add-meal-date').value = trigger.dataset.date;
    if (trigger.dataset.mealType) $('add-meal-type').value = trigger.dataset.mealType;
    renderAvailability();
  }

  /* ------------------------------------------------------------------ meal options modal */
  function updateMoveState() {
    if (!current) return;
    var same = $('move-date').value === current.date && $('move-type').value === current.mealType;
    $('move-submit').disabled = same;
    $('move-submit').dataset.keepDisabled = same ? '1' : '';
    $('move-hint').hidden = !same;
  }

  function fillMeal(trigger) {
    var d = trigger.dataset;
    current = d;

    $('meal-modal-title').textContent = d.mealName;
    $('meal-modal-slot').textContent = d.dateLabel + ' \u00b7 ' + d.mealTypeLabel;
    var cooked = d.isCooked === 'true';

    // Pantry status
    var box = $('meal-pantry');
    var list = $('meal-missing-list');
    var cookedStatus = $('meal-cooked-status');
    var missing = d.missing ? d.missing.split('|') : [];
    box.className = 'mp-avail';
    list.innerHTML = '';
    cookedStatus.hidden = !cooked;
    cookedStatus.textContent = cooked
      ? 'Cooked' + (d.cookedAt ? ' on ' + d.cookedAt : '') + '. Pantry quantities have been deducted.'
      : '';

    if (cooked) {
      box.classList.add('is-ready');
      box.textContent = 'This scheduled meal has already been cooked.';
    } else if (d.pantryState === 'unknown') {
      box.textContent = 'No ingredients are listed for this meal yet.';
    } else if (d.pantryState === 'ready') {
      box.classList.add('is-ready');
      box.textContent = '\u2713 Ready to cook \u2014 all ' + d.total + ' ingredients available.';
    } else {
      box.classList.add(d.pantryState === 'missing' ? 'is-missing' : 'is-partial');
      box.textContent = d.available + ' of ' + d.total + ' ingredients available; missing ingredients must be added before cooking.';
    }

    missing.forEach(function (label) {
      var li = document.createElement('li');
      li.textContent = label;           // textContent: never inject HTML
      list.appendChild(li);
    });
    list.hidden = !missing.length || cooked;

    var missingForm = $('meal-missing-form');
    missingForm.hidden = !missing.length || cooked;
    missingForm.action = urlFor(urls.missing, d.scheduleId);
    var cookForm = $('meal-cook-form');
    cookForm.hidden = cooked || d.pantryState !== 'ready';
    cookForm.action = urlFor(urls.cook, d.scheduleId);
    $('meal-servings-section').hidden = cooked;
    $('meal-move-section').hidden = cooked;
    $('meal-duplicate-section').hidden = cooked;
    $('meal-remove-link').hidden = cooked;

    // Servings
    $('meal-servings-form').action = urlFor(urls.servings, d.scheduleId);
    $('meal-servings').value = d.servings;

    // Move
    $('meal-move-form').action = urlFor(urls.move, d.scheduleId);
    $('move-meal-id').value = d.mealId;
    $('move-servings').value = d.servings;
    $('move-from-date').value = d.date;
    $('move-from-type').value = d.mealType;
    $('move-date').value = d.date;
    $('move-type').value = d.mealType;
    updateMoveState();

    // Duplicate (same meal type, another day)
    $('dup-meal-id').value = d.mealId;
    $('dup-servings').value = d.servings;
    $('dup-type').value = d.mealType;
    $('dup-date').value = d.date;

    // Meal detail link
    $('meal-detail-link').href = urlFor(urls.detail, d.mealId);
  }

  function fillRemove() {
    if (!current) return;
    $('remove-meal-form').action = urlFor(urls.remove, current.scheduleId);
    $('remove-meal-text').textContent =
      current.mealName + ' will be removed from ' + current.dateLabel + ' ' + current.mealTypeLabel.toLowerCase() + '.';
  }

  function fill(modal, trigger) {
    resetForms(modal);
    if (modal.id === 'add-meal-modal') fillAdd(trigger);
    else if (modal.id === 'meal-modal') fillMeal(trigger);
    else if (modal.id === 'remove-meal-modal') fillRemove();
  }

  /* ------------------------------------------------------------------ events */
  document.addEventListener('click', function (e) {
    var opener = e.target.closest('[data-open-modal]');
    if (opener) {
      var modal = $(opener.dataset.openModal);
      if (modal && modal.classList.contains('mp-modal')) {
        e.preventDefault();
        e.stopPropagation();
        fill(modal, opener);
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

    if (e.target === top && pressOnBackdrop) {      // press AND release on the backdrop
      e.stopPropagation();
      closeModal(top);
    }
  }, true);

  document.addEventListener('mousedown', function (e) {
    pressOnBackdrop = e.target.classList && e.target.classList.contains('mp-modal');
  }, true);

  document.addEventListener('keydown', function (e) {
    var top = openModals().pop();
    if (!top) return;

    if (e.key === 'Escape' || e.key === 'Esc') {
      e.preventDefault();
      closeModal(top);
      return;
    }

    if (e.key === 'Tab') {                            // keep focus inside the dialog
      var f = top.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  if ($('add-meal-select')) $('add-meal-select').addEventListener('change', renderAvailability);
  if ($('move-date')) $('move-date').addEventListener('change', updateMoveState);
  if ($('move-type')) $('move-type').addEventListener('change', updateMoveState);

  /* Normal Django POSTs: first submit goes through, repeats are blocked */
  [].slice.call(document.querySelectorAll('.mp-modal form')).forEach(function (form) {
    var btn = form.querySelector('button[type="submit"]');
    if (btn) btn.dataset.label = btn.textContent;

    form.addEventListener('submit', function (e) {
      if (form.dataset.submitting === '1') { e.preventDefault(); return; }
      form.dataset.submitting = '1';
      if (btn) {
        btn.disabled = true;
        btn.textContent = form.id === 'meal-cook-form' ? 'Cooking...' : 'Saving\u2026';
      }
    });
  });

  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    resetForms(document);
    openModals().forEach(function (m) { closeModal(m, false); });
  });

  /* ------------------------------------------------------------------ small-screen scroll to today */
  var scroller = page.querySelector('.tt-scroll');
  var todayHead = scroller && scroller.querySelector('th.is-today');
  if (scroller && todayHead && scroller.scrollWidth > scroller.clientWidth) {
    var corner = scroller.querySelector('.tt-corner');
    scroller.scrollLeft = Math.max(0, todayHead.offsetLeft - (corner ? corner.offsetWidth : 0) - 8);
  }

})();
