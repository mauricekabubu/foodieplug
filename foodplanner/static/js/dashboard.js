(function () {
  'use strict';

  /* ---------------- theme toggle (light / dark / system) ---------------- */

  var STORAGE_KEY = 'foodieplug-theme';
  var root = document.documentElement;
  var themeButtons = document.querySelectorAll('.theme-toggle button');
  var mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

  function applyTheme(pref) {
    var resolved = pref === 'system'
      ? (mediaQuery.matches ? 'dark' : 'light')
      : pref;

    root.setAttribute('data-theme', resolved);

    themeButtons.forEach(function (btn) {
      var isActive = btn.getAttribute('data-theme-value') === pref;
      btn.classList.toggle('is-active', isActive);
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
  }

  function getStoredPref() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) || 'system';
    } catch (e) {
      return 'system';
    }
  }

  function setPref(pref) {
    try {
      window.localStorage.setItem(STORAGE_KEY, pref);
    } catch (e) {}

    applyTheme(pref);
  }

  applyTheme(getStoredPref());

  themeButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setPref(btn.getAttribute('data-theme-value'));
    });
  });

  mediaQuery.addEventListener('change', function () {
    if (getStoredPref() === 'system') {
      applyTheme('system');
    }
  });


  /* ---------------- mobile drawer ---------------- */

  var drawerOpenBtn = document.getElementById('drawer-open');
  var drawerCloseBtn = document.getElementById('drawer-close');
  var drawerScrim = document.getElementById('drawer-scrim');

  function openDrawer() {
    document.body.classList.add('drawer-open');
    document.body.setAttribute('data-drawer-open', 'true');

    if (drawerOpenBtn) {
      drawerOpenBtn.setAttribute('aria-expanded', 'true');
    }
  }

  function closeDrawer() {
    document.body.classList.remove('drawer-open');
    document.body.removeAttribute('data-drawer-open');

    if (drawerOpenBtn) {
      drawerOpenBtn.setAttribute('aria-expanded', 'false');
    }
  }

  if (drawerOpenBtn) {
    drawerOpenBtn.addEventListener('click', function () {
      var isOpen = document.body.classList.contains('drawer-open');
      if (isOpen) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });
  }

  if (drawerCloseBtn) {
    drawerCloseBtn.addEventListener('click', closeDrawer);
  }

  if (drawerScrim) {
    drawerScrim.addEventListener('click', closeDrawer);
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && document.body.classList.contains('drawer-open')) {
      closeDrawer();
    }
  });

  document.querySelectorAll('.sidebar a.nav-item').forEach(function (link) {
    link.addEventListener('click', function () {
      if (window.innerWidth <= 980) {
        closeDrawer();
      }
    });
  });


  /* ---------------- shared modal system ---------------- */

  /* ============================================================
   SHARED MODAL SYSTEM
   ============================================================ */

