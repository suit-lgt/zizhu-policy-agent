"""日志与审计脱敏：手机号、身份证、银行卡、邮箱，以及姓名字段。

规则（I-4）：日志不记家庭明细；审计 detail 落库前同样脱敏。
自测：`python -X utf8 -m app.logs --selftest`
"""
from __future__ import annotations

import re

PHONE = re.compile(r"1[3-9]\d{9}")
ID18 = re.compile(r"\b\d{17}[\dXx]\b")
BANK = re.compile(r"\b\d{16,19}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SENSITIVE_KEYS = ("name", "姓名", "real_name", "phone", "mobile", "手机", "tel",
                  "id_card", "idcard", "身份证", "bank", "bank_card", "银行卡",
                  "account", "账号", "email", "邮箱")


def _phone(m: re.Match) -> str:
    s = m.group(0)
    return s[:3] + "****" + s[-4:]


def _id(m: re.Match) -> str:
    s = m.group(0)
    return s[:6] + "********" + s[-4:]


def _bank(m: re.Match) -> str:
    s = m.group(0)
    return "**** **** **** " + s[-4:]


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    text = EMAIL.sub("[email]", text)
    text = ID18.sub(_id, text)
    text = PHONE.sub(_phone, text)
    text = BANK.sub(_bank, text)
    return text


def redact_obj(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(token in str(k).lower() for token in SENSITIVE_KEYS):
                out[k] = "***"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _selftest() -> int:
    cases = [
        ("手机号", redact_text("联系 13812345678 家长"), "138****5678"),
        ("身份证", redact_text("证件 440301200501011234 已收"), "440301********1234"),
        ("银行卡", redact_text("卡号 6222021234567890123"), "**** **** **** 0123"),
        ("邮箱", redact_text("发到 student@example.com"), "[email]"),
        ("姓名字段", redact_obj({"name": "张三", "income": 3000}), "***"),
        ("嵌套", redact_obj({"note": "电话 13900001111", "items": [{"phone": "13700002222"}]}), "139****1111"),
    ]
    ok = 0
    for name, got, want in cases:
        passed = want in str(got)
        print(("PASS" if passed else "FAIL"), name, "→", got)
        ok += 1 if passed else 0
    print(f"{ok}/{len(cases)} PASS")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(redact_text("13812345678 440301200501011234 6222021234567890123"))
