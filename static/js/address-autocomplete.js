/**
 * Free address autocomplete (Photon via /api/address-suggest).
 * Biased server-side to Pooler / Coastal GA.
 *
 * attachAddressAutocomplete({
 *   input: '#job_address' | HTMLElement,
 *   cityInput: '#city' | HTMLElement | null,
 *   stateInput: '#state' | HTMLElement | null,
 *   latInput: '#job_lat' | HTMLElement | null,
 *   lonInput: '#job_lon' | HTMLElement | null,
 *   apiUrl: '/api/address-suggest',
 *   debounceMs: 400,
 *   onSelect: function(suggestion) {}
 * })
 */
(function (global) {
  'use strict';

  function el(ref) {
    if (!ref) return null;
    if (typeof ref === 'string') return document.querySelector(ref);
    return ref;
  }

  function attachAddressAutocomplete(opts) {
    opts = opts || {};
    var input = el(opts.input);
    if (!input) return null;

    var cityInput = el(opts.cityInput);
    var stateInput = el(opts.stateInput);
    var latInput = el(opts.latInput);
    var lonInput = el(opts.lonInput);
    var apiUrl = opts.apiUrl || '/api/address-suggest';
    var debounceMs = opts.debounceMs != null ? opts.debounceMs : 250;
    var onSelect = typeof opts.onSelect === 'function' ? opts.onSelect : null;

    var wrap = document.createElement('div');
    wrap.className = 'addr-ac-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var list = document.createElement('ul');
    list.className = 'addr-ac-list';
    list.hidden = true;
    list.setAttribute('role', 'listbox');
    wrap.appendChild(list);

    var timer = null;
    var abort = null;
    var items = [];
    var active = -1;
    var suppress = false; // after select, ignore next input briefly

    function hide() {
      list.hidden = true;
      list.innerHTML = '';
      items = [];
      active = -1;
    }

    function setActive(idx) {
      var lis = list.querySelectorAll('li');
      for (var i = 0; i < lis.length; i++) {
        lis[i].classList.toggle('is-active', i === idx);
      }
      active = idx;
      if (idx >= 0 && lis[idx]) {
        lis[idx].scrollIntoView({ block: 'nearest' });
      }
    }

    function applySuggestion(s) {
      suppress = true;
      // Street in address; city + state in their own fields (miles composes all three)
      var street = (s.street || '').trim();
      var formatted = (s.formatted || s.display || '').trim();
      input.value = street || formatted;

      if (cityInput) {
        // City name only — never append ZIP (ZIP stays on suggestion display / street geocode)
        var cityVal = (s.city || '').trim();
        // Strip accidental ZIP if a prior bug left one in the field value we overwrite
        cityVal = cityVal.replace(/\s+\d{5}(?:-\d{4})?\s*$/, '').trim();
        if (cityVal) cityInput.value = cityVal;
      }

      if (stateInput && s.state) {
        var st = String(s.state).trim().toUpperCase();
        if (st.length > 2) {
          var map = { GEORGIA: 'GA', 'SOUTH CAROLINA': 'SC', FLORIDA: 'FL',
                      'NORTH CAROLINA': 'NC', ALABAMA: 'AL', TENNESSEE: 'TN' };
          st = map[st] || st.slice(0, 2);
        }
        // Prefer matching <select> option; otherwise set value
        var matched = false;
        if (stateInput.tagName === 'SELECT') {
          for (var i = 0; i < stateInput.options.length; i++) {
            if (stateInput.options[i].value === st) {
              stateInput.selectedIndex = i;
              matched = true;
              break;
            }
          }
        }
        if (!matched) stateInput.value = st;
      }

      if (latInput) {
        latInput.value = s.lat != null ? String(s.lat) : '';
      }
      if (lonInput) {
        lonInput.value = s.lon != null ? String(s.lon) : '';
      }

      hide();
      // change → parent can refresh miles WITH lat/lon + state already set
      input.dispatchEvent(new Event('change', { bubbles: true }));
      if (onSelect) onSelect(s);
      setTimeout(function () { suppress = false; }, 50);
    }

    function render(suggestions) {
      items = suggestions || [];
      list.innerHTML = '';
      active = -1;
      if (!items.length) {
        hide();
        return;
      }
      for (var i = 0; i < items.length; i++) {
        (function (idx) {
          var s = items[idx];
          var li = document.createElement('li');
          li.setAttribute('role', 'option');
          li.textContent = s.display || s.formatted || s.street || '';
          li.addEventListener('mousedown', function (e) {
            e.preventDefault(); // keep focus, avoid blur-before-click
            applySuggestion(s);
          });
          li.addEventListener('mouseenter', function () { setActive(idx); });
          list.appendChild(li);
        })(i);
      }
      list.hidden = false;
    }

    function showSearching() {
      list.innerHTML = '';
      var li = document.createElement('li');
      li.className = 'addr-ac-status';
      li.textContent = 'Searching addresses…';
      list.appendChild(li);
      list.hidden = false;
    }

    function fetchSuggestions(q) {
      if (abort) abort.abort();
      abort = new AbortController();
      showSearching();
      var url = apiUrl + (apiUrl.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(q);
      fetch(url, {
        headers: { Accept: 'application/json' },
        signal: abort.signal,
        credentials: 'same-origin',
      })
        .then(function (r) {
          if (!r.ok) throw new Error('suggest ' + r.status);
          return r.json();
        })
        .then(function (data) {
          render((data && data.suggestions) || []);
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          list.innerHTML = '';
          var li = document.createElement('li');
          li.className = 'addr-ac-status';
          li.textContent = 'No matches — add city or keep typing';
          list.appendChild(li);
          list.hidden = false;
          items = [];
        });
    }

    function onInput() {
      if (suppress) return;
      // Typing invalidates prior coords
      if (latInput) latInput.value = '';
      if (lonInput) lonInput.value = '';
      clearTimeout(timer);
      var q = (input.value || '').trim();
      if (q.length < 3) {
        hide();
        return;
      }
      timer = setTimeout(function () { fetchSuggestions(q); }, debounceMs);
    }

    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-autocomplete', 'list');
    input.addEventListener('input', onInput);
    input.addEventListener('keydown', function (e) {
      if (list.hidden || !items.length) {
        if (e.key === 'Escape') hide();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive(active < items.length - 1 ? active + 1 : 0);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(active > 0 ? active - 1 : items.length - 1);
      } else if (e.key === 'Enter') {
        if (active >= 0 && items[active]) {
          e.preventDefault();
          applySuggestion(items[active]);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        hide();
      }
    });
    input.addEventListener('blur', function () {
      setTimeout(hide, 150);
    });

    return { hide: hide, input: input };
  }

  global.attachAddressAutocomplete = attachAddressAutocomplete;
})(window);
