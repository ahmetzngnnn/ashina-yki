#!/usr/bin/env python3
"""SINIF A: ebeveynsiz kurulup HICBIR yere eklenmeyen yerel Qt widget'i.
Bu depoda road_tab ve ai_tab'da IKI KEZ yasandi.

KRITIK INCELIK: `QVBoxLayout(w)` cagrisi w'yi HAYATTA TUTMAZ — layout w'nin
COCUGU olur, tersi degil. Ilk tarayicim bunu "eklendi" sayip bilinen hatayi
KACIRDI; bu surum o bilinen hataya karsi DOGRULANDI."""
import ast, io, os, re, sys

KOK = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAPLAR = {"QWidget","QFrame","QGroupBox","QTabWidget","QScrollArea","QSplitter","QStackedWidget"}
# widget'i GERCEKTEN sahiplendiren cagrilar
# NOT: insertWidget(indeks, w) / insertTab(indeks, w, ...) widget'i IKINCI
# argumanda tasir. Ilk surum yalniz ilk argumana bakiyordu ve iki gercek
# "eklendi" durumunu YANLIS POZITIF olarak isaretledi. Artik cagrinin TUM
# argumanlari taraniyor.
SAHIPLEN_AD = re.compile(r"\b(addWidget|addTab|setWidget|insertWidget|insertTab|"
                         r"setCentralWidget|addPermanentWidget|setTitleBarWidget|"
                         r"setMenuWidget|setViewport|setCornerWidget|setTabWidget)\s*\(")

def _sahiplenenler(govde: str) -> set:
    """Sahiplendiren cagrilarin TUM argumanlarindaki isimleri toplar."""
    out = set()
    for m in SAHIPLEN_AD.finditer(govde):
        i = m.end(); derinlik = 1; j = i
        while j < len(govde) and derinlik:
            if govde[j] == "(": derinlik += 1
            elif govde[j] == ")": derinlik -= 1
            j += 1
        out.update(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", govde[i:j-1]))
    return out
# widget ebeveyni olarak gecirilme (LAYOUT DEGIL — layout sahiplendirmez)
WIDGET_EBEVEYN = re.compile(r"=\s*Q(?!.*Layout)[A-Za-z_]+\s*\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*[,)]")

bulgular = []
for kok, _, dosyalar in os.walk(KOK):
    if "__pycache__" in kok or "/.git" in kok: continue
    for d in sorted(dosyalar):
        if not d.endswith(".py"): continue
        yol = os.path.join(kok, d)
        src = io.open(yol, encoding="utf-8", errors="replace").read()
        try: agac = ast.parse(src)
        except SyntaxError: continue
        satirlar = src.splitlines()
        for fn in [n for n in ast.walk(agac) if isinstance(n, ast.FunctionDef)]:
            govde = "\n".join(satirlar[fn.lineno-1: fn.end_lineno])
            sahiplenen = _sahiplenenler(govde)
            ebeveynlenen = set(m.group(1) for m in WIDGET_EBEVEYN.finditer(govde))
            for n in ast.walk(fn):
                if not isinstance(n, ast.Assign) or len(n.targets) != 1: continue
                t = n.targets[0]
                if not isinstance(t, ast.Name): continue      # self.x ise Python refi yasar
                v = n.value
                if not isinstance(v, ast.Call) or not isinstance(v.func, ast.Name): continue
                if v.func.id not in KAPLAR: continue
                if v.args or v.keywords: continue              # ebeveyn verilmis
                ad = t.id
                if ad in sahiplenen or ad in ebeveynlenen: continue
                if re.search(r"\b%s\s*\.\s*setParent\s*\(" % re.escape(ad), govde): continue
                if re.search(r"\breturn\s+%s\b" % re.escape(ad), govde): continue   # cagirana veriliyor
                bulgular.append((os.path.relpath(yol, KOK), n.lineno, fn.name, ad, v.func.id))

print("== SINIF A: ebeveynsiz + hicbir yere eklenmemis widget ==")
for f, l, fn, ad, tip in bulgular:
    print("  %-32s :%-5d %-20s %s = %s()" % (f, l, fn, ad, tip))
print("  toplam: %d" % len(bulgular))
sys.exit(1 if bulgular else 0)
