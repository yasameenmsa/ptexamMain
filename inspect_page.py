import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        
        # Find the VitalSource page
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "vitalsource" in page.url and "pageid" in page.url:
                    print(f"Found page: {page.url}")
                    
                    # Inspect the DOM structure
                    structure = await page.evaluate("""
                        () => {
                            function inspect(el, depth = 0) {
                                if (depth > 8) return '';
                                const tag = el.tagName?.toLowerCase() || '';
                                const id = el.id ? '#' + el.id : '';
                                const cls = el.className && typeof el.className === 'string' 
                                    ? '.' + el.className.split(' ').filter(Boolean).slice(0, 3).join('.') 
                                    : '';
                                const style = window.getComputedStyle(el);
                                const overflow = style.overflow + '/' + style.overflowY;
                                const dims = `${el.clientWidth}x${el.clientHeight} scroll:${el.scrollWidth}x${el.scrollHeight}`;
                                const indent = '  '.repeat(depth);
                                let result = `${indent}${tag}${id}${cls} [${overflow}] ${dims}\\n`;
                                
                                // Only recurse into elements that are large enough to matter
                                for (const child of el.children) {
                                    if (child.clientHeight > 50 || child.tagName === 'IFRAME') {
                                        result += inspect(child, depth + 1);
                                    }
                                }
                                return result;
                            }
                            return inspect(document.body);
                        }
                    """)
                    print("=== MAIN PAGE STRUCTURE ===")
                    print(structure)
                    
                    # Check iframes
                    print(f"\n=== FRAMES ({len(page.frames)}) ===")
                    for i, frame in enumerate(page.frames):
                        print(f"\nFrame {i}: {frame.url[:100]}")
                        try:
                            frame_struct = await frame.evaluate("""
                                () => {
                                    function inspect(el, depth = 0) {
                                        if (depth > 6) return '';
                                        const tag = el.tagName?.toLowerCase() || '';
                                        const id = el.id ? '#' + el.id : '';
                                        const cls = el.className && typeof el.className === 'string'
                                            ? '.' + el.className.split(' ').filter(Boolean).slice(0, 3).join('.')
                                            : '';
                                        const style = window.getComputedStyle(el);
                                        const overflow = style.overflow + '/' + style.overflowY;
                                        const dims = `${el.clientWidth}x${el.clientHeight} scroll:${el.scrollWidth}x${el.scrollHeight}`;
                                        const indent = '  '.repeat(depth);
                                        let result = `${indent}${tag}${id}${cls} [${overflow}] ${dims}\\n`;
                                        for (const child of el.children) {
                                            if (child.clientHeight > 30 || child.tagName === 'IFRAME') {
                                                result += inspect(child, depth + 1);
                                            }
                                        }
                                        return result;
                                    }
                                    return inspect(document.body);
                                }
                            """)
                            print(frame_struct)
                        except Exception as e:
                            print(f"  Cannot access: {e}")
                    
                    return

asyncio.run(main())
