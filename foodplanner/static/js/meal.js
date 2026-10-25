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
  if (page.dataset.mealModalController === 'ready') return;
  page.dataset.mealModalController = 'ready';

  function $(id) { return document.getElementById(id); }

  var modals = [].slice.call(document.querySelectorAll('.mp-modal'));
  var urls = $('mp-urls') ? $('mp-urls').dataset : {};
  var mealOptions = [];
  try { mealOptions = JSON.parse($('meal-options') ? $('meal-options').textContent : '[]') || []; } catch (e) { mealOptions = []; }

  var current = null;     // data of the meal cell whose options are open
  var modalStack = [];
  var viewportTracking = false;

  /* ------------------------------------------------------------------ helpers */
  
  function urlFor(template, id) {
    return (template || '').replace(
      /\/0\/$/,
      '/' + encodeURIComponent(id) + '/'
    );
  }


  function topModal() {
    return modalStack.length ? modalStack[modalStack.length - 1].modal : null;
  }

  function isUsable(element) {
    return !!(element && element.isConnected && !element.disabled &&
      !element.closest('[hidden], [inert]') && element.getClientRects().length);
  }

  function focusElement(element) {
    if (!isUsable(element)) return false;
    try { element.focus({ preventScroll: true }); } catch (e) { element.focus(); }
    return true;
  }

  function focusFirst(modal) {
    var candidates = modal.querySelectorAll('[autofocus], select, input:not([type="hidden"]), textarea, button:not([disabled]), a[href]');
    for (var i = 0; i < candidates.length; i += 1) {
      if (isUsable(candidates[i])) { focusElement(candidates[i]); return; }
    }
    focusElement(modal.querySelector('[role="dialog"]'));
  }

  function syncModalViewport() {
    var modal = topModal();
    var viewport = window.visualViewport;
    if (!modal || !viewport || window.innerWidth > 640) return;
    var height = viewport.height + 'px';
    var offset = viewport.offsetTop + 'px';
    if (modal.style.getPropertyValue('--mp-viewport-height') !== height) {
      modal.style.setProperty('--mp-viewport-height', height);
    }
    if (modal.style.getPropertyValue('--mp-viewport-offset-top') !== offset) {
      modal.style.setProperty('--mp-viewport-offset-top', offset);
    }
  }

  function startViewportTracking() {
    if (!window.visualViewport || viewportTracking) return;
    window.visualViewport.addEventListener('resize', syncModalViewport);
    window.visualViewport.addEventListener('scroll', syncModalViewport);
    viewportTracking = true;
  }

  function stopViewportTracking() {
    if (window.visualViewport && viewportTracking) {
      window.visualViewport.removeEventListener('resize', syncModalViewport);
      window.visualViewport.removeEventListener('scroll', syncModalViewport);
    }
    viewportTracking = false;
    modals.forEach(function (modal) {
      modal.style.removeProperty('--mp-viewport-height');
      modal.style.removeProperty('--mp-viewport-offset-top');
    });
  }

  function dismissCompetingUi() {
    if (document.body.classList.contains('drawer-open')) {
      var drawerClose = $('drawer-close');
      if (drawerClose) drawerClose.click();
    }
    document.querySelectorAll('.dropdown.is-open').forEach(function (dropdown) {
      dropdown.classList.remove('is-open');
      var trigger = dropdown.querySelector('[aria-haspopup="true"]');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  function updateScrollLock() {
    var locked = modalStack.length > 0;
    document.body.classList.toggle(LOCK, locked);
    document.documentElement.classList.toggle(LOCK, locked && window.innerWidth <= 640);
  }

  function openModal(modal, trigger) {
    var parent = topModal();
    var nested = parent && parent.id === 'meal-modal' && modal.id === 'remove-meal-modal';

    if (parent && nested) {
      var parentEntry = modalStack[modalStack.length - 1];
      parentEntry.inertBeforeCover = parent.hasAttribute('inert');
      parentEntry.ariaBeforeCover = parent.getAttribute('aria-hidden');
      parent.setAttribute('inert', '');
      parent.setAttribute('aria-hidden', 'true');
      parent.classList.add('is-covered');
      modal.classList.add('is-nested');
    } else if (parent) {
      while (modalStack.length) closeTop(false);
      dismissCompetingUi();
    } else {
      dismissCompetingUi();
    }

    modal.removeAttribute('inert');
    modal.classList.add(OPEN);
    modal.setAttribute('aria-hidden', 'false');
    modalStack.push({ modal: modal, opener: trigger });
    updateScrollLock();
    startViewportTracking();
    syncModalViewport();
    window.requestAnimationFrame(function () {
      if (topModal() === modal) focusFirst(modal);
    });
  }

  function closeTop(restoreFocus) {
    if (!modalStack.length) return;
    var entry = modalStack.pop();
    entry.modal.classList.remove(OPEN, 'is-nested', 'is-covered');
    entry.modal.setAttribute('aria-hidden', 'true');
    entry.modal.removeAttribute('inert');
    entry.modal.style.removeProperty('--mp-viewport-height');
    entry.modal.style.removeProperty('--mp-viewport-offset-top');

    var parentEntry = modalStack.length ? modalStack[modalStack.length - 1] : null;
    if (parentEntry) {
      var parent = parentEntry.modal;
      parent.classList.remove('is-covered');
      if (!parentEntry.inertBeforeCover) parent.removeAttribute('inert');
      if (parentEntry.ariaBeforeCover === null) parent.removeAttribute('aria-hidden');
      else parent.setAttribute('aria-hidden', parentEntry.ariaBeforeCover);
      delete parentEntry.inertBeforeCover;
      delete parentEntry.ariaBeforeCover;
    }

    updateScrollLock();
    if (modalStack.length) syncModalViewport();
    else stopViewportTracking();

    if (restoreFocus !== false) {
      if (focusElement(entry.opener)) return;
      if (parentEntry) focusFirst(parentEntry.modal);
      else {
        var fallback = page.querySelector('[data-open-modal]:not([disabled])');
        focusElement(fallback);
      }
    }
  }

  function closeAll() {
    while (modalStack.length) closeTop(false);
  }

  var drawerObserver = new MutationObserver(function () {
    document.documentElement.classList.toggle(
      'drawer-open', window.innerWidth <= 640 && document.body.classList.contains('drawer-open')
    );
  });
  drawerObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });

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
    var target = e.target instanceof Element ? e.target : null;
    if (!target) return;
    var opener = target.closest('[data-open-modal]');
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

    var top = topModal();
    if (!top) return;

    var closer = target.closest('[data-close-modal]');
    if (closer && top.contains(closer)) {
      e.preventDefault();
      e.stopPropagation();
      closeTop();
      return;
    }

    if (target === top) {
      e.stopPropagation();
      closeTop();
    }
  }, true);

  document.addEventListener('keydown', function (e) {
    var top = topModal();
    if (!top) return;

    if (e.key === 'Escape' || e.key === 'Esc') {
      e.preventDefault();
      e.stopPropagation();
      closeTop();
      return;
    }

    if (e.key === 'Tab') {
      var candidates = top.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
      var focusable = [].filter.call(candidates, isUsable);
      if (!focusable.length) { e.preventDefault(); focusFirst(top); return; }
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (!top.contains(document.activeElement)) { e.preventDefault(); focusElement(e.shiftKey ? last : first); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); focusElement(last); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); focusElement(first); }
    }
  });

  // dashboard.js toggles these menus on the trigger itself. This listener runs
  // after that handler and keeps their accessibility state in sync.
  document.querySelectorAll('.mobile-topbar .dropdown > [aria-haspopup="true"]').forEach(function (trigger) {
    trigger.setAttribute('aria-expanded', 'false');
    trigger.addEventListener('click', function () {
      window.requestAnimationFrame(function () {
        var dropdown = trigger.closest('.dropdown');
        trigger.setAttribute('aria-expanded', dropdown && dropdown.classList.contains('is-open') ? 'true' : 'false');
      });
    });
  });
  document.addEventListener('click', function (e) {
    document.querySelectorAll('.mobile-topbar .dropdown').forEach(function (dropdown) {
      var trigger = dropdown.querySelector(':scope > [aria-haspopup="true"]');
      if (trigger && !dropdown.contains(e.target)) trigger.setAttribute('aria-expanded', 'false');
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.mobile-topbar .dropdown > [aria-haspopup="true"]').forEach(function (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
    });
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
    closeAll();
  });

  /* ------------------------------------------------------------------ small-screen scroll to today */
  var scroller = page.querySelector('.tt-scroll');
  var todayHead = scroller && scroller.querySelector('th.is-today');
  if (scroller && todayHead && scroller.scrollWidth > scroller.clientWidth) {
    var corner = scroller.querySelector('.tt-corner');
    scroller.scrollLeft = Math.max(0, todayHead.offsetLeft - (corner ? corner.offsetWidth : 0) - 8);
  }

})();
