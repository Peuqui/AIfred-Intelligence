"""Bild-Vergrößerung: Lightbox und Hand-Schwenken (Hauptseite und Live-Vorschau).

Einheitliche Klickfolge für alle vergrößerbaren Bilder: Bubble/Thumbnail →
eingepasst → Zoom → zurück zum Ausgangszustand. Hier: Klick auf ein
``/_upload/``-Bild öffnet die VOLLE Auflösung als Overlay (eingepasst); ein
weiterer Klick ins Bild zeigt die 1:1-Pixelansicht, der nächste Klick schließt
(ebenso Klick neben das Bild/Escape; Bilder, die eingepasst schon 1:1 passen,
schließen beim zweiten Klick). Greift nur auf ``/_upload/``-Bilder
(Kamera-Fotos, Uploads, Crops) — Emojis/Icons bleiben unberührt.

Gezoomte Bilder lassen sich mit der Hand verschieben: jedes scrollbare Element
mit dem Attribut ``data-drag-pan`` (die 1:1-Lightbox selbst, der 200-%-Viewport
im Casus-Overlay) wird mit gedrückter Maus oder dem Finger geschwenkt. Ein Zug
von mehr als ein paar Pixeln ist kein Klick und schaltet den Zoom nicht um.

Das window-Flag macht den Listener idempotent gegen React-Remounts (gleiche
Falle wie beim custom_js).
"""

LIGHTBOX_JS = """
(function() {
    if (window.__aifredLightbox) return;
    window.__aifredLightbox = true;
    var st = document.createElement('style');
    st.textContent = 'img[src*="/_upload/"]{max-height:320px;max-width:100%;width:auto;object-fit:contain;cursor:zoom-in;border-radius:8px;}'
        + '.aifred-thumbrow{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;}'
        + '.aifred-thumbrow img{width:50px;height:50px;object-fit:cover;border-radius:4px;margin:0;}'
        + '[data-drag-pan]{cursor:grab;touch-action:none;user-select:none;}'
        + '[data-drag-pan]:active{cursor:grabbing;}'
        + '[data-drag-pan] img{-webkit-user-drag:none;cursor:inherit;}';
    document.head.appendChild(st);

    // Hand-Schwenken: scrollbares Element mit data-drag-pan per Zug verschieben.
    // Das Browser-eigene Bild-Ziehen (Geisterbild) würde die Zugbewegung
    // abfangen; ein Zug schluckt den folgenden Klick (Capture-Phase, vor dem
    // Lightbox- und dem React-Klick-Handler).
    var pan = null, panMoved = false;
    document.addEventListener('dragstart', function(ev) {
        if (ev.target.closest && ev.target.closest('[data-drag-pan]')) ev.preventDefault();
    });
    document.addEventListener('pointerdown', function(ev) {
        var box = ev.target.closest && ev.target.closest('[data-drag-pan]');
        if (!box || ev.button !== 0) return;
        pan = {box: box, x: ev.clientX, y: ev.clientY, left: box.scrollLeft, top: box.scrollTop};
        panMoved = false;
        box.setPointerCapture(ev.pointerId);
    }, true);
    document.addEventListener('pointermove', function(ev) {
        if (!pan) return;
        var dx = ev.clientX - pan.x, dy = ev.clientY - pan.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) panMoved = true;
        pan.box.scrollLeft = pan.left - dx;
        pan.box.scrollTop = pan.top - dy;
    });
    function endPan() { pan = null; }
    document.addEventListener('pointerup', endPan);
    document.addEventListener('pointercancel', endPan);
    document.addEventListener('click', function(ev) {
        if (!panMoved) return;
        panMoved = false;
        ev.stopPropagation();
        ev.preventDefault();
    }, true);

    document.addEventListener('click', function(e) {
        var img = e.target.closest && e.target.closest('img[src*="/_upload/"]');
        if (!img || img.closest('#aifred-lightbox')) return;
        var ov = document.createElement('div');
        ov.id = 'aifred-lightbox';
        var big = document.createElement('img');
        big.src = img.src;
        var fitted = true;
        // Zwei Modi: eingepasst (96vw/96vh, zentriert) und 1:1-Pixelansicht
        // (Bild in nativer Größe, Overlay scrollt → Schwenken per data-drag-pan).
        // Flex-Zentrierung muss im 1:1-Modus weg, sonst sind die linken/oberen
        // Ränder nicht erreichbar (Flexbox-Overflow-Falle).
        function applyMode() {
            ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.88);z-index:10000;overflow:auto;'
                + (fitted ? 'cursor:zoom-out;display:flex;align-items:center;justify-content:center;' : 'display:block;');
            big.style.cssText = fitted
                ? 'max-width:96vw;max-height:96vh;object-fit:contain;cursor:zoom-in;box-shadow:0 0 40px rgba(0,0,0,0.8);'
                : 'width:' + big.naturalWidth + 'px;max-width:none;max-height:none;display:block;';
            if (fitted) ov.removeAttribute('data-drag-pan');
            else ov.setAttribute('data-drag-pan', '');
        }
        // Klickfolge: Bubble → eingepasst → 1:1 → zu. Passt das Bild schon
        // eingepasst in 1:1 (nicht größer als der Bildschirm), entfällt die
        // 1:1-Stufe und der zweite Klick schließt.
        big.addEventListener('click', function(ev) {
            var zoomable = big.naturalWidth > ov.clientWidth + 8 || big.naturalHeight > ov.clientHeight + 8;
            if (!fitted || !zoomable) { close(); return; }
            ev.stopPropagation();
            var rx = ev.offsetX / big.clientWidth, ry = ev.offsetY / big.clientHeight;
            fitted = false;
            applyMode();
            // Klickstelle in die Bildschirmmitte scrollen
            ov.scrollLeft = rx * big.naturalWidth - ov.clientWidth / 2;
            ov.scrollTop = ry * big.naturalHeight - ov.clientHeight / 2;
        });
        function esc(ev) { if (ev.key === 'Escape') close(); }
        function close() { ov.remove(); document.removeEventListener('keydown', esc); }
        ov.addEventListener('click', close);
        document.addEventListener('keydown', esc);
        ov.appendChild(big);
        document.body.appendChild(ov);
        applyMode();
    });
})();
"""
