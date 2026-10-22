"""
pbir_to_legacy.py — convert a .pbix whose report is stored in the enhanced report format
(PBIR, Report/definition/...) back to the classic single-file format (Report/Layout) that
Power BI Report Server accepts.

Why: the regular Power BI Desktop saves reports as PBIR; Power BI Report Server rejects
PBIR files on upload ("There was an error uploading your .pbix file"). The data model,
custom visuals, images and theme are accepted unchanged — only the report layer differs.

Usage (Python 3.9+, standard library only):
    python tools/pbir_to_legacy.py report.pbix                     # -> report_RS.pbix
    python tools/pbir_to_legacy.py report.pbix -o converted.pbix
    python tools/pbir_to_legacy.py a.pbix b.pbix "C:\\Reports"      # several files / whole folders
    python tools/pbir_to_legacy.py "C:\\Reports" --out-dir "C:\\Reports\\converted"

Input files are never modified. Files already in the classic format are skipped.
Open each output in Power BI Desktop for Report Server, check every page, save, then publish.
Full guide: docs/PBIRS_REPORT_FORMAT_GUIDE.md

Notes:
- The cached "query"/"dataTransforms" blocks of the classic format are not written;
  Power BI rebuilds them the first time each visual renders.
- TMDL-view script tabs and DAX-query-view tabs (TMDLScripts/, DAXQueries/) are dropped;
  the classic format has no place for them.
- SecurityBindings (encrypted data-source permissions) is dropped; Desktop rebuilds it.
- Before saving the output in Desktop, switch OFF the preview features "Store PBIX reports
  using enhanced metadata format (PBIR)" and "Store reports using enhanced metadata format
  (PBIR)" (File > Options > Preview features), or Desktop converts it back to PBIR.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import zipfile
from pathlib import Path

DEF = "Report/definition/"

# Classic-format numeric codes.
# Calibrated 2026-10-22 against a layout saved by Power BI Desktop for Report Server
# (September 2026): drillthrough (pods, type 1; page filters howCreated 5), tooltip page
# (section type 2), interactions, hidden pages, groups, sorting, custom-visual and
# image/theme registration. Entries marked "uncalibrated" are inferred; the self-check
# warns when a report uses them.
DISPLAY_OPTION = {"FitToPage": 1, "FitToWidth": 2, "ActualSize": 3}
POD_TYPE = {"Drillthrough": 1}                       # Tooltip binding: uncalibrated (2 assumed)
POD_TYPE_UNCALIBRATED = {"Tooltip": 2}
FILTER_HOW_CREATED = {"Auto": 0, "User": 1, "Drill": 2, "Include": 3, "Exclude": 4,
                      "Drillthrough": 5}
INTERACTION_TYPE = {"DataFilter": 1, "HighlightFilter": 2, "NoFilter": 3}
SORT_DIRECTION = {"Ascending": 1, "Descending": 2}
GROUP_MODE = {"ScaleMode": 0, "ScrollMode": 1}
EXPORT_DATA_MODE = {"None": 0, "AllowSummarized": 1, "AllowSummarizedAndUnderlying": 2}
RESOURCE_PACKAGE_TYPE = {"CustomVisual": 0, "RegisteredResources": 1, "SharedResources": 2}
RESOURCE_ITEM_TYPE = {"CustomVisualMetadata": 5, "Image": 100, "CustomTheme": 201,
                      "BaseTheme": 202}
THEME_TYPE = {"RegisteredResources": 1, "SharedResources": 2}

# The source file's Version part is kept as-is: it tells Desktop how the data model and
# its Power Query (mashup) content are stored. Stamping an older version (e.g. 1.28) makes
# Desktop look for the queries in the old place and fail with MashupValidationError.
# Report Server accepts a classic Report/Layout with the newer version stamp.
LAYOUT_CONFIG_VERSION = "5.77"

JSON_PARTS = {"Settings", "Metadata"}   # extensionless parts Desktop marks as application/json


def content_types(part_names: list[str]) -> str:
    """[Content_Types].xml in the shape Desktop writes: a Default per file extension and an
    Override per extensionless part."""
    exts = sorted({Path(p).suffix[1:] for p in part_names if Path(p).suffix})
    xml = ['<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/'
           'package/2006/content-types">']
    xml += [f'<Default Extension="{e}" ContentType="" />' for e in exts]
    for p in part_names:
        if not Path(p).suffix:
            ctype = "application/json" if p in JSON_PARTS else ""
            xml.append(f'<Override PartName="/{p}" ContentType="{ctype}" />')
    xml.append("</Types>")
    return "".join(xml)


def jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- fields
class Aliases:
    """Assigns short query aliases (Source names) to entities, as the classic format does."""

    def __init__(self):
        self.by_entity: dict[str, str] = {}

    def get(self, entity: str) -> str:
        if entity not in self.by_entity:
            letters = re.sub(r"[^a-z]", "", entity.lower()) or "t"
            base, alias, n = letters[0], letters[0], 1
            while alias in self.by_entity.values():
                alias = f"{base}{n}"
                n += 1
            self.by_entity[entity] = alias
        return self.by_entity[entity]

    def from_clause(self):
        return [{"Name": a, "Entity": e, "Type": 0} for e, a in self.by_entity.items()]


def to_source_refs(expr, aliases: Aliases):
    """Replace {"SourceRef": {"Entity": E}} with {"SourceRef": {"Source": alias}}."""
    if isinstance(expr, dict):
        out = {}
        for k, v in expr.items():
            if k == "SourceRef" and isinstance(v, dict) and "Entity" in v:
                out[k] = {"Source": aliases.get(v["Entity"])}
            else:
                out[k] = to_source_refs(v, aliases)
        return out
    if isinstance(expr, list):
        return [to_source_refs(v, aliases) for v in expr]
    return expr


# --------------------------------------------------------------------------- filters
def convert_filters(filters: list) -> list:
    out = []
    for f in filters or []:
        g = {}
        for k, v in f.items():
            if k == "field":
                g["expression"] = v
            elif k == "howCreated":
                g[k] = FILTER_HOW_CREATED.get(v, v) if isinstance(v, str) else v
            else:
                g[k] = v
        g.setdefault("howCreated", FILTER_HOW_CREATED["User"])   # what Desktop writes when absent
        out.append(g)
    return out


# --------------------------------------------------------------------------- visuals
def convert_query(query: dict) -> dict:
    """PBIR visual.query -> classic singleVisual projections / prototypeQuery / columnProperties."""
    aliases = Aliases()
    projections, select, seen, column_props = {}, [], set(), {}
    for role, state in query.get("queryState", {}).items():
        items = []
        for p in state.get("projections", []):
            qr = p["queryRef"]
            item = {"queryRef": qr}
            if p.get("active"):
                item["active"] = True
            items.append(item)
            if "displayName" in p:
                column_props[qr] = {"displayName": p["displayName"]}
            if qr not in seen:
                seen.add(qr)
                sel = to_source_refs(copy.deepcopy(p["field"]), aliases)
                sel["Name"] = qr
                if "nativeQueryRef" in p:
                    sel["NativeReferenceName"] = p["nativeQueryRef"]
                select.append(sel)
        projections[role] = items

    result = {"projections": projections}
    order_by = []
    sort_def = query.get("sortDefinition", {})
    for s in sort_def.get("sort", []):
        order_by.append({"Direction": SORT_DIRECTION.get(s.get("direction"), 1),
                         "Expression": to_source_refs(copy.deepcopy(s["field"]), aliases)})
    proto = {"Version": 2, "From": aliases.from_clause(), "Select": select}
    if order_by:
        proto["OrderBy"] = order_by
    result["prototypeQuery"] = proto
    if column_props:
        result["columnProperties"] = column_props
    if sort_def.get("isDefaultSort"):
        result["hasDefaultSort"] = True
    return result


def convert_visual(v: dict) -> dict:
    pos = v["position"]
    layout_pos = {k: pos[k] for k in ("x", "y", "z", "width", "height") if k in pos}
    layout_pos["tabOrder"] = pos.get("tabOrder", 0)
    cfg = {"name": v["name"], "layouts": [{"id": 0, "position": layout_pos}]}

    if "visualGroup" in v:
        grp = v["visualGroup"]
        cfg["singleVisualGroup"] = {"displayName": grp.get("displayName", ""),
                                    "groupMode": GROUP_MODE.get(grp.get("groupMode"), 0)}
        if v.get("isHidden"):
            cfg["singleVisualGroup"]["isHidden"] = True
    else:
        src = v.get("visual", {})
        sv = {"visualType": src.get("visualType")}
        if src.get("query"):
            sv.update(convert_query(src["query"]))
        for k_src, k_dst in (("objects", "objects"), ("visualContainerObjects", "vcObjects"),
                             ("syncGroup", "syncGroup"), ("expansionStates", "expansionStates")):
            if k_src in src:
                sv[k_dst] = src[k_src]
        sv["drillFilterOtherVisuals"] = src.get("drillFilterOtherVisuals", True)
        if v.get("isHidden"):
            sv["display"] = {"mode": "hidden"}
        cfg["singleVisual"] = sv

    if "parentGroupName" in v:
        cfg["parentGroupName"] = v["parentGroupName"]
    if "howCreated" in v:
        cfg["howCreated"] = v["howCreated"]

    container = {**{k: pos[k] for k in ("x", "y", "z", "width", "height") if k in pos},
                 "config": jdump(cfg),
                 "filters": jdump(convert_filters(v.get("filterConfig", {}).get("filters"))),
                 "tabOrder": pos.get("tabOrder", 0)}
    return container


# --------------------------------------------------------------------------- pages
def convert_page(page: dict, visuals: list, ordinal: int) -> dict:
    cfg = {}
    if "objects" in page:
        cfg["objects"] = page["objects"]
    if page.get("visibility") == "HiddenInViewMode":
        cfg["visibility"] = 1
    if page.get("visualInteractions"):
        cfg["relationships"] = [{"source": r["source"], "target": r["target"],
                                 "type": INTERACTION_TYPE.get(r["type"], 1)}
                                for r in page["visualInteractions"]]
    # drillthrough/tooltip bindings are not stored on the page: see make_pods()
    if page.get("type") == "Tooltip":
        cfg["type"] = 2
    # groups must appear before their children so Desktop can attach them
    visuals = sorted(visuals, key=lambda v: (0 if "visualGroup" in v else 1,
                                             v["position"].get("z", 0)))
    return {
        "name": page["name"],
        "displayName": page["displayName"],
        "filters": jdump(convert_filters(page.get("filterConfig", {}).get("filters"))),
        "ordinal": ordinal,
        "visualContainers": [convert_visual(v) for v in visuals],
        "config": jdump(cfg),
        "displayOption": DISPLAY_OPTION.get(page.get("displayOption"), 1),
        "width": page.get("width", 1280),
        "height": page.get("height", 720),
    }


def make_pod(page: dict) -> dict:
    """PBIR page.pageBinding -> classic top-level "pods" entry (how Desktop stores drillthrough).
    The page's own filters with the matching names (howCreated 5) hold the drillthrough values."""
    pb = page["pageBinding"]
    params = []
    for p in pb.get("parameters", []):
        q = {"name": p["name"], "boundFilter": p["boundFilter"], "fieldExpr": p["fieldExpr"]}
        if p.get("asAggregation"):
            q["asAggregation"] = True
        params.append(q)
    kind = pb.get("type", "Drillthrough")
    return {"name": pb["name"], "boundSection": page["name"], "parameters": params,
            "type": POD_TYPE.get(kind, POD_TYPE_UNCALIBRATED.get(kind, 1)), "config": {}}


