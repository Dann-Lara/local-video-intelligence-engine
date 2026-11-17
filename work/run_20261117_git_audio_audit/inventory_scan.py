"""Auditoria de archivos sin seguimiento: inventario, tamanos y sensibles.

Solo lectura. Escribe work/run_20261117_git_audio_audit/inventory.json.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
OUT = ROOT / "work" / "run_20261117_git_audio_audit"
OUT.mkdir(parents=True, exist_ok=True)

SENSITIVE_NAME = re.compile(
    r"(?i)(\.env($|\.)|credential|secret|token|password|passwd|private[_-]?key|"
    r"id_rsa|\.pem$|\.key$|\.p12$|\.pfx$|\.npmrc$|\.pypirc$|\.netrc$|"
    r"service[_-]?account|api[_-]?key)"
)
SENSITIVE_CONTENT = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}|"
    r"secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}|"
    r"token\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}|"
    r"password\s*[:=]\s*['\"]?[^\s'\"]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"
)
TEXT_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg",
             ".ini", ".example", ".ass", ".log", ".csv", ".sh", ".ps1", ".js",
             ".ts", ".tsx", ".css", ".html", ".gitignore", ".gitattributes"}

files = []
for p in ROOT.rglob("*"):
    if not p.is_file() or ".git" in p.parts:
        continue
    rel = p.relative_to(ROOT).as_posix()
    try:
        size = p.stat().st_size
    except OSError:
        continue
    item = {"path": rel, "bytes": size, "ext": p.suffix.lower()}
    if SENSITIVE_NAME.search(p.name):
        item["sensitive_name"] = True
    if size <= 2_000_000 and (p.suffix.lower() in TEXT_EXTS or p.name.startswith(".")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        hits = []
        for m in SENSITIVE_CONTENT.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append({"line": line, "match": m.group(0)[:80]})
        if hits:
            item["sensitive_content"] = hits[:5]
    if size >= 50_000_000:
        item["large"] = True
    files.append(item)

by_dir = Counter(f["path"].split("/")[0] for f in files)
by_ext = Counter(f["ext"] or "(sin extension)" for f in files)
large = sorted([f for f in files if f.get("large")], key=lambda x: -x["bytes"])
sensitive = [f for f in files if f.get("sensitive_name") or f.get("sensitive_content")]
payload = {
    "total_files": len(files),
    "total_bytes": sum(f["bytes"] for f in files),
    "by_top_dir": dict(by_dir),
    "by_extension": dict(by_ext.most_common()),
    "large_50mb_plus": large,
    "sensitive": sensitive,
    "files": files,
}
(OUT / "inventory.json").write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print("archivos", payload["total_files"], "bytes", payload["total_bytes"])
print("por directorio", dict(by_dir))
print("extensiones", dict(by_ext.most_common(12)))
print("grandes", len(large), "sensibles", len(sensitive))
for f in large[:25]:
    print("LARGE", f["bytes"], f["path"])
for f in sensitive[:25]:
    print("SENSITIVE", f["path"], f.get("sensitive_name"), f.get("sensitive_content"))
