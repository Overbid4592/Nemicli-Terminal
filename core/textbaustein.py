"""Fester Textbaustein vor jeder Anleitung (gepackt)."""
from __future__ import annotations

import base64
import functools
import hashlib
import zlib

_D = (
    "c-nPTOK#jq4Bh7xTuv~W2C@sBm17%DFtK4c2n=LbYEcr?CCaEzH`1%zAy@Djvg(~~l9S}Iq;Ah-Fe|C5SbUG~k#!xiHqoU${rvWw"
    "I8aR7Cz=j5{+;(coGpun2fF6SX<z4ck7cCWoc0`Ig@q_W?y#7CsF!uWVM&D4^o1Looa>GyX6;rlUR=8PKZ(e5m*_gM?pZ7Ph&&wn"
    "V<uow;P}X!v>m}L(v2i+IyHorV=6WD&L^e@cgZP5W46HYimoCJZjA!Vxr+T}O&oU9A+*vJpe+aQ6ihbda?Oos2kz02k?C`eNrnOI"
    "Hv|slb`z$<dXr`y{v$(y0k(9E(<{-q?x&};M+z&m<__0U1QOAMpqTg=piD)72X;xek`P_dyF84cn$w6%5<BC<c*ecxU2q#@VG99>"
    "YZEKgo~6du<v8;+VpK}DAY_iC)oY^hQGHXp<D#X~4i&Pl=)KP38suB?CcCTY)oc3W?$zr*Us4pa3F74%S$C{T7BjnxQ>LAGUtE%b"
    "?p;6d9AW0+*K)$&5EyOpN#VovWaic`oY7^bb~^MS9(@T6@^L3kuyW}g`oI@St<uU|bn0D1ydfXGZf3_~nht6I&m$-&+m*rO0hOO%"
    "p1Uv(Ky)h6Xj>7x(%Yumc2v6MK`uVLCml;y+udTs99;dz{#?5tZOb6r(mfJmfGXsGR4{&2(_^&sH^s`is>0t-PgYdv;Km_qI98CO"
    "n$+kimrw0IPw%}=jK)V{UmeGxqDzMXviJwCF)1g^2M(J(=kBko&~c55BWC9VcWAodUy8J206C~$(H+d%vL+dk?>x5~@WkWNEe+ZN"
    "-P-lyE9k`M$v$@H(l#&v&qF8PymB?>L_3V7>3aie9_-Jx7Mqpox#1tf*orSm&o9fHlhVuEQ_kl-46z$vWAVg}3}BH(spmS`Y{MFR"
    "uLS!zJ-w8j)TV!41m20Y!P&CfLnBNeG*~)rfIy+tc-*oFE2rOVvNpKI)6@osNwn?)KyRTQU?APP;<w_n`USFdD)#"
)
_S = "5035838eed6884892ba595c481d9e52d40922961a4b1ecfa51f262d99bb083a7"


@functools.lru_cache(maxsize=1)
def einleitung() -> str:
    """Der Baustein mit Leerzeile dahinter; leer, wenn er nicht mehr zur Prüfsumme passt."""
    try:
        t = zlib.decompress(base64.b85decode("".join(_D))).decode("utf-8")
    except Exception:
        return ""
    return t + "\n\n" if hashlib.sha256(t.encode("utf-8")).hexdigest() == _S else ""