# --------------------------------------------------------------------------- report
def convert_report(files: dict[str, bytes]) -> dict:
    def load(path):
        return json.loads(files[DEF + path].decode("utf-8-sig"))

    report = load("report.json")
    pages_meta = load("pages/pages.json")
    order = pages_meta.get("pageOrder", [])

    visuals_by_page: dict[str, list] = {}
    for path in files:
        m = re.match(rf"{re.escape(DEF)}pages/([^/]+)/visuals/([^/]+)/visual\.json$", path)
        if m:
            visuals_by_page.setdefault(m.group(1), []).append(
                json.loads(files[path].decode("utf-8-sig")))

    sections, pods = [], []
    for i, page_name in enumerate(order):
        page = load(f"pages/{page_name}/page.json")
        sections.append(convert_page(page, visuals_by_page.get(page_name, []), i))
        if "pageBinding" in page:
            pods.append(make_pod(page))

    # bookmarks (groups have "children")
    bookmarks = []
    if DEF + "bookmarks/bookmarks.json" in files:
        def bm(name):
            b = load(f"bookmarks/{name}.bookmark.json")
            b.pop("$schema", None)
            return b
        for item in load("bookmarks/bookmarks.json").get("items", []):
            if "children" in item:
                bookmarks.append({"displayName": item.get("displayName", ""), "name": item["name"],
                                  "children": [bm(c) for c in item["children"]]})
            else:
                bookmarks.append(bm(item["name"]))

    # theme + resource packages
    theme_collection = {}
    for key, t in report.get("themeCollection", {}).items():
        entry = {"name": t["name"], "type": THEME_TYPE.get(t.get("type"), 2)}
        if "reportVersionAtImport" in t:
            entry["version"] = t["reportVersionAtImport"]
        theme_collection[key] = entry

    packages = []
    for guid in report.get("publicCustomVisuals", []):
        if f"Report/CustomVisuals/{guid}/package.json" in files:
            packages.append({"resourcePackage": {
                "name": guid, "type": RESOURCE_PACKAGE_TYPE["CustomVisual"],
                "items": [{"type": RESOURCE_ITEM_TYPE["CustomVisualMetadata"],
                           "path": "package.json", "name": "package.json"}],
                "disabled": False}})
    for rp in report.get("resourcePackages", []):
        packages.append({"resourcePackage": {
            "name": rp["name"], "type": RESOURCE_PACKAGE_TYPE.get(rp.get("type"), 1),
            "items": [{"type": RESOURCE_ITEM_TYPE.get(it.get("type"), 100),
                       "path": it["path"], "name": it["name"]} for it in rp.get("items", [])],
            "disabled": False}})

    settings = dict(report.get("settings", {}))
    if isinstance(settings.get("exportDataMode"), str):
        settings["exportDataMode"] = EXPORT_DATA_MODE.get(settings["exportDataMode"], 1)
    default_drill = settings.pop("defaultDrillFilterOtherVisuals", True)

    active = pages_meta.get("activePageName")
    config = {
        "version": LAYOUT_CONFIG_VERSION,
        "themeCollection": theme_collection,
        "activeSectionIndex": order.index(active) if active in order else 0,
        "defaultDrillFilterOtherVisuals": default_drill,
        "settings": settings,
    }
    if "objects" in report:
        config["objects"] = report["objects"]
    if bookmarks:
        config["bookmarks"] = bookmarks

    layout = {
        "id": 0,
        "resourcePackages": packages,
        "sections": sections,
        "config": jdump(config),
        "layoutOptimization": 0,
    }
    if report.get("publicCustomVisuals"):
        layout["publicCustomVisuals"] = report["publicCustomVisuals"]
    if pods:
        layout["pods"] = pods
    custom_theme = report.get("themeCollection", {}).get("customTheme")
    if custom_theme:
        layout["theme"] = custom_theme["name"]
    report_filters = convert_filters(report.get("filterConfig", {}).get("filters"))
    if report_filters:
        layout["filters"] = jdump(report_filters)
    return layout


