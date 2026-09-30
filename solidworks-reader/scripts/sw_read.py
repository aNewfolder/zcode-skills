# -*- coding: utf-8 -*-
"""sw_read.py — 只读提取 SolidWorks 文件数据（.sldprt / .sldasm / .slddrw）。

主路线: SolidWorks COM API (pywin32)，需要本机装有 SolidWorks（面向 2018 开发/验证）。
兜底: olefile 直接读 OLE2 容器（仅 SW2014 及更早的老文件），提取摘要信息与预览图。
本脚本只以只读方式打开文档，绝不保存；若自行启动了 SolidWorks 进程，退出前会关闭它。

用法:
  python sw_read.py FILE [FILE...] [--json OUT] [--preview DIR] [--fast]
                      [--max-components N] [--max-features N]

输出: UTF-8 JSON（stdout；--json 可另存文件）。

COM 绑定语义（重要）: 本机 SolidWorks 对象是动态/早期绑定的混合体——
顶层对象是动态 CDispatch（属性访问即自动调用零参方法），子对象是
gen_py 类型化包装（类上定义了同名函数，需要显式调用）。g() 统一处理两种情况：
先 getattr，若该名字在类型对象上可调用（gen_py 方法）则补一次调用，否则直接用值。
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DOC_TYPES = {"sldprt": 1, "sldasm": 2, "slddrw": 3}  # swDocPART/ASSEMBLY/DRAWING
OPT_SILENT = 0x1          # swOpenDocOptions_Silent
OPT_READONLY = 0x2        # swOpenDocOptions_ReadOnly
OPT_RAPIDDRAFT = 0x8      # swOpenDocOptions_RapidDraft (工程图不加载模型，快)
OPT_LIGHTWEIGHT = 0x10    # swOpenDocOptions_LoadLightweight (装配体轻化，快)

OLER_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


# ---------------------------------------------------------------- COM helpers
def g(obj, name, *args, **kw):
    """统一 COM 成员访问。kw['default'] 作为失败时的返回值。

    - 动态 CDispatch: getattr 即已调用零参方法 → 直接返回其结果。
    - gen_py 类型化包装: 名字在类型上定义为可调用函数 → 补一次调用；否则是属性。
    """
    default = kw.get("default")
    try:
        v = getattr(obj, name)
    except Exception:
        return default
    if v is None:
        return default
    if args:
        try:
            return v(*args)
        except Exception:
            return default
    cls_fn = getattr(type(obj), name, None)
    if cls_fn is not None and callable(cls_fn) and not isinstance(cls_fn, property):
        try:
            return v()
        except Exception:
            return default
    return v


class Reader:
    """一次 SolidWorks 会话。spawned=True 时负责退出进程。"""

    def __init__(self, fast=False, max_components=5000, max_features=5000):
        import pythoncom
        import win32com.client
        self.pythoncom = pythoncom
        self.win32com = win32com.client
        self.fast = fast
        self.max_components = max_components
        self.max_features = max_features
        self.spawned = False
        self.sw = None
        self._preopen_paths = set()

    def byref_i4(self, v=0):
        return self.win32com.VARIANT(
            self.pythoncom.VT_BYREF | self.pythoncom.VT_I4, v)

    def byref_bstr(self, v=""):
        return self.win32com.VARIANT(
            self.pythoncom.VT_BYREF | self.pythoncom.VT_BSTR, v)

    def connect(self):
        try:
            self.sw = self.win32com.GetActiveObject("SldWorks.Application")
            self.attached = True
        except Exception:
            self.sw = self.win32com.Dispatch("SldWorks.Application")
            self.attached = False
            self.spawned = True
        if self.spawned:
            try:
                self.sw.Visible = False
                self.sw.UserControl = False
            except Exception:
                pass
        self._preopen_paths = self._open_paths()
        return {
            "revision": str(g(self.sw, "RevisionNumber", default="")),
            "attached_to_running_instance": self.attached,
        }

    def _open_paths(self):
        paths = set()
        doc = g(self.sw, "GetFirstDocument")
        n = 0
        while doc is not None and n < 1000:
            p = g(doc, "GetPathName", default="")
            if p:
                paths.add(os.path.normcase(p))
            doc = g(doc, "GetNext")
            n += 1
        return paths

    def open(self, path, doctype):
        opts = OPT_SILENT | OPT_READONLY
        if self.fast:
            if doctype == 2:
                opts |= OPT_LIGHTWEIGHT
            elif doctype == 3:
                opts |= OPT_RAPIDDRAFT
        errors, warnings = self.byref_i4(), self.byref_i4()
        model = g(self.sw, "OpenDoc6", path, doctype, opts, "", errors, warnings,
                  default=None)
        return model, errors.value, warnings.value

    def close(self, model, path):
        # 文档若本就已打开（用户正在用），不动它
        if os.path.normcase(path) in self._preopen_paths:
            return
        title = g(model, "GetTitle", default="")
        if title:
            try:
                g(self.sw, "CloseDoc", title)
            except Exception:
                pass

    def shutdown(self):
        if self.spawned:
            try:
                self.sw.ExitApp()
            except Exception:
                pass


# ----------------------------------------------------------------- extractors
def read_custom_props(reader, model, cfg):
    pm = g(model.Extension, "CustomPropertyManager", cfg)
    if pm is None:
        return {}
    names = g(pm, "GetNames", default=[]) or []
    props = {}
    for n in names:
        v1, v2 = reader.byref_bstr(), reader.byref_bstr()
        try:
            g(pm, "Get3", n, True, v1, v2)
            props[str(n)] = {"value": v1.value, "resolved": v2.value}
        except Exception as e:
            props[str(n)] = {"error": str(e)}
    return props


def read_mass(mp):
    m = {}
    for key, names in [
        ("mass", ("Mass",)), ("volume", ("Volume",)),
        ("surface_area", ("SurfaceArea",)), ("density", ("Density",)),
    ]:
        for nm in names:
            v = g(mp, nm)
            if v is not None:
                m[key] = v
                break
    com = g(mp, "CenterOfMass")
    if com is not None:
        m["center_of_mass"] = list(com)
    return m or None


def read_part(reader, model, out):
    feats = []
    f = g(model, "FirstFeature")
    n = 0
    while f is not None and n < reader.max_features:
        feats.append({
            "name": g(f, "Name", default=""),
            "type": g(f, "GetTypeName2", default=""),
            "suppressed": bool(g(f, "IsSuppressed", default=False)),
        })
        f = g(f, "GetNextFeature")
        n += 1
    out["feature_count"] = len(feats)
    out["features"] = feats

    bodies = g(model, "GetBodies2", 0, False)
    if bodies:
        out["solid_bodies"] = [g(b, "Name", default="") for b in bodies]
    out["bounding_box"] = g(model, "GetPartBox", True)

    # 材料（按激活配置；database 通过 ByRef BSTR 带回）
    db = reader.byref_bstr("")
    mat = g(model, "GetMaterialPropertyName2",
            out.get("active_configuration", ""), db)
    if mat is not None:
        out["material"] = {"name": mat, "database": db.value}

    mp = g(model.Extension, "CreateMassProperty")
    out["mass_properties"] = read_mass(mp) if mp is not None else None


MATE_TYPE_CN = {
    "MateConcentric": "同心", "MateCoincident": "重合", "MateParallel": "平行",
    "MatePerpendicular": "垂直", "MateDistance": "距离", "MateDistanceDim": "距离",
    "MateAngle": "角度", "MateAngleDim": "角度", "MateTangent": "相切",
    "MateWidth": "宽度", "MateLimitDist": "距离限制", "MateLimitAng": "角度限制",
    "MateGear": "齿轮", "MateRackPinion": "齿条", "MateScrew": "螺旋",
    "MateUniversal": "万向节", "MatePath": "路径", "MateLinearCouple": "线性耦合",
    "MateProfileCenter": "轮廓中心", "MateSymmetric": "对称", "MateHinge": "铰链",
}


def collect_mates(model, max_features):
    """装配体配合：顶层 MateGroup（SW2013+）或 Mate 特征，
    用 GetFirstSubFeature/GetNextSubFeature 链遍历子配合。"""
    mates = []
    f = g(model, "FirstFeature")
    n = 0
    while f is not None and n < max_features:
        t = g(f, "GetTypeName2", default="")
        if t in ("Mate", "MateGroup", "MateFolder"):
            sub = g(f, "GetFirstSubFeature")
            m = 0
            while sub is not None and m < max_features:
                row = _mate_row(sub)
                row["mate_cn"] = MATE_TYPE_CN.get(row["type"], "")
                mates.append(row)
                sub = g(sub, "GetNextSubFeature")
                m += 1
        f = g(f, "GetNextFeature")
        n += 1
    return mates


def _mate_row(s):
    return {
        "name": g(s, "Name", default=""),
        "type": g(s, "GetTypeName2", default=""),
        "suppressed": bool(g(s, "IsSuppressed", default=False)),
    }


def read_assembly(reader, model, out):
    comps = g(model, "GetComponents", False, default=[]) or []
    out["component_count"] = len(comps)
    rows, body_objs = [], []
    for c in comps[:reader.max_components]:
        name2 = g(c, "Name2", default="")
        t = g(c, "Transform")
        row = {
            "name": name2,
            "depth": name2.count("/") if name2 else 0,
            "path": g(c, "GetPathName", default=""),
            "configuration": g(c, "ReferencedConfiguration", default=""),
            "suppressed": bool(g(c, "IsSuppressed", default=False)),
            "hidden": g(c, "Visible", None) in (0, False),
            "transform": g(t, "ArrayData") if t is not None else None,
        }
        b = g(c, "GetBody")
        if b is not None:
            body_objs.append(b)
        rows.append(row)
    out["components"] = rows

    mates = collect_mates(model, reader.max_features)
    out["mate_count"] = len(mates)
    out["mates"] = mates

    # 整体质量：把已解析零部件的实体加入后计算（轻化/不可用时跳过）
    out["mass_properties"] = None
    mp = g(model.Extension, "CreateMassProperty")
    if mp is not None and body_objs:
        added = False
        try:
            g(mp, "AddBodies", body_objs)
            added = True
        except Exception:
            for b in body_objs:
                if g(mp, "AddBodies", [b]) is None:
                    break
                added = True
        m = read_mass(mp) if added else None
        if m and (m.get("mass") or 0) > 0:
            out["mass_properties"] = m
        else:
            out["mass_properties_hint"] = (
                "零部件轻化或实体不可用时无法汇总质量；"
                "去掉 --fast 或在 SolidWorks 中解析后再读")


def read_drawing(reader, model, out):
    out["sheet_count"] = g(model, "GetSheetCount", default=0)
    sheet_names = list(g(model, "GetSheetNames", default=[]) or [])

    # 视图遍历：GetFirstView 链最可靠（首个“视图”是图纸本身）。
    # GetViews(sheet) 在部分文件上返回空，仅作为补充。
    views_all = []
    v = g(model, "GetFirstView")
    n = 0
    while v is not None and n < 2000:
        views_all.append(v)
        v = g(v, "GetNextView")
        n += 1

    per_sheet = {name: [] for name in sheet_names}
    extras = []
    if views_all:
        first_sheet_view = g(views_all[0], "Name", default="")
        for v in views_all[1:]:
            vn = g(v, "Name", default="") or ""
            if vn in per_sheet:
                per_sheet[vn].append(v)
            else:
                extras.append(v)
    for name in sheet_names:
        if not per_sheet[name]:
            per_sheet[name] = list(g(model, "GetViews", name, default=[]) or [])

    sheets = []
    for name in sheet_names:
        sheet = {"name": name}
        vrows = []
        for v in per_sheet[name] + (extras if not per_sheet[name] else []):
            vrow = _read_view(v)
            if vrow:
                vrows.append(vrow)
        sheet["views"] = vrows
        sheets.append(sheet)
    out["sheets"] = sheets


def _read_view(v):
    vrow = {
        "name": g(v, "Name", default="") or "",
        "caption": g(v, "Caption", default=""),
        "referenced_model": g(v, "GetReferencedModelName", default=""),
        "scale": g(v, "ScaleDecimal"),
        "position": g(v, "Position"),
    }
    tables = []
    for t in (g(v, "GetTableAnnotations", default=[]) or []):
        tc = {
            "rows": g(t, "RowCount", default=0),
            "cols": g(t, "ColumnCount", default=0),
            "table_type": g(t, "TableType"),
        }
        tc["column_titles"] = [
            g(t, "GetColumnTitle", c) for c in range(min(tc["cols"] or 0, 100))]
        nr, nc = tc["rows"] or 0, tc["cols"] or 0
        grid = []
        for r in range(min(nr, max(1, 5000 // max(nc, 1)))):
            row = []
            for c in range(nc):
                txt = g(t, "Text", r, c)
                if txt is None:
                    txt = g(t, "Text", r, c, False)
                row.append(txt)
            grid.append(row)
        tc["cells"] = grid
        tables.append(tc)
    if tables:
        vrow["tables"] = tables

    notes = []
    note = g(v, "GetFirstNote")
    m = 0
    while note is not None and m < 2000:
        txt = g(note, "GetText") or g(note, "Text")
        if txt:
            notes.append(txt)
        note = g(note, "GetNext")
        m += 1
    if notes:
        vrow["notes"] = notes

    dims = []
    for dd in (g(v, "GetDisplayDimensions", default=[]) or []):
        d = g(dd, "GetDimension2", 0) or g(dd, "GetDimension2")
        if d is None:
            continue
        dv = {"name": g(d, "FullName", default="")
                     or g(d, "GetFullName", default="") or ""}
        val = g(d, "GetSystemValue", "")
        if val is None:
            val = g(d, "GetSystemValue")
        if val is not None:
            dv["system_value"] = val
        dims.append(dv)
    if dims:
        vrow["dimensions"] = dims
    return vrow


# ------------------------------------------------------------ static fallback
def read_static(path, preview_dir=None):
    """不启动 SolidWorks 的兜底：仅对 OLE2 容器（SW2014 及更早）有效。"""
    out = {"format": "unknown"}
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head == OLER_MAGIC:
        out["format"] = "OLE2 compound document (SolidWorks 2014 or older)"
        try:
            import olefile
            ole = olefile.OleFileIO(path)
            try:
                meta = ole.get_metadata()
                out["summary_info"] = {
                    "title": meta.title, "subject": meta.subject,
                    "author": meta.author, "keywords": meta.keywords,
                    "comments": meta.comments,
                    "create_time": str(meta.create_time),
                    "last_saved_time": str(meta.last_saved_time),
                    "last_saved_by": meta.last_saved_by,
                }
                if preview_dir:
                    for stream in (["PreviewPNG"], ["PreviewBitmap"],
                                   ["AVIPreviewA"]):
                        if ole.exists("/".join(stream)):
                            data = ole.openstream("/".join(stream)).read()
                            ext = "png" if stream == ["PreviewPNG"] else "bmp"
                            dst = os.path.join(
                                preview_dir,
                                os.path.basename(path) + "." + ext)
                            with open(dst, "wb") as pf:
                                pf.write(data)
                            out["preview_image"] = dst
                            break
            finally:
                ole.close()
        except Exception as e:
            out["ole_error"] = str(e)
    elif len(head) >= 8 and head[4:8] == b"\x00\x00\x00\x04":
        out["format"] = ("SolidWorks chunk format (2015+，容器加密，"
                         "纯 Python 只能读格式标记；完整数据请走 COM 路线)")
    else:
        out["format"] = "unrecognized"
    return out


# ---------------------------------------------------------------------- main
def process(reader, path, out):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    out["doc_type"] = {"sldprt": "part", "sldasm": "assembly",
                       "slddrw": "drawing"}.get(ext, "unknown")

    out["file_format"] = read_static(path, out.pop("_preview", None))

    model, err, warn = reader.open(path, DOC_TYPES.get(ext, 1))
    if not model:
        out["open_error"] = "OpenDoc6 failed: errors=%s warnings=%s" % (err, warn)
        return
    out["open_errors"] = err
    out["open_warnings"] = warn
    out["title"] = g(model, "GetTitle", default="")
    out["path"] = g(model, "GetPathName", default="") or path

    cfgs = g(model, "GetConfigurationNames", default=[]) or []
    out["configurations"] = list(cfgs)
    active = g(model, "GetActiveConfiguration")
    out["active_configuration"] = g(active, "Name", default="") if active else ""

    props = {"(document default)": read_custom_props(reader, model, "")}
    for cfg in cfgs:
        if cfg and cfg != out["active_configuration"]:
            props[cfg] = read_custom_props(reader, model, cfg)
    if not any(props.values()):
        props = {}
    out["custom_properties"] = props

    if out["doc_type"] == "part":
        read_part(reader, model, out)
    elif out["doc_type"] == "assembly":
        read_assembly(reader, model, out)
    elif out["doc_type"] == "drawing":
        read_drawing(reader, model, out)

    reader.close(model, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help=".sldprt/.sldasm/.slddrw 文件")
    ap.add_argument("--json", help="把 JSON 结果另存到该文件")
    ap.add_argument("--preview", help="提取预览图的输出目录（仅老 OLE2 格式）")
    ap.add_argument("--fast", action="store_true",
                    help="装配体轻化加载、工程图 RapidDraft（更快，数据略少）")
    ap.add_argument("--max-components", type=int, default=5000)
    ap.add_argument("--max-features", type=int, default=5000)
    args = ap.parse_args(argv)

    if args.preview:
        os.makedirs(args.preview, exist_ok=True)

    result = {"solidworks": None, "files": []}
    errors = []
    reader = Reader(fast=args.fast, max_components=args.max_components,
                    max_features=args.max_features)
    try:
        try:
            result["solidworks"] = reader.connect()
        except Exception as e:
            errors.append("无法连接 SolidWorks COM（是否已安装/注册？）: %s" % e)
            reader.sw = None
        for path in args.files:
            path = os.path.abspath(path)
            out = {"path": path, "exists": os.path.isfile(path)}
            if not out["exists"]:
                errors.append("文件不存在: %s" % path)
                result["files"].append(out)
                continue
            if reader.sw is None:
                out["file_format"] = read_static(path, args.preview)
            else:
                try:
                    process(reader, path, out)
                except Exception as e:
                    out["processing_error"] = "%s: %s" % (type(e).__name__, e)
            result["files"].append(out)
    finally:
        try:
            reader.shutdown()
        except Exception:
            pass
    if errors:
        result["errors"] = errors

    js = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(js)
    print(js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
