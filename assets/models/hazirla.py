#!/usr/bin/env python3
"""
assets/models/hazirla.py — STEP → görüntülenebilir GLB hazırlayıcı

SolidWorks'ten çıkan IHA.STEP dosyasını YKİ'nin 3B görünümünde
kullanılabilecek hale getirir:

  1. STEP → GLB dönüşümü (cascadio / OCCT — renk ve montaj yapısını okur)
  2. GÖRÜNMEYEN İÇ PARÇALARI ATAR (lehim pedleri, konnektör içleri,
     rulmanlar...). Bunlar üçgenlerin beşte birini yerken ekranda
     görünmezler; RPi4'te WebGL'i boşuna yorarlar.
  3. PARÇA ADINA GÖRE RENK ATAR.

NEDEN RENK ATANIYOR: Bu STEP dosyasında 663 renk tanımı var ama hepsi AYNI
— SolidWorks'ün varsayılan açık gri/lavanta rengi (0.79, 0.82, 0.93).
Yani modelde parça bazlı görünüm (appearance) hiç atanmamış. "Orijinal
renkleriyle" göstermek bu dosyadan mümkün değil; en iyi yaklaşım parça
ADINDAN anlamlı renk türetmektir. Gerçek renkler isteniyorsa SolidWorks'te
parçalara appearance atanıp yeniden dışa aktarılmalıdır.

Kullanım:
    python3 assets/models/hazirla.py ~/İndirilenler/IHA.STEP
    → assets/models/drone.glb
"""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

# ── Parça adı → renk (sRGB 0-1) ─────────────────────────────────────────────
# Sıra ÖNEMLİ: ilk eşleşen kural kazanır (özelden genele).
RENK_KURALLARI: list = [
    (r"prop|pervane",              (0.86, 0.26, 0.22), "Pervane"),
    (r"mn5006|motor",              (0.15, 0.15, 0.17), "Motor"),
    (r"esc|xrotor",                (0.20, 0.22, 0.26), "ESC"),
    (r"raspberry|rpi4|pcb|board",  (0.05, 0.42, 0.28), "Kart (PCB)"),
    (r"batarya|battery|lipo",      (0.55, 0.20, 0.60), "Batarya"),
    (r"kamera|camera|imx",         (0.12, 0.12, 0.14), "Kamera"),
    (r"gorev|görev|mekanizma|servo|sg90", (0.90, 0.62, 0.15), "Görev mekanizması"),
    (r"kapak|cover|yan-kapak",     (0.30, 0.34, 0.40), "Kapak"),
    (r"center_top|center_bot|govde|gövde|frame|sasi|şasi",
                                   (0.22, 0.25, 0.30), "Gövde"),
    (r"tup|tüp|kol|arm",           (0.18, 0.20, 0.24), "Kol"),
    (r"rfd|anten|gps|telemetri",   (0.20, 0.55, 0.75), "Telemetri/GPS"),
    (r"titre|damper|sönüm|sonum",  (0.75, 0.35, 0.30), "Titreşim sönümleyici"),
    (r"xt60|konnekt|connector|kablo|cable", (0.70, 0.55, 0.15), "Bağlantı"),
]
VARSAYILAN_RENK = (0.45, 0.48, 0.55)

# Ekranda görünmeyen ama üçgen yiyen parçalar — atılır.
AT_DESENI = re.compile(
    r"solder\s*pad|silkscreen|skf_|rulman|bearing|"
    r"usb\d|usb\s|hdmi|ethernet|jack|micro\s*hdmi|"
    r"gh[sm]-tb|clik-mate|b\d+b-zr|zx62|pin\s*header",
    re.IGNORECASE)


def glb_oku(yol: Path):
    ham = yol.read_bytes()
    sihir, surum, toplam = struct.unpack_from("<III", ham, 0)
    if sihir != 0x46546C67:
        raise ValueError("GLB dosyası değil")
    ofs = 12
    js = None
    bin_ = b""
    while ofs < toplam:
        uz, tur = struct.unpack_from("<II", ham, ofs)
        veri = ham[ofs + 8: ofs + 8 + uz]
        if tur == 0x4E4F534A:
            js = json.loads(veri.decode("utf-8"))
        elif tur == 0x004E4942:
            bin_ = veri
        ofs += 8 + uz + ((4 - uz % 4) % 4 if uz % 4 else 0)
    return js, bin_


def glb_yaz(yol: Path, js: dict, bin_: bytes) -> None:
    jb = json.dumps(js, separators=(",", ":")).encode("utf-8")
    jb += b" " * ((4 - len(jb) % 4) % 4)
    bb = bin_ + b"\0" * ((4 - len(bin_) % 4) % 4)
    toplam = 12 + 8 + len(jb) + 8 + len(bb)
    with open(yol, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, toplam))
        f.write(struct.pack("<II", len(jb), 0x4E4F534A)); f.write(jb)
        f.write(struct.pack("<II", len(bb), 0x004E4942)); f.write(bb)


