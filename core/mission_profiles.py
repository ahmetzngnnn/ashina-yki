"""
mission_profiles.py — Görev Parametre Profilleri (kayıtlı ön ayarlar)

Operatör, bir görev sekmesindeki tüm parametreleri (irtifa, hız, kamera,
tarama davranışı, servo kanalları ...) isimlendirilmiş bir profil olarak
kaydeder; sonraki uçuşta profili seçince hepsi tek hamlede geri yüklenir.
Bir profil "varsayılan" işaretlenirse o sekme açıldığında OTOMATİK uygulanır.

Depolama: <app_data>/mission_profiles.json
    {
      "TargetDropMissionTab": {
        "default": "Saha - Rüzgarlı",
        "profiles": {
          "Saha - Rüzgarlı": {"_scan_alt": 18.0, "_scan_speed": 4.0, ...}
        }
      }
    }

Sekme başına ayrı alan tutulur (tab_key = sekme sınıfının adı): Görev 1 ile
Görev 2'nin parametre kümeleri farklıdır, karışmamalı.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.apppaths import app_data_dir

logger = logging.getLogger(__name__)

_FILE = app_data_dir() / "mission_profiles.json"

# Profil adı sınırları — dosya JSON olduğu için ad serbest metin olabilir,
# ama boş/aşırı uzun ad arayüzde kullanılamaz hale gelir.
MAX_NAME_LEN = 60


def _read() -> dict:
    if not _FILE.exists():
        return {}
    try:
        with open(_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("Görev profilleri okunamadı (%s): %s", _FILE, exc)
        return {}


def _write(data: dict) -> bool:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        # Önce geçici dosyaya yaz, sonra taşı: yazma ortasında çökme
        # olursa mevcut profiller bozulmasın.
        tmp = _FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(_FILE)
        return True
    except OSError as exc:
        logger.error("Görev profilleri kaydedilemedi (%s): %s", _FILE, exc)
        return False


def _section(data: dict, tab_key: str) -> dict:
    sec = data.get(tab_key)
    if not isinstance(sec, dict):
        sec = {}
        data[tab_key] = sec
    if not isinstance(sec.get("profiles"), dict):
        sec["profiles"] = {}
    return sec


# ── Genel API ────────────────────────────────────────────────────────────────
def list_profiles(tab_key: str) -> list[str]:
    """Sekmenin kayıtlı profil adları (alfabetik)."""
    sec = _read().get(tab_key) or {}
    profiles = sec.get("profiles") or {}
    return sorted(profiles.keys(), key=str.casefold)


def get_profile(tab_key: str, name: str) -> dict[str, Any] | None:
    sec = _read().get(tab_key) or {}
    profiles = sec.get("profiles") or {}
    values = profiles.get(name)
    return dict(values) if isinstance(values, dict) else None


def save_profile(tab_key: str, name: str, values: dict[str, Any]) -> bool:
    """Profili oluşturur ya da üzerine yazar."""
    name = (name or "").strip()[:MAX_NAME_LEN]
    if not name:
        return False
    data = _read()
    sec = _section(data, tab_key)
    sec["profiles"][name] = dict(values)
    ok = _write(data)
    if ok:
        logger.info("Görev profili kaydedildi: %s / %s (%d parametre)",
                    tab_key, name, len(values))
    return ok


def delete_profile(tab_key: str, name: str) -> bool:
    data = _read()
    sec = _section(data, tab_key)
    if name not in sec["profiles"]:
        return False
    del sec["profiles"][name]
    # Silinen profil varsayılansa varsayılanlığı da kaldır — aksi halde
    # sekme açılışında var olmayan profili yüklemeye çalışır.
    if sec.get("default") == name:
        sec.pop("default", None)
    return _write(data)


def rename_profile(tab_key: str, old: str, new: str) -> bool:
    new = (new or "").strip()[:MAX_NAME_LEN]
    if not new:
        return False
    data = _read()
    sec = _section(data, tab_key)
    if old not in sec["profiles"]:
        return False
    sec["profiles"][new] = sec["profiles"].pop(old)
    if sec.get("default") == old:
        sec["default"] = new
    return _write(data)


def get_default(tab_key: str) -> str | None:
    """Sekme açılışında otomatik uygulanacak profil adı (yoksa None)."""
    sec = _read().get(tab_key) or {}
    name = sec.get("default")
    profiles = sec.get("profiles") or {}
    return name if isinstance(name, str) and name in profiles else None


def set_default(tab_key: str, name: str | None) -> bool:
    """Varsayılan profili işaretler; name=None varsayılanı kaldırır."""
    data = _read()
    sec = _section(data, tab_key)
    if name is None:
        sec.pop("default", None)
    else:
        if name not in sec["profiles"]:
            return False
        sec["default"] = name
    return _write(data)


def storage_path() -> str:
    """Arayüzde göstermek için profil dosyasının tam yolu."""
    return str(_FILE)
