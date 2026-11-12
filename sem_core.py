"""SEM sampling core: corner geometry, parsing, random planning, TIF metadata."""
import re, os, json, hashlib, csv
import numpy as np

CORNER_KEYS = ("tl", "tr", "bl", "br")
_CORNER_RE = re.compile(r"(top|bottom)\s*[-_ ]?\s*(left|right)\s*\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)", re.I)


# ---------------------------------------------------------------- geometry
class Film:
    """A film defined by four stage-coordinate corners (mm), as seen in the SEM view."""

    def __init__(self, name, corners):
        self.name = name
        self.c = {k: np.asarray(corners[k], float) for k in CORNER_KEYS}

    def uv2xy(self, u, v):
        c = self.c
        p = (1 - u) * (1 - v) * c["tl"] + u * (1 - v) * c["tr"] + (1 - u) * v * c["bl"] + u * v * c["br"]
        return float(p[0]), float(p[1])

    def xy2uv(self, x, y):
        c = self.c; p = np.array([x, y], float); uv = np.array([0.5, 0.5])
        for _ in range(30):
            u, v = uv
            f = (1 - u) * (1 - v) * c["tl"] + u * (1 - v) * c["tr"] + (1 - u) * v * c["bl"] + u * v * c["br"] - p
            du = -(1 - v) * c["tl"] + (1 - v) * c["tr"] - v * c["bl"] + v * c["br"]
            dv = -(1 - u) * c["tl"] - u * c["tr"] + (1 - u) * c["bl"] + u * c["br"]
            J = np.column_stack([du, dv])
            step = np.linalg.solve(J, f)
            uv = uv - step
            if np.abs(step).max() < 1e-10:
                break
        return float(uv[0]), float(uv[1])

    @property
    def width(self):
        c = self.c
        return float((np.linalg.norm(c["tr"] - c["tl"]) + np.linalg.norm(c["br"] - c["bl"])) / 2)

    @property
    def height(self):
        c = self.c
        return float((np.linalg.norm(c["bl"] - c["tl"]) + np.linalg.norm(c["br"] - c["tr"])) / 2)

    def to_dict(self):
        return {k: [float(v) for v in self.c[k]] for k in CORNER_KEYS}


def parse_corners_text(text):
    """Parse the user's txt format. Returns (batch_name, [Film,...])."""
    films, batch, cur, cur_c = [], None, None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _CORNER_RE.search(line)
        if m:
            key = ("t" if m.group(1).lower() == "top" else "b") + ("l" if m.group(2).lower() == "left" else "r")
            cur_c[key] = (float(m.group(3)), float(m.group(4)))
            if cur and len(cur_c) == 4:
                films.append(Film(cur, cur_c)); cur, cur_c = None, {}
            continue
        if "(" in line or "." in line.split()[0] and line.lower().endswith((".tif", ".tiff")):
            continue  # image coordinate lines etc.
        tok = line.split()[0]
        if len(line.split()) == 1 and re.fullmatch(r"[\w\-]+", tok):
            if batch is None and not films and cur is None:
                batch = tok  # first bare token is a candidate batch name
            if cur is not None and not cur_c and cur == batch:
                pass
            cur, cur_c = tok, {}
    # If first token had corners it was a sample, not a batch.
    if batch and any(f.name == batch for f in films):
        batch = None
    return batch, films


def _overlaps(a, b):
    g = [i / 6 for i in range(1, 6)]
    for u in g:
        for v in g:
            uu, vv = b.xy2uv(*a.uv2xy(u, v))
            if 0.02 < uu < 0.98 and 0.02 < vv < 0.98:
                return True
    return False


def mount_groups(films):
    """Split films (in input order) into mounts: a new mount starts when stage areas overlap."""
    groups = [[]]
    for f in films:
        if any(_overlaps(f, g) or _overlaps(g, f) for g in groups[-1]):
            groups.append([])
        groups[-1].append(f)
    return [g for g in groups if g]


# ---------------------------------------------------------------- planning
def plan_round(films, rnd, seed, rows, cols, margin_mm, paired=True, edge=True, edge_mm=0.5):
    """Stratified random points for one round. Returns list of dict rows."""
    out = []
    W = np.mean([f.width for f in films]); H = np.mean([f.height for f in films])

    def uv_set(rng, w, h):
        mu, mv = margin_mm / w, margin_mm / h
        pts = []
        for r in range(rows):
            cs = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)  # serpentine
            for cc in cs:
                u0 = mu + (1 - 2 * mu) * cc / cols; u1 = mu + (1 - 2 * mu) * (cc + 1) / cols
                v0 = mv + (1 - 2 * mv) * r / rows; v1 = mv + (1 - 2 * mv) * (r + 1) / rows
                pts.append((rng.uniform(u0, u1), rng.uniform(v0, v1), f"r{r+1}c{cc+1}"))
        return pts

    def edge_set(w, h):
        eu, ev = edge_mm / w, edge_mm / h
        return [(0.5, ev, "E-T"), (1 - eu, 0.5, "E-R"), (0.5, 1 - ev, "E-B"), (eu, 0.5, "E-L")]

    shared = uv_set(np.random.default_rng([seed, rnd]), W, H) if paired else None
    for i, f in enumerate(films):
        pts = shared if paired else uv_set(np.random.default_rng([seed, rnd, i + 1]), f.width, f.height)
        for k, (u, v, cell) in enumerate(pts, 1):
            x, y = f.uv2xy(u, v)
            out.append(dict(sample=f.name, id=f"R{rnd}-P{k:02d}", type="random", cell=cell, round=rnd,
                            u=round(u, 4), v=round(v, 4), X=round(x, 4), Y=round(y, 4)))
        if edge and rnd == 1:
            for u, v, lab in edge_set(f.width, f.height):
                x, y = f.uv2xy(u, v)
                out.append(dict(sample=f.name, id=lab, type="edge", cell="edge", round=rnd,
                                u=round(u, 4), v=round(v, 4), X=round(x, 4), Y=round(y, 4)))
    return out