(function () {
  'use strict';

  function openModal(modal) {
    if (!modal) return;

    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');

    var focusTarget = modal.querySelector(
      'input:not([type="hidden"]), select, textarea, button'
    );

    if (focusTarget) {
      setTimeout(function () {
        try {
          focusTarget.focus({ preventScroll: true });
        } catch (err) {
          focusTarget.focus();
        }
      }, 50);
    }
  }

  function closeModal(modal) {
    if (!modal) return;

    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');

    if (!document.querySelector('.modal-overlay.is-open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function closeAllModals() {
    document.querySelectorAll('.modal-overlay.is-open').forEach(function (modal) {
      closeModal(modal);
    });
  }

  /* Open modal */
  document.addEventListener('click', function (e) {
    var trigger = e.target.closest('[data-open-modal]');

    if (!trigger) return;

    var modalId = trigger.dataset.openModal;
    if (!modalId) return;

    var modal = document.getElementById(modalId);

    if (!modal) {
      console.warn(
        '[FoodiePlug] Modal #' + modalId + ' was not found.'
      );
      return;
    }

    openModal(modal);
  });

  /* Close modal */
  document.addEventListener('click', function (e) {
    var closeButton = e.target.closest('[data-close-modal]');

    if (!closeButton) return;

    var modal = closeButton.closest('.modal-overlay');

    if (modal) {
      closeModal(modal);
    }
  });

  /* Close when clicking the dark backdrop */
  document.addEventListener('click', function (e) {
    if (!e.target.classList.contains('modal-overlay')) return;

    closeModal(e.target);
  });

  /* Close with Escape */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;

    var openModals = document.querySelectorAll('.modal-overlay.is-open');

    if (openModals.length) {
      closeModal(openModals[openModals.length - 1]);
    }
  });

})();
  /* ---------------- dropdowns (notifications, profile) ---------------- */

  var dropdowns = document.querySelectorAll('.dropdown');

  function closeAllDropdowns(except) {
    dropdowns.forEach(function (d) {
      if (d !== except) {
        d.classList.remove('is-open');
      }
    });
  }

  dropdowns.forEach(function (dropdown) {
    var trigger = dropdown.querySelector('.icon-btn, .dropdown-trigger');

    if (!trigger) return;

    trigger.addEventListener('click', function (e) {
      e.stopPropagation();

      var willOpen = !dropdown.classList.contains('is-open');

      closeAllDropdowns();

      dropdown.classList.toggle('is-open', willOpen);
    });
  });

  document.addEventListener('click', function () {
    closeAllDropdowns();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeAllDropdowns();
    }
  });


  /* ---------------- AI assistant ---------------- */

  var aiForm = document.getElementById('ai-prompt-form');
  var aiInput = document.getElementById('ai-prompt-input');
  var aiReply = document.getElementById('ai-reply');
  var aiSubmitBtn = document.getElementById('ai-submit-btn');

  document.querySelectorAll('.ai-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      if (aiInput) {
        aiInput.value = chip.textContent.trim();
        aiInput.focus();
      }
    });
  });

  if (aiForm) {
    aiForm.addEventListener('submit', function (event) {
      event.preventDefault();

      if (!aiInput || !aiInput.value.trim()) {
        if (aiInput) aiInput.focus();
        return;
      }

      if (aiSubmitBtn) {
        aiSubmitBtn.classList.add('is-loading');
        aiSubmitBtn.disabled = true;
      }

      if (aiReply) {
        aiReply.classList.remove('is-visible');
      }

      window.setTimeout(function () {
        if (aiSubmitBtn) {
          aiSubmitBtn.classList.remove('is-loading');
          aiSubmitBtn.disabled = false;
        }

        if (aiReply) {
          aiReply.textContent =
            "You've got what you need for tonight — I'd go with rice, beans and a quick kachumbari. " +
            "No extra shopping needed. Want me to add it to today's plan?";

          aiReply.classList.add('is-visible');
        }
      }, 1000);
    });
  }

  /* ---------------- Gemini key reminder ---------------- */

  var keyReminder = document.getElementById('ai-key-reminder');

  if (keyReminder) {
    var reminderDismissForm = document.getElementById(
      'ai-key-reminder-dismiss-form'
    );
    var reminderDismissButtons = keyReminder.querySelectorAll(
      '[data-dismiss-reminder]'
    );
    var reminderError = keyReminder.querySelector(
      '.ai-key-reminder-error'
    );
    var reminderReturnFocus = null;
    var reminderTimer = null;

    function anotherModalIsOpen() {
      return Boolean(
        document.querySelector(
          '.modal-overlay.is-open, dialog[open], [aria-modal="true"]:not([hidden])'
        )
      );
    }

    function showKeyReminder() {
      if (anotherModalIsOpen()) {
        reminderTimer = window.setTimeout(showKeyReminder, 500);
        return;
      }

      reminderReturnFocus = document.activeElement;
      keyReminder.hidden = false;
      keyReminder.classList.add('is-visible');
    }

    function dismissKeyReminder() {
      if (!reminderDismissForm) return;

      var formData = new FormData(reminderDismissForm);
      var body = new URLSearchParams();
      formData.forEach(function (value, key) {
        body.append(key, value);
      });

      reminderDismissButtons.forEach(function (button) {
        button.disabled = true;
      });
      if (reminderError) reminderError.hidden = true;

      fetch(keyReminder.dataset.dismissUrl, {
        method: 'POST',
        body: body,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
        }
      }).then(function (response) {
        if (!response.ok) throw new Error('Reminder preference was not saved.');
        keyReminder.classList.remove('is-visible');
        keyReminder.hidden = true;
        if (
          reminderReturnFocus &&
          reminderReturnFocus !== document.body &&
          document.contains(reminderReturnFocus)
        ) {
          reminderReturnFocus.focus();
        }
      }).catch(function () {
        if (reminderError) reminderError.hidden = false;
      }).finally(function () {
        reminderDismissButtons.forEach(function (button) {
          button.disabled = false;
        });
      });
    }

    reminderDismissButtons.forEach(function (button) {
      button.addEventListener('click', dismissKeyReminder);
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !keyReminder.hidden) {
        dismissKeyReminder();
      }
    });

    if (document.readyState === 'complete') {
      reminderTimer = window.setTimeout(showKeyReminder, 600);
    } else {
      window.addEventListener('load', function () {
        reminderTimer = window.setTimeout(showKeyReminder, 600);
      }, { once: true });
    }
  }


  /* ---------------- weekly overview ---------------- */

  document.querySelectorAll('.week-day').forEach(function (day) {
    day.addEventListener('click', function () {
      document.querySelectorAll('.week-day').forEach(function (d) {
        d.classList.remove('is-selected');
      });

      day.classList.add('is-selected');
    });
  });


  /* ---------------- pantry quantity controls ---------------- */

  var csrfInput = document.querySelector(
    'input[name=csrfmiddlewaretoken]'
  );

  var csrf = csrfInput ? csrfInput.value : '';

  document.querySelectorAll('.item-qty-control').forEach(function (ctrl) {
    var valueEl = ctrl.querySelector('.qty-value');

    if (!valueEl) return;

    ctrl.addEventListener('click', function (e) {
      var btn = e.target.closest('.qty-btn');

      if (!btn) return;

      var step = parseFloat(ctrl.dataset.step) || 1;
      var current = parseFloat(valueEl.dataset.value) || 0;

      var next = btn.classList.contains('qty-plus')
        ? current + step
        : current - step;

      if (next <= 0) return;

      var body = new FormData();

      body.append('quantity', next.toFixed(2));

      fetch(ctrl.dataset.url, {
        method: 'POST',
        body: body,
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrf
        }
      }).then(function () {
        window.location.reload();
      });
    });
  });


})();