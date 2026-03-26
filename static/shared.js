/* shared.js — Solvulator common utilities */

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function formatSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function formatDate(iso) {
  if (!iso) return '';
  return iso.replace('T',' ').replace(/:\d{2}Z$/,'');
}

function toast(msg, duration) {
  duration = duration || 2000;
  var el = document.querySelector('.toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function(){ el.classList.remove('show'); }, duration);
}

async function apiFetch(path, opts) {
  var t0 = performance.now();
  try {
    var r = await fetch(path, opts);
    var ms = Math.round(performance.now() - t0);
    var data = await r.json();
    return {ok: r.ok, status: r.status, data: data, ms: ms};
  } catch(e) {
    return {ok: false, status: 0, data: {error: e.message}, ms: Math.round(performance.now() - t0)};
  }
}
