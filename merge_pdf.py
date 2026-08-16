from PIL import Image
import os

folder = "vitalsource_pages"
images = sorted([
    Image.open(os.path.join(folder, f)).convert("RGB")
    for f in os.listdir(folder) if f.endswith(".png")
])

if not images:
    print("No images found to merge!")
else:
    images[0].save("PTEXAM.pdf", save_all=True, append_images=images[1:])
    print("✅ PTEXAM.pdf created!")
