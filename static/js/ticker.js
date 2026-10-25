/* The news ticker (CSP: no inline JS). Every [data-ticker] scrolls its messages endlessly at data-speed px/s,
 * towards data-move (left | right); it pauses on hover (data-pause="1") and when the tab is hidden, and stays
 * still for people who ask for reduced motion. The admin page's form updates its preview live. */
(function () {
  'use strict';
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function start(box) {
    if (box._stop) box._stop();
    var vp = box.querySelector('.ticker-viewport'), track = box.querySelector('.ticker-track');
    if (!vp || !track || !track.children.length) return;
    if (!track.dataset.orig) track.dataset.orig = track.innerHTML;
    track.innerHTML = track.dataset.orig;
    var unit = track.scrollWidth || 1, guard = 0;
    while (track.scrollWidth < vp.offsetWidth + unit && guard++ < 30) track.insertAdjacentHTML('beforeend', track.dataset.orig);
    track.insertAdjacentHTML('beforeend', track.innerHTML);          // two equal halves → seamless wrap
    if (still) { track.style.transform = ''; return; }
    var half = track.scrollWidth / 2, x = 0, last = null, paused = false, raf = null;
    var speed = parseFloat(box.getAttribute('data-speed')) || 60;
    var toRight = box.getAttribute('data-move') === 'right';
    function frame(t) {
      if (last !== null && !paused) {
        x += speed * Math.min(t - last, 100) / 1000;
        if (x >= half) x -= half;
      }
      last = t;
      track.style.transform = 'translate3d(' + (toRight ? x - half : -x) + 'px,0,0)';
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    function over() { if (box.getAttribute('data-pause') === '1') paused = true; }
    function out() { paused = false; }
    box.addEventListener('mouseenter', over);
    box.addEventListener('mouseleave', out);
    box._stop = function () {
      cancelAnimationFrame(raf);
      box.removeEventListener('mouseenter', over);
      box.removeEventListener('mouseleave', out);
    };
    track.querySelectorAll('img').forEach(function (im) {
      if (!im.complete) im.addEventListener('load', function () { start(box); }, { once: true });
    });
  }

  function all() { document.querySelectorAll('[data-ticker]').forEach(start); }

  // ── admin page: live preview ──────────────────────────────────────────
  function hexToRgba(hex, op) {
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return null;
    return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + (op / 100) + ')';
  }
  function bindPreview(form) {
    var box = document.querySelector('.ticker-preview');
    if (!box) return;
    var track = box.querySelector('.ticker-track');
    function v(n) { var el = form.elements[n]; return el ? (el.type === 'checkbox' ? el.checked : el.value) : ''; }
    function sepHtml() {
      var sep = document.createElement('span');
      sep.className = 'ticker-sep'; sep.setAttribute('aria-hidden', 'true');
      var kind = v('separator'), logo = v('logo') === 'url' ? v('logo_url') : (v('logo') === 'school' ? box.getAttribute('data-school-logo') : '');
      var src = kind === 'image' ? v('sep_img_url') : (kind === 'logo' ? logo : '');
      if (src && /^(https:\/\/|\/)/.test(src)) {
        var im = document.createElement('img'); im.src = src; im.alt = '';
        if (kind === 'logo') im.className = 'tk-logo';
        sep.appendChild(im);
      } else {
        sep.textContent = kind === 'diamond' ? '◆' : (kind === 'bar' ? '|' : '•');
      }
      return sep.outerHTML;
    }
    function refresh(rebuild) {
      var st = box.style;
      st.setProperty('--tk-fg', v('fg'));
      var bg = hexToRgba(v('bg'), parseInt(v('opacity'), 10) || 0);
      if (bg) st.setProperty('--tk-bg', bg);
      st.setProperty('--tk-size', (parseInt(v('size'), 10) || 15) + 'px');
      st.setProperty('--tk-font', v('font'));
      st.setProperty('--tk-weight', v('weight'));
      st.setProperty('--tk-fade', (parseInt(v('mask_fade'), 10) || 0) + '%');
      st.setProperty('--tk-logo', (parseInt(v('logo_size'), 10) || 26) + 'px');
      st.setProperty('--tk-pulse', (parseInt(v('logo_pulse_speed'), 10) || 6) + 's');
      track.classList.toggle('pulse', !!v('logo_pulse'));
      box.setAttribute('data-speed', v('speed'));
      var dir = v('direction'), en = document.documentElement.lang === 'en';
      box.setAttribute('data-move', dir === 'auto' ? (en ? 'left' : 'right') : dir);
      box.setAttribute('data-pause', v('pause_on_hover') ? '1' : '0');
      if (rebuild) {
        var src = (en ? v('feeds_en') || v('feeds_ar') : v('feeds_ar') || v('feeds_en')).split(/\n/)
          .map(function (s) { return s.trim(); }).filter(Boolean);
        var html = '';
        src.forEach(function (line) {
          var span = document.createElement('span');
          span.className = 'ticker-item'; span.setAttribute('dir', 'auto'); span.textContent = line;
          html += span.outerHTML + sepHtml();
        });
        track.dataset.orig = html || track.dataset.orig;
      }
      start(box);
    }
    form.addEventListener('input', function (e) { refresh(/^feeds_/.test(e.target.name)); });
    form.addEventListener('change', function () { refresh(true); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    all();
    var f = document.querySelector('form[data-ticker-form]');
    if (f) bindPreview(f);
    var t = null;
    window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(all, 250); });
  });
})();
