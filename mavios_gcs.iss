; MAVİOS-GCS Windows Setup Script
; Inno Setup 6 — Kullanım: ISCC.exe mavios_gcs.iss

#define AppName    "MAVIOS-GCS"
#define AppVersion "2.0"
#define AppPublisher "MAVIOS"
#define AppURL     "https://mavios.local"
#define ExeName    "MAVIOS-GCS.exe"
#define SrcDir     "dist\MAVIOS-GCS"

[Setup]
AppId={{7B3F2A1C-9E4D-4F8B-A2C6-1D5E8F3B7A90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Çıktı klasörü ve dosya adı
OutputDir=installer_output
OutputBaseFilename=MAVIOS-GCS-v{#AppVersion}-Setup
; Sıkıştırma
Compression=lzma2/max
SolidCompression=yes
; Minimum Windows 10
MinVersion=10.0
; 64-bit kurulum
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Kurulum dili Türkçe yoksa İngilizce
WizardStyle=modern
; Yönetici hakları gerektir
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Uninstall
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName} {#AppVersion}

[Languages]
Name: "turkish";  MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";     Description: "Masaüstüne kısayol oluştur";     GroupDescription: "Ek görevler:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "Görev çubuğuna sabitle (kısayol)"; GroupDescription: "Ek görevler:"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
; Tüm PyInstaller çıktısını kopyala
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Başlat menüsü
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{group}\{#AppName} Kaldır"; Filename: "{uninstallexe}"
; Masaüstü
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
; Kurulum sonunda başlatma seçeneği
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
