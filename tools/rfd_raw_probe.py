#!/usr/bin/env python3
"""
tools/rfd_raw_probe.py — RFD900A portunda TAM olarak ne olduğunu ham bayt
seviyesinde gösterir. AT komut moduna girme denemeleri (birkaç farklı
yöntemle) başarısız olduğunda, "hiçbir şey mi gelmiyor" ile "bir şey
geliyor ama beklenmedik" arasındaki farkı ayırt etmek için.

1) Hiçbir şey göndermeden 3 sn dinler (paired radyodan kendiliğinden RF
   trafiği geliyor mu — bu, +++ öncesi/sonrası gereken SESSİZLİĞİ bozar).
2) Düz "ATI\\r\\n" gönderip 1 sn dinler.
3) Klasik "+++" (tek write) dener, 1sn dinler.
4) 50ms aralıklı "+ + +" dener, 1sn dinler.
Her adımda gelen HAM baytları hex + printable-ascii olarak basar.
"""
from __future__ import annotations

import sys
import time

DEFAULT_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"


def hexdump(data: bytes) -> str:
    if not data:
        return "(bos - 0 bayt)"
    h = " ".join(f"{b:02x}" for b in data)
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{len(data)} bayt\n  hex:   {h}\n  ascii: {ascii_}"


def main() -> int:
    import serial
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    print(f"Port: {port}  Baud: {baud}")
    ser = serial.Serial(port, baud, timeout=1.0)
    print(f"DTR={ser.dtr} RTS={ser.rts} CTS={ser.cts} DSR={ser.dsr}")

    print("\n=== 1) Sessizce 3sn dinleme (hiçbir şey göndermeden) ===")
    time.sleep(3.0)
    data = ser.read(4096)
    print(hexdump(data))

    print("\n=== 2) Düz 'ATI\\r\\n' gönder, 1sn dinle ===")
    ser.reset_input_buffer()
    ser.write(b"ATI\r\n")
    time.sleep(1.0)
    print(hexdump(ser.read(4096)))

    print("\n=== 3) Klasik '+++' (tek write, guard 1.1+1.1sn) ===")
    ser.reset_input_buffer()
    time.sleep(1.1)
    ser.write(b"+++")
    time.sleep(1.1)
    print(hexdump(ser.read(4096)))

    print("\n=== 4) 50ms aralıklı '+' '+' '+' (guard 1.1+1.1sn) ===")
    ser.reset_input_buffer()
    time.sleep(1.1)
    ser.write(b"+")
    time.sleep(0.05)
    ser.write(b"+")
    time.sleep(0.05)
    ser.write(b"+")
    time.sleep(1.1)
    print(hexdump(ser.read(4096)))

    print("\n=== 5) Karışık deneme: 200ms guard + 20ms aralık (bazı eski firmware) ===")
    ser.reset_input_buffer()
    time.sleep(0.3)
    ser.write(b"+")
    time.sleep(0.02)
    ser.write(b"+")
    time.sleep(0.02)
    ser.write(b"+")
    time.sleep(0.3)
    print(hexdump(ser.read(4096)))

    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
