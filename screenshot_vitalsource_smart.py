import asyncio
import os
import io
import base64
import random
from playwright.async_api import async_playwright
from PIL import Image, ImageChops

# ── CONFIG ───────────────────────────────────────────────────────────────────
BOOK_ID       = "978-1-890989-46-0"
START_PAGE    = 11
END_PAGE      = 1163
OUTPUT_DIR    = "vitalsource_pages"
MIN_DELAY     = 1     # minimum seconds between page turns
MAX_DELAY     = 15    # maximum seconds between page turns
SKIP_EXISTING = True  # safe to resume if interrupted
MIN_WIDTH     = 1500  # any image with width smaller than this will be redownloaded
# ─────────────────────────────────────────────────────────────────────────────


async def get_jigsaw_frame(page):
    """Find the jigsaw content iframe (prefer /content path)."""
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url and "/content" in frame.url:
            return frame
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url:
            return frame
    return None


async def wait_for_image_load(frame, max_wait=45):
    """Wait until img#pbk-page is fully loaded inside the frame with high resolution."""
    for _ in range(max_wait):
        await asyncio.sleep(1)
        try:
            loaded = await frame.evaluate("""
                () => {
                    const img = document.querySelector('img#pbk-page');
                    // Check if it's loaded and natural width is large enough to be a high-res page (not a small placeholder)
                    return img && img.naturalWidth > 500 && img.complete;
                }
            """)
            if loaded:
                # Add a small buffer to ensure the highest resolution image has fully rendered
                await asyncio.sleep(2)
                return True
        except Exception:
            pass
    return False


