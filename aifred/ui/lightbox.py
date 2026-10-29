"""Lightbox für Content-Bilder (Hauptseite und Live-Vorschau).

In der Bubble verkleinert (CSS), Klick öffnet die VOLLE Auflösung als Overlay
(eingepasst); ein weiterer Klick ins Bild zeigt die 1:1-Pixelansicht mit
Schwenken, der nächste Klick schließt (ebenso Klick neben das Bild/Escape;
Bilder, die eingepasst schon 1:1 passen, schließen beim zweiten Klick). Greift nur auf ``/_upload/``-Bilder (Kamera-Fotos,
Uploads, Crops) — Emojis/Icons bleiben unberührt. Das window-Flag macht den
Listener idempotent gegen React-Remounts (gleiche Falle wie beim custom_js).
"""

LIGHTBOX_JS = """
(function() {
    if (window.__aifredLightbox) return;
    window.__aifredLightbox = true;
    var st = document.createElement('style');
    st.textContent = 'img[src*="/_upload/"]{max-height:320px;max-width:100%;width:auto;object-fit:contain;cursor:zoom-in;border-radius:8px;}'
        + '.aifred-thumbrow{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;}'
        + '.aifred-thumbrow img{width:50px;height:50px;object-fit:cover;border-radius:4px;margin:0;}';
    document.head.appendChild(st);
    document.addEventListener('click', function(e) {
        var img = e.target.closest && e.target.closest('img[src*="/_upload/"]');
        if (!img || img.closest('#aifred-lightbox')) return;
        var ov = document.createElement('div');
        ov.id = 'aifred-lightbox';
        var big = document.createElement('img');
        big.src = img.src;
        var fitted = true;
        // Zwei Modi: eingepasst (96vw/96vh, zentriert) und 1:1-Pixelansicht
        // (Bild in nativer Größe, Overlay scrollt → Schwenken). Flex-Zentrierung
        // muss im 1:1-Modus weg, sonst sind die linken/oberen Ränder nicht
        // erreichbar (Flexbox-Overflow-Falle).
        function applyMode() {
            ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.88);z-index:10000;overflow:auto;cursor:zoom-out;'
                + (fitted ? 'display:flex;align-items:center;justify-content:center;' : 'display:block;');
            big.style.cssText = fitted
                ? 'max-width:96vw;max-height:96vh;object-fit:contain;cursor:zoom-in;box-shadow:0 0 40px rgba(0,0,0,0.8);'
                : 'width:' + big.naturalWidth + 'px;max-width:none;max-height:none;display:block;cursor:grab;touch-action:none;user-select:none;-webkit-user-drag:none;';
        }
        // 1:1-Ansicht: Bild mit gedrückter Maus (oder dem Finger) verschieben.
        // Ein Zug von mehr als ein paar Pixeln ist kein Klick und schaltet die
        // Ansicht nicht um.
        var drag = null, moved = false;
        // Das Browser-eigene Bild-Ziehen (Geisterbild mit Verbotszeichen) würde die
        // Zugbewegung abfangen.
        big.draggable = false;
        big.addEventListener('dragstart', function(ev) { ev.preventDefault(); });
        big.addEventListener('pointerdown', function(ev) {
            if (fitted) return;
            ev.preventDefault();
            drag = {x: ev.clientX, y: ev.clientY, left: ov.scrollLeft, top: ov.scrollTop};
            moved = false;
            big.setPointerCapture(ev.pointerId);
            big.style.cursor = 'grabbing';
        });
        big.addEventListener('pointermove', function(ev) {
            if (!drag) return;
            var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
            if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
            ov.scrollLeft = drag.left - dx;
            ov.scrollTop = drag.top - dy;
        });
        function endDrag() {
            if (!drag) return;
            drag = null;
            big.style.cursor = 'grab';
        }
        big.addEventListener('pointerup', endDrag);
        big.addEventListener('pointercancel', endDrag);
        // Klickfolge: Bubble → eingepasst → 1:1 → zu. Passt das Bild schon
        // eingepasst in 1:1 (nicht größer als der Bildschirm), entfällt die
        // 1:1-Stufe und der zweite Klick schließt.
        big.addEventListener('click', function(ev) {
            if (moved) { moved = false; ev.stopPropagation(); return; }
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
