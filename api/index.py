from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
import fitz  # PyMuPDF
import re
import os
import zipfile
import shutil
import uuid

app = FastAPI()

@app.post("/api/extract")
async def extract_questions(file: UploadFile = File(...)):
    # Create a unique session ID to avoid collisions
    session_id = str(uuid.uuid4())
    base_dir = f"/tmp/{session_id}"
    output_folder = os.path.join(base_dir, "output")
    os.makedirs(output_folder, exist_ok=True)
    
    # Save uploaded PDF
    pdf_path = os.path.join(base_dir, file.filename)
    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = fitz.open(pdf_path)
    question_pattern = re.compile(r'^\d+\.')
    q_count = 0

    for page in doc:
        width, height = page.rect.width, page.rect.height
        # Process columns (preserving your logic)
        columns = [fitz.Rect(0, 0, width/2, height), fitz.Rect(width/2, 0, width, height)]
        
        for col in columns:
            text_dict = page.get_text("dict", clip=col)
            lines = []
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        text = "".join(span["text"] for span in line["spans"]).strip()
                        if text:
                            lines.append({"text": text, "bbox": line["bbox"]})

            lines.sort(key=lambda x: x["bbox"][1])
            
            # Group lines into questions
            questions = []
            current_q = None
            for line in lines:
                if question_pattern.match(line["text"]):
                    if current_q: questions.append(current_q)
                    current_q = [line]
                elif current_q:
                    current_q.append(line)
            if current_q: questions.append(current_q)

            # Generate Images
            for q in questions:
                y0 = min(l["bbox"][1] for l in q) - 5
                y1 = max(l["bbox"][3] for l in q) + 5
                crop_rect = fitz.Rect(0, y0, width, y1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2,2), clip=crop_rect)
                q_count += 1
                pix.save(os.path.join(output_folder, f"question_{q_count}.png"))

    # Zip the results
    zip_filename = f"{os.path.splitext(file.filename)[0]}_questions.zip"
    zip_path = os.path.join(base_dir, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in os.listdir(output_folder):
            zipf.write(os.path.join(output_folder, f), arcname=f)

    return FileResponse(zip_path, media_type='application/zip', filename=zip_filename)
