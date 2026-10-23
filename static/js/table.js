/* Data tables & card lists — one behaviour for every list in the system (no inline JS: the CSP blocks it).
 *
 *  <table data-table id="t-users">            sticky header + per-column filters + click-to-sort + row count
 *    <th data-filter="text|select" data-sort="text|num|date">   (data-nosort / no data-filter to opt out)
 *    <td data-value="2026-10-21">              optional sort/filter value (else the cell text)
 *    <tr data-id="5"> <td class="sel"><input type="checkbox" data-row-select value="5"></td>
 *  <div data-cards id="c-users"> .card-item[data-id][data-search="…"][data-group="…"]  (card view, same selection)
 *  <form data-bulk-for="t-users c-users" method="post">   bulk bar: ids of the ticked rows are added on submit;
 *    <button name="action" value="delete" data-confirm="…">      a button's data-confirm asks first
 *  <input data-list-search="t-users c-users">  one search box for a table and/or cards
 *  <span data-count-for="t-users">             "12 of 40"
 *  URL ?f=<column title>:<value>  pre-fills a column filter (links from the dashboard).
 */
(function () {
  'use strict';
  var AR = document.documentElement.lang !== 'en';

  // Arabic-insensitive matching: diacritics, alef/hamza forms, taa marbuta, alef maqsura, tatweel, Hindi digits
  function norm(s) {
    return String(s || '').toLowerCase()
      .replace(/[ً-ٰٟـ]/g, '')
      .replace(/[أإآٱ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي').replace(/ؤ/g, 'و').replace(/ئ/g, 'ي')
      .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 1632); })
      .replace(/\s+/g, ' ').trim();
  }
  function cellValue(td) {
    if (!td) return '';
    return td.hasAttribute('data-value') ? td.getAttribute('data-value') : td.textContent.trim();
  }
  function byId(ids) {
    return String(ids || '').split(/\s+/).map(function (i) { return document.getElementById(i); }).filter(Boolean);
  }
  function urlFilters() {
    var out = [];
    new URLSearchParams(location.search).getAll('f').forEach(function (f) {
      var i = f.indexOf(':');
      if (i > 0) out.push([norm(f.slice(0, i)), f.slice(i + 1)]);
    });
    return out;
  }

  // ── tables ────────────────────────────────────────────────────────────
  function setupTable(table) {
    var thead = table.tHead, tbody = table.tBodies[0];
    if (!thead || !tbody) return;
    var head = thead.rows[0], cols = Array.prototype.slice.call(head.cells);
    var filters = [], sortCol = -1, sortDir = 1, searchText = '';
    var anyFilter = cols.some(function (th) { return th.hasAttribute('data-filter'); });

    var frow = null;
    if (anyFilter) {
      frow = thead.insertRow(-1);
      frow.className = 'filter-row';
      cols.forEach(function (th, i) {
        var cell = document.createElement('th');
        frow.appendChild(cell);
        var kind = th.getAttribute('data-filter');
        if (!kind) return;
        var el;
        if (kind === 'select') {
          el = document.createElement('select');
          var all = document.createElement('option');
          all.value = ''; all.textContent = AR ? 'الكل' : 'All';
          el.appendChild(all);
          var seen = {};
          Array.prototype.forEach.call(tbody.rows, function (tr) {
            var v = cellValue(tr.cells[i]);
            if (v && !seen[v]) { seen[v] = 1; }
          });
          Object.keys(seen).sort(function (a, b) { return a.localeCompare(b, 'ar'); }).forEach(function (v) {
            var o = document.createElement('option'); o.value = v; o.textContent = v; el.appendChild(o);
          });
        } else {
          el = document.createElement('input');
          el.type = 'search';
          el.placeholder = AR ? 'تصفية…' : 'Filter…';
        }
        el.setAttribute('aria-label', (AR ? 'تصفية: ' : 'Filter: ') + th.textContent.trim());
        el.addEventListener('input', apply);
        el.addEventListener('change', apply);
        cell.appendChild(el);
        filters[i] = el;
      });
    }

    cols.forEach(function (th, i) {
      if (th.hasAttribute('data-nosort') || th.classList.contains('sel')) return;
      th.classList.add('sortable');
      th.tabIndex = 0;
      th.title = AR ? 'اضغط للترتيب' : 'Click to sort';
      var go = function () {
        sortDir = sortCol === i ? -sortDir : 1;
        sortCol = i;
        cols.forEach(function (c) { c.classList.remove('asc', 'desc'); });
        th.classList.add(sortDir > 0 ? 'asc' : 'desc');
        sort();
      };
      th.addEventListener('click', go);
      th.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    });

    function sort() {
      var kind = cols[sortCol].getAttribute('data-sort') || 'auto';
      var rows = Array.prototype.slice.call(tbody.rows).filter(function (r) { return !r.classList.contains('empty-row'); });
      rows.sort(function (a, b) {
        var x = cellValue(a.cells[sortCol]), y = cellValue(b.cells[sortCol]);
        var nx = parseFloat(x.replace(/[^\d.\-]/g, '')), ny = parseFloat(y.replace(/[^\d.\-]/g, ''));
        if ((kind === 'num' || (kind === 'auto' && !isNaN(nx) && !isNaN(ny) && /^[\d\s.,%\-]+$/.test(x + y))))
          return (nx - ny) * sortDir;
        return x.localeCompare(y, 'ar', { numeric: true }) * sortDir;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
    }

    function apply() {
      var shown = 0, total = 0;
      Array.prototype.forEach.call(tbody.rows, function (tr) {
        if (tr.classList.contains('empty-row')) return;
        total++;
        var ok = true;
        filters.forEach(function (el, i) {
          if (!ok || !el || !el.value) return;
          var v = cellValue(tr.cells[i]);
          ok = el.tagName === 'SELECT' ? v === el.value : norm(v).indexOf(norm(el.value)) > -1;
        });
        if (ok && searchText) ok = norm(tr.textContent).indexOf(searchText) > -1;
        tr.hidden = !ok;
        if (ok) shown++;
      });
      updateCount(table.id, shown, total);
      syncSelectAll(table);
    }

    table._search = function (t) { searchText = norm(t); apply(); };
    // dashboard links: ?f=<column title>:<value>
    urlFilters().forEach(function (pair) {
      cols.forEach(function (th, i) {
        if (filters[i] && norm(th.textContent) === pair[0]) filters[i].value = pair[1];
      });
    });
    // the filter row sits under the sticky header row
    if (frow) {
      var fix = function () {
        var h = head.getBoundingClientRect().height;
        Array.prototype.forEach.call(frow.cells, function (c) { c.style.top = h + 'px'; });
      };
      fix();
      window.addEventListener('resize', fix);
    }
    apply();
  }

  // ── card lists (same search / selection as the tables) ──────────────────
  function setupCards(box) {
    var searchText = '', group = '';
    function apply() {
      var shown = 0, items = box.querySelectorAll('.card-item');
      Array.prototype.forEach.call(items, function (it) {
        var ok = (!searchText || norm(it.getAttribute('data-search') || it.textContent).indexOf(searchText) > -1) &&
                 (!group || (' ' + (it.getAttribute('data-group') || '') + ' ').indexOf(' ' + group + ' ') > -1);
        it.hidden = !ok;
        if (ok) shown++;
      });
      updateCount(box.id, shown, items.length);
      syncSelectAll(box);
    }
    box._search = function (t) { searchText = norm(t); apply(); };
    box._group = function (g) { group = g; apply(); };
    apply();
  }

  function updateCount(id, shown, total) {
    document.querySelectorAll('[data-count-for~="' + id + '"]').forEach(function (el) {
      el.textContent = shown === total ? String(total) : (shown + (AR ? ' من ' : ' of ') + total);
    });
  }

  // ── selection + bulk bar ─────────────────────────────────────────────────
  function visibleBoxes(scope) {
    return Array.prototype.filter.call(scope.querySelectorAll('[data-row-select]'), function (cb) {
      return !cb.closest('[hidden]');
    });
  }
  function syncSelectAll(scope) {
    var all = scope.querySelector('[data-select-all]');
    var boxes = visibleBoxes(scope);
    if (all) all.checked = boxes.length > 0 && boxes.every(function (b) { return b.checked; });
    refreshBulkBars();
  }
  function refreshBulkBars() {
    document.querySelectorAll('form[data-bulk-for]').forEach(function (form) {
      var n = 0, seen = {};
      byId(form.getAttribute('data-bulk-for')).forEach(function (scope) {
        visibleBoxes(scope).forEach(function (cb) { if (cb.checked && !seen[cb.value]) { seen[cb.value] = 1; n++; } });
      });
      form.classList.toggle('has-selection', n > 0);
      form.querySelectorAll('[data-selected-count]').forEach(function (el) { el.textContent = n; });
    });
  }
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.matches('[data-select-all]')) {
      var scope = t.closest('table, [data-cards]');
      visibleBoxes(scope).forEach(function (cb) { cb.checked = t.checked; cb.closest('tr, .card-item').classList.toggle('selected', t.checked); });
      refreshBulkBars();
    } else if (t.matches('[data-row-select]')) {
      // the same item may appear in the table and in the cards: keep both ticks in step
      document.querySelectorAll('[data-row-select][value="' + t.value + '"]').forEach(function (cb) {
        cb.checked = t.checked;
        var row = cb.closest('tr, .card-item');
        if (row) row.classList.toggle('selected', t.checked);
      });
      var sc = t.closest('table, [data-cards]');
      if (sc) syncSelectAll(sc);
    }
  });
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches('form[data-bulk-for]')) return;
    form.querySelectorAll('input[name="ids"]').forEach(function (i) { i.remove(); });
    var ids = {};
    byId(form.getAttribute('data-bulk-for')).forEach(function (scope) {
      visibleBoxes(scope).forEach(function (cb) { if (cb.checked) ids[cb.value] = 1; });
    });
    var list = Object.keys(ids);
    var btn = e.submitter;
    if (!list.length) { e.preventDefault(); window.alert(AR ? 'حدّد عنصراً واحداً على الأقل.' : 'Select at least one item.'); return; }
    var msg = btn && btn.getAttribute('data-confirm');
    if (msg && !window.confirm(msg.replace('{n}', list.length))) { e.preventDefault(); return; }
    list.forEach(function (id) {
      var h = document.createElement('input'); h.type = 'hidden'; h.name = 'ids'; h.value = id; form.appendChild(h);
    });
  }, true);

  // single-item forms whose button carries data-confirm (e.g. ✕ on a card)
  document.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-confirm]');
    if (!b || b.form && b.form.matches('form[data-bulk-for]')) return;
    if (!window.confirm(b.getAttribute('data-confirm'))) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  // ── search boxes, group chips, view toggles ──────────────────────────────
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!t.matches('[data-list-search]')) return;
    byId(t.getAttribute('data-list-search')).forEach(function (s) { if (s._search) s._search(t.value); });
  });
  document.addEventListener('click', function (e) {
    var chip = e.target.closest('[data-group-chip]');
    if (chip) {
      var box = document.getElementById(chip.getAttribute('data-group-chip'));
      chip.parentElement.querySelectorAll('[data-group-chip]').forEach(function (c) { c.classList.remove('on'); });
      chip.classList.add('on');
      if (box && box._group) box._group(chip.getAttribute('data-group') || '');
    }
    var tog = e.target.closest('[data-view]');
    if (tog) {
      var target = tog.getAttribute('data-view');
      document.querySelectorAll('[data-view-pane]').forEach(function (p) { p.hidden = p.getAttribute('data-view-pane') !== target; });
      tog.parentElement.querySelectorAll('[data-view]').forEach(function (b) { b.classList.toggle('on', b === tog); });
      try { localStorage.setItem('view:' + location.pathname, target); } catch (err) { /* private mode */ }
    }
  });

  function init() {
    document.querySelectorAll('table[data-table]').forEach(setupTable);
    document.querySelectorAll('[data-cards]').forEach(setupCards);
    var saved = null;
    try { saved = localStorage.getItem('view:' + location.pathname); } catch (err) { /* private mode */ }
    // a dashboard link with column filters (?f=) opens the table view, where those filters live
    if (urlFilters().length && document.querySelector('[data-view="table"]')) saved = 'table';
    var btn = saved && document.querySelector('[data-view="' + saved + '"]');
    if (btn) btn.click();
    refreshBulkBars();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
