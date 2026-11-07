"""从备份目录恢复业务库与政策库；恢复前先把当前文件另存为 pre-restore 快照。

用法：python -X utf8 backend\scripts\restore.py --from backups\20261106-120000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    args = ap.parse_args()
    src = Path(args.src)
    if not src.is_absolute():
        src = ROOT / src
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safety = ROOT / "backups" / f"pre-restore-{stamp}"
    safety.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        target = Path(item["src"])
        if target.exists():
            shutil.copy2(target, safety / target.name)
        backup_file = src / item["name"]
        if sha256(backup_file) != item["sha256"]:
            raise SystemExit(f"校验失败，未恢复：{backup_file}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_file, target)
    print(json.dumps({"restored_from": str(src), "files": len(manifest["files"]),
                      "safety_copy": str(safety)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
