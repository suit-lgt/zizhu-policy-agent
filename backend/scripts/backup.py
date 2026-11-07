"""备份业务库与政策库到 backups/<时间戳>/，附 SHA256 清单。

用法：python -X utf8 backend\scripts\backup.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "data" / "app.db", ROOT / "data" / "staging" / "policies.db"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "backups" / stamp
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": stamp, "files": []}
    for src in FILES:
        if not src.exists():
            continue
        dst = out / src.name
        shutil.copy2(src, dst)
        manifest["files"].append({"name": src.name, "src": str(src), "bytes": dst.stat().st_size,
                                  "sha256": sha256(dst)})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backup_dir": str(out), "files": len(manifest["files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
