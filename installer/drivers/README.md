# COM Port Sürücüleri

Bu klasöre aşağıdaki sürücü dosyalarını indirip koyun.
İnno Setup kurulum paketine otomatik olarak dahil edilir.

## Gerekli Dosyalar

| Sürücü | Dosya adı | İndirme Linki |
|--------|-----------|---------------|
| Silicon Labs CP210x | `CP210xVCPInstaller_x64.exe` | https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers |
| WCH CH340 / CH341  | `CH341SER.EXE`               | https://www.wch-ic.com/downloads/CH341SER_ZIP.html |
| FTDI CDM           | `CDM_Setup_x64.exe`          | https://ftdichip.com/drivers/vcp-drivers/ |

## Hangi Sürücüye İhtiyacım Var?

- **CP210x** → Pixhawk, mSERIAL, çoğu modern uçuş kontrolcüsü
- **CH340 / CH341** → Çinli Arduino clone'lar, bazı USB-TTL adaptörler
- **FTDI** → FTDI FT232 çipli adaptörler (genellikle eski cihazlar)

## Sürücü Olmadan Kurulum

Sürücü dosyaları bu klasörde bulunmazsa kurulum devam eder;
sadece sürücü kurma adımları atlanır. Sürücüleri Windows Update
üzerinden veya yukarıdaki linklerden daha sonra kurabilirsiniz.

## Notlar

- Windows 10/11 çoğu sürücüyü otomatik kurar.
- Sürücü zaten kuruluysa yeniden kurulum zarar vermez.
- 64-bit sürücü kullanın (x64).
