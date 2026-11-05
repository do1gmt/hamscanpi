"""Unabhängiger Leser für WSJT-X-UDP-Pakete (NetworkMessage.hpp, QDataStream big-endian) – für den Selbsttest
der Netzwerk-Ausgabe (bin/wsjtx_udp.py): liest Heartbeat, Status und Decode Feld für Feld zurück."""
import struct
class R:
    def __init__(s, b): s.buf, s.i = b, 0
    def u32(s): v = struct.unpack_from(">I", s.buf, s.i)[0]; s.i += 4; return v
    def i32(s): v = struct.unpack_from(">i", s.buf, s.i)[0]; s.i += 4; return v
    def u64(s): v = struct.unpack_from(">Q", s.buf, s.i)[0]; s.i += 8; return v
    def f64(s): v = struct.unpack_from(">d", s.buf, s.i)[0]; s.i += 8; return v
    def u8(s): v = s.buf[s.i]; s.i += 1; return v
    def bo(s): return bool(s.u8())
    def st(s):
        n = s.u32()
        if n == 0xFFFFFFFF: return None
        v = s.buf[s.i:s.i+n].decode(); s.i += n; return v
def read(pkt):
    r = R(pkt); magic, schema, typ, ident = r.u32(), r.u32(), r.u32(), r.st()
    out = dict(magic=hex(magic), schema=schema, typ=typ, id=ident)
    if typ == 0: out.update(maxschema=r.u32(), version=r.st(), rev=r.st())
    if typ == 1:
        out.update(dial=r.u64(), mode=r.st(), dxcall=r.st(), report=r.st(), txmode=r.st(), txen=r.bo(), tx=r.bo(), dec=r.bo(),
                   rxdf=r.u32(), txdf=r.u32(), decall=r.st(), degrid=r.st(), dxgrid=r.st(), wd=r.bo(), sub=r.st(), fast=r.bo(),
                   special=r.u8(), tol=r.u32(), trp=r.u32(), cfg=r.st(), txmsg=r.st())
    if typ == 2:
        out.update(new=r.bo(), ms=r.u32(), snr=r.i32(), dt=r.f64(), df=r.u32(), mode=r.st(), msg=r.st(), lowconf=r.bo(), offair=r.bo())
    out["rest"] = len(pkt) - r.i
    return out
