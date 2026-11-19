"""Read a resume file and turn it into a structured Profile with Claude."""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

from .config import data_dir
from .llm import LLM
from .models import Profile

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}

PARSE_SYSTEM = """You extract a candidate's resume into structured data for a job-search assistant in India.
Copy facts exactly as written; do not invent anything that is not in the resume. Use null for
missing single values and [] for missing lists. total_experience_years counts full-time work only
(internships count as 0); use 0 for students and freshers. suggested_roles should be realistic
titles used on Indian job boards (LinkedIn, Naukri, Internshala) at the right seniority."""


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages).strip()
    if suffix == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(p for p in parts if p.strip())
    if suffix in {".txt", ".md"}:
        return path.read_text(errors="replace")
    raise ValueError(f"Unsupported resume format '{suffix}'. Use PDF, DOCX, TXT or MD.")


def store_copy(path: Path) -> Path:
    """Copy the resume into the agent's data folder so uploads keep working if the original moves."""
    dest_dir = data_dir() / "resume"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / path.name
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    return dest


def parse_resume(llm: LLM, path: Path, effort: str = "medium") -> Profile:
    path = path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"No file at {path}")
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"Unsupported resume format '{path.suffix}'. Use PDF, DOCX, TXT or MD.")
    instruction = "Extract this resume into the schema."
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        content: list[dict] | str = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": instruction},
        ]
    else:
        text = extract_text(path)
        if not text.strip():
            raise ValueError("The resume file has no readable text.")
        content = f"<resume>\n{text}\n</resume>\n\n{instruction}"
    return llm.structured(schema=Profile, system=PARSE_SYSTEM, content=content, effort=effort)


def write_docx(text: str, path: Path, title: str | None = None) -> Path:
    """Save plain text (e.g. a cover letter) as a .docx for upload fields."""
    import docx

    d = docx.Document()
    if title:
        d.add_heading(title, level=1)
    for para in text.strip().split("\n\n"):
        d.add_paragraph(para.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    return path
