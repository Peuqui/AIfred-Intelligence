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
    var lines = {};        // VLM-Analysis-Zeilen pro source
    var faceLines = {};    // Face-Events pro source
    var MAX_LINES = 10;
    var MAX_FACE_LINES = 8;

    function escapeHtml(s) {
      return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function hms(iso) {
      var t = (iso || '').split('T')[1] || '';
      return t.split('.')[0];  // HH:MM:SS ohne Millisekunden
    }

    // ``< 50 px Abstand vom Boden`` zählt als „User schaut die
    // neuesten an" → wir scrollen mit. Wenn er bewusst hochscrollt
    // (Abstand > 50), lassen wir die Position in Ruhe.
    var AUTO_SCROLL_THRESHOLD = 50;
    function wasNearBottom(el) {
      return (el.scrollHeight - el.scrollTop - el.clientHeight)
             < AUTO_SCROLL_THRESHOLD;
    }

    function render(sid) {
      var items = lines[sid] || [];
      document.querySelectorAll(
        '.vlm-event-target[data-vlm-source="' + sid + '"]'
      ).forEach(function (el) {
        if (items.length === 0) {
          var idle = el.dataset.idleText || '';
          if (el.innerHTML !== escapeHtml(idle)) el.innerHTML = escapeHtml(idle);
          if (el.style.fontStyle !== 'italic') el.style.fontStyle = 'italic';
          return;
        }
        if (el.style.fontStyle !== 'normal') el.style.fontStyle = 'normal';
        // Eine Zeile = ein Div. CSS-Border-Bottom liefert die
        // Separator-Linie ohne zusätzliche Zeilenhöhe.
        var html = items.map(function (line) {
          return '<div class="vlm-line">' + escapeHtml(line) + '</div>';
        }).join('');
        if (el.innerHTML !== html) {
          var stickToBottom = wasNearBottom(el);
          el.innerHTML = html;
          if (stickToBottom) el.scrollTop = el.scrollHeight;
        }
      });
    }

    function renderFaces(sid) {
      var items = faceLines[sid] || [];
      document.querySelectorAll(
        '.vlm-face-target[data-vlm-face-source="' + sid + '"]'
      ).forEach(function (el) {
        if (items.length === 0) {
          var idle = el.dataset.idleText || '';
          if (el.innerHTML !== escapeHtml(idle)) el.innerHTML = escapeHtml(idle);
          if (el.style.fontStyle !== 'italic') el.style.fontStyle = 'italic';
          return;
        }
        if (el.style.fontStyle !== 'normal') el.style.fontStyle = 'normal';
        // Jede Zeile: 64×64 Thumb + Dot + Text + (bei unknown)
        // Enroll- + Verwerfen-Button. ``data-item-id`` ist ein
        // synthetischer Index (push-counter), den der Click-Handler
        // nutzt, um den richtigen Eintrag aus ``faceLines[sid]`` zu
        // entfernen.
        var html = items.map(function (item) {
          var dotColor = item.band === 'known' ? '#22c55e'
                       : item.band === 'unsure' ? '#eab308'
                       : '#ef4444';
          var dot = '<span class="vlm-face-dot" style="background:' +
                    dotColor + '"></span>';
          var thumb = item.crop_url
            ? '<img class="vlm-face-thumb" src="' +
              escapeHtml(item.crop_url) + '" alt="" />'
            : '<span class="vlm-face-thumb-placeholder"></span>';
          var actions = '';
          if (item.band === 'unknown' && item.embedding_b64) {
            actions =
              '<button class="vlm-face-enroll-btn" ' +
                'data-embedding="' + escapeHtml(item.embedding_b64) + '" ' +
                'data-source="' + escapeHtml(sid) + '" ' +
                'data-item-id="' + item._id + '">' +
                escapeHtml(item.enroll_label || '+ taggen') +
              '</button>' +
              '<button class="vlm-face-discard-btn" ' +
                'data-source="' + escapeHtml(sid) + '" ' +
                'data-item-id="' + item._id + '" ' +
                'title="' + escapeHtml(item.discard_label || 'Verwerfen') + '">' +
                '✗' +
              '</button>';
          }
          return '<div class="vlm-line vlm-face-line" data-item-id="' +
                 item._id + '">' +
                 thumb + dot +
                 '<span class="vlm-face-text">' + escapeHtml(item.text) + '</span>' +
                 actions + '</div>';
        }).join('');
        if (el.innerHTML !== html) {
          var stickToBottom = wasNearBottom(el);
          el.innerHTML = html;
          if (stickToBottom) el.scrollTop = el.scrollHeight;
        }
      });
    }

    function handleVlmEvent(sid, data) {
      var startTs = hms(data.timestamp);
      var endTs = hms(data.inference_end);
      var captureTs = hms(data.frame_timestamp);
      var desc = (data.description || '').replace(/\s+/g, ' ').trim();
      var parts = [];
      if (captureTs) parts.push(captureTs);
      if (startTs) parts.push(startTs);
      if (endTs) parts.push(endTs);
      var prefix = parts.length > 0 ? parts.join(' → ') : '';
      lines[sid] = lines[sid] || [];
      lines[sid].push((prefix ? prefix + '  ' : '') + desc);
      if (lines[sid].length > MAX_LINES) lines[sid].shift();
      render(sid);
    }

    var _faceItemCounter = 0;

    function handleFaceEvent(sid, data) {
      var band = data.confidence_band || 'unknown';
      var ts = hms(data.timestamp);
      var name = (data.name || '').trim();
      var sim = data.similarity != null
        ? ' · ' + Math.round(data.similarity * 100) + '%'
        : '';
      var label = (band === 'unknown' || !name)
        ? ts + '  Unbekannt' + sim
        : ts + '  ' + name + sim;
      faceLines[sid] = faceLines[sid] || [];

      // Dedup anhand session_id: solange die Person kontinuierlich
      // gesehen wird, hat das Backend dieselbe session_id → UI-Zeile
      // wird aktualisiert statt eine neue zu pushen. Eine neue
      // Sichtung nach > 10 s Pause bekommt vom Backend eine neue
      // session_id → neue Zeile, neuer Zeitstempel.
      var sessionId = data.session_id || '';
      var existingIdx = -1;
      if (sessionId) {
        for (var i = 0; i < faceLines[sid].length; i++) {
          if (faceLines[sid][i].session_id === sessionId) {
            existingIdx = i;
            break;
          }
        }
      }

      var existingId = existingIdx >= 0
        ? faceLines[sid][existingIdx]._id
        : (_faceItemCounter += 1, _faceItemCounter);

      var item = {
        _id: existingId,
        session_id: sessionId,
        band: band,
        text: label,
        crop_url: data.crop_url || '',
        embedding_b64: data.embedding_b64 || '',
        event_id: data.id || 0,
        enroll_label: window.__aifredFaceEnrollLabel || '+ taggen',
        discard_label: window.__aifredFaceDiscardLabel || 'Verwerfen',
      };

      if (existingIdx >= 0) {
        faceLines[sid][existingIdx] = item;
      } else {
        faceLines[sid].push(item);
        if (faceLines[sid].length > MAX_FACE_LINES) faceLines[sid].shift();
      }
      renderFaces(sid);
    }

    function removeFaceItem(sid, itemId) {
      if (!sid || !itemId) return;
      faceLines[sid] = (faceLines[sid] || []).filter(function (it) {
        return String(it._id) !== String(itemId);
      });
      renderFaces(sid);
    }

    function openStream(sid) {
      if (streams[sid] && streams[sid].readyState !== 2) return;
      var url = '/api/vision/events/' + sid;
      console.log('[AIfred-VLM] EventSource opening:', url);
      var es = new EventSource(url);
      streams[sid] = es;
      lines[sid] = lines[sid] || [];
      faceLines[sid] = faceLines[sid] || [];
      es.onopen = function () {
        console.log('[AIfred-VLM] EventSource open:', sid);
      };
      es.onmessage = function (ev) {
        try {
          var data = JSON.parse(ev.data);
          var type = data.type || 'vlm_analysis';
          if (type === 'vlm_analysis') {
            handleVlmEvent(sid, data);
          } else if (type === 'face_known' || type === 'face_unsure'
                     || type === 'face_unknown') {
            handleFaceEvent(sid, data);
          }
        } catch (e) {
          console.warn('[AIfred-VLM] parse error', e);
        }
      };
      es.onerror = function (e) {
        console.warn('[AIfred-VLM] EventSource error:', sid, e);
      };
    }

    function scan() {
      // VLM-Targets sammeln Source-IDs für EventSource-Öffnung.
      var seen = new Set();
      document.querySelectorAll(
        '.vlm-event-target[data-vlm-source]'
      ).forEach(function (el) {
        var sid = el.dataset.vlmSource;
        if (!sid) return;
        if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
        seen.add(sid);
        openStream(sid);
        render(sid);
      });
      // Face-Targets: gleiche Source-ID-Set, idle-Text cachen + render.
      document.querySelectorAll(
        '.vlm-face-target[data-vlm-face-source]'
      ).forEach(function (el) {
        var sid = el.dataset.vlmFaceSource;
        if (!sid) return;
        if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
        seen.add(sid);
        openStream(sid);
        renderFaces(sid);
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

    // „Light-Table"-Overlay PRO image-Slot: ein großes Bild liegt
    // über dem Live-Video-Bereich. Klick auf weitere Crop-Thumbs
    // wechselt das Bild im selben Slot (kein neues Modal). Klick
    // aufs große Bild schließt das Overlay → Live-Video kommt zurück.
    function showLightTable(sourceId, imageUrl) {
      if (!sourceId || !imageUrl) return;
      var slot = document.querySelector(
        '[data-vlm-image-slot="' + sourceId + '"]'
      );
      if (!slot) {
        console.warn('[AIfred-VLM] no image-slot found for', sourceId);
        return;
      }
      var overlay = slot.querySelector('.vlm-light-table-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'vlm-light-table-overlay';
        var img = document.createElement('img');
        img.className = 'vlm-light-table-img';
        overlay.appendChild(img);
        // Klick auf das Overlay (inkl. das Bild) schließt — wir lassen
        // pointer-events am img passieren, damit ein Klick darauf
        // bis zum Overlay-Handler durchkommt.
        overlay.addEventListener('click', function () {
          overlay.style.display = 'none';
        });
        slot.appendChild(overlay);
      }
      var img = overlay.querySelector('.vlm-light-table-img');
      img.src = imageUrl;
      overlay.style.display = 'flex';
    }

    // Click-Delegation: Crop-Thumb → Lightbox, Enroll-Button → Tag,
    // Verwerfen-Button → Zeile raus.
    document.addEventListener('click', function (ev) {
      var thumb = ev.target.closest('.vlm-face-thumb');
      if (thumb && thumb.src) {
        // Source-ID vom umgebenden .vlm-face-target holen.
        var target = thumb.closest('.vlm-face-target');
        var srcId = target ? target.dataset.vlmFaceSource : '';
        showLightTable(srcId, thumb.src);
        return;
      }
      var discardBtn = ev.target.closest('.vlm-face-discard-btn');
      if (discardBtn) {
        removeFaceItem(discardBtn.dataset.source, discardBtn.dataset.itemId);
        return;
      }
      var btn = ev.target.closest('.vlm-face-enroll-btn');
      if (!btn) return;
      var emb = btn.dataset.embedding;
      var src = btn.dataset.source;
      var itemId = btn.dataset.itemId;
      if (!emb || !src) return;
      var promptLabel = window.__aifredFaceEnrollPrompt || 'Name?';
      var name = window.prompt(promptLabel, '');
      if (!name) return;
      btn.disabled = true;
      btn.textContent = '…';
      fetch('/api/vision/face/enroll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          source_id: src,
          embedding_b64: emb,
        }),
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).then(function (data) {
        console.log('[AIfred-VLM] enrolled:', data);
        // Erfolg: Zeile aus Liste raus — beim nächsten Frame wird
        // diese Person dann hoffentlich als known erkannt.
        removeFaceItem(src, itemId);
      }).catch(function (e) {
        console.warn('[AIfred-VLM] enroll failed:', e);
        btn.disabled = false;
        btn.textContent = window.__aifredFaceEnrollLabel || '+ taggen';
      });
    });

    // Clear-Handler für die Teleprompter-Box (VLM-Analyse).
    window.clearVlmTeleprompter = function (sid) {
      if (!sid) return;
      lines[sid] = [];
      render(sid);
    };
    // Clear-Handler für die „Erkannte Personen"-Box.
    window.clearVlmFaces = function (sid) {
      if (!sid) return;
      faceLines[sid] = [];
      renderFaces(sid);
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
