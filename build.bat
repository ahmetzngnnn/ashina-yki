@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title MAVİOS-GCS Build

echo.
echo ╔══════════════════════════════════════════════╗
echo ║       MAVİOS-GCS — Windows Build Aracı       ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ── Python yorumlayıcısını bul ─────────────────────────────────────────────
set PYTHON=python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [HATA] 'python' komutu bulunamadı.
    echo        Python 3.10+ kurulu ve PATH'de olduğundan emin olun.
    pause & exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON% --version 2^>^&1') do set PYVER=%%v
echo [OK] %PYVER% bulundu

:: ── Bağımlılıkları kur ────────────────────────────────────────────────────
echo.
echo [1/5] Bağımlılıklar kontrol ediliyor...
%PYTHON% -m pip install --quiet --upgrade pip
%PYTHON% -m pip install --quiet -r requirements.txt
%PYTHON% -m pip install --quiet pyinstaller
if %errorlevel% neq 0 (
    echo [HATA] pip install başarısız oldu.
    pause & exit /b 1
)
echo [OK] Tüm paketler hazır.

:: ── Eski build temizle ────────────────────────────────────────────────────
echo.
echo [2/5] Eski build temizleniyor...
if exist "dist\MAVIOS-GCS"  rmdir /s /q "dist\MAVIOS-GCS"
if exist "build\MAVIOS-GCS" rmdir /s /q "build\MAVIOS-GCS"
echo [OK] Temizlendi.

:: ── PyInstaller ile derle ─────────────────────────────────────────────────
echo.
echo [3/5] PyInstaller çalışıyor (bu birkaç dakika sürebilir)...
%PYTHON% -m PyInstaller mavios_gcs.spec --noconfirm
if %errorlevel% neq 0 (
    echo [HATA] PyInstaller build başarısız oldu.
    echo        build\MAVIOS-GCS\warn-MAVIOS-GCS.txt dosyasını inceleyin.
    pause & exit /b 1
)
echo [OK] dist\MAVIOS-GCS\ klasörü oluşturuldu.

:: ── Test çalıştır ─────────────────────────────────────────────────────────
echo.
echo [4/5] Executable test ediliyor...
if exist "dist\MAVIOS-GCS\MAVIOS-GCS.exe" (
    echo [OK] dist\MAVIOS-GCS\MAVIOS-GCS.exe mevcut.
) else (
    echo [HATA] MAVIOS-GCS.exe bulunamadı!
    pause & exit /b 1
)

:: ── Inno Setup ile kurulum paketi oluştur ────────────────────────────────
echo.
echo [5/5] Installer paketi oluşturuluyor...

:: Drivers klasörü kontrolü
if not exist "installer\drivers" (
    mkdir "installer\drivers"
    echo [BİLGİ] installer\drivers\ klasörü oluşturuldu.
    echo         CP210x, CH340, FTDI sürücü EXE'lerini buraya koyun.
    echo         Şimdi olmadan devam ediliyor...
)

:: Installer çıktı klasörü
if not exist "installer_output" mkdir "installer_output"

:: Inno Setup Compiler yolları
set ISCC=""
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) do (
    if exist %%p ( set ISCC=%%p )
)

if %ISCC% == "" (
    echo [UYARI] Inno Setup 6 bulunamadı.
    echo         https://jrsoftware.org/isdl.php adresinden kurun,
    echo         sonra installer\setup.iss dosyasını elle derleyin.
    echo.
    echo ────────────────────────────────────────────────
    echo  EXE paketi OLMADAN sadece dist\ klasörü hazır.
    echo  Hedef bilgisayara dist\MAVIOS-GCS\ klasörünü
    echo  kopyalayıp MAVIOS-GCS.exe çalıştırabilirsiniz.
    echo ────────────────────────────────────────────────
) else (
    echo [OK] Inno Setup bulundu: %ISCC%
    %ISCC% "installer\setup.iss"
    if %errorlevel% neq 0 (
        echo [HATA] Inno Setup derleme başarısız.
    ) else (
        echo [OK] Installer oluşturuldu: installer_output\
        dir "installer_output\*.exe" 2>nul
    )
)

echo.
echo ══════════════════════════════════════════════════
echo  Build tamamlandı!
echo  Çalıştırılabilir: dist\MAVIOS-GCS\MAVIOS-GCS.exe
if not %ISCC% == "" echo  Installer:        installer_output\MAVIOS-GCS-Setup-v2.0.exe
echo ══════════════════════════════════════════════════
echo.
pause
