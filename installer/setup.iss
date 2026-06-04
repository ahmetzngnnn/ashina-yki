; MAVİOS-GCS Windows Installer
; Inno Setup 6.x ile derleyin: https://jrsoftware.org/isinfo.php
; Build sonrası installer_output\ klasöründe MAVIOS-GCS-Setup-v2.0.exe oluşur.

#define AppName      "MAV\u0130OS GCS"
#define AppVersion   "2.0"
#define AppPublisher "MAV\u0130OS"
#define AppExe       "MAVIOS-GCS.exe"
#define AppId        "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://mavios.local
AppSupportURL=https://mavios.local/support
AppUpdatesURL=https://mavios.local/updates
DefaultDirName={autopf}\MAVIOS-GCS
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Kurulum paketinin çıkacağı yer (proje kökünden bir üst dizin önerilidir)
OutputDir=..\installer_output
OutputBaseFilename=MAVIOS-GCS-Setup-v{#AppVersion}
; Sıkıştırma (en iyi boyut için lzma2/ultra64)
Compression=lzma2/ultra64
SolidCompression=yes
; Yalnızca 64-bit Windows 10+
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
WizardStyle=modern
; UAC — sürücü kurulumu için yönetici yetkisi gerekir
PrivilegesRequired=admin
; Kaldırıcı
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; Dil
ShowLanguageDialog=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon";   Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek görevler:"; Flags: checkedonce
Name: "startmenuicon"; Description: "Başlat menüsüne ekle";       GroupDescription: "Ek görevler:"; Flags: checkedonce
Name: "driver_cp210x"; Description: "Silicon Labs CP210x USB-Seri sürücüsü kur (önerilen)";  GroupDescription: "COM Port Sürücüleri:"; Flags: unchecked
Name: "driver_ch340";  Description: "WCH CH340/CH341 USB-Seri sürücüsü kur";                 GroupDescription: "COM Port Sürücüleri:"; Flags: unchecked
Name: "driver_ftdi";   Description: "FTDI USB-Seri sürücüsü kur";                            GroupDescription: "COM Port Sürücüleri:"; Flags: unchecked

[Files]
; ── Ana uygulama (PyInstaller çıktısı) ───────────────────────────────────────
Source: "..\dist\MAVIOS-GCS\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ── COM Port sürücüleri (drivers\ klasörüne önceden koyun) ────────────────────
; Silicon Labs CP210x — indirme: https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
Source: "drivers\CP210xVCPInstaller_x64.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: NeedsDriver('CP210x')
; WCH CH340/CH341 — indirme: https://www.wch-ic.com/downloads/CH341SER_ZIP.html
Source: "drivers\CH341SER.EXE"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: NeedsDriver('CH340')
; FTDI CDM — indirme: https://ftdichip.com/drivers/vcp-drivers/
Source: "drivers\CDM_Setup_x64.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: NeedsDriver('FTDI')

[Dirs]
; Uygulama veri dizini (SQLite veritabanı buraya yazar)
Name: "{userappdata}\MAVIOS-GCS"; Permissions: everyone-full

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExe}"; Tasks: startmenuicon
Name: "{group}\Kaldır {#AppName}";    Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";   Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; CP210x sürücüsü
Filename: "{tmp}\CP210xVCPInstaller_x64.exe"; \
    Parameters: "/S"; \
    StatusMsg: "CP210x USB-Seri sürücüsü kuruluyor..."; \
    Flags: waituntilterminated runhidden; \
    Check: FileExists(ExpandConstant('{tmp}\CP210xVCPInstaller_x64.exe'))
; CH340 sürücüsü
Filename: "{tmp}\CH341SER.EXE"; \
    Parameters: "/S"; \
    StatusMsg: "CH340 USB-Seri sürücüsü kuruluyor..."; \
    Flags: waituntilterminated runhidden; \
    Check: FileExists(ExpandConstant('{tmp}\CH341SER.EXE'))
; FTDI sürücüsü
Filename: "{tmp}\CDM_Setup_x64.exe"; \
    Parameters: "/S"; \
    StatusMsg: "FTDI USB-Seri sürücüsü kuruluyor..."; \
    Flags: waituntilterminated runhidden; \
    Check: FileExists(ExpandConstant('{tmp}\CDM_Setup_x64.exe'))
; Kurulum sonrası uygulamayı başlat (isteğe bağlı)
Filename: "{app}\{#AppExe}"; \
    Description: "{#AppName} uygulamasını başlat"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uygulama veri klasörünü kaldırırken sil (isteğe bağlı)
Type: filesandordirs; Name: "{userappdata}\MAVIOS-GCS"

[Code]
{ Sürücü dosyası varsa ve görev seçildiyse kurulum yapar }
function NeedsDriver(DriverName: string): Boolean;
begin
  if DriverName = 'CP210x' then
    Result := IsTaskSelected('driver_cp210x')
  else if DriverName = 'CH340' then
    Result := IsTaskSelected('driver_ch340')
  else if DriverName = 'FTDI' then
    Result := IsTaskSelected('driver_ftdi')
  else
    Result := False;
end;

{ Kurulum öncesi mesaj }
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
