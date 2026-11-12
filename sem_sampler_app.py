"""SEM Sampler — 随机取点规划 + SEM 图像位置浏览 (TESCAN MIRA3)."""
import os, sys, random, threading, subprocess, math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw, ImageFont
import sem_core as core

APP_TITLE = "SEM Sampler"
IS_WIN = sys.platform.startswith("win")
UI_FONT = "Microsoft YaHei UI" if IS_WIN else "Noto Sans CJK SC"
MONO_FONT = "Consolas" if IS_WIN else "DejaVu Sans Mono"

# palette
C_BG = "#f3f4f7"; C_FILM = "#ffffff"; C_FILM_EDGE = "#8a93a6"; C_GRID = "#e3e6ee"
C_RAND = "#6d3fc0"; C_EDGE = "#8a8f99"; C_MISS = "#e5484d"; C_TEXT = "#2b2f38"; C_SUB = "#6b7280"
MAG_COLORS = [(30e3, "#e5484d", "≥ 30k"), (5e3, "#f59e0b", "5k – 30k"), (500, "#10b981", "0.5k – 5k"), (0, "#3b82f6", "< 500")]
HOVER_CACHE = 512      # cached preview size (px)
GALLERY_CACHE = 1024   # cached size for the resizable gallery window


def mag_color(m):
    for th, col, _ in MAG_COLORS:
        if (m or 0) >= th:
            return col
    return "#888"


def open_with_system(path):
    try:
        if IS_WIN:
            os.startfile(path)  # default Windows app (Photos)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror(APP_TITLE, f"无法打开：\n{path}\n\n{e}")


def open_folder_select(path):
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_with_system(os.path.dirname(path))


def f(size, weight="normal"):
    return (UI_FONT, size, weight)


# ====================================================================== map canvas
class FilmMap(tk.Canvas):
    """Draws films either in stage coordinates (as seen in SEM) or as a normalized grid."""

    def __init__(self, master, **kw):
        super().__init__(master, bg=C_BG, highlightthickness=0, **kw)
        self._films, self._groups, self.mode = [], [], "stage"
        self._job = None
        self.bind("<Configure>", lambda e: self.request_redraw())
        self.draw_points = lambda: None
        self.empty_text = "（尚未加载四角坐标）"

    @property
    def films(self):
        return self._films

    @films.setter
    def films(self, v):
        self._films = list(v); self._groups = core.mount_groups(self._films)  # computed once, not on every redraw

    def request_redraw(self, delay=40):
        """Coalesce bursts of resize/selection events into one redraw."""
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(delay, self.redraw)

    def setup(self):
        W, H = max(self.winfo_width(), 50), max(self.winfo_height(), 50) - 34  # bottom band = legend
        pad = 56
        if self.mode == "stage":
            layers = self._groups
            def bbox(fl):
                xs = [-p[0] for p in fl.c.values()]; ys = [p[1] for p in fl.c.values()]
                return min(xs), max(xs), min(ys), max(ys)
            ext = []
            for L in layers:
                bs = [bbox(x) for x in L]
                ext.append((min(b[0] for b in bs), max(b[1] for b in bs), min(b[2] for b in bs), max(b[3] for b in bs)))
            n = len(layers); best = None
            for nc in range(1, n + 1):
                nr = math.ceil(n / nc); cw = (W - 20) / nc; ch = (H - 20) / nr
                s = min(min((cw - pad) / max(e[1] - e[0], 1e-6), (ch - pad) / max(e[3] - e[2], 1e-6)) for e in ext)
                if best is None or s > best[0]:
                    best = (s, nc, cw, ch)
            s, nc, cw, ch = best
            self._st = {}
            for i, (L, e) in enumerate(zip(layers, ext)):
                r, c = divmod(i, nc)
                ox = 10 + c * cw + (cw - s * (e[1] - e[0])) / 2 - s * e[0]
                oy = 10 + r * ch + (ch - s * (e[3] - e[2])) / 2 + 8 - s * e[2]
                for x in L:
                    self._st[x.name] = (s, ox, oy)
        else:
            n = len(self.films); nc = min(n, 3) if n <= 9 else math.ceil(math.sqrt(n)); nr = math.ceil(n / nc)
            ar = sum(x.height for x in self.films) / max(sum(x.width for x in self.films), 1e-6)
            cw = (W - 20) / nc; ch = (H - 20) / nr
            fw = min(cw - pad, (ch - pad) / ar); fh = fw * ar
            self._cells = {}
            for i, x in enumerate(sorted(self.films, key=lambda q: q.name)):
                r, c = divmod(i, nc)
                self._cells[x.name] = (10 + c * cw + (cw - fw) / 2, 10 + r * ch + (ch - fh) / 2 + 10, fw, fh)

    def to_screen(self, sample, u=None, v=None, X=None, Y=None):
        if self.mode == "stage":
            if X is None:
                X, Y = self.film(sample).uv2xy(u, v)
            s, ox, oy = self._st[sample]
            return ox + s * (-X), oy + s * Y
        x, y, fw, fh = self._cells[sample]
        if u is None:
            u, v = self.film(sample).xy2uv(X, Y)
        return x + u * fw, y + v * fh

    def film(self, name):
        for x in self.films:
            if x.name == name:
                return x

    def redraw(self):
        self._job = None
        self.delete("all")
        if not self.films:
            self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text=self.empty_text, fill=C_SUB, font=f(11))
            return
        self.setup()
        for fl in self.films:
            if self.mode == "stage":
                pts = [self.to_screen(fl.name, X=fl.c[k][0], Y=fl.c[k][1]) for k in ("tl", "tr", "br", "bl")]
            else:
                x, y, fw, fh = self._cells[fl.name]
                pts = [(x, y), (x + fw, y), (x + fw, y + fh), (x, y + fh)]
            self.create_polygon(*[c for p in pts for c in p], fill=C_FILM, outline=C_FILM_EDGE, width=1.2)
            for t in (1 / 3, 2 / 3):
                self.create_line(*self.to_screen(fl.name, t, 0), *self.to_screen(fl.name, t, 1), fill=C_GRID, dash=(3, 3))
                self.create_line(*self.to_screen(fl.name, 0, t), *self.to_screen(fl.name, 1, t), fill=C_GRID, dash=(3, 3))
            tx, ty = self.to_screen(fl.name, 0.5, 0)
            self.create_text(tx, ty - 16, text=fl.name, font=f(12, "bold"), fill=C_TEXT)
            lx, ly = self.to_screen(fl.name, 0, 0)
            self.create_text(lx - 3, ly - 3, text="TL", anchor="se", fill="#a0a6b4", font=f(7))
        self.draw_points()

    def legend(self, items):
        """One-line legend in the reserved bottom band. kind: dot/ring/square/dsquare."""
        H = self.winfo_height(); y = H - 17; x = 16
        self.create_line(10, H - 34, self.winfo_width() - 10, H - 34, fill="#dde1ea")
        for kind, col, lab in items:
            if kind == "dot":
                self.create_oval(x, y - 5, x + 10, y + 5, fill=col, outline="")
            elif kind == "ring":
                self.create_oval(x, y - 5, x + 10, y + 5, outline=col, width=2)
            elif kind == "square":
                self.create_rectangle(x, y - 5, x + 10, y + 5, outline=col, width=2)
            else:
                self.create_rectangle(x, y - 5, x + 10, y + 5, outline=col, width=1.5, dash=(2, 2))
            t = self.create_text(x + 16, y, text=lab, anchor="w", font=f(9), fill=C_TEXT)
            x = self.bbox(t)[2] + 22