# --------------------------------------------------------------------------- self-check
def self_check(files: dict[str, bytes], layout: dict) -> tuple[dict, list[str]]:
    """Compare counts before/after and list features whose classic codes are not calibrated."""
    pbir_pages = [p for p in files if re.match(rf"{re.escape(DEF)}pages/[^/]+/page\.json$", p)]
    pbir_visuals = [p for p in files if p.endswith("/visual.json") and p.startswith(DEF)]
    pbir_bookmarks = [p for p in files if p.endswith(".bookmark.json") and p.startswith(DEF)]
    bindings = sum(1 for p in pbir_pages if "pageBinding" in json.loads(files[p].decode("utf-8-sig")))
    cfg = json.loads(layout["config"])
    n_bm = sum(len(b.get("children", [])) or 1 for b in cfg.get("bookmarks", []))
    counts = {
        "pages": (len(pbir_pages), len(layout["sections"])),
        "visuals": (len(pbir_visuals), sum(len(s["visualContainers"]) for s in layout["sections"])),
        "bookmarks": (len(pbir_bookmarks), n_bm),
        "drillthrough": (bindings, len(layout.get("pods", []))),
    }
    warnings = [f"{k} count changed: {a} -> {b}" for k, (a, b) in counts.items() if a != b]

    def scan(o, found):
        if isinstance(o, dict):
            is_filter = "field" in o and "type" in o          # a PBIR filter entry
            for k, v in o.items():
                if k == "groupMode" and v == "ScrollMode":
                    found.add("visual group in scroll mode")
                if k == "displayOption" and v in ("FitToWidth", "ActualSize"):
                    found.add(f"page view '{v}'")
                if is_filter and k == "howCreated" and v not in ("User", "Drillthrough", "Auto"):
                    found.add(f"filter created as '{v}'")
                scan(v, found)
        elif isinstance(o, list):
            for v in o:
                scan(v, found)

    found = set()
    for p, data in files.items():
        if p.startswith(DEF) and p.endswith(".json"):
            doc = json.loads(data.decode("utf-8-sig"))
            scan(doc, found)
            kind = doc.get("pageBinding", {}).get("type") if p.endswith("/page.json") else None
            if kind and kind not in POD_TYPE:
                found.add(f"page binding of type '{kind}'")
    report = json.loads(files[DEF + "report.json"].decode("utf-8-sig"))
    if report.get("filterConfig", {}).get("filters"):
        found.add("report-level filters")
    if any("children" in b for b in cfg.get("bookmarks", [])):
        found.add("bookmark groups")
    warnings += [f"uses {f} (uncalibrated: check it in Desktop)" for f in sorted(found)]
    return counts, warnings