def renk_bul(ad: str):
    d = ad.lower()
    for desen, renk, etiket in RENK_KURALLARI:
        if re.search(desen, d):
            return renk, etiket
    return VARSAYILAN_RENK, "Diğer"


def yonelim_duzelt(js: dict, yaw_deg: float) -> None:
    """Modelin burnunu YKİ kuralına (-Z ileri) çevirir.

    NEDEN gerekli: CAD'de aracın ileri yönü tasarımcıya göre değişir; bu
    modelde burun +Z'ye bakıyordu, yani 3B panelde mavi "ileri" oku aracın
    ARKASINI gösteriyordu. Düzeltme MODELE işlenir, görüntüleyiciye değil —
    görüntüleyici genel kalmalı ki başka bir model gelince kural bozulmasın.
    """
    if abs(yaw_deg) < 1e-6:
        return
    import math
    a = math.radians(yaw_deg) / 2.0
    # Y ekseni etrafında dönüş kuaterniyonu [x, y, z, w]
    q = [0.0, math.sin(a), 0.0, math.cos(a)]
    kok = js["scenes"][js.get("scene", 0)]["nodes"]
    for i in kok:
        n = js["nodes"][i]
        if "matrix" in n:
            # Matris varsa kuaterniyonla birleştirmek karmaşık; araya yeni bir
            # düğüm koymak daha güvenli.
            yeni = {"name": "yonelim_duzeltme", "rotation": q, "children": [i]}
            js["nodes"].append(yeni)
            js["scenes"][js.get("scene", 0)]["nodes"] = [len(js["nodes"]) - 1]
            return
        n["rotation"] = q


def isle(giris: Path, cikis: Path, tol: float = 3.0, yaw_duzelt: float = 180.0) -> None:
    ara = cikis.with_suffix(".ham.glb")
    if giris.suffix.lower() in (".step", ".stp"):
        import cascadio
        print(f"[1/3] STEP → GLB (tolerans {tol} mm)…")
        cascadio.step_to_glb(str(giris), str(ara), tol_linear=tol,
                             tol_angular=0.8, merge_primitives=True,
                             use_parallel=True)
    else:
        ara = giris

    js, bin_ = glb_oku(ara)
    acc = js["accessors"]

    def ucgen(mesh) -> int:
        return sum(acc[p["indices"]]["count"] // 3
                   for p in mesh.get("primitives", []) if p.get("indices") is not None)

    # ── 2) Görünmeyen parçaları at ──────────────────────────────────────
    print("[2/3] Görünmeyen iç parçalar ayıklanıyor…")
    onceki = sum(ucgen(m) for m in js["meshes"])
    atilan = set()
    for i, m in enumerate(js["meshes"]):
        if AT_DESENI.search(m.get("name") or ""):
            atilan.add(i)
    # Atılan mesh'e işaret eden düğümlerden mesh referansını kaldır
    for n in js.get("nodes", []):
        if n.get("mesh") in atilan:
            n.pop("mesh", None)
    kalan = sum(ucgen(m) for i, m in enumerate(js["meshes"]) if i not in atilan)
    print(f"      {len(atilan)} parça atıldı · üçgen {onceki:,} → {kalan:,}")

    # ── 3) Parça adına göre renk ata ────────────────────────────────────
    print("[3/3] Renkler atanıyor…")
    js["materials"] = []
    mat_index: dict = {}
    sayim: dict = {}
    for i, m in enumerate(js["meshes"]):
        if i in atilan:
            continue
        ad = (m.get("name") or "").replace(".step", "").replace("_Varsayılan", "")
        renk, etiket = renk_bul(ad)
        if etiket not in mat_index:
            mat_index[etiket] = len(js["materials"])
            js["materials"].append({
                "name": etiket,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [renk[0], renk[1], renk[2], 1.0],
                    "metallicFactor": 0.25, "roughnessFactor": 0.65,
                },
                "doubleSided": True,
            })
        for p in m.get("primitives", []):
            p["material"] = mat_index[etiket]
        sayim[etiket] = sayim.get(etiket, 0) + ucgen(m)

    if yaw_duzelt:
        yonelim_duzelt(js, yaw_duzelt)
        print(f"      yönelim düzeltmesi: {yaw_duzelt:.0f}° (burun -Z'ye)")

    glb_yaz(cikis, js, bin_)
    print(f"\n✓ {cikis}  ({cikis.stat().st_size/1048576:.1f} MB)")
    print("  renk grupları (üçgen):")
    for etiket, t in sorted(sayim.items(), key=lambda x: -x[1]):
        print(f"    {t:>8,}  {etiket}")
    if ara != giris and ara.exists():
        ara.unlink()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    kaynak = Path(sys.argv[1]).expanduser()
    hedef = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "drone.glb"
    tol = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    # 4. arguman: yonelim duzeltmesi (derece, Y ekseni). Bu modelde burun
    # +Z'ye baktigi icin varsayilan 180.
    yaw = float(sys.argv[4]) if len(sys.argv) > 4 else 180.0
    isle(kaynak, hedef, tol, yaw)
