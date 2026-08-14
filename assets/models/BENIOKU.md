# 3B Araç Modeli — kalibrasyon animasyonu için

Bu klasöre **`drone.glb`** koyun; kalibrasyon ekranı otomatik olarak onu
kullanır (kod değişikliği gerekmez). Dosya yoksa yordamsal bir yer tutucu
quad çizilir ve panel yine çalışır.

## Neden GLB (glTF ikili)?

| Format | Renk/malzeme | Not |
|---|---|---|
| **GLB** ✔ | **Taşır** | Önerilen. three.js doğrudan açar, tek dosya. |
| STEP (AP214) | Taşır | Python'da okunamaz (OpenCascade gerekir). Önce dönüştürün. |
| STL | **TAŞIMAZ** | Yalnızca üçgen. Tek renk blok görünür. |

STL "orijinal renkleriyle" gösteremez — içinde renk bilgisi yoktur.

## STEP → GLB dönüşümü (FreeCAD, ücretsiz)

1. FreeCAD ile `.step` dosyasını açın.
2. Ağaçtan gereksiz parçaları gizleyin/silin: vidalar, somunlar, pullar,
   iç elektronik. Bunlar üçgen sayısını katlar, görünürlüğe katkısı olmaz.
3. Model ağacındaki tüm nesneleri seçin.
4. **File → Export…** → tür olarak **glTF (*.glb *.gltf)** → `drone.glb`.
5. Dosyayı bu klasöre koyun.

## Boyut hedefi

- **10–15 MB altı**, **200 bin üçgenden az.**
- Model, YKİ'nin koştuğu Raspberry Pi 4'te WebGL ile çizilecek; ağır model
  kalibrasyon ekranını yavaşlatır.
- Çok büyükse FreeCAD'de "Mesh Design → Decimation" ile üçgen azaltın.

## Yönelim (ÖNEMLİ)

Model şu eksen düzeninde olmalı; değilse dönüşler yanlış yöne gider:

- **-Z** → aracın BURNU (ileri)
- **+Y** → YUKARI
- **+X** → SAĞ

FreeCAD'de araç farklı duruyorsa dışa aktarmadan önce döndürün. Panelde
kırmızı/yeşil/mavi eksen okları çizilir; mavi ok burunla aynı yöne
bakmıyorsa yönelim yanlıştır.

## Doğrulama

Kalibrasyon sekmesini açın. Sol üstte "Yer tutucu model…" uyarısı
KAYBOLMUŞSA modeliniz yüklenmiştir. "Model yüklenemedi" yazıyorsa dosya
bozuk ya da desteklenmeyen bir glTF sürümüdür.
