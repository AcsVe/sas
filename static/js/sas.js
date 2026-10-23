/* SAS page behaviour (CSP: no inline JS).
 *  <div data-show-when="scope_type=stages">  shown only while the form control «scope_type» has that value
 *                                             (radio, select or checkbox); hidden fields are left untouched.
 *  <input type="checkbox" data-check-all="student_ids">  ticks / unticks every checkbox of that name in the form.
 */
(function () {
  'use strict';

  function valueOf(form, name) {
    var els = form.querySelectorAll('[name="' + name + '"]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.type === 'radio' || el.type === 'checkbox') {
        if (el.checked) return el.value;
      } else {
        return el.value;
      }
    }
    return '';
  }

  function refresh(form) {
    form.querySelectorAll('[data-show-when]').forEach(function (box) {
      var rule = box.getAttribute('data-show-when').split('=');
      box.hidden = valueOf(form, rule[0]) !== rule[1];
    });
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-print]')) window.print();
  });
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (f) {
      if (f.querySelector('[data-show-when]')) refresh(f);
    });
  });
  document.addEventListener('change', function (e) {
    var form = e.target.form || e.target.closest('form');
    if (!form) return;
    if (e.target.matches('[data-check-all]')) {
      var name = e.target.getAttribute('data-check-all');
      form.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(function (cb) {
        cb.checked = e.target.checked;
      });
    }
    if (form.querySelector('[data-show-when]')) refresh(form);
  });
})();
