from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Form
from fastapi.responses import FileResponse, HTMLResponse
import fitz  # PyMuPDF
import re
import os
import zipfile
import shutil
import uuid
from typing import Optional

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def main():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>PDF Question Extractor</title>
            <style>
                body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f4f4f9; }
                .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; width: 400px; }
                .range-group { display: flex; gap: 10px; justify-content: center; margin: 15px 0; }
                .range-group div { text-align: left; }
                .range-group input { width: 80px; padding: 5px; border: 1px solid #ccc; border-radius: 4px; }
                .toggle-group { margin: 15px 0; text-align: left; font-size: 0.85rem; background: #eee; padding: 10px; border-radius: 4px; }
                input[type="file"] { margin: 1rem 0; font-size: 0.9rem; }
                button { background: #0070f3; color: white; border: none; padding: 0.7rem 1.2rem; border-radius: 5px; cursor: pointer; width: 100%; font-weight: bold; }
                button:hover { background: #0051bb; }
                label { font-size: 0.75rem; color: #666; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>PDF Question Extractor</h2>
                <form action="/api/extract" enctype="multipart/form-data" method="post">
                    <input name="file" type="file" accept=".pdf" required>
                    <div class="range-group">
                        <div>
                            <label>START #</label><br>
                            <input type="number" name="start_q" value="1" min="1">
                        </div>
                        <div>
                            <label>END #</label><br>
                            <input type="number" name="end_q" placeholder="Last">
                        </div>
                    </div>
                    <div class="toggle-group">
                        <input type="checkbox" name="enforce_sequence" id="seq" checked>
                        <label for="seq" style="color: #333; font-weight: normal;">Enforce strict sequence</label>
                    </div>
                    <button type="submit">Extract Questions (ZIP)</button>
                </form>
            </div>
        </body>
    </html>
    """

def cleanup(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)

@app.post("/api/extract")
async def extract_questions(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...),
    start_q: int = Form(1),
    end_q: Optional[int] = Form(None),
    enforce_sequence: bool = Form(False)
):
    session_id = str(uuid.uuid4())
    base_dir = os.path.join("/tmp", session_id)
    output_folder = os.path.join(base_dir, "output")
    os.makedirs(output_folder, exist_ok=True)
    
    expected_num = start_q
    option_seen = True  # Initialized to True to catch the first question in range
    
    pdf_path = os.path.join(base_dir, file.filename)
    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = fitz.open(pdf_path)
    question_pattern = re.compile(r'^(\d+)\.')
    option_pattern = re.compile(r'^[A-D]\)')
    q_count = 0

    for page in doc:
        width, height = page.rect.width, page.rect.height
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
            
            questions = []
            current_q = None
            for line in lines:
                match = question_pattern.match(line["text"])
                found_num = int(match.group(1)) if match else None
                
                if option_pattern.match(line["text"]):
                    option_seen = True

                if found_num is not None:
                    # Logic for range and sequence
                    is_in_range = (found_num >= start_q) and (end_q is None or found_num <= end_q)
                    is_correct_seq = not enforce_sequence or (found_num == expected_num)

                    if is_in_range and is_correct_seq and option_seen:
                        if current_q: questions.append(current_q)
                        current_q = [line]
                        expected_num = found_num + 1
                        option_seen = False
                    elif current_q:
                        current_q.append(line)
                elif current_q:
                    current_q.append(line)
            
            if current_q:
                questions.append(current_q)

            for q in questions:
                y0 = min(l["bbox"][1] for l in q) - 5
                y1 = max(l["bbox"][3] for l in q) + 5
                crop_rect = fitz.Rect(0, y0, width, y1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=crop_rect)
                q_count += 1
                pix.save(os.path.join(output_folder, f"question_{q_count}.png"))

    zip_filename = f"{os.path.splitext(file.filename)[0]}_questions.zip"
    zip_path = os.path.join(base_dir, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in sorted(os.listdir(output_folder)):
            zipf.write(os.path.join(output_folder, f), arcname=f)

    background_tasks.add_task(cleanup, base_dir)
    return FileResponse(zip_path, media_type='application/zip', filename=zip_filename)
