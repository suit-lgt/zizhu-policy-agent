from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
targets = [
    ROOT / "docs" / "project-brief.md",
    ROOT / "docs" / "data-contract.md",
    ROOT / "docs" / "verification.md",
    ROOT / "README.md",
    ROOT / "CONTEXT.md",
    ROOT / ".scratch" / "qualification-triage" / "spec.md",
    ROOT / ".scratch" / "qualification-triage" / "issues" / "01-first-slice.md",
]
pairs = [
    ("v1（14 个字段", "v2（16 个字段"),
    ("字段字典 v1，14 字段", "字段字典 v2，16 字段"),
    ("29 条", "30 条"),
    ("29/29", "30/30"),
    ("4 条旧标准标 superseded", "5 条旧标准标 superseded"),
]
for path in targets:
    text = path.read_text(encoding="utf-8-sig")
    changed = 0
    for old, new in pairs:
        n = text.count(old)
        if n:
            text = text.replace(old, new)
            changed += n
    path.write_text(text, encoding="utf-8")
    print(f"{path.name}: {changed} replacements")
