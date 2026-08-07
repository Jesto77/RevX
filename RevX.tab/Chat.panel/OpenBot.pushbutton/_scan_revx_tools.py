# One-off scanner - run locally to refresh revx_tools_knowledge.py
import os
import re

ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "RevX.extension")
)


def clean_part(p):
    for s in (".panel", ".tab", ".stack", ".pulldown", ".splitbutton"):
        p = p.replace(s, "")
    return p


def parse_bundle_yaml(path):
    tooltip = title = description = None
    if not os.path.exists(path):
        return tooltip, title, description
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()
    title_m = re.search(r"^title:\s*(.+)$", raw, re.M)
    if title_m:
        title = title_m.group(1).strip()
    # multiline tooltip/description
    for key in ("tooltip", "description"):
        m = re.search(r"^%s:\s*(?:>\s*\n((?:  .+\n)+)|\|\s*\n((?:  .+\n)+))" % key, raw, re.M)
        if m:
            block = m.group(1) or m.group(2) or ""
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            val = " ".join(lines)
            if key == "tooltip":
                tooltip = val
            else:
                description = val
        else:
            m2 = re.search(r"^%s:\s*(.+)$" % key, raw, re.M)
            if m2:
                val = m2.group(1).strip()
                if key == "tooltip":
                    tooltip = val
                else:
                    description = val
    return tooltip, title, description


def main():
    tools = []
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        if not dirpath.endswith(".pushbutton"):
            continue
        rel = os.path.relpath(dirpath, ROOT)
        name = os.path.basename(dirpath).replace(".pushbutton", "")
        parts = rel.split(os.sep)
        cleaned = []
        for p in parts[:-1]:
            c = clean_part(p)
            if not cleaned or cleaned[-1] != c:
                cleaned.append(c)
        ui_path = "RevX tab > " + " > ".join(cleaned)
        tooltip, title, description = parse_bundle_yaml(os.path.join(dirpath, "bundle.yaml"))
        doc = None
        for fn in ("Script.py", "script.py"):
            sp = os.path.join(dirpath, fn)
            if os.path.exists(sp):
                with open(sp, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(15000)
                m = re.search('"""([\s\S]*?)"""', text)
                if m:
                    doc = m.group(1).strip()
                break
        tools.append({
            "ui_path": ui_path,
            "button": name,
            "title": title,
            "tooltip": tooltip,
            "description": description,
            "doc": doc,
        })
    tools.sort(key=lambda t: (t["ui_path"], t["button"]))
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "revx_tools_catalog.json")
    import json
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)
    print("Wrote %d tools to %s" % (len(tools), out_path))


if __name__ == "__main__":
    main()
