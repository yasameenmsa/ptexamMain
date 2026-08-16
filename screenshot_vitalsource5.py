import asyncio
import os
import io
import base64
import random
from playwright.async_api import async_playwright
from PIL import Image, ImageChops

# ── CONFIG ───────────────────────────────────────────────────────────────────
BOOK_ID       = "978-1-890989-46-0"
START_PAGE    = 1005
END_PAGE      = 1215
TARGET_NEW_PAGES = 211  # capture this many new pages, then stop
OUTPUT_DIR    = "vitalsource_pages"
MIN_DELAY     = 8     # minimum seconds between page turns (safety: avoid bans)
MAX_DELAY     = 14    # maximum seconds between page turns
BREAK_EVERY   = 8     # take a longer rest after this many pages
BREAK_SLEEP   = (25, 50)  # rest range in seconds
SKIP_EXISTING = True  # safe to resume if interrupted
# ─────────────────────────────────────────────────────────────────────────────


def get_missing_pages():
    existing = set()
    if os.path.exists(OUTPUT_DIR):
        for f in os.listdir(OUTPUT_DIR):
            if f.startswith("page_") and f.endswith(".png"):
                try:
                    existing.add(int(f.split("_")[1].split(".")[0]))
                except ValueError:
                    pass
    missing = [p for p in range(START_PAGE, END_PAGE + 1) if p not in existing]
    return missing


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
                    return img && img.naturalWidth > 500 && img.complete;
                }
            """)
            if loaded:
                await asyncio.sleep(2)
                return True
        except Exception:
            pass
    return False


def autocrop(img_bytes: bytes) -> bytes:
    """Remove uniform white/near-white borders (screenshot fallbacks only)."""
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


async def capture_page(page, pid: int) -> tuple:
    """
    Returns (image_bytes, is_raw_fetch).

      is_raw_fetch = True  → bytes from direct src fetch  → skip autocrop
      is_raw_fetch = False → bytes from a screenshot      → apply autocrop

    Strategy order:
      1. Fetch img#pbk-page src directly  — native server resolution (BEST)
      2. High-DPR element screenshot      — 2x rendered quality   (good fallback)
      3. iframe element screenshot        — last resort
      4. None                             — no book content found (never captures UI)
    """
    frame = await get_jigsaw_frame(page)
    if not frame:
        return None, False

    await wait_for_image_load(frame, max_wait=45)

    # ── Strategy 1: Fetch the raw image from its src URL ─────────────────────
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
        return base64.b64decode(b64), True

    # ── Strategy 2: High-DPR element screenshot ──────────────────────────────
    el = await frame.query_selector("img#pbk-page")
    if el:
        await el.scroll_into_view_if_needed()
        raw = await el.screenshot(type="png", scale="device")
        return raw, False

    # ── Strategy 3: Fallback — screenshot the iframe element itself ───────────
    iframe_el = await page.query_selector("iframe[src*='jigsaw']")
    if iframe_el:
        raw = await iframe_el.screenshot(type="png")
        return raw, False

    # ── Strategy 4: Give up ───────────────────────────────────────────────────
    return None, False


async def click_next_or_navigate(page, pid: int):
    """Click the Next button, or fall back to direct URL navigation."""
    delay_before = random.randint(MIN_DELAY, MAX_DELAY)
    print(f"  → Download complete. Waiting {delay_before}s before clicking Next...")
    await asyncio.sleep(delay_before)

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

    missing = get_missing_pages()
    if not missing:
        print("\n✅ All pages already captured.")
        return

    run_start = missing[0]
    run_end   = missing[min(TARGET_NEW_PAGES - 1, len(missing) - 1)]
    target    = min(TARGET_NEW_PAGES, len(missing))
    print(f"Missing: {len(missing)} | Capturing next {target} pages: "
          f"{run_start}–{run_end}")

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

        # ── CDP: force 3x device pixel ratio for screenshot fallbacks ─────────
        cdp = await context.new_cdp_session(page)
        await cdp.send("Emulation.setDeviceMetricsOverride", {
            "width":             2000,
            "height":            2600,
            "deviceScaleFactor": 3,
            "mobile":            False,
            "screenWidth":       2000,
            "screenHeight":      2600,
        })

        done = skipped = failed = 0
        _last_break_at = 0
        current_pid = run_start

        # ── Navigate to the first missing page ───────────────────────────────
        start_url = (
            f"https://online.vitalsource.com/reader/books/"
            f"{BOOK_ID}/pageid/{current_pid}"
        )
        print(f"→ Navigating to starting page {current_pid} …")
        try:
            await page.goto(start_url, wait_until="networkidle", timeout=60_000)
        except Exception as e:
            print(f"  Warning on initial navigation: {e}")

        await asyncio.sleep(2)

        # ── Force reader to fit-by-height ────────────────────────────────────
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

        # ── Main capture loop ─────────────────────────────────────────────────
        for pid in range(current_pid, run_end + 1):
            out = os.path.join(OUTPUT_DIR, f"page_{pid:05d}.png")

            if SKIP_EXISTING and os.path.exists(out):
                print(f"─ skip  {pid}  (already exists)")
                skipped += 1
                if pid < run_end:
                    await click_next_or_navigate(page, pid)
                continue

            try:
                raw, is_raw_fetch = await capture_page(page, pid)

                if raw is None:
                    print(f"✗ skip  {pid}  (no book content detected)")
                    failed += 1
                else:
                    final     = raw if is_raw_fetch else autocrop(raw)
                    src_label = "fetch" if is_raw_fetch else "screenshot"

                    with open(out, "wb") as f:
                        f.write(final)

                    size_kb = len(final) // 1024
                    done += 1
                    print(f"✓ {pid:05d}  [{src_label}]  {size_kb} KB  → {out}")

                    if done >= target:
                        print(f"\n  Target of {target} new pages reached. Stopping.")
                        break

                # Periodic long rest to avoid harsh rate limiting / bans
                cur = done + failed
                if cur > 0 and cur % BREAK_EVERY == 0 and cur != _last_break_at:
                    _last_break_at = cur
                    b_sleep = random.randint(*BREAK_SLEEP)
                    print(f"\n  ⏸ Safety break after {cur} pages. "
                          f"Resting {b_sleep}s...")
                    await asyncio.sleep(b_sleep)

                # Turn the page (not needed after the last one)
                if pid < run_end and done < target:
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

                # Recovery: jump to the next page via URL
                if pid < run_end:
                    recovery_url = (
                        f"https://online.vitalsource.com/reader/books/"
                        f"{BOOK_ID}/pageid/{pid + 1}"
                    )
                    try:
                        await page.goto(
                            recovery_url,
                            wait_until="networkidle",
                            timeout=30_000,
                        )
                        delay = random.randint(MIN_DELAY, MAX_DELAY)
                        await asyncio.sleep(delay)
                    except Exception:
                        pass

    print(f"\n{'─'*55}")
    print(f"  ✅ Done")
    print(f"     Captured  : {done}")
    print(f"     Skipped   : {skipped}")
    print(f"     Failed    : {failed}")
    print(f"     Output    : '{OUTPUT_DIR}/'")
    print(f"{'─'*55}")


if __name__ == "__main__":
    asyncio.run(main())