class ScrollSide(ttk.Frame):
    """Side panel: width set by the user via the pane sash; scrolls vertically if too short."""

    def __init__(self, master, width):
        super().__init__(master)
        bg = ttk.Style().lookup("TFrame", "background") or "#fafafa"
        self.cv = tk.Canvas(self, width=width, highlightthickness=0, bd=0, bg=bg)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.sb.set)
        self.inner = ttk.Frame(self.cv, padding=(0, 0, 8, 0))
        self._win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.cv.pack(side="left", fill="both", expand=True); self.sb.pack(side="left", fill="y")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self._win, width=e.width))
        self.cv.bind("<Enter>", lambda e: self.cv.bind_all("<MouseWheel>", self._wheel))
        self.cv.bind("<Leave>", lambda e: self.cv.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        if self.inner.winfo_reqheight() > self.cv.winfo_height():
            self.cv.yview_scroll(int(-e.delta / 120), "units")


def make_tree(parent, cols, widths, anchors=None):
    fr = ttk.Frame(parent)
    tree = ttk.Treeview(fr, columns=cols, show="headings", selectmode="extended")
    for i, (c, w) in enumerate(zip(cols, widths)):
        tree.heading(c, text=c)
        tree.column(c, width=w, minwidth=40, anchor=(anchors[i] if anchors else "center"), stretch=True)
    sb = ttk.Scrollbar(fr, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True); sb.pack(side="left", fill="y")
    tree.tag_configure("odd", background="#f6f7fa")
    return fr, tree


# ====================================================================== planning tab
class PlanTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=(10, 8)); self.app = app
        self.batch, self.films, self.rows, self.rounds = None, [], [], 0

        outer = ttk.PanedWindow(self, orient="horizontal"); outer.pack(fill="both", expand=True)
        side_w = tkfont.Font(font=f(10)).measure("配对模式（全部样品同一组位置）") + 90  # scales with Windows DPI
        sw = ScrollSide(outer, side_w); outer.add(sw, weight=0); side = sw.inner
        sec = ttk.LabelFrame(side, text=" 1 · 四角坐标 ", padding=8); sec.pack(fill="x")
        self.txt = tk.Text(sec, height=9, width=10, font=(MONO_FONT, 9), wrap="none", relief="flat", bd=0,
                           highlightthickness=1, highlightbackground="#d5d9e2", highlightcolor="#6d3fc0", padx=6, pady=4)
        self.txt.pack(fill="x")
        row = ttk.Frame(sec); row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="打开 txt…", command=self.open_txt).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="解析坐标", command=self.parse).pack(side="left", expand=True, fill="x", padx=(6, 0))

        p = ttk.LabelFrame(side, text=" 2 · 取点参数 ", padding=8); p.pack(fill="x", pady=8)
        self.v_margin = tk.DoubleVar(value=1.0); self.v_rows = tk.IntVar(value=3); self.v_cols = tk.IntVar(value=3)
        self.v_edge = tk.BooleanVar(value=True); self.v_edge_mm = tk.DoubleVar(value=0.5)
        self.v_paired = tk.BooleanVar(value=True); self.v_seed = tk.IntVar(value=random.randint(1000, 99999))
        p.columnconfigure(1, weight=1)
        specs = [("边缘排除宽度 (mm)", self.v_margin, "entry"), ("区域行数（纵向）", self.v_rows, "spin"),
                 ("区域列数（横向）", self.v_cols, "spin"), ("边缘点距边 (mm)", self.v_edge_mm, "entry")]
        for i, (lab, var, kind) in enumerate(specs):
            ttk.Label(p, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            w = ttk.Spinbox(p, from_=1, to=10, textvariable=var, width=8, font=f(10)) if kind == "spin" else ttk.Entry(p, textvariable=var, width=10, font=f(10))
            w.grid(row=i, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(p, text="随机种子").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(p, textvariable=self.v_seed, width=10, font=f(10)).grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Button(p, text="↻", width=3, command=lambda: self.v_seed.set(random.randint(1000, 99999))).grid(row=4, column=2, padx=(4, 0))
        ttk.Checkbutton(p, text="每片加 4 个边缘参考点", variable=self.v_edge).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(p, text="配对模式（全部样品同一组位置）", variable=self.v_paired).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))

        g = ttk.LabelFrame(side, text=" 3 · 生成 ", padding=8); g.pack(fill="x")
        ttk.Button(g, text="生成第 1 轮（清空重来）", command=self.new_plan).pack(fill="x")
        row = ttk.Frame(g); row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="＋ 追加一轮", command=self.add_round).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="撤销最后一轮", command=self.undo_round).pack(side="left", expand=True, fill="x", padx=(6, 0))

        e = ttk.LabelFrame(side, text=" 4 · 保存 / 导出 ", padding=8); e.pack(fill="x", pady=8)
        e.columnconfigure((0, 1), weight=1)
        ttk.Button(e, text="保存项目", command=self.save_proj).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(e, text="打开项目", command=self.open_proj).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Button(e, text="导出 CSV", command=self.export_csv).grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(6, 0))
        ttk.Button(e, text="导出点位图", command=self.export_png).grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=(6, 0))

        main = ttk.PanedWindow(outer, orient="vertical"); outer.add(main, weight=1)
        top = ttk.Frame(main); main.add(top, weight=3)
        bar = ttk.Frame(top); bar.pack(fill="x", pady=(0, 6))
        self.v_mode = tk.StringVar(value="stage")
        ttk.Radiobutton(bar, text="载物台视图（同 SEM 画面）", value="stage", variable=self.v_mode, command=self.set_mode).pack(side="left")
        ttk.Radiobutton(bar, text="归一化视图", value="norm", variable=self.v_mode, command=self.set_mode).pack(side="left", padx=6)
        ttk.Label(bar, text="双击表格行可复制 X Y", foreground=C_SUB).pack(side="right")
        self.map = FilmMap(top); self.map.pack(fill="both", expand=True)
        self.map.draw_points = self.draw_points
        bot = ttk.Frame(main, padding=(0, 6, 0, 0)); main.add(bot, weight=2)
        cols = ("sample", "id", "type", "cell", "u", "v", "X", "Y")
        tf, self.tree = make_tree(bot, cols, (70, 90, 70, 70, 70, 70, 100, 100)); tf.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.highlight())
        self.tree.bind("<Double-1>", self.copy_xy)

    def status(self, msg):
        self.app.set_status(msg)

    # --- input
    def open_txt(self):
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if p:
            self.txt.delete("1.0", "end"); self.txt.insert("1.0", open(p, encoding="utf-8", errors="replace").read())
            self.parse()

    def parse(self):
        self.batch, self.films = core.parse_corners_text(self.txt.get("1.0", "end"))
        self.map.films = self.films; self.rows, self.rounds = [], 0; self.refresh()
        if not self.films:
            self.status("没有解析到四角坐标。格式：样品名一行，下面 top-left(x,y,…) / top-right / bottom-left / bottom-right。")
        else:
            info = "，".join(f"{x.name} {x.width:.1f}×{x.height:.1f} mm" for x in self.films)
            self.status(f"批次 {self.batch or '—'}：已解析 {len(self.films)} 片 — {info}")

    # --- planning
    def params(self):
        return dict(margin_mm=self.v_margin.get(), rows=self.v_rows.get(), cols=self.v_cols.get(),
                    edge=self.v_edge.get(), edge_mm=self.v_edge_mm.get(), paired=self.v_paired.get(), seed=self.v_seed.get())

    def _round(self, r):
        p = self.params()
        return core.plan_round(self.films, r, p["seed"], p["rows"], p["cols"], p["margin_mm"], p["paired"], p["edge"], p["edge_mm"])

    def new_plan(self):
        if not self.films:
            self.parse()
        if self.films:
            self.rows, self.rounds = self._round(1), 1; self.refresh()

    def add_round(self):
        if not self.rows:
            return self.new_plan()
        self.rounds += 1; self.rows += self._round(self.rounds); self.refresh()

    def undo_round(self):
        if self.rounds:
            self.rows = [r for r in self.rows if r["round"] != self.rounds]; self.rounds -= 1; self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        order = {x.name: i for i, x in enumerate(self.films)}
        for i, r in enumerate(sorted(self.rows, key=lambda r: (order.get(r["sample"], 0), r["round"], r["type"] == "edge", r["id"]))):
            vals = (r["sample"], r["id"], r["type"], r["cell"], f"{r['u']:.3f}", f"{r['v']:.3f}", f"{r['X']:.4f}", f"{r['Y']:.4f}")
            self.tree.insert("", "end", iid=str(i), values=vals, tags=("odd",) if i % 2 else ())
        if self.rows:
            self.status(f"共 {len(self.rows)} 个点（{self.rounds} 轮），种子 {self.v_seed.get()}。记得「保存项目」，浏览图像时可对照计划。")
        self.map.redraw()

    def set_mode(self):
        self.map.mode = self.v_mode.get(); self.map.redraw()

    def draw_points(self):
        m = self.map; self._pt_items = {}
        for r in self.rows:
            x, y = m.to_screen(r["sample"], r["u"], r["v"])
            rad = 5
            if r["type"] == "edge":
                it = m.create_rectangle(x - rad, y - rad, x + rad, y + rad, outline=C_EDGE, width=2, fill="#fafafa")
            else:
                it = m.create_oval(x - rad, y - rad, x + rad, y + rad, fill="white", outline=C_RAND, width=2)
            self._pt_items[(r["sample"], r["id"])] = (it, x, y, r["type"])
            m.create_text(x + rad + 2, y - rad, text=r["id"].replace("R1-", "").replace("E-", ""), anchor="sw", font=f(8), fill=C_SUB)
        if self.films:
            m.legend([("ring", C_RAND, "随机点（R轮-P号）"), ("square", C_EDGE, "边缘参考点 T/B/L/R")])
        self._hot = set(); self.highlight()

    def highlight(self):
        """Update only the selected markers (no full redraw)."""
        items = getattr(self, "_pt_items", {}); m = self.map
        sel = {tuple(self.tree.item(i, "values")[:2]) for i in self.tree.selection()}
        for key in getattr(self, "_hot", set()) | sel:
            if key not in items:
                continue
            it, x, y, typ = items[key]; hot = key in sel; rad = 8 if hot else 5
            m.coords(it, x - rad, y - rad, x + rad, y + rad)
            if typ != "edge":
                m.itemconfigure(it, fill=C_RAND if hot else "white")
        self._hot = sel

    def copy_xy(self, _e):
        s = self.tree.selection()
        if s:
            v = self.tree.item(s[0], "values")
            self.clipboard_clear(); self.clipboard_append(f"{v[6]}\t{v[7]}")
            self.status(f"已复制 {v[0]} {v[1]}：X = {v[6]}   Y = {v[7]}")

    # --- save/export
    def _default_name(self, ext):
        return f"{self.batch or 'SEM'}_plan{ext}"

    def save_proj(self):
        if not self.rows:
            return messagebox.showinfo(APP_TITLE, "还没有生成点位。")
        p = filedialog.asksaveasfilename(defaultextension=".json", initialfile=self._default_name(".json"), filetypes=[("Project", "*.json")])
        if p:
            core.save_project(p, self.batch, self.films, self.params(), self.rows); self.status(f"已保存：{p}")

    def open_proj(self):
        p = filedialog.askopenfilename(filetypes=[("Project", "*.json")])
        if not p:
            return
        self.batch, self.films, prm, self.rows = core.load_project(p)
        self.map.films = self.films; self.rounds = max([r["round"] for r in self.rows] or [0])
        for k, var in (("margin_mm", self.v_margin), ("rows", self.v_rows), ("cols", self.v_cols), ("edge", self.v_edge),
                       ("edge_mm", self.v_edge_mm), ("paired", self.v_paired), ("seed", self.v_seed)):
            if k in prm:
                var.set(prm[k])
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", (self.batch or "") + "\n" + "\n".join(
            f"{x.name}\n" + "\n".join(f"\t{lab}({x.c[k][0]:.4f},{x.c[k][1]:.4f})" for k, lab in
                                      (("tl", "top-left"), ("tr", "top-right"), ("bl", "bottom-left"), ("br", "bottom-right"))) for x in self.films))
        self.refresh()

    def export_csv(self):
        if not self.rows:
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=self._default_name(".csv"), filetypes=[("CSV", "*.csv")])
        if p:
            core.export_csv(self.rows, p); self.status(f"已导出：{p}")

    def export_png(self):
        if not self.rows:
            return
        p = filedialog.asksaveasfilename(defaultextension=".png", initialfile=self._default_name(".png"), filetypes=[("PNG", "*.png")])
        if p:
            out = render_plan_png(self.films, self.rows, p, title=f"{self.batch or ''}  seed={self.v_seed.get()}")
            self.status("已导出：" + "；".join(out))


