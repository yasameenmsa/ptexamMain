"""
VitalSource Auto Page Capture
==============================
Automatically screenshots every page and saves as PDF.

SETUP (run once in terminal/cmd):
  pip install pyautogui pillow img2pdf

HOW TO USE:
  1. Open VitalSource in browser → go to page 1 of your book
  2. Close DevTools panel (press F12 to toggle off)
  3. Maximize the browser window (press F11 for fullscreen)
  4. Run this script: python vitalsource_capture.py
  5. Quickly click on the VitalSource window before countdown ends
  6. Do NOT touch mouse/keyboard while running

OUTPUT: vitalsource_book.pdf in the same folder as this script
"""

import pyautogui
import time
import os
import sys
import pyperclip
from PIL import Image

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION — adjust these values
# ─────────────────────────────────────────────

TOTAL_PAGES     = 1208        # Total pages in your book
START_PAGE      = 1           # Resume from this page if interrupted
BASE_URL        = "https://bookshelf.vitalsource.com/reader/books/978-1-890989-46-0/pageid/"
OUTPUT_DIR      = "vs_pages"  # Folder to save screenshots
OUTPUT_PDF      = "vitalsource_book.pdf"
PAGE_LOAD_DELAY = 1.8         # Seconds to wait after turning page (increase if pages load slow)
COUNTDOWN       = 8           # Seconds before capture starts

# ─────────────────────────────────────────────
# 📐 CAPTURE REGION — the book content area
#    Format: (left, top, width, height)
#    Run the helper below to find your coordinates
# ─────────────────────────────────────────────

# Default: captures full screen (safe starting point)
# Once you know your book area, set it more precisely e.g.:
# CAPTURE_REGION = (57, 145, 563, 645)
CAPTURE_REGION = None  # None = full screen


# ─────────────────────────────────────────────
# 🖱️  HELPER: Find your capture coordinates
#    Uncomment and run to print mouse position
# ─────────────────────────────────────────────

def show_mouse_position():
    """Run this to find coordinates. Move mouse to corners of book area."""
    print("Move your mouse around. Press Ctrl+C to stop.")
    try:
        while True:
            x, y = pyautogui.position()
            print(f"\r Mouse position: X={x}, Y={y}   ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDone.")

# Uncomment next line to use the helper, then re-comment it:
# show_mouse_position(); sys.exit()


# ─────────────────────────────────────────────
# 📸 CAPTURE FUNCTIONS
# ─────────────────────────────────────────────

def countdown(seconds):
    print(f"\n⚡ Starting in {seconds} seconds — click on VitalSource NOW!\n")
    for i in range(seconds, 0, -1):
        print(f"  {i}...", end=" ", flush=True)
        time.sleep(1)
    print("\n🔴 CAPTURING — do not touch mouse or keyboard!\n")

def capture_screenshot(page_num):
    filename = os.path.join(OUTPUT_DIR, f"page_{page_num:04d}.png")
    if CAPTURE_REGION:
        img = pyautogui.screenshot(region=CAPTURE_REGION)
    else:
        img = pyautogui.screenshot()
    img.save(filename)
    return filename

def go_to_page(page_num):
    url = f"{BASE_URL}{page_num}"
    pyperclip.copy(url)
    
    # Focus address bar (Ctrl+L usually works in Chrome/Firefox)
    pyautogui.hotkey('ctrl', 'l')
    time.sleep(0.5)
    
    # Paste URL and press Enter
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.2)
    pyautogui.press('enter')

def combine_to_pdf(image_folder, output_path):
    print("\n📄 Combining images into PDF...")
    files = sorted([
        os.path.join(image_folder, f)
        for f in os.listdir(image_folder)
        if f.endswith(".png")
    ])

    if not files:
        print("❌ No images found!")
        return

    try:
        import img2pdf
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(files))
        print(f"✅ PDF saved: {output_path}  ({len(files)} pages)")

    except ImportError:
        # Fallback: use Pillow if img2pdf not installed
        print("img2pdf not found, using Pillow fallback...")
        images = [Image.open(f).convert("RGB") for f in files]
        images[0].save(output_path, save_all=True, append_images=images[1:])
        print(f"✅ PDF saved: {output_path}  ({len(files)} pages)")


# ─────────────────────────────────────────────
# 🚀 MAIN
# ─────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Check how many pages already captured (resume support)
    existing = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".png")]
    if existing:
        valid_pages = []
        for f in existing:
            try:
                valid_pages.append(int(f.split('_')[1].split('.')[0]))
            except (IndexError, ValueError):
                pass
        
        if valid_pages:
            last_page = max(valid_pages)
            resume_from = max(START_PAGE, last_page + 1)
        else:
            resume_from = START_PAGE
    else:
        resume_from = START_PAGE

    pages_left = max(0, TOTAL_PAGES - (resume_from - 1))

    if resume_from > 1:
        print(f"⏩ Resuming from page {resume_from} ({len(existing)} already captured)")

    est_minutes = round((pages_left * (PAGE_LOAD_DELAY + 0.5)) / 60, 1)
    print(f"📖 Pages to capture: {pages_left}")
    print(f"⏱️  Estimated time:   {est_minutes} minutes")
    print(f"📁 Saving to:        {OUTPUT_DIR}/")

    captured = 0
    failed   = []

    if pages_left > 0:
        countdown(COUNTDOWN)

        # ── Navigate to correct starting page first ──────────────────────────
        # VitalSource is already open — just start capturing

        for page_num in range(resume_from, TOTAL_PAGES + 1):
            try:
                # Navigate to the specific page URL first
                go_to_page(page_num)
                time.sleep(PAGE_LOAD_DELAY)
                
                # Take screenshot after page loads
                capture_screenshot(page_num)

                captured += 1
                if captured % 10 == 0:
                    print(f"  ✓ Page {page_num}/{TOTAL_PAGES}  ({captured} captured)")

            except KeyboardInterrupt:
                print(f"\n⛔ Stopped at page {page_num}. Run again to resume.")
                break
            except Exception as e:
                print(f"  ⚠️  Error on page {page_num}: {e}")
                failed.append(page_num)
                time.sleep(1)
                continue

    print(f"\n✅ Capture complete: {captured} pages")
    if failed:
        print(f"⚠️  Failed pages: {failed}")

    # Combine all images into PDF
    combine_to_pdf(OUTPUT_DIR, OUTPUT_PDF)


if __name__ == "__main__":
    main()
