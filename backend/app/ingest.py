"""本地上传政策文档：解析正文 → 入暂存库（待核文）→ 人工核文后切块。

支持 .docx／.pdf／.txt／.md；解析器缺失时给出明确提示，不静默失败。
上传只创建草稿，不直接进知识库：必须走“核文 → 切块 → 发布版本”三步（I-7）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import sqlite3
import zipfile
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = Path(__import__("os").environ.get("UPLOAD_DIR", str(ROOT / "data" / "uploads")))
MAX_BYTES = 10 * 1024 * 1024

GUIDE_UPLOAD = [
    "第 1 步：选择本地文件（.docx／.pdf／.txt／.md，≤10MB），点“上传并入库”。",
    "第 2 步：系统解析正文并生成草稿，状态为“待核文”；此时条款还不能被检索，也不能发布。",
    "第 3 步：到“政策核文”核对文号、发布机关、生效日期，填完点“核文通过”。",
    "第 4 步：点“切块”把正文按条款切开，再点“发布版本”；学生端立刻按新版本作答，旧版本可回滚。",
]


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=422, detail="文本编码无法识别，请另存为 UTF-8 后重试")


def _docx_text(data: bytes) -> str:
    try:
        import docx  # type: ignore
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append("　".join(c.text for c in row.cells))
        return "\n".join(x for x in parts if x.strip())
    except ImportError:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except ImportError:
        pass
    try:
        import fitz  # type: ignore
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    except ImportError:
        raise HTTPException(status_code=501,
                            detail="当前镜像未安装 PDF 解析器（pypdf），请上传 .docx／.txt，或先在本地转成 Word")
    except Exception as exc:  # 损坏的 PDF
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{exc}")


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    lower = filename.lower()
    if lower.endswith(".docx"):
        text, method = _docx_text(data), "docx"
    elif lower.endswith(".pdf"):
        text, method = _pdf_text(data), "pdf"
    elif lower.endswith((".txt", ".md", ".csv")):
        text, method = _decode_text(data), "text"
    else:
        raise HTTPException(status_code=422, detail="仅支持 .docx／.pdf／.txt／.md")
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(text) < 50:
        raise HTTPException(status_code=422, detail="正文太短（<50 字），可能不是政策全文，请检查文件")
    return text, method


def save_upload(policy_db: Path | str, filename: str, content_b64: str, actor: str) -> dict:
    try:
        data = base64.b64decode((content_b64 or "").split(",")[-1], validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="文件内容不是合法 base64")
    if not data:
        raise HTTPException(status_code=422, detail="文件为空")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB")
    name = Path(filename or "upload.txt").name
    text, method = extract_text(name, data)
    digest = hashlib.sha256(data).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = UPLOAD_DIR / f"{digest[:12]}_{name}"
    raw_path.write_bytes(data)

    c = sqlite3.connect(str(policy_db))
    c.row_factory = sqlite3.Row
    dup = c.execute("SELECT id,status FROM policy_staging WHERE content_hash=?", (digest,)).fetchone()
    if dup:
        c.close()
        raise HTTPException(status_code=409,
                            detail=f"同一份文件已入库（doc {dup['id']}，状态 {dup['status']}），不重复上传")
    cur = c.execute(
        "INSERT INTO policy_staging(url,host,layer,title,body_text,raw_path,content_hash,fetched_at,"
        "status,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"upload://{digest[:12]}/{name}", "upload", "校级" if "校" in name else "国家",
         Path(name).stem, text, str(raw_path), digest,
         __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
         "pending_verify", f"本地上传（{method}），待人工核文"),
    )
    doc_id = cur.lastrowid
    c.commit()
    c.close()
    return {"doc_id": doc_id, "title": Path(name).stem, "chars": len(text), "method": method,
            "sha256": digest, "status": "pending_verify", "raw_path": str(raw_path),
            "guidance": GUIDE_UPLOAD, "next": "到“政策核文”填写文号、发布机关、生效日期后核文"}
