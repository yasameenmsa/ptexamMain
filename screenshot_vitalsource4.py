#!/usr/bin/env python3
import asyncio
import os
import io
import base64
import json
import random
from datetime import datetime, timedelta

from playwright.async_api import async_playwright
from PIL import Image, ImageChops

BOOK_ID           = "978-1-890989-46-0"
START_PAGE        = 11
END_PAGE          = 1163

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR        = os.path.join(BASE_DIR, "vitalsource_pages")
SESSION_FILE      = os.path.join(BASE_DIR, "vitalsource4_state.json")

READ_TIME_MIN     = 20
READ_TIME_MAX     = 900
READ_MU           = 5.0
READ_SIGMA        = 0.9

PAGES_PER_SESSION_MIN = 20
PAGES_PER_SESSION_MAX = 35
BREAK_MIN_MINUTES     = 15
BREAK_MAX_MINUTES     = 45

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-automation",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-features=ChromeWhatsNewUI,ChromeCleanupUI",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]

VIEWPORT_W = 2000
VIEWPORT_H = 2600
DPR = 3

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def reading_time() -> float:
    return max(READ_TIME_MIN, min(READ_TIME_MAX, random.lognormvariate(READ_MU, READ_SIGMA)))


def autocrop(img_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox:
        pad = 8
        x1 = max(0, bbox[0] - pad)
        y1 = max(0, bbox[1] - pad)
        x2 = min(img.width, bbox[2] + pad)
        y2 = min(img.height, bbox[3] + pad)
        img = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Stealth ─────────────────────────────────────────────────────────────

async def apply_stealth(page):
    await page.evaluate("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }))
        });
    """)


# ── Frame / image helpers ──────────────────────────────────────────────

async def get_jigsaw_frame(page):
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url and "/content" in frame.url:
            return frame
    for frame in page.frames:
        if "jigsaw.vitalsource.com" in frame.url:
            return frame
    return None


async def wait_for_image_load(frame, max_wait=60):
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


async def capture_page(page, pid: int) -> tuple:
    frame = await get_jigsaw_frame(page)
    if not frame:
        return None, False

    await wait_for_image_load(frame, max_wait=60)

    b64 = await frame.evaluate("""
        async () => {
            const img = document.querySelector('img#pbk-page');
            if (!img || !img.src) return null;
            try {
                const resp = await fetch(img.src, { credentials: 'include' });
                if (!resp.ok) return null;
                const buf   = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary  = '';
                const chunk = 8192;
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

    el = await frame.query_selector("img#pbk-page")
    if el:
        await el.scroll_into_view_if_needed()
        raw = await el.screenshot(type="png", scale="device")
        return raw, False

    iframe_el = await page.query_selector("iframe[src*='jigsaw']")
    if iframe_el:
        raw = await iframe_el.screenshot(type="png")
        return raw, False

    return None, False


# ── Human simulation ───────────────────────────────────────────────────

async def human_mouse_move(page):
    x = random.randint(100, VIEWPORT_W - 100)
    y = random.randint(100, VIEWPORT_H - 100)
    await page.mouse.move(x, y, steps=random.randint(5, 15))
    await asyncio.sleep(random.uniform(0.3, 1.5))


async def human_scroll(page):
    direction = random.choice([-1, 1])
    amount = random.randint(100, 600)
    await page.evaluate(
        f"window.scrollBy({{top: {direction * amount}, behavior: 'smooth'}})"
    )
    await asyncio.sleep(random.uniform(0.5, 2.5))


async def simulate_reading(page):
    secs = reading_time()
    log(f"reading {secs:.0f}s")
    elapsed = 0
    while elapsed < secs:
        chunk = min(random.uniform(5, 18), secs - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk
        roll = random.random()
        if roll < 0.35:
            await human_mouse_move(page)
        elif roll < 0.55:
            await human_scroll(page)
    log("done reading")


# ── Session management ─────────────────────────────────────────────────

def load_session_state():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {"session_num": 0, "pages_in_session": 0, "total_captured": 0, "total_failed": 0}


def save_session_state(state):
    with open(SESSION_FILE, "w") as f:
        json.dump(state, f, indent=2)


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
    return missing, len(existing)


async def is_blocked(page, pid):
    try:
        title = await page.title()
        if any(w in title.lower() for w in ["blocked", "captcha", "suspicious", "unusual traffic"]):
            log(f"SUSPECTED BLOCK — page title: {title}")
            return True
    except Exception:
        pass

    for wait_sec in [0, 5, 10, 20, 30]:
        if wait_sec:
            log(f"waiting for jigsaw frame ({wait_sec}s)...")
            await asyncio.sleep(wait_sec)
        frame = await get_jigsaw_frame(page)
        if frame:
            break
    else:
        log(f"SUSPECTED BLOCK — no jigsaw frame (pid={pid})")
        return True

    for wait_sec in [0, 5, 10, 20]:
        if wait_sec:
            log(f"waiting for img#pbk-page ({wait_sec}s)...")
            await asyncio.sleep(wait_sec)
        try:
            has_img = await frame.evaluate("() => !!document.querySelector('img#pbk-page')")
            if has_img:
                return False
        except Exception:
            pass
    log(f"SUSPECTED BLOCK — no img#pbk-page in frame (pid={pid})")
    return True


async def ensure_logged_in(page):
    await page.goto("https://online.vitalsource.com", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(5)
    if "login" in page.url.lower() or "signin" in page.url.lower():
        print("=" * 60)
        print("  LOGIN REQUIRED")
        print("  A browser window has opened. Please log into VitalSource,")
        print("  navigate to your book, and press Enter here.")
        print("=" * 60)
        input("  Press Enter after logging in... ")
        await asyncio.sleep(3)


# ── Browser connection ──────────────────────────────────────────────────

async def connect_browser(p):
    log("Connecting to Chrome on http://localhost:9222 ...")
    browser = await p.chromium.connect_over_cdp("http://localhost:9222")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await apply_stealth(page)
    return browser, ctx, page


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    missing, existing_count = get_missing_pages()
    if not missing:
        log(f"✅ All {existing_count} pages already captured. Nothing to do.")
        return

    log(f"Existing: {existing_count} | Missing: {len(missing)} pages "
        f"({missing[0]}–{missing[-1]})")
    log(f"Estimated time: ~{len(missing) * 3.5:.0f} min + breaks")

    missing_file = os.path.join(OUTPUT_DIR, "missing_pages.txt")
    with open(missing_file, "w") as f:
        f.write(f"# Missing pages ({len(missing)} total)\n")
        for p in missing:
            f.write(f"{p}\n")
    log(f"Missing list written to {missing_file}")

    state = load_session_state()
    block_attempt = 0

    async with async_playwright() as p:
        browser, ctx, page = await connect_browser(p)
        await ensure_logged_in(page)

        captured_this_session = 0
        target_idx = 0

        while target_idx < len(missing):
            pid = missing[target_idx]

            # ── Session break check ──────────────────────────────────────
            max_per_session = random.randint(PAGES_PER_SESSION_MIN, PAGES_PER_SESSION_MAX)
            if captured_this_session >= max_per_session:
                break_min = random.randint(BREAK_MIN_MINUTES, BREAK_MAX_MINUTES)
                log(f"SESSION BREAK — {break_min} min (captured {captured_this_session} this session)")
                state["pages_in_session"] = captured_this_session
                state["session_num"] += 1
                save_session_state(state)

                resume_at = datetime.now() + timedelta(minutes=break_min)
                log(f"Resume at ~{resume_at.strftime('%H:%M')} (page {pid})")
                await page.goto("about:blank")
                await asyncio.sleep(break_min * 60)

                captured_this_session = 0
                block_attempt = 0

            # ── Navigate to page ─────────────────────────────────────────
            url = f"https://online.vitalsource.com/reader/books/{BOOK_ID}/pageid/{pid}"
            log(f"Page {pid}/{END_PAGE}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            except Exception as e:
                log(f"Nav warning: {e}")
            await asyncio.sleep(5)

            # ── Login redirect check ──────────────────────────────────────
            if "login" in page.url.lower() or "signin" in page.url.lower():
                log("SESSION EXPIRED — redirect to login page")
                await ensure_logged_in(page)
                continue

            # ── Block detection ──────────────────────────────────────────
            if await is_blocked(page, pid):
                if block_attempt >= 3:
                    log("Too many blocks in a row. Exiting.")
                    break
                wait_min = min(15 * (2 ** block_attempt), 120)
                log(f"BLOCK DETECTED (attempt {block_attempt}). Backing off {wait_min} min...")
                block_attempt += 1
                await page.goto("about:blank")
                await asyncio.sleep(wait_min * 60)
                continue

            block_attempt = 0

            # ── Simulate reading ─────────────────────────────────────────
            await simulate_reading(page)

            # ── Capture ──────────────────────────────────────────────────
            raw, is_raw = await capture_page(page, pid)
            if raw is None:
                log(f"✗ pid={pid} no content")
                state["total_failed"] += 1
            else:
                final = raw if is_raw else autocrop(raw)
                out = os.path.join(OUTPUT_DIR, f"page_{pid:05d}.png")
                with open(out, "wb") as f:
                    f.write(final)
                size_kb = len(final) // 1024
                label = "fetch" if is_raw else "shot"
                log(f"✓ pid={pid}  {size_kb} KB [{label}]")
                state["total_captured"] += 1
                captured_this_session += 1

            save_session_state(state)
            target_idx += 1

    log(f"\n{'─'*50}")
    log(f"  Done")
    log(f"  Captured : {state['total_captured']}")
    log(f"  Failed   : {state['total_failed']}")
    log(f"  Sessions : {state['session_num']}")
    log(f"  Output   : {OUTPUT_DIR}/")
    log(f"{'─'*50}")


if __name__ == "__main__":
    asyncio.run(main())
