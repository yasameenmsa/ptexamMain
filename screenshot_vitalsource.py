import asyncio
import os
import io
from playwright.async_api import async_playwright
from PIL import Image, ImageChops

# ── CONFIG ──────────────────────────────────────────────────────────────
BOOK_ID    = "978-1-890989-46-0"
START_PAGE = 11
END_PAGE   = 1163
OUTPUT_DIR = "vitalsource_pages"
DELAY      = 5        # ✅ was 20s — 5s is enough, saves you ~4 hours total
SKIP_EXISTING = True  # skip pages already captured (safe to resume)
# ────────────────────────────────────────────────────────────────────────


async def get_jigsaw_frame(page):
    """Find the jigsaw content iframe."""
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url and "/content" in frame.url:
            return frame
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url:
            return frame
    return None


async def wait_for_image_load(frame, max_wait=12):
    """Wait until img#pbk-page is fully loaded inside the frame."""
    for _ in range(max_wait):
        await asyncio.sleep(1)
        try:
            loaded = await frame.evaluate("""
                () => {
                    const img = document.querySelector('img#pbk-page');
                    return img && img.naturalWidth > 0 && img.complete;
                }
            """)
            if loaded:
                return True
        except Exception:
            pass
    return False


def autocrop(img_bytes: bytes) -> bytes:
    """Remove uniform white/near-white borders from image."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    bg  = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()

    if bbox:
        pad = 8
        x1 = max(0,         bbox[0] - pad)
        y1 = max(0,         bbox[1] - pad)
        x2 = min(img.width, bbox[2] + pad)
        y2 = min(img.height,bbox[3] + pad)
        img = img.crop((x1, y1, x2, y2))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def capture_page(page, pid: int) -> bytes | None:
    """
    Strategy order:
      1. img#pbk-page element  (best — clean book image)
      2. Full jigsaw iframe    (fallback for HTML-based pages)
      3. Skip                  (never capture browser UI)
    """
    frame = await get_jigsaw_frame(page)

    if not frame:
        return None

    # ── Strategy 1: img#pbk-page ────────────────────────────────────────
    loaded = await wait_for_image_load(frame, max_wait=DELAY * 2)
    if loaded:
        el = await frame.query_selector("img#pbk-page")
        if el:
            await el.scroll_into_view_if_needed()
            return await el.screenshot(type="png", scale="device")

    # ── Strategy 2: screenshot the iframe element itself ─────────────────
    iframe_el = await page.query_selector("iframe[src*='jigsaw']")
    if iframe_el:
        return await iframe_el.screenshot(type="png")

    # ── Strategy 3: give up (don't capture browser UI) ───────────────────
    return None


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else await context.new_page()

        await page.set_viewport_size({"width": 1000, "height": 1200})

        done = skipped = failed = 0

        # 1. Find the first page we need to capture
        current_pid = START_PAGE
        while current_pid <= END_PAGE:
            out = os.path.join(OUTPUT_DIR, f"page_{current_pid:05d}.png")
            if SKIP_EXISTING and os.path.exists(out):
                print(f"─ skip  {current_pid}  (already exists)")
                skipped += 1
                current_pid += 1
            else:
                break

        if current_pid > END_PAGE:
            print("\n✅ All pages already captured.")
            return

        # 2. Navigate to the first missing page
        url = f"https://online.vitalsource.com/reader/books/{BOOK_ID}/pageid/{current_pid}"
        print(f"Navigating to starting page {current_pid}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"Warning on initial navigation: {e}")
        
        await asyncio.sleep(1)

        # Force VitalSource reader to fit by height
        await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button, [role="button"]');
                for (const btn of btns) {
                    const label = (btn.getAttribute('aria-label') || btn.title || btn.textContent || '').toLowerCase();
                    if (label.includes('height') || label.includes('fit page') || label.includes('full page')) {
                        btn.click();
                        break;
                    }
                }
            }
        """)
        await asyncio.sleep(2)  # let it re-render after zoom change

        # 3. Loop and click "Next" button
        for pid in range(current_pid, END_PAGE + 1):
            out = os.path.join(OUTPUT_DIR, f"page_{pid:05d}.png")
            
            try:
                # Capture the current page
                raw = await capture_page(page, pid)

                if raw is None:
                    print(f"✗ skip  {pid}  (no book content found — not a standard page)")
                    failed += 1
                else:
                    cropped = autocrop(raw)
                    with open(out, "wb") as f:
                        f.write(cropped)
                    done += 1
                    print(f"✓ page  {pid}  → {out}")

                # If not the last page, click NEXT
                if pid < END_PAGE:
                    print("  Clicking Next...")
                    next_btn = await page.query_selector('button[aria-label="Next"]')
                    if next_btn:
                        await next_btn.click()
                        await asyncio.sleep(DELAY) # Wait for page turn and image load
                    else:
                        print("  ! Next button not found. Using URL navigation.")
                        next_url = f"https://online.vitalsource.com/reader/books/{BOOK_ID}/pageid/{pid+1}"
                        await page.goto(next_url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(DELAY)

            except KeyboardInterrupt:
                print("\n🛑 Stopped by user.")
                break
            except Exception as e:
                if "closed" in str(e).lower() or "disconnected" in str(e).lower():
                    print(f"\n🛑 Browser connection closed. Stopping.")
                    break
                print(f"✗ error {pid}: {e}")
                failed += 1
                # Recovery: try to navigate via URL for the next iteration
                if pid < END_PAGE:
                    next_url = f"https://online.vitalsource.com/reader/books/{BOOK_ID}/pageid/{pid+1}"
                    try:
                        await page.goto(next_url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(DELAY)
                    except Exception:
                        pass

    print(f"\n✅ Done │ captured: {done} │ skipped: {skipped} │ failed: {failed}")
    print(f"   Output → '{OUTPUT_DIR}/'")


if __name__ == "__main__":
    asyncio.run(main())