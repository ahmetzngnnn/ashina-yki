"""
ui/widgets/model3d_view.py — 3B Araç Görüntüleyici (kalibrasyon animasyonu)
============================================================================

Kalibrasyon sırasında aracın HANGİ POZİSYONA getirilmesi gerektiğini üç
boyutlu, dönen bir modelle gösterir. Uçuş kontrol kartı "aracı sol yanına
yatırın" dediğinde model gerçekten o pozisyona döner.

NEDEN QWebEngine + three.js:
  Bu projede zaten üç harita QWebEngine ile çalışıyor, yani altyapı kurulu
  ve çevrimdışı gömme yöntemi (bkz. ui/map_assets.py) kanıtlanmış.
  Alternatifler elenmiştir: `pyqtgraph.opengl` PyOpenGL ister (kurulu değil),
  Qt3D ayrı bir paket (PyQt6-3D, kurulu değil). three.js ile ek Python
  bağımlılığı GEREKMEZ.

MODEL FORMATI
  Öncelik: assets/models/drone.glb  (glTF ikili)
    → glTF renkleri ve malzemeleri TAŞIR; STEP'ten FreeCAD ile dışa
      aktarılır. Model yoksa yordamsal (proceduraI) bir yer tutucu quad
      çizilir — böylece boru hattı model gelmeden de çalışır ve test edilir.
  STL BİLİNÇLİ OLARAK TERCİH EDİLMEDİ: STL yalnızca üçgen taşır, RENK
  TAŞIMAZ; "orijinal renkleriyle" göstermek STL ile mümkün değildir.

Kullanım:
    v = Model3DView()
    v.show_pose("sol")          # ivmeölçer pozisyonu
    v.set_attitude(roll, pitch, yaw)   # canlı telemetriden
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    _WEB = True
except ImportError:  # WebEngine kurulu değilse panel çökmemeli
    _WEB = False

_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
_VENDOR = _ASSETS / "vendor" / "three"
_MODELS = _ASSETS / "models"

# İvmeölçer kalibrasyon pozisyonları — ArduPilot'un istediği sıra.
# (roll, pitch, yaw) derece cinsinden hedef yönelim.
POZLAR: dict = {
    "duz":        (0.0,    0.0,  0.0),
    "sol":        (-90.0,  0.0,  0.0),
    "sag":        (90.0,   0.0,  0.0),
    "burun_asagi": (0.0, -90.0,  0.0),
    "burun_yukari": (0.0, 90.0,  0.0),
    "sirt":       (180.0,  0.0,  0.0),
}
POZ_ADI: dict = {
    "duz": "Düz (yatay) konumda",
    "sol": "SOL yanına yatırın",
    "sag": "SAĞ yanına yatırın",
    "burun_asagi": "Burnu AŞAĞI bakacak şekilde",
    "burun_yukari": "Burnu YUKARI bakacak şekilde",
    "sirt": "SIRT ÜSTÜ (ters) çevirin",
}
# ArduPilot ivmeölçer kalibrasyon adım sırası
POZ_SIRASI = ("duz", "sol", "sag", "burun_asagi", "burun_yukari", "sirt")


def _oku(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def model_var_mi() -> Path | None:
    """assets/models/ altındaki ilk .glb dosyası (yoksa None)."""
    if not _MODELS.is_dir():
        return None
    for ad in sorted(_MODELS.glob("*.glb")):
        return ad
    return None


_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;height:100%;overflow:hidden;background:@@BG@@;}
  #c{width:100%;height:100%;display:block;}
  #bilgi{position:absolute;left:10px;bottom:10px;font:12px "Segoe UI",sans-serif;
         color:@@TXT@@;background:rgba(0,0,0,.45);padding:5px 9px;border-radius:5px;}
  #uyari{position:absolute;left:10px;top:10px;font:11px "Segoe UI",sans-serif;
         color:@@WARN@@;background:rgba(0,0,0,.45);padding:4px 8px;border-radius:5px;}
</style></head><body>
<canvas id="c"></canvas><div id="bilgi">—</div><div id="uyari"></div>
<script>/*@@THREE@@*/</script>
<script>/*@@GLTF@@*/</script>
<script>/*@@ORBIT@@*/</script>
<script>
let sahne, kamera, cizer, model, kontrol;
let hedefQ = new THREE.Quaternion();      // animasyonun gittiği yönelim
const ADIM = 0.08;                        // yumuşak geçiş oranı (slerp)

function eksenOku(uzunluk, renk, yon, etiket){
  // Gövde eksenlerini gösteren ok — operatör "burun" nerede anlasın diye
  const g = new THREE.Group();
  const govde = new THREE.Mesh(
    new THREE.CylinderGeometry(0.012, 0.012, uzunluk, 8),
    new THREE.MeshBasicMaterial({color: renk}));
  govde.position.y = uzunluk/2;
  const uc = new THREE.Mesh(
    new THREE.ConeGeometry(0.035, 0.09, 12),
    new THREE.MeshBasicMaterial({color: renk}));
  uc.position.y = uzunluk;
  g.add(govde); g.add(uc);
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), yon.clone().normalize());
  return g;
}

function yerTutucuDrone(){
  // Model gelmeden önce kullanılan yordamsal quad. Gerçek GLB konunca
  // bu fonksiyon hiç çağrılmaz.
  const g = new THREE.Group();
  const govde = new THREE.Mesh(
    new THREE.BoxGeometry(0.42, 0.10, 0.42),
    new THREE.MeshStandardMaterial({color: 0x2b3440, metalness:.3, roughness:.6}));
  g.add(govde);
  const kolMal = new THREE.MeshStandardMaterial({color: 0x39424f, metalness:.3, roughness:.7});
  const pervaneOn = new THREE.MeshStandardMaterial({color: 0xd94a3d, roughness:.5});
  const pervaneArka = new THREE.MeshStandardMaterial({color: 0x2f8fd0, roughness:.5});
  const R = 0.34;
  [[1,1],[1,-1],[-1,1],[-1,-1]].forEach(([sx,sz],i)=>{
    const kol = new THREE.Mesh(new THREE.BoxGeometry(0.30,0.045,0.045), kolMal);
    kol.position.set(sx*R/2, 0, sz*R/2);
    kol.rotation.y = (sx*sz>0)? -Math.PI/4 : Math.PI/4;
    g.add(kol);
    const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.05,0.05,0.06,16), kolMal);
    motor.position.set(sx*R, 0.05, sz*R); g.add(motor);
    const p = new THREE.Mesh(new THREE.CylinderGeometry(0.17,0.17,0.006,24),
                             (sz<0)? pervaneOn : pervaneArka);
    p.position.set(sx*R, 0.09, sz*R); g.add(p);
  });
  // Burun işareti (ileri yön = -Z)
  const burun = new THREE.Mesh(new THREE.ConeGeometry(0.05,0.14,12),
    new THREE.MeshStandardMaterial({color: 0xf2c14e}));
  burun.rotation.x = -Math.PI/2; burun.position.set(0,0.02,-0.30);
  g.add(burun);
  return g;
}

function webglVar(){
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl') ||
              c.getContext('experimental-webgl'));
  } catch(e){ return false; }
}

function kur(){
  // WebGL yoksa SESSİZCE boş panel bırakma: RPi4'te sürücü/GPU yapılandırması
  // eksikse operatör "3B çalışmıyor" der ama sebebini bilemez. Açıkça yaz.
  if (!webglVar()){
    document.getElementById('uyari').textContent =
      "3B görünüm açılamadı: bu makinede WebGL yok (GPU sürücüsü / yazılım " +
      "hızlandırma kapalı). Kalibrasyon adımları metinle devam eder.";
    document.getElementById('bilgi').textContent = "3B devre dışı";
    window.WEBGL_YOK = true;
    return;
  }
  sahne = new THREE.Scene();
  kamera = new THREE.PerspectiveCamera(42, 1, 0.05, 100);
  kamera.position.set(0.95, 0.75, 1.15);
  const cv = document.getElementById('c');
  cizer = new THREE.WebGLRenderer({canvas: cv, antialias:true, alpha:true});
  cizer.setPixelRatio(Math.min(devicePixelRatio,2));

  sahne.add(new THREE.HemisphereLight(0xffffff, 0x223044, 0.85));
  const d = new THREE.DirectionalLight(0xffffff, 0.9);
  d.position.set(2,3,2); sahne.add(d);

  const izgara = new THREE.GridHelper(2.4, 12, 0x3a4650, 0x2a323d);
  izgara.position.y = -0.25; sahne.add(izgara);

  kontrol = new THREE.OrbitControls(kamera, cv);
  kontrol.enableDamping = true; kontrol.enablePan = false;
  kontrol.minDistance = 0.7; kontrol.maxDistance = 4.0;

  model = new THREE.Group(); sahne.add(model);
  // Gövde eksenleri: kırmızı=sağ(X), yeşil=yukarı(Y), mavi=ileri(-Z)
  model.add(eksenOku(0.34, 0xff5a55, new THREE.Vector3(1,0,0)));
  model.add(eksenOku(0.30, 0x6ee7a0, new THREE.Vector3(0,1,0)));
  model.add(eksenOku(0.40, 0x4aa3e0, new THREE.Vector3(0,0,-1)));

  const yol = window.MODEL_URL || "";
  if (yol) {
    new THREE.GLTFLoader().load(yol, (gltf)=>{
      const o = gltf.scene;
      // Modeli birim küreye ölçekle ve merkeze al — parça boyutu ne olursa
      // olsun kamera ayarı değişmesin.
      const kutu = new THREE.Box3().setFromObject(o);
      const boy = kutu.getSize(new THREE.Vector3()).length();
      const mrk = kutu.getCenter(new THREE.Vector3());
      o.position.sub(mrk);
      const s = 0.75 / (boy || 1); o.scale.setScalar(s);
      model.add(o);
      document.getElementById('uyari').textContent = "";
    }, undefined, (e)=>{
      document.getElementById('uyari').textContent = "Model yüklenemedi — yer tutucu gösteriliyor";
      model.add(yerTutucuDrone());
    });
  } else {
    document.getElementById('uyari').textContent =
      "Yer tutucu model (assets/models/drone.glb koyun)";
    model.add(yerTutucuDrone());
  }
  boyutla(); window.addEventListener('resize', boyutla);
  dongu();
}

function boyutla(){
  const w = window.innerWidth, h = window.innerHeight;
  cizer.setSize(w, h, false);
  kamera.aspect = w/Math.max(1,h); kamera.updateProjectionMatrix();
}

function dongu(){
  requestAnimationFrame(dongu);
  model.quaternion.slerp(hedefQ, ADIM);   // yumuşak geçiş
  kontrol.update();
  cizer.render(sahne, kamera);
}

// ── Python'dan çağrılan API ────────────────────────────────────────────
window.setYonelim = function(roll, pitch, yaw){
  if (window.WEBGL_YOK) return;      // sahne kurulmadı, çağrı yok sayılır
  const e = new THREE.Euler(
    THREE.MathUtils.degToRad(pitch),
    THREE.MathUtils.degToRad(yaw),
    THREE.MathUtils.degToRad(roll), 'YXZ');
  hedefQ.setFromEuler(e);
};
window.setBilgi = function(metin){
  document.getElementById('bilgi').textContent = metin;
};
kur();
</script></body></html>"""


