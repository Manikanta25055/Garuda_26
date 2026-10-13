/* Actions: every click, change, input and submit handler in the markup, with
   no script written inline.

   The page used to say onclick="G.nav('devices', this)". Inline handlers are
   script inside HTML, and allowing them meant allowing any script that found
   its way into the page: the site's Content-Security-Policy had to keep
   'unsafe-inline', so one injected tag could run as the signed-in person.
   Now the markup only names what to call:

     <button data-click="G.nav" data-args='["devices", {"$": "this"}]'>

   data-click / data-change / data-input / data-submit   the function, as
       "Namespace.name"; only G, H, N and UI (below) can be named
   data-args   a JSON array of arguments. Plain values pass as they are; an
       object stands for something live:
         {"$": "this"}    the element carrying the attribute
         {"$": "value"}   that element's value
         {"$": "event"}   the event
         {"$": "data", "key": "k"}   that element's data-k attribute
         {"$q": "sel"}    document.querySelector(sel)

   Handlers run innermost first and stop when one calls stopPropagation(),
   the way inline handlers bubbled. A submit never reloads the page. */
(function () {
  'use strict';

  const NAMESPACES = new Set(['G', 'H', 'N', 'UI']);
  const NAME = /^[A-Za-z_$][\w$]*$/;

  // The few handlers that were expressions rather than a call.
  window.UI = {
    // A range input's value shown elsewhere, scaled: (value / by).toFixed(digits)
    showScaled(el, id, by, digits) {
      const out = document.getElementById(id);
      if (out) out.textContent = (Number(el.value) / by).toFixed(digits);
    },
    addClass(id, cls) {
      const el = document.getElementById(id);
      if (el) el.classList.add(cls);
    },
  };

  function resolve(path) {
    const parts = String(path || '').split('.');
    if (parts.length !== 2 || !NAMESPACES.has(parts[0]) || !NAME.test(parts[1])) return null;
    const owner = window[parts[0]];
    const fn = owner && owner[parts[1]];
    return typeof fn === 'function' ? [owner, fn] : null;
  }

  function arg(a, el, ev) {
    if (a && typeof a === 'object' && !Array.isArray(a)) {
      if (a.$ === 'this') return el;
      if (a.$ === 'value') return el.value;
      if (a.$ === 'event') return ev;
      if (a.$ === 'data' && typeof a.key === 'string') return el.dataset[a.key];
      if (typeof a.$q === 'string') return document.querySelector(a.$q);
    }
    return a;
  }

  function args(el, ev) {
    const raw = el.getAttribute('data-args');
    if (!raw) return [];
    try {
      const list = JSON.parse(raw);
      return Array.isArray(list) ? list.map(a => arg(a, el, ev)) : [];
    } catch (_) {
      console.warn('actions: bad data-args on', el);
      return [];
    }
  }

  function dispatch(ev) {
    const attr = 'data-' + ev.type;
    if (ev.type === 'submit') ev.preventDefault();
    for (let el = ev.target; el && el.nodeType === 1; el = el.parentElement) {
      if (!el.hasAttribute(attr)) continue;
      const target = resolve(el.getAttribute(attr));
      if (!target) { console.warn('actions: nothing to call for', el.getAttribute(attr)); continue; }
      target[1].apply(target[0], args(el, ev));
      if (ev.cancelBubble) break;
    }
  }

  for (const type of ['click', 'change', 'input', 'submit']) document.addEventListener(type, dispatch);
})();