def autocrop(img_bytes: bytes) -> bytes:
    """
    Remove uniform white/near-white borders.
    Only applied to screenshot fallbacks, not raw-fetched images.
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    bg  = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()

    if bbox:
        pad = 8
        x1 = max(0,          bbox[0] - pad)
        y1 = max(0,          bbox[1] - pad)
        x2 = min(img.width,  bbox[2] + pad)
        y2 = min(img.height, bbox[3] + pad)
        img = img.crop((x1, y1, x2, y2))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def capture_page(page, pid: int) -> tuple[bytes | None, bool]:
    """
    Returns (image_bytes, is_raw_fetch).

      is_raw_fetch = True  → bytes came from direct src fetch  → skip autocrop
      is_raw_fetch = False → bytes came from a screenshot      → apply autocrop

    Strategy order:
      1. Fetch img#pbk-page src directly  — native server resolution (BEST)
      2. High-DPR element screenshot      — 2× rendered quality   (good fallback)
      3. iframe element screenshot        — last resort
      4. None                             — no book content found (never captures UI)
    """
    frame = await get_jigsaw_frame(page)
    if not frame:
        return None, False

    # Wait up to 45 seconds for the high resolution image to load
    await wait_for_image_load(frame, max_wait=45)

    # ── Strategy 1: Fetch the raw image from its src URL ─────────────────────
    # Uses btoa inside the browser so we never hit Python array-size limits.
    b64 = await frame.evaluate("""
        async () => {
            const img = document.querySelector('img#pbk-page');
            if (!img || !img.src) return null;
            try {
                const resp = await fetch(img.src, { credentials: 'include' });
                if (!resp.ok) return null;
                const buf    = await resp.arrayBuffer();
                const bytes  = new Uint8Array(buf);
                let binary   = '';
                const chunk  = 8192;
                for (let i = 0; i < bytes.byteLength; i += chunk) {
                    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
                }
                return btoa(binary);
            } catch (e) {
                return null;
            }
        }
    """)

    if b64:
        return base64.b64decode(b64), True      # raw fetch — native resolution

    # ── Strategy 2: High-DPR element screenshot (2× via CDP override) ────────
    el = await frame.query_selector("img#pbk-page")
    if el:
        await el.scroll_into_view_if_needed()
        raw = await el.screenshot(type="png", scale="device")
        return raw, False                        # screenshot — autocrop needed

    # ── Strategy 3: Fallback — screenshot the iframe element itself ───────────
    iframe_el = await page.query_selector("iframe[src*='jigsaw']")
    if iframe_el:
        raw = await iframe_el.screenshot(type="png")
        return raw, False                        # screenshot — autocrop needed

    # ── Strategy 4: Give up — no book content detected ───────────────────────
    return None, False


async def click_next_or_navigate(page, pid: int):
    """Click the Next button, or fall back to direct URL navigation."""
    # Delay BEFORE clicking Next (simulating reading time)
    delay_before = random.randint(MIN_DELAY, MAX_DELAY)
    print(f"  → Download complete. Waiting {delay_before}s before clicking Next...")
    await asyncio.sleep(delay_before)

    # Delay AFTER clicking Next (simulating network jitter)
    delay_after = random.randint(MIN_DELAY, MAX_DELAY)
    
    next_btn = await page.query_selector('button[aria-label="Next"]')
    if next_btn:
        await next_btn.click()
        print(f"  → Clicked Next. Waiting {delay_after}s before continuing...")
        await asyncio.sleep(delay_after)
    else:
        print(f"  ! Next button not found — navigating via URL. Waiting {delay_after}s...")
        next_url = (
            f"https://online.vitalsource.com/reader/books/"
            f"{BOOK_ID}/pageid/{pid + 1}"
        )
        try:
            await page.goto(next_url, wait_until="networkidle", timeout=30_000)
        except Exception as e:
            print(f"  ! URL navigation warning: {e}")
        await asyncio.sleep(delay_after)


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print("\n🛑 ERROR: Cannot connect to Chrome.")
            print("Chrome is not running with the remote debugging port open.")
            print("\nTo fix this:")
            print("1. Completely close ALL open Chrome/Chromium windows.")
            print("2. Open a terminal and run:  chromium --remote-debugging-port=9222  (or google-chrome --remote-debugging-port=9222)")
            print("3. Log into Vitalsource and open your book in that new browser window.")
            print("4. Run this script again.")
            print("\nTechnical details:", e)
            return

        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else await context.new_page()

        # ── Viewport ──────────────────────────────────────────────────────────
        await page.set_viewport_size({"width": 2000, "height": 2600})

        # ── CDP: force 3× device pixel ratio for screenshot fallbacks ─────────
        cdp = await context.new_cdp_session(page)
        await cdp.send("Emulation.setDeviceMetricsOverride", {
            "width":             2000,
            "height":            2600,
            "deviceScaleFactor": 3,      # ultra-high resolution screenshots
            "mobile":            False,
            "screenWidth":       2000,
            "screenHeight":      2600,
        })

        done = skipped = failed = replaced = 0
        needs_navigation = True

        # ── Main Smart Capture Loop ───────────────────────────────────────────
        for pid in range(START_PAGE, END_PAGE + 1):
            out = os.path.join(OUTPUT_DIR, f"page_{pid:05d}.png")

            # Check if file exists and verify its quality
            if SKIP_EXISTING and os.path.exists(out):
                try:
                    with Image.open(out) as img:
                        width, height = img.size
                    
                    if width >= MIN_WIDTH:
                        print(f"─ skip  {pid:05d}  (already exists, good quality {width}x{height})")
                        skipped += 1
                        needs_navigation = True  # We skipped, so we lost our place in the book
                        continue
                    else:
                        print(f"─ replacing {pid:05d} (low quality: {width}x{height})")
                        os.remove(out)
                        replaced += 1
                except Exception as e:
                    print(f"─ replacing {pid:05d} (corrupted or unreadable image)")
                    os.remove(out)
                    replaced += 1

            # If we skipped pages, we must manually navigate to the correct page again
            if needs_navigation:
                nav_url = f"https://online.vitalsource.com/reader/books/{BOOK_ID}/pageid/{pid}"
                print(f"→ Navigating directly to page {pid} …")
                try:
                    await page.goto(nav_url, wait_until="networkidle", timeout=60_000)
                except Exception as e:
                    print(f"  Warning on navigation: {e}")
                
                await asyncio.sleep(3)

                # Force reader to fit-by-height
                await page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button, [role="button"]');
                        for (const btn of btns) {
                            const label = (
                                btn.getAttribute('aria-label') ||
                                btn.title ||
                                btn.textContent || ''
                            ).toLowerCase();
                            if (
                                label.includes('height') ||
                                label.includes('fit page') ||
                                label.includes('full page')
                            ) {
                                btn.click();
                                break;
                            }
                        }
                    }
                """)
                await asyncio.sleep(2)
                needs_navigation = False

            # Capture the current page
            try:
                raw, is_raw_fetch = await capture_page(page, pid)

                if raw is None:
                    print(f"✗ skip  {pid:05d}  (no book content detected)")
                    failed += 1
                else:
                    final     = raw if is_raw_fetch else autocrop(raw)
                    src_label = "fetch" if is_raw_fetch else "screenshot"

                    with open(out, "wb") as f:
                        f.write(final)

                    size_kb = len(final) // 1024
                    done += 1
                    print(f"✓ {pid:05d}  [{src_label}]  {size_kb} KB  → {out}")

                # If this is not the last page, click next
                if pid < END_PAGE:
                    await click_next_or_navigate(page, pid)

            except KeyboardInterrupt:
                print("\n🛑 Stopped by user.")
                break

            except Exception as e:
                msg = str(e).lower()
                if "closed" in msg or "disconnected" in msg:
                    print(f"\n🛑 Browser connection lost. Stopping.")
                    break

                print(f"✗ error {pid}: {e}")
                failed += 1
                needs_navigation = True # Need to recover position on next loop

    print(f"\n{'─'*55}")
    print(f"  ✅ Done")
    print(f"     Captured     : {done}")
    print(f"     Replaced     : {replaced}")
    print(f"     Skipped      : {skipped}")
    print(f"     Failed       : {failed}")
    print(f"     Output       : '{OUTPUT_DIR}/'")
    print(f"{'─'*55}")


if __name__ == "__main__":
    asyncio.run(main())