def _font(size):
    for name in ("msyh.ttc", "msyh.ttf", "NotoSansCJK-Regular.ttc", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def render_plan_png(films, rows, path, title=""):
    """Printable map(s): one PNG per mount (stage view + coordinate table)."""
    groups = core.mount_groups(films)
    base, ext = os.path.splitext(path); out = []
    for gi, grp in enumerate(groups, 1):
        names = [x.name for x in grp]
        rws = sorted([r for r in rows if r["sample"] in names],
                     key=lambda r: (names.index(r["sample"]), r["round"], r["type"] == "edge", r["id"]))
        W, H = 2480, 1754  # A4 landscape @ 150 dpi
        img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
        f_b, f_s, f_t = _font(34), _font(21), _font(22)
        d.text((40, 24), f"SEM 随机取点  {title}   [{' / '.join(names)}]", fill="black", font=f_b)
        xs = [-p[0] for x in grp for p in x.c.values()]; ys = [p[1] for x in grp for p in x.c.values()]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        box = (50, 140, 1110, H - 80)
        sc = min((box[2] - box[0]) / (x1 - x0), (box[3] - box[1]) / (y1 - y0))
        T = lambda X, Y: (box[0] + sc * (-X - x0), box[1] + sc * (Y - y0))
        for x in grp:
            pts = [T(*x.c[k]) for k in ("tl", "tr", "br", "bl")]
            d.polygon(pts, fill="#f4f6fa", outline="#555")
            tx, ty = T(*x.uv2xy(0.5, 0)); d.text((tx - 20, ty - 44), x.name, fill="black", font=f_b)
        for r in rws:
            x, y = T(r["X"], r["Y"]); rad = 8
            if r["type"] == "edge":
                d.rectangle((x - rad, y - rad, x + rad, y + rad), outline="#888", width=3)
            else:
                d.ellipse((x - rad, y - rad, x + rad, y + rad), outline="#6d3fc0", width=3)
            d.text((x + 10, y - 24), r["id"], fill="#333", font=f_s)
        cols = [("sample", 0), ("id", 85), ("u", 195), ("v", 262), ("X (mm)", 330), ("Y (mm)", 450), ("✓", 575)]
        tx0, ty0, rowh, colw = 1150, 130, 27, 660
        per_col = (H - ty0 - 60) // rowh - 1
        for k, r in enumerate(rws):
            cx = tx0 + (k // per_col) * colw
            if cx + colw > W + 20:
                break
            if k % per_col == 0:
                for lab, off in cols:
                    d.text((cx + off, ty0), lab, fill="black", font=f_t)
            y = ty0 + rowh * (1 + k % per_col)
            if k % 2 == 0:
                d.rectangle((cx - 4, y - 2, cx + 610, y + rowh - 4), fill="#f2f2f6")
            vals = [r["sample"], r["id"], f"{r['u']:.2f}", f"{r['v']:.2f}", f"{r['X']:.4f}", f"{r['Y']:.4f}", "□"]
            for (lab, off), val in zip(cols, vals):
                d.text((cx + off, y), val, fill="#222", font=f_s)
        p = path if len(groups) == 1 else f"{base}_mount{gi}{ext}"
        img.save(p); out.append(p)
    return out


# ====================================================================== gallery window (resizable)
class Gallery(tk.Toplevel):
    """Images of one position; content rescales with the window. Click = open original."""

    def __init__(self, master, g):
        super().__init__(master)
        self.g = g; self.title(f"{g['sample']}   u={g['u']:.2f}  v={g['v']:.2f}   X={g['X']:.4f}  Y={g['Y']:.4f}")
        self.configure(bg=C_BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{int(sw * 0.75)}x{int(sh * 0.75)}+{int(sw * 0.12)}+{int(sh * 0.08)}")
        self.minsize(400, 300)
        head = ttk.Frame(self, padding=(12, 8)); head.pack(fill="x")
        ttk.Label(head, text=f"{g['sample']} · {len(g['imgs'])} 张", font=f(12, "bold")).pack(side="left")
        ttk.Label(head, text="   单击图片 = 用系统程序打开原图    右键 = 在文件夹中显示    拖动窗口边缘可缩放",
                  foreground=C_SUB).pack(side="left")
        self.cv = tk.Canvas(self, bg=C_BG, highlightthickness=0); self.cv.pack(fill="both", expand=True)
        self.src = []
        for r in g["imgs"]:
            try:
                self.src.append(core.make_thumb(r["path"], GALLERY_CACHE))
            except Exception:
                self.src.append(Image.new("L", (GALLERY_CACHE, GALLERY_CACHE), 128))
        self._job, self._sharp, self._refs = None, None, []
        self.cv.bind("<Configure>", self._schedule)
        self.menu = tk.Menu(self, tearoff=0)

    def _schedule(self, _e=None):
        for j in (self._job, self._sharp):
            if j:
                self.after_cancel(j)
        self._job = self.after(40, lambda: self._layout(Image.NEAREST))
        self._sharp = self.after(350, lambda: self._layout(Image.LANCZOS))  # refine once resizing stops

    def _layout(self, resample=Image.LANCZOS):
        self._job = None
        W, H = self.cv.winfo_width(), self.cv.winfo_height()
        n = len(self.src); cap = 40; gap = 12
        best = None
        for nc in range(1, n + 1):
            nr = math.ceil(n / nc)
            cell = min((W - gap * (nc + 1)) / nc, (H - gap * (nr + 1)) / nr - cap)
            if best is None or cell > best[0]:
                best = (cell, nc, nr)
        cell, nc, nr = best; cell = max(int(cell), 60)
        x0 = (W - (nc * cell + (nc - 1) * gap)) / 2; y0 = (H - (nr * (cell + cap) + (nr - 1) * gap)) / 2
        self.cv.delete("all"); self._refs = []
        for i, (im, r) in enumerate(zip(self.src, self.g["imgs"])):
            rr, cc = divmod(i, nc)
            x = x0 + cc * (cell + gap); y = y0 + rr * (cell + cap + gap)
            ph = ImageTk.PhotoImage(im.resize((cell, cell), resample)); self._refs.append(ph)
            tag = f"im{i}"
            self.cv.create_rectangle(x - 1, y - 1, x + cell + 1, y + cell + 1, outline="#cfd4de", tags=tag)
            self.cv.create_image(x, y, image=ph, anchor="nw", tags=tag)
            self.cv.create_text(x + cell / 2, y + cell + 6, anchor="n", text=f"{core.mag_label(r['mag'])}   {r['name']}",
                                font=f(10), fill=C_TEXT, width=cell)
            self.cv.tag_bind(tag, "<Button-1>", lambda e, p=r["path"]: open_with_system(p))
            self.cv.tag_bind(tag, "<Button-3>", lambda e, p=r["path"]: self._ctx(e, p))
            self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.configure(cursor="hand2"))
            self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.configure(cursor=""))

    def _ctx(self, e, p):
        self.menu.delete(0, "end")
        self.menu.add_command(label="用系统程序打开原图", command=lambda: open_with_system(p))
        self.menu.add_command(label="在文件夹中显示", command=lambda: open_folder_select(p))
        self.menu.tk_popup(e.x_root, e.y_root)


# ====================================================================== browse tab
class BrowseTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=(10, 8)); self.app = app
        self.folder, self.films, self.recs, self.groups, self.plan = None, [], [], [], []
        self._tip, self._tip_refs, self._tip_job = None, [], None
        bar = ttk.Frame(self); bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="选择图像文件夹…", command=self.choose_folder).pack(side="left")
        ttk.Button(bar, text="加载角点 txt…", command=self.load_corners).pack(side="left", padx=6)
        ttk.Button(bar, text="加载规划项目…", command=self.load_plan).pack(side="left")
        ttk.Button(bar, text="刷新", command=self.rescan).pack(side="left", padx=6)
        self.v_mode = tk.StringVar(value="stage")
        ttk.Radiobutton(bar, text="归一化视图", value="norm", variable=self.v_mode, command=self.set_mode).pack(side="right")
        ttk.Radiobutton(bar, text="载物台视图", value="stage", variable=self.v_mode, command=self.set_mode).pack(side="right", padx=6)
        body = ttk.PanedWindow(self, orient="horizontal"); body.pack(fill="both", expand=True)
        lf = ttk.Frame(body); body.add(lf, weight=5)
        self.map = FilmMap(lf); self.map.pack(fill="both", expand=True); self.map.draw_points = self.draw_points
        self.map.empty_text = "选择一个包含 TESCAN .tif 的文件夹\n（文件夹里的角点 txt / 项目 json 会自动读取）"
        rf = ttk.Frame(body, padding=(8, 0, 0, 0)); body.add(rf, weight=2)
        tf, self.tree = make_tree(rf, ("sample", "name", "mag", "u", "v"), (60, 230, 60, 50, 50), ("center", "w", "center", "center", "center"))
        tf.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.tree_open)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.highlight())
        self._photo = {}  # (path, size) -> PhotoImage cache for hover previews

    def status(self, msg):
        self.app.set_status(msg)

    # --- loading
    def choose_folder(self):
        d = filedialog.askdirectory()
        if not d:
            return
        self.folder = d
        if not self.films:
            for fn in os.listdir(d):
                if fn.lower().endswith(".txt"):
                    try:
                        t = open(os.path.join(d, fn), encoding="utf-8", errors="replace").read()
                    except Exception:
                        continue
                    if "top-left" in t.lower():
                        _, films = core.parse_corners_text(t)
                        if films:
                            self.films = films; break
        if not self.plan:
            for fn in os.listdir(d):
                if fn.lower().endswith(".json"):
                    try:
                        _, _, _, pts = core.load_project(os.path.join(d, fn)); self.plan = pts
                    except Exception:
                        pass
        self.rescan()

    def load_corners(self):
        p = filedialog.askopenfilename(filetypes=[("Text / Project", "*.txt *.json")])
        if not p:
            return
        if p.lower().endswith(".json"):
            _, self.films, _, _ = core.load_project(p)
        else:
            _, self.films = core.parse_corners_text(open(p, encoding="utf-8", errors="replace").read())
        self.rescan()

    def load_plan(self):
        p = filedialog.askopenfilename(filetypes=[("Project", "*.json")])
        if p:
            _, films, _, self.plan = core.load_project(p)
            if not self.films:
                self.films = films
            self.rescan()

    def rescan(self):
        self.map.films = self.films
        if not self.folder:
            return self.map.redraw()
        if not self.films:
            self.status("没有找到角点坐标：请点「加载角点 txt…」。")
            return self.map.redraw()
        self.status("正在读取图像元数据…")

        def work():
            recs = core.scan_folder(self.folder, self.films)
            self.after(0, lambda: self._scanned(recs))
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, recs):
        self.recs = recs; self.groups = core.group_positions([r for r in recs if r["sample"]])
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(recs):
            self.tree.insert("", "end", iid=str(i), tags=("odd",) if i % 2 else (),
                             values=(r["sample"] or "?", r["name"], core.mag_label(r["mag"]),
                                     "" if r["u"] is None else f"{r['u']:.2f}", "" if r["v"] is None else f"{r['v']:.2f}"))
        miss = self.missing_plan()
        unk = sum(r["sample"] is None for r in recs)
        rot = sum(abs(r.get("rot") or 0) > 0.01 for r in recs)
        msg = f"{len(recs)} 张图 · {len(self.groups)} 个拍摄位置 · {len(self.films)} 片样品    悬停 = 预览，单击 = 打开"
        if self.plan:
            msg += f"    计划点 {len(self.plan)} 个，未拍 {len(miss)} 个"
        if unk:
            msg += f"    ⚠ {unk} 张未能归属样品"
        if rot:
            msg += f"    ⚠ {rot} 张图样品台有旋转，位置可能不准"
        self.status(msg)
        self.map.redraw()
        threading.Thread(target=self._prefetch, daemon=True).start()

    def _prefetch(self):
        import time
        for r in list(self.recs):
            try:
                core.make_thumb(r["path"], HOVER_CACHE)
            except Exception:
                pass
            time.sleep(0.05)  # yield to the UI thread so the window stays responsive

    def missing_plan(self, tol=0.05):
        return [p for p in self.plan if not any(
            g["sample"] == p["sample"] and abs(g["X"] - p["X"]) < tol and abs(g["Y"] - p["Y"]) < tol for g in self.groups)]

    def set_mode(self):
        self.map.mode = self.v_mode.get(); self.map.redraw()

    # --- drawing
    def draw_points(self):
        m = self.map
        miss = {(p["sample"], p["id"]) for p in self.missing_plan()} if self.plan else set()
        for p in self.plan:
            if not m.film(p["sample"]):
                continue
            x, y = m.to_screen(p["sample"], p["u"], p["v"])
            col = C_MISS if (p["sample"], p["id"]) in miss else "#b4b9c4"
            m.create_rectangle(x - 7, y - 7, x + 7, y + 7, outline=col, width=1.5, dash=(2, 2))
            m.create_text(x + 9, y + 9, text=p["id"].replace("R1-", "").replace("E-", ""), anchor="nw", font=f(7), fill=col)
        self._g_items = {}
        for gi, g in enumerate(self.groups):
            if not m.film(g["sample"]):
                continue
            x, y = m.to_screen(g["sample"], X=g["X"], Y=g["Y"])
            rad = 6 + min(len(g["imgs"]), 5)
            tag = f"g{gi}"
            it = m.create_oval(x - rad, y - rad, x + rad, y + rad, fill=mag_color(g["maxmag"]),
                               outline="white", width=2, tags=(tag,))
            for r in g["imgs"]:
                self._g_items[r["path"]] = it
            m.tag_bind(tag, "<Enter>", lambda e, g=g: self._hover_in(e, g))
            m.tag_bind(tag, "<Leave>", lambda e: self.hide_tip())
            m.tag_bind(tag, "<Button-1>", lambda e, g=g: self.open_group(g))
        if self.films and self.groups:
            items = [("dot", c, f"最高倍率 {lab}") for _, c, lab in MAG_COLORS]
            if self.plan:
                items.append(("dsquare", C_MISS, "计划点（红 = 未拍）"))
            m.legend(items)
        self._hot = set(); self.highlight()

    def highlight(self):
        items = getattr(self, "_g_items", {}); m = self.map
        sel = {items[self.recs[int(i)]["path"]] for i in self.tree.selection()
               if self.recs and self.recs[int(i)]["path"] in items}
        for it in getattr(self, "_hot", set()) | sel:
            hot = it in sel
            m.itemconfigure(it, outline=C_TEXT if hot else "white", width=3 if hot else 2)
        self._hot = sel

    # --- hover preview (large)
    def _hover_in(self, e, g):
        self.map.configure(cursor="hand2")
        if self._tip_job:
            self.after_cancel(self._tip_job)
        self._tip_job = self.after(120, lambda: self.show_tip(e.x_root, e.y_root, g))

    def show_tip(self, xr, yr, g):
        self._tip_job = None
        self._destroy_tip()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        imgs = g["imgs"][:8]; n = len(imgs)
        # the preview must never cover the cursor -> use the wider side of the screen
        avail_w = max(xr, sw - xr) - 48 - 20
        avail_h = sh * 0.92 - 60
        best = None
        for nc in range(1, n + 1):
            nr = math.ceil(n / nc)
            sz = min(avail_w / nc - 8, avail_h / nr - 36, HOVER_CACHE)
            if best is None or sz > best[0]:
                best = (sz, nc)
        size, ncol = max(int(best[0]), 120), best[1]
        tip = tk.Toplevel(self); tip.wm_overrideredirect(True); tip.attributes("-topmost", True)
        fr = tk.Frame(tip, bg="#1f2329", padx=8, pady=8); fr.pack()
        tk.Label(fr, text=f"{g['sample']}    u = {g['u']:.2f}   v = {g['v']:.2f}    X = {g['X']:.4f}   Y = {g['Y']:.4f}    ({len(g['imgs'])} 张)",
                 bg="#1f2329", fg="white", font=f(10, "bold")).grid(row=0, column=0, columnspan=ncol, sticky="w", pady=(0, 6))
        self._tip_refs = []
        for i, r in enumerate(imgs):
            rr, cc = divmod(i, ncol)
            key = (r["path"], size); ph = self._photo.get(key)
            if ph is None:
                try:
                    ph = ImageTk.PhotoImage(core.make_thumb(r["path"], HOVER_CACHE).resize((size, size), Image.BILINEAR))
                    if len(self._photo) > 80:
                        self._photo.clear()
                    self._photo[key] = ph
                except Exception:
                    ph = None
            self._tip_refs.append(ph)
            tk.Label(fr, image=ph, bg="#1f2329", bd=0).grid(row=1 + 2 * rr, column=cc, padx=3)
            tk.Label(fr, text=f"{core.mag_label(r['mag'])}  ·  {r['name']}", bg="#1f2329", fg="#cfd3da",
                     font=f(9), wraplength=size).grid(row=2 + 2 * rr, column=cc, pady=(2, 6))
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = xr + 28 if (sw - xr) >= xr else xr - 28 - tw   # open on the wider side, never under the cursor
        x = max(4, min(x, sw - tw - 4))
        y = max(4, min(yr - th // 2, sh - th - 40))
        tip.geometry(f"+{x}+{y}"); self._tip = tip

    def _destroy_tip(self):
        if self._tip:
            self._tip.destroy(); self._tip = None

    def hide_tip(self):
        self.map.configure(cursor="")
        if self._tip_job:
            self.after_cancel(self._tip_job); self._tip_job = None
        self._destroy_tip()

    def open_group(self, g):
        self.hide_tip()
        if len(g["imgs"]) == 1:
            return open_with_system(g["imgs"][0]["path"])
        Gallery(self, g)

    def tree_open(self, _e):
        s = self.tree.selection()
        if s:
            open_with_system(self.recs[int(s[0])]["path"])


# ====================================================================== app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(int(sw * 0.85), 1700)}x{min(int(sh * 0.85), 1000)}")
        self.minsize(900, 600)
        if os.environ.get("SEM_SAMPLER_SCALE"):  # testing hook: emulate Windows display scaling
            self.tk.call("tk", "scaling", float(os.environ["SEM_SAMPLER_SCALE"]))
        self._style()
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.plan_tab = PlanTab(nb, self); self.browse_tab = BrowseTab(nb, self)
        nb.add(self.plan_tab, text="  ① 取点规划  "); nb.add(self.browse_tab, text="  ② 图像浏览  ")
        sb = ttk.Frame(self, padding=(12, 6)); sb.pack(fill="x")
        self.status_lbl = ttk.Label(sb, text="就绪", foreground=C_SUB); self.status_lbl.pack(side="left")

    def set_status(self, msg):
        self.status_lbl.config(text=msg)

    def _style(self):
        try:  # native Windows controls: lightest and smoothest
            ttk.Style(self).theme_use("vista" if IS_WIN else "clam")
        except Exception:
            pass
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=10)
            except Exception:
                pass
        st = ttk.Style(self)
        body = tkfont.Font(family=UI_FONT, size=10)
        line = body.metrics("linespace")   # already DPI-scaled -> correct row height on any screen
        st.configure("Treeview", font=(UI_FONT, 10), rowheight=int(line * 1.7))
        st.configure("Treeview.Heading", font=(UI_FONT, 10, "bold"))
        for s in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TLabelframe.Label", "TEntry", "TSpinbox"):
            st.configure(s, font=(UI_FONT, 10))
        st.configure("TLabelframe.Label", font=(UI_FONT, 10, "bold"))
        st.configure("TNotebook.Tab", font=(UI_FONT, 10, "bold"))


if __name__ == "__main__":
    if IS_WIN:
        try:
            import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp on high-DPI screens
        except Exception:
            pass
    App().mainloop()
