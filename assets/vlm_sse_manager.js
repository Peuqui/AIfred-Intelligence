// Live-VLM teleprompter SSE manager.
//
// Geladen einmal im vision-preview-popup. Sucht im DOM nach
// .vlm-event-target Elementen (jedes trägt ein data-vlm-source
// Attribut) und öffnet pro source_id einen EventSource auf
// /api/vision/events/<source_id>. Server-Sent-Events sind JSON
// {timestamp, description, ...} und werden im jeweiligen Target
// als 1-Zeile-pro-Event Teleprompter angehängt.
//
// Wichtig: KEIN MutationObserver — ein observer der auf jeden
// childList/subtree-Change reagiert würde sich durch unsere eigenen
// textContent-Updates selbst triggern und den Main-Thread einfrieren.
// Stattdessen polling alle 2 s plus idempotenter render (nur DOM-
// Update wenn der Inhalt wirklich anders ist).

console.log('[AIfred-VLM] script file fetched + parsed');

(function () {
  function boot() {
    if (window.__aifredVLMSSEInit) {
      console.log('[AIfred-VLM] already init, skip');
      return;
    }
    window.__aifredVLMSSEInit = true;
    console.log('[AIfred-VLM] SSE manager booting');

    var streams = {};
    var lines = {};
    var MAX_LINES = 8;

    function render(sid) {
      var items = lines[sid] || [];
      document.querySelectorAll(
        '.vlm-event-target[data-vlm-source="' + sid + '"]'
      ).forEach(function (el) {
        if (items.length === 0) {
          var idle = el.dataset.idleText || '';
          if (el.textContent !== idle) el.textContent = idle;
          if (el.style.fontStyle !== 'italic') el.style.fontStyle = 'italic';
          return;
        }
        if (el.style.fontStyle !== 'normal') el.style.fontStyle = 'normal';
        var joined = items.join('\n');
        if (el.textContent !== joined) {
          el.textContent = joined;
          el.scrollTop = el.scrollHeight;
        }
      });
    }

    function openStream(sid) {
      if (streams[sid] && streams[sid].readyState !== 2) return;
      var url = '/api/vision/events/' + sid;
      console.log('[AIfred-VLM] EventSource opening:', url);
      var es = new EventSource(url);
      streams[sid] = es;
      lines[sid] = lines[sid] || [];
      es.onopen = function () {
        console.log('[AIfred-VLM] EventSource open:', sid);
      };
      es.onmessage = function (ev) {
        try {
          var data = JSON.parse(ev.data);
          var ts = (data.timestamp || '').split('T')[1] || '';
          var desc = (data.description || '').replace(/\s+/g, ' ').trim();
          lines[sid].push(ts + '  ' + desc);
          if (lines[sid].length > MAX_LINES) lines[sid].shift();
          render(sid);
        } catch (e) {
          console.warn('[AIfred-VLM] parse error', e);
        }
      };
      es.onerror = function (e) {
        console.warn('[AIfred-VLM] EventSource error:', sid, e);
      };
    }

    function scan() {
      var targets = document.querySelectorAll(
        '.vlm-event-target[data-vlm-source]'
      );
      var seen = new Set();
      targets.forEach(function (el) {
        var sid = el.dataset.vlmSource;
        if (!sid) return;
        if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
        seen.add(sid);
        openStream(sid);
        render(sid);
      });
      Object.keys(streams).forEach(function (sid) {
        if (!seen.has(sid) && streams[sid]) {
          streams[sid].close();
          delete streams[sid];
        }
      });
    }

    scan();
    setInterval(scan, 2000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