def export_csv(rows, path):
    keys = ["sample", "id", "type", "cell", "round", "u", "v", "X", "Y"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def save_project(path, batch, films, params, rows):
    data = dict(app="SEM Sampler", version=1, batch=batch, params=params,
                films={f.name: f.to_dict() for f in films}, points=rows)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def load_project(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    films = [Film(n, c) for n, c in d["films"].items()]
    return d.get("batch"), films, d.get("params", {}), d.get("points", [])


# ---------------------------------------------------------------- TIF metadata
_META_KEYS = ("StageX", "StageY", "StageZ", "StageRotation", "StageTilt", "Magnification",
              "PixelSizeX", "WD", "HV", "Detector", "Date", "Time", "ImageStripSize")


def read_tescan_meta(tif_path):
    """Read TESCAN metadata embedded in the TIF (tag 50431); fall back to the .hdr file."""
    text = ""
    try:
        from PIL import Image
        with Image.open(tif_path) as im:
            b = im.tag_v2.get(50431)
            if isinstance(b, (bytes, bytearray)):
                i = b.find(b"[MAIN]")
                text = b[i if i >= 0 else 0:].decode("latin1", "replace")
    except Exception:
        pass
    if "StageX" not in text:
        stem = os.path.splitext(tif_path)[0]
        for h in (stem + "-tif.hdr", stem + ".hdr"):
            if os.path.exists(h):
                text = open(h, encoding="latin1").read(); break
    meta = {}
    for k in _META_KEYS:
        m = re.search(rf"^{k}=(.*)$", text, re.M)
        if m:
            val = m.group(1).strip()
            try:
                meta[k] = float(val)
            except ValueError:
                meta[k] = val
    if "StageX" not in meta:
        return None
    meta["X"] = meta["StageX"] * 1e3
    meta["Y"] = meta["StageY"] * 1e3
    return meta


def mag_label(m):
    if m is None:
        return "?"
    return f"{m/1e3:.3g}kx" if m >= 950 else f"{m:.0f}x"


def scan_folder(folder, films):
    """Return list of image records with sample assignment and (u,v)."""
    recs = []
    nat = lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    for fn in sorted(os.listdir(folder), key=nat):
        if not fn.lower().endswith((".tif", ".tiff")):
            continue
        p = os.path.join(folder, fn)
        meta = read_tescan_meta(p)
        if not meta:
            continue
        # sample assignment: filename token first, then geometry
        film = None
        toks = re.split(r"[_\-\s.]", os.path.splitext(fn)[0])
        for f in films:
            if f.name in toks:
                film = f; break
        if film is None:
            best = None
            for f in films:
                u, v = f.xy2uv(meta["X"], meta["Y"])
                d = max(0, -u, u - 1) + max(0, -v, v - 1)
                if best is None or d < best[0]:
                    best = (d, f)
            if best and best[0] < 0.2:
                film = best[1]
        u = v = None
        if film:
            u, v = film.xy2uv(meta["X"], meta["Y"])
        recs.append(dict(path=p, name=fn, sample=film.name if film else None, u=u, v=v,
                         X=meta["X"], Y=meta["Y"], mag=meta.get("Magnification"),
                         rot=meta.get("StageRotation", 0.0), meta=meta))
    return recs


def group_positions(recs, tol_mm=0.02):
    """Cluster images taken at (nearly) the same stage position."""
    groups = []
    for r in recs:
        for g in groups:
            if g["sample"] == r["sample"] and abs(g["X"] - r["X"]) < tol_mm and abs(g["Y"] - r["Y"]) < tol_mm:
                g["imgs"].append(r); break
        else:
            groups.append(dict(sample=r["sample"], X=r["X"], Y=r["Y"], u=r["u"], v=r["v"], imgs=[r]))
    for g in groups:
        g["imgs"].sort(key=lambda r: -(r["mag"] or 0))
        g["maxmag"] = g["imgs"][0]["mag"] or 0
    return groups


# ---------------------------------------------------------------- thumbnails
def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "SEMSampler", "thumbs"); os.makedirs(d, exist_ok=True)
    return d


def make_thumb(path, size=220, strip=None):
    """Contrast-stretched square thumbnail (data bar cropped). Cached as PNG."""
    from PIL import Image
    st = os.stat(path)
    key = hashlib.md5(f"{path}|{st.st_mtime}|{size}".encode()).hexdigest()
    cp = os.path.join(cache_dir(), key + ".png")
    if os.path.exists(cp):
        return Image.open(cp)
    with Image.open(path) as im:
        a = np.asarray(im).astype(np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(-1)
    h, w = a.shape
    if h > w:
        a = a[:w, :]  # drop the TESCAN data bar
    lo, hi = np.percentile(a, [0.5, 99.5])
    a = np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    t = Image.fromarray(a).resize((size, size), Image.LANCZOS)
    t.save(cp)
    return t
