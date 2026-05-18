import subprocess
import tempfile
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class TranslateRequest(BaseModel):
    tsql: str


@app.post("/translate")
def translate(req: TranslateRequest):
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "input.sql"
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        input_file.write_text(req.tsql)

        result = subprocess.run(
            ["scai", "--input", str(input_file), "--output", str(output_dir)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=503,
                detail={"error": "SPCS unavailable", "stderr": result.stderr},
            )

        output_files = list(output_dir.glob("*.sql"))
        if not output_files:
            raise HTTPException(status_code=503, detail={"error": "No output produced"})

        return {"snowflake_sql": output_files[0].read_text()}


@app.get("/health")
def health():
    return {"status": "ok"}
