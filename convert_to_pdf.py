import os
from PIL import Image

IMG_DIR = "vitalsource_pages"
OUTPUT = "vitalsource_pages.pdf"

images = sorted(
    [f for f in os.listdir(IMG_DIR) if f.endswith(".png")],
    key=lambda f: int(f.split("_")[-1].split(".")[0])
)

rgb_images = []
for img_path in [os.path.join(IMG_DIR, f) for f in images]:
    img = Image.open(img_path).convert("RGB")
    rgb_images.append(img)

if rgb_images:
    rgb_images[0].save(OUTPUT, save_all=True, append_images=rgb_images[1:], resolution=150)
    print(f"Created {OUTPUT} with {len(rgb_images)} pages")
else:
    print("No images found")