class AlreadyClassic(Exception):
    pass


def convert_file(src_p: Path, dst_p: Path) -> tuple[str, list[str]]:
    """Convert one .pbix. Returns (summary, self-check warnings). Raises AlreadyClassic if the
    file needs no conversion."""
    if src_p.resolve() == dst_p.resolve():
        raise ValueError("output must be a different file from the input")
    with zipfile.ZipFile(src_p) as z:
        infos = z.infolist()
        files = {i.filename: z.read(i.filename) for i in infos}
    if DEF + "report.json" not in files:
        raise AlreadyClassic()

    layout = convert_report(files)
    drop_prefixes = (DEF, "TMDLScripts/", "DAXQueries/")
    # SecurityBindings is encrypted and bound to the original file; kept, it makes Desktop
    # refuse the converted file (MashupValidationError). Desktop recreates it on save.
    replaced = {"[Content_Types].xml", "Report/Layout", "SecurityBindings"}

    kept = [i for i in infos
            if not i.filename.startswith(drop_prefixes) and i.filename not in replaced]
    part_names = ["Report/Layout"] + [i.filename for i in kept]
    version = next(i for i in kept if i.filename == "Version")
    rest = [i for i in kept if i.filename != "Version"]

    # Same part order Desktop writes: Version, [Content_Types].xml, Report/Layout, then the rest
    with zipfile.ZipFile(dst_p, "w") as out:
        out.writestr(version, files["Version"])
        out.writestr("[Content_Types].xml", content_types(part_names).encode("utf-8"),
                     zipfile.ZIP_DEFLATED)
        out.writestr("Report/Layout", jdump(layout).encode("utf-16-le"), zipfile.ZIP_DEFLATED)
        for info in rest:
            out.writestr(info, files[info.filename])  # keeps each part's original compression

    counts, warnings = self_check(files, layout)
    summary = ", ".join(f"{b} {k}" for k, (a, b) in counts.items())
    return summary, warnings