class Model3DView(QWidget):
    """Kalibrasyon pozisyonlarını 3B gösteren görüntüleyici."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._web = None
        self._hazir = False

        if not _WEB:
            uyari = QLabel("3B görünüm için PyQt6-WebEngine gerekli.")
            uyari.setObjectName("mutedLabel")
            uyari.setWordWrap(True)
            lay.addWidget(uyari)
            return

        self._web = QWebEngineView()
        self._web.loadFinished.connect(self._on_load)
        lay.addWidget(self._web)
        self._web.setHtml(self._html(), QUrl("qrc:///"))

    # ── HTML kurulumu ────────────────────────────────────────────────
    def _html(self) -> str:
        from core import theme
        html = _HTML
        # three.js ve eklentileri INLINE gömülür — CDN'e istek gitmez
        # (saha şartı: internet yok). Dosyalar assets/vendor/three/ altında.
        # NEDEN "/*@@...@@*/" gibi tuhaf işaretler: three.min.js kendi içinde
        # `window.__THREE__` global'ini tanımlıyor. Yer tutucu olarak
        # `__THREE__` kullanılırsa kütüphanenin KENDİ metniyle çakışır ve
        # sıralamaya bağlı, teşhisi zor bozulmalar üretir.
        for isaret, dosya in (("/*@@THREE@@*/", "three.min.js"),
                              ("/*@@GLTF@@*/", "GLTFLoader.js"),
                              ("/*@@ORBIT@@*/", "OrbitControls.js")):
            icerik = _oku(_VENDOR / dosya)
            if not icerik:
                logger.warning("three.js parçası bulunamadı: %s — 3B görünüm çalışmaz",
                               dosya)
            # NOT: `str.replace` DÜZ METİN değiştirir, şablon işlemez; bu yüzden
            # küçültülmüş JS'teki ters bölü dizileri sorun çıkarmaz. (Leaflet'te
            # yaşanan "bad escape" hatası `re.sub` kaynaklıydı — orada
            # değiştirici şablon olarak yorumlanıyordu, bkz. ui/map_assets.py.)
            html = html.replace(isaret, icerik)
        model = model_var_mi()
        url_js = ""
        if model is not None:
            try:
                b64 = base64.b64encode(model.read_bytes()).decode("ascii")
                url_js = f'<script>window.MODEL_URL="data:model/gltf-binary;base64,{b64}";</script>'
            except OSError as exc:
                logger.warning("3B model okunamadı: %s", exc)
        html = html.replace("@@BG@@", theme.c("bg"))
        html = html.replace("@@TXT@@", theme.c("text"))
        html = html.replace("@@WARN@@", theme.c("warning"))
        return html.replace("</head>", url_js + "</head>")

    def _on_load(self, ok: bool) -> None:
        self._hazir = bool(ok)

    # ── Python API ───────────────────────────────────────────────────
    def _js(self, kod: str) -> None:
        if self._web is not None:
            self._web.page().runJavaScript(kod)

    def set_attitude(self, roll: float, pitch: float, yaw: float) -> None:
        """Modeli verilen yönelime döndürür (derece)."""
        self._js(f"window.setYonelim && setYonelim({roll:.2f},{pitch:.2f},{yaw:.2f});")

    def show_pose(self, ad: str) -> bool:
        """İvmeölçer kalibrasyon pozisyonunu gösterir ('sol', 'sirt' ...)."""
        if ad not in POZLAR:
            return False
        r, p, y = POZLAR[ad]
        self.set_attitude(r, p, y)
        self.set_info(POZ_ADI.get(ad, ad))
        return True

    def show_step(self, index: int) -> bool:
        """Kalibrasyon adım numarasından pozisyon gösterir (0 tabanlı)."""
        if 0 <= index < len(POZ_SIRASI):
            return self.show_pose(POZ_SIRASI[index])
        return False

    def set_info(self, metin: str) -> None:
        self._js(f"window.setBilgi && setBilgi({json.dumps(metin)});")
