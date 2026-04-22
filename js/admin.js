/* CPEEN Admin — shared auth, nav, and utilities
   Loaded after React and db.js, so React is already on window. */
var ADMIN = (function () {
  'use strict';

  var SESSION_KEY = 'cpeen_admin_session';
  var TTL = 2 * 60 * 60 * 1000; // 2 hours

  // ── Auth ──────────────────────────────────────────────────────────────────
  function isAuthed() {
    try {
      var s = JSON.parse(sessionStorage.getItem(SESSION_KEY));
      return !!(s && (Date.now() - s.ts) < TTL);
    } catch(e) { return false; }
  }

  function check() {
    if (isAuthed()) return true;
    // Not on login page → redirect there
    if (!window.location.pathname.endsWith('admin/index.html') &&
        !window.location.pathname.endsWith('admin/')) {
      window.location.replace('index.html');
    }
    return false;
  }

  function login(password) {
    return CPEEN.hashPass(password).then(function (hash) {
      if (hash === CPEEN.getConfig().adminHash) {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify({ ts: Date.now() }));
        return true;
      }
      return false;
    });
  }

  function logout() {
    sessionStorage.removeItem(SESSION_KEY);
    window.location.replace('index.html');
  }

  // ── Shared nav (React.createElement — no JSX needed) ─────────────────────
  var LINKS = [
    { href: 'index.html',        key: 'dashboard',    label: 'Dashboard' },
    { href: 'participants.html', key: 'participants', label: 'Participanți' },
    { href: 'results.html',      key: 'results',      label: 'Rezultate' },
    { href: 'exams.html',        key: 'exams',        label: 'Subiecte' },
    { href: 'settings.html',     key: 'settings',     label: 'Setări' },
  ];

  function Nav(props) {
    var active = props.active;
    var R = React;
    return R.createElement('nav', { className: 'nav' },
      R.createElement('div', { style: { display: 'flex', flexDirection: 'column' } },
        R.createElement('span', { className: 'nav-domain' }, 'englishgrammarchallenge.ro'),
        R.createElement('a', { href: '../index.html', className: 'nav-title', style: { color: '#fff', textDecoration: 'none' } }, 'CPEEN 2026')
      ),
      R.createElement('div', { className: 'nav-links' },
        LINKS.map(function (l) {
          return R.createElement('a', {
            key: l.key,
            href: l.href,
            className: 'nav-link' + (active === l.key ? ' active' : '')
          }, l.label);
        }).concat([
          R.createElement('button', {
            key: 'logout',
            onClick: logout,
            style: {
              background: 'rgba(255,255,255,.15)', color: '#fff',
              border: '1px solid rgba(255,255,255,.3)', borderRadius: 6,
              padding: '4px 12px', cursor: 'pointer', fontSize: 13, marginLeft: 8
            }
          }, 'Ieși')
        ])
      )
    );
  }

  // ── CSV export ────────────────────────────────────────────────────────────
  function csv(header, rows, filename) {
    var lines = [header].concat(rows.map(function (row) {
      return row.map(function (cell) {
        return '"' + String(cell == null ? '' : cell).replace(/"/g, '""') + '"';
      }).join(',');
    }));
    var a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,﻿' + encodeURIComponent(lines.join('\n'));
    a.download = filename || 'export.csv';
    a.click();
  }

  // ── Lookups ───────────────────────────────────────────────────────────────
  var levelColor = { A2: '#27AE60', B1: '#E67E22', B2: '#8E44AD', C1: '#2E75B6' };
  var stageLabel = { calificare: 'Calificare', finala: 'Finală' };

  return { isAuthed, check, login, logout, Nav, csv, levelColor, stageLabel };
})();
