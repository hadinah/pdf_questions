from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
import fitz  # PyMuPDF
import re
import os
import zipfile
import shutil
import uuid

app = FastAPI()

# Simple UI for the root URL to avoid 404 errors
@app.get("/", response_class=HTMLResponse)
async def main():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>PDF Question Extractor</title>
            <style>
                body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f4f4f9; }
                .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; }
                input[type="file"] { margin: 1rem 0; }
                button { background: #0070f3; color: white; border: none; padding: 0.5rem 1rem; border-radius: 5px; cursor: pointer; }
                button:hover { background: #0051bb; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>PDF Question Extractor</h2>
                <p>Upload a 2-column PDF to extract numbered questions as images.</p>
                <form action="/api/extract" enctype="multipart/form-data" method="post">
                    <input name="file" type="file" accept=".pdf" required><br>
                    <button type="submit">Extract Questions (ZIP)</button>
                </form>
            </div>
        </body>
    </html>
    """

def cleanup(path: str):
    """Removes the temporary session folder after the response is sent."""
    if os.path.exists(path):
        shutil.rmtree(path)

@app.post("/api/extract")
async def extract_questions(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    # Create a unique session ID in /tmp/
    session_id = str(uuid.uuid4())
    base_dir = os.path.join("/tmp", session_id)
    output_folder = os.path.join(base_dir, "output")
    os.makedirs(output_folder, exist_ok=True)
    
    # Save uploaded PDF
    pdf_path = os.path.join(base_dir, file.filename)
    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = fitz.open(pdf_path)
    question_pattern = re.compile(r'^\d+\.(?!\s*[A-D]\.)')
    q_count = 0

    for page in doc:
        width, height = page.rect.width, page.rect.height
        # Logic for 2-column layout
        columns = [
            fitz.Rect(0, 0, width/2, height),
            fitz.Rect(width/2, 0, width, height)
        ]
        
        for col in columns:
            text_dict = page.get_text("dict", clip=col)
            lines = []
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        text = "".join(span["text"] for span in line["spans"]).strip()
                        if text:
                            lines.append({"text": text, "bbox": line["bbox"]})

            # Sort lines top-to-bottom
            lines.sort(key=lambda x: x["bbox"][1])
            
            # Group lines into questions based on leading number
            questions = []
            current_q = None
            expected_num = 1
            for line in lines:
                match = question_pattern.match(line["text"])
                found_num = None
                if match:
                    found_num = int(match.group(1))
                if found_num is not None and found_num == expected_num:
                    if current_q:
                        questions.append(current_q)
                    current_q = [line]
                    expected_num += 1
                elif current_q:
                    current_q.append(line)
            if current_q:
                questions.append(current_q)

            # Save each question as a high-res PNG
            for q in questions:
                y0 = min(l["bbox"][1] for l in q) - 5
                y1 = max(l["bbox"][3] for l in q) + 5
                # Crop across full page width as per original request
                crop_rect = fitz.Rect(0, y0, width, y1)
                
                # 2x scale for clarity
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=crop_rect)
                q_count += 1
                pix.save(os.path.join(output_folder, f"question_{q_count}.png"))

    # Create ZIP
    zip_filename = f"{os.path.splitext(file.filename)[0]}_questions.zip"
    zip_path = os.path.join(base_dir, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in sorted(os.listdir(output_folder)):
            zipf.write(os.path.join(output_folder, f), arcname=f)

    # Schedule cleanup to run after the file is downloaded
    background_tasks.add_task(cleanup, base_dir)

    return FileResponse(
        zip_path, 
        media_type='application/zip', 
        filename=zip_filename
    )
