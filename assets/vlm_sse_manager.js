// Live-VLM teleprompter SSE manager.
//
// Loaded once on the vision-preview-popup page. Watches the DOM for
// .vlm-event-target elements (each carrying a data-vlm-source
// attribute) and opens an EventSource per unique source into the
// backend's /api/vision/events/<source_id> endpoint. Server-Sent-
// Events arrive as JSON {timestamp, description, ...} and get
// appended to the matching DOM target as a 1-per-line teleprompter.

console.log('[AIfred-VLM] script file fetched + parsed');
(function () {
  if (window.__aifredVLMSSEInit) {
    console.log('[AIfred-VLM] already init, skip');
    return;
  }
  window.__aifredVLMSSEInit = true;
  console.log('[AIfred-VLM] SSE manager booting');

  const streams = {};
  const lines = {};
  const MAX_LINES = 8;

  function render(sid) {
    const items = lines[sid] || [];
    document.querySelectorAll(
      '.vlm-event-target[data-vlm-source="' + sid + '"]'
    ).forEach(function (el) {
      if (items.length === 0) {
        el.textContent = el.dataset.idleText || '';
        el.style.fontStyle = 'italic';
        return;
      }
      el.style.fontStyle = 'normal';
      el.textContent = items.join('\n');
      el.scrollTop = el.scrollHeight;
    });
  }

  function openStream(sid) {
    if (streams[sid] && streams[sid].readyState !== 2) return;
    const url = '/api/vision/events/' + sid;
    console.log('[AIfred-VLM] EventSource opening:', url);
    const es = new EventSource(url);
    streams[sid] = es;
    lines[sid] = lines[sid] || [];
    es.onopen = function () {
      console.log('[AIfred-VLM] EventSource open:', sid);
    };
    es.onmessage = function (ev) {
      try {
        const data = JSON.parse(ev.data);
        const ts = (data.timestamp || '').split('T')[1] || '';
        const desc = (data.description || '').replace(/\s+/g, ' ').trim();
        const line = ts + '  ' + desc;
        lines[sid].push(line);
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
    const targets = document.querySelectorAll(
      '.vlm-event-target[data-vlm-source]'
    );
    const seen = new Set();
    targets.forEach(function (el) {
      const sid = el.dataset.vlmSource;
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

  const obs = new MutationObserver(function () { scan(); });
  obs.observe(document.body, { childList: true, subtree: true });
  scan();
})();