# --------------------------------------------------------------------------- verify in Desktop
DESKTOP_RS = Path(r"C:\Program Files\Microsoft Power BI Desktop RS\bin\PBIDesktop.exe")


def verify_in_desktop(path: Path, timeout: int = 180) -> str:
    """Open the file in Power BI Desktop for Report Server (Windows only) and report whether it
    loads: 'opened', 'refused: <dialog>' or 'timeout'. The Desktop window is closed afterwards."""
    import ctypes
    import subprocess
    import time
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def titles(pid: int) -> list[str]:
        found = []

        def cb(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                if buf.value:
                    found.append(buf.value)
            return True

        user32.EnumWindows(enum_proc(cb), 0)
        return found

    proc = subprocess.Popen([str(DESKTOP_RS), str(path)])
    result, start = "timeout", time.time()
    try:
        while time.time() - start < timeout:
            time.sleep(4)
            t = titles(proc.pid)
            if any(x.startswith(f"{path.stem} - ") for x in t):
                result = "opened"
                break
            bad = [x for x in t if "Unable to open" in x or "error" in x.lower()]
            if bad:
                result = "refused: " + bad[0]
                break
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    return result


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Convert PBIR-format .pbix files to the classic format Power BI Report "
                    "Server accepts. Input files are never modified.")
    ap.add_argument("inputs", nargs="+", help=".pbix files and/or folders containing .pbix files")
    ap.add_argument("-o", "--output", help="output file (only when converting a single file)")
    ap.add_argument("--out-dir", help="folder for converted files (default: next to each input)")
    ap.add_argument("--suffix", default="_RS", help="added to converted file names (default: _RS)")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files")
    ap.add_argument("--verify", action="store_true",
                    help="open each converted file in Power BI Desktop for Report Server and "
                         "report whether it loads (Windows; closes Desktop afterwards)")
    args = ap.parse_args(argv)
    if args.verify and not DESKTOP_RS.exists():
        ap.error(f"--verify needs Power BI Desktop for Report Server at {DESKTOP_RS}")

    sources: list[Path] = []
    for item in map(Path, args.inputs):
        if item.is_dir():
            sources += sorted(p for p in item.glob("*.pbix") if not p.stem.endswith(args.suffix))
        elif item.suffix.lower() == ".pbix" and item.is_file():
            sources.append(item)
        else:
            print(f"SKIP     {item}  (not a .pbix file or folder)")
    if args.output and len(sources) != 1:
        ap.error("--output can only be used with exactly one input file")

    failures = 0
    for src in sources:
        if args.output:
            dst = Path(args.output)
        else:
            folder = Path(args.out_dir) if args.out_dir else src.parent
            folder.mkdir(parents=True, exist_ok=True)
            dst = folder / f"{src.stem}{args.suffix}.pbix"
        if dst.exists() and not args.overwrite and not args.output:
            print(f"SKIP     {src.name}  ({dst.name} already exists; use --overwrite)")
            continue
        try:
            summary, warnings = convert_file(src, dst)
            print(f"OK       {src.name} -> {dst}  ({summary})")
            for w in warnings:
                print(f"           check: {w}")
            if args.verify:
                outcome = verify_in_desktop(dst)
                print(f"           Desktop: {outcome}")
                if outcome != "opened":
                    failures += 1
        except AlreadyClassic:
            print(f"SKIP     {src.name}  (already in the classic format; no conversion needed)")
        except Exception as e:  # keep going with the other files
            failures += 1
            print(f"FAILED   {src.name}  ({type(e).__name__}: {e})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
