/*
 * VitalSource console page downloader
 * =========================================================
 * The book page images load inside a frame on jigsaw.vitalsource.com.
 * A script running in the "top" console CANNOT reach them (cross-origin),
 * so this must be run with the Console's execution context set to the
 * jigsaw reader frame (wrapper.html).
 *
 * HOW TO USE
 *   1. Open the book in Chrome, log in, and navigate to the first page you
 *      want to download (e.g. page 532).
 *   2. Press F12 -> Console tab.
 *   3. Top-left of the Console is a context dropdown (shows "top"). Click it
 *      and select the frame named:
 *          https://jigsaw.vitalsource.com/mosaic/wrapper.html...  (type=book)
 *   4. Paste this entire script and press Enter.
 *      It will ask for the current page number -> enter it (e.g. 532).
 *      You will see: "Auto-watch running from page 532 — flip pages..."
 *   5. Go back to the book and flip pages normally (one page per ~30s).
 *      ~13s after each page loads it downloads as page_XXXXX.png into your
 *      Downloads folder. Page numbers increment automatically.
 *   6. To stop: refresh the page (Ctrl+R).
 *
 * NOTES
 *   - The next page number is remembered in localStorage, so after a refresh
 *     you only need to re-run the script (no prompt if it already knows).
 *   - If you jump around instead of going forward, the numbering follows
 *     page turns, not the printed page number.
 *   - Keep a human reading pace to avoid rate-limit blocks.
 */
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rnd = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const LS_KEY = "vs_pid";

  function findImg(win) {
    const doc = win.document;
    const el = doc.querySelector("img#pbk-page");
    if (el) return el;
    for (const f of doc.querySelectorAll("iframe")) {
      try {
        const found = findImg(f.contentWindow);
        if (found) return found;
      } catch (e) { /* cross-origin frame, cannot reach */ }
    }
    return null;
  }

  const img = findImg(window);
  if (!img) {
    console.error("✖ No img#pbk-page reachable from this frame — you are in the 'top' console context.");
    console.error("  The book page images live inside a jigsaw.vitalsource.com frame, and the");
    console.error("  'top' page (online.vitalsource.com) is cross-origin to them, so a script");
    console.error("  here CANNOT see or download the page images. No code can fix this.");
    console.error("");
    console.error("  HOW TO FIX (once, ~5 seconds):");
    console.error("  1. In the Console, look at the very top-LEFT corner of the console panel.");
    console.error("  2. There is a dropdown that currently says 'top'.");
    console.error("  3. Click it and choose the entry:");
    console.error("        jigsaw.vitalsource.com/mosaic/wrapper.html  (or 'wrapper')");
    console.error("  4. The Console will switch. Re-paste this script and press Enter.");
    console.error("  If you don't see the dropdown, click the gear/three-dot menu of the Console");
    console.error("  and make sure 'Autocomplete' / toolbar is visible, or resize the console wider.");
    return;
  }
  console.log(`✓ Found img#pbk-page from context: ${location.href}`);

  let pid = parseInt(localStorage.getItem(LS_KEY), 10);
  if (!pid || pid < 0) {
    pid = parseInt(prompt("Current page number shown? e.g. 532"), 10) || 532;
  }

  let seen = null;
  console.log(`Auto-watch running from page ${pid} — flip pages; refresh to stop.`);

  while (true) {
    const el = findImg(window);
    const src = el && el.src;
    if (src && src !== seen && el.complete && el.naturalWidth > 500) {
      seen = src;
      await sleep(rnd(8, 18) * 1000);
      const cur = findImg(window);
      if (!cur || cur.src !== src) continue;

      try {
        const name = `page_${String(pid).padStart(5, "0")}.png`;
        let blob = null;

        try {
          const resp = await fetch(src, { credentials: "include" });
          if (resp.ok) blob = await resp.blob();
        } catch (e) { /* fall through to canvas */ }

        if (!blob) {
          const c = document.createElement("canvas");
          c.width = cur.naturalWidth;
          c.height = cur.naturalHeight;
          c.getContext("2d").drawImage(cur, 0, 0);
          blob = await new Promise(r => c.toBlob(r, "image/png"));
        }

        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);

        localStorage.setItem(LS_KEY, String(pid + 1));
        console.log(`downloaded ${name}  (${(blob.size / 1024).toFixed(0)} KB)`);
        pid += 1;
        await sleep(rnd(8, 18) * 1000);
      } catch (e) {
        console.log(`failed page_${pid}: ${e}`);
      }
    }
    await sleep(2000);
  }
})();
