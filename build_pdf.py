from PIL import Image
import os
import glob

def build_pdf(folder_path, output_filename):
    if not os.path.exists(folder_path):
        print(f"Error: Folder '{folder_path}' does not exist.")
        return

    # Find all png files in the directory
    search_pattern = os.path.join(folder_path, "*.png")
    image_files = glob.glob(search_pattern)
    
    # Sort files to ensure pages are in the correct order
    image_files.sort()

    if not image_files:
        print(f"No PNG files found in '{folder_path}'.")
        return

    print(f"Found {len(image_files)} images in '{folder_path}'.")
    print("Processing images... Please wait.")

    try:
        # Open the first image and convert to RGB (PDFs require RGB, not RGBA)
        first_image = Image.open(image_files[0]).convert('RGB')
        
        # Open and convert the rest of the images
        other_images = []
        for img_path in image_files[1:]:
            img = Image.open(img_path).convert('RGB')
            other_images.append(img)
            
        print(f"Saving to {output_filename}... This might take a minute depending on the number of images.")
        
        # Save all images to a single PDF
        first_image.save(
            output_filename,
            save_all=True,
            append_images=other_images
        )
        print(f"✅ Successfully created '{output_filename}'!")
        
    except Exception as e:
        print(f"❌ An error occurred while building the PDF: {e}")

if __name__ == "__main__":
    target_folder = "vitalsource_pages"
    output_pdf = "vitalsource2.pdf"
    
    build_pdf(target_folder, output_pdf)
