"""
Own CUDA kernels: matrix-vector products directly on GGUF block data for every block format
(legacy Q4/Q5/Q8, K-quants, the IQ family, MXFP4).  Nothing is unpacked, each weight byte is
read once.  Used for one token per step: MoE experts (also from pinned RAM, read directly over
PCIe) and dense layers in formats without the int4 path (IMMER_GEPACKT).

Each format contributes one device function "32 weights of a block times 32 inputs";
one kernel skeleton per format turns it into y[e, row] = W_ids[e][row, :] . x_e, where
x_e is either one input shared by all experts or each expert's own input.

The CUDA source is compiled at runtime with NVRTC, which ships with the PyTorch wheel
(no toolkit, no host compiler), and launched through the driver API on torch's current
stream, so the launches can be captured in CUDA graphs.  Without NVRTC or on errors the
callers fall back to unpacking with torch (quant.py).
"""
from __future__ import annotations

import ctypes
import glob
import os
import threading
from typing import Optional

import torch

from . import debuglog as dbg

THREADS = 128                 # 4 warps per block, one output row per warp
MAX_N_IN = 12288              # input row as float32 in shared memory (48 KiB)

# format -> (values per block, bytes per block)
FORMATE = {
    "Q2_K": (256, 84), "Q3_K": (256, 110), "IQ1_S": (256, 50), "IQ1_M": (256, 56),
    "IQ2_XXS": (256, 66), "IQ2_XS": (256, 74), "IQ2_S": (256, 82), "IQ3_XXS": (256, 98),
    "IQ3_S": (256, 110), "IQ4_NL": (32, 18), "IQ4_XS": (256, 136), "MXFP4": (32, 17),
    "Q4_0": (32, 18), "Q4_1": (32, 20), "Q5_0": (32, 22), "Q5_1": (32, 24), "Q8_0": (32, 34),
    "Q4_K": (256, 144), "Q5_K": (256, 176), "Q6_K": (256, 210),
}
# formats without the int4 path (qlinear): always kept in block format
IMMER_GEPACKT = frozenset(("Q2_K", "Q3_K", "IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ3_XXS",
                           "IQ3_S", "IQ4_NL", "IQ4_XS", "MXFP4"))

_GRUNDLAGEN = r'''
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
__device__ __forceinline__ float h2f(unsigned short u) {
    float f; asm("cvt.f32.f16 %0, %1;" : "=f"(f) : "h"(u)); return f;
}
__device__ __forceinline__ float bf2f(unsigned short u) { return __uint_as_float(((u32)u) << 16); }
// blocks of odd size (MXFP4: 17 bytes) are not aligned: read multi-byte fields bytewise
__device__ __forceinline__ u32 ld16(const u8* p) { return p[0] | ((u32)p[1] << 8); }
__device__ __forceinline__ u32 ld32(const u8* p) { return ld16(p) | (ld16(p + 2) << 16); }
__device__ __forceinline__ u32 ksign7(u32 s) { return s | ((__popc(s) & 1) << 7); }
__device__ __forceinline__ float warp_sum(float s) {
    for (int o = 16; o > 0; o >>= 1) s += __shfl_xor_sync(0xffffffffu, s, o);
    return s;
}
// 8 unsigned grid bytes with signs (bit j of sg set: negative) times x[0..7]
__device__ __forceinline__ float grid8(u64 g, u32 sg, const float* x) {
    float s = 0.f;
    #pragma unroll
    for (int j = 0; j < 8; ++j) {
        const float v = (float)((g >> (8 * j)) & 255);
        s += ((sg >> j) & 1 ? -v : v) * x[j];
    }
    return s;
}
__device__ __forceinline__ float grid4(u32 g, u32 sg, const float* x) {
    float s = 0.f;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const float v = (float)((g >> (8 * j)) & 255);
        s += ((sg >> j) & 1 ? -v : v) * x[j];
    }
    return s;
}
__constant__ float KV_IQ4NL[16] = {-127, -104, -83, -65, -49, -35, -22, -10, 1, 13, 25, 38, 53, 69, 89, 113};
__constant__ float KV_MXFP4[16] = {0, 1, 2, 3, 4, 6, 8, 12, 0, -1, -2, -3, -4, -6, -8, -12};
'''

# dot_<FORMAT>(block, unit s of 32 values in the block, x of these 32 values, value tables)
_FORMATE = r'''
__device__ float dot_Q2_K(const u8* b, int s, const float* x, const float* kv) {
    const float d = h2f(ld16(b + 80)), dmin = h2f(ld16(b + 82));
    const u8* q = b + 16 + (s >> 2) * 32;
    const int sh = 2 * (s & 3);
    float r = 0.f;
    #pragma unroll
    for (int h = 0; h < 2; ++h) {
        const u32 sc = b[2 * s + h];
        float aq = 0.f, ax = 0.f;
        #pragma unroll
        for (int t = 0; t < 16; ++t) {
            aq += (float)((q[16 * h + t] >> sh) & 3) * x[16 * h + t];
            ax += x[16 * h + t];
        }
        r += d * (float)(sc & 15) * aq - dmin * (float)(sc >> 4) * ax;
    }
    return r;
}

__device__ float dot_Q3_K(const u8* b, int s, const float* x, const float* kv) {
    const u8* hm = b;
    const u8* q = b + 32 + (s >> 2) * 32;
    const u8* sc = b + 96;
    const float d = h2f(ld16(b + 108));
    const int sh = 2 * (s & 3);
    float r = 0.f;
    #pragma unroll
    for (int h = 0; h < 2; ++h) {
        const int k = 2 * s + h;
        const int lo = k < 8 ? (sc[k] & 15) : (sc[k - 8] >> 4);
        const int hi = (sc[8 + (k & 3)] >> (2 * (k >> 2))) & 3;
        float a = 0.f;
        #pragma unroll
        for (int t = 0; t < 16; ++t) {
            const int i = 16 * h + t;
            const int v = ((q[i] >> sh) & 3) - (((hm[i] >> s) & 1) ? 0 : 4);
            a += (float)v * x[i];
        }
        r += d * (float)((lo | (hi << 4)) - 32) * a;
    }
    return r;
}

__device__ float dot_IQ2_XXS(const u8* b, int s, const float* x, const float* kv) {
    const u8* q = b + 2 + 8 * s;
    const u32 aux0 = ld32(q), aux1 = ld32(q + 4);
    float r = 0.f;
    #pragma unroll
    for (int l = 0; l < 4; ++l)
        r += grid8(IQ2XXS_GRID[(aux0 >> (8 * l)) & 255], ksign7((aux1 >> (7 * l)) & 127), x + 8 * l);
    return h2f(ld16(b)) * (0.125f + 0.25f * (float)(aux1 >> 28)) * r;
}

__device__ float dot_IQ2_XS(const u8* b, int s, const float* x, const float* kv) {
    const float d = h2f(ld16(b));
    const u32 sc = b[66 + s];
    float r = 0.f;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 q = ld16(b + 2 + 2 * (4 * s + l));
        const float db = d * (0.125f + 0.25f * (float)(l < 2 ? sc & 15 : sc >> 4));
        r += db * grid8(IQ2XS_GRID[q & 511], ksign7(q >> 9), x + 8 * l);
    }
    return r;
}

__device__ float dot_IQ2_S(const u8* b, int s, const float* x, const float* kv) {
    const float d = h2f(ld16(b));
    const u8* qs = b + 2; const u8* sg = b + 34; const u32 qh = b[66 + s], sc = b[74 + s];
    float r = 0.f;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 g = qs[4 * s + l] | ((qh << (8 - 2 * l)) & 0x300);
        const float db = d * (0.125f + 0.25f * (float)(l < 2 ? sc & 15 : sc >> 4));
        r += db * grid8(IQ2S_GRID[g], sg[4 * s + l], x + 8 * l);
    }
    return r;
}

__device__ float dot_IQ3_XXS(const u8* b, int s, const float* x, const float* kv) {
    const u8* qs = b + 2 + 8 * s;
    const u32 aux = ld32(b + 66 + 4 * s);
    float r = 0.f;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 sg = ksign7((aux >> (7 * l)) & 127);
        r += grid4(IQ3XXS_GRID[qs[2 * l]], sg, x + 8 * l) + grid4(IQ3XXS_GRID[qs[2 * l + 1]], sg >> 4, x + 8 * l + 4);
    }
    return h2f(ld16(b)) * (0.25f + 0.5f * (float)(aux >> 28)) * r;
}

__device__ float dot_IQ3_S(const u8* b, int s, const float* x, const float* kv) {
    const u8* qs = b + 2 + 8 * s; const u32 qh = b[66 + s]; const u8* sg = b + 74 + 4 * s;
    const u32 sc = (b[106 + (s >> 1)] >> (4 * (s & 1))) & 15;
    float r = 0.f;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 g1 = qs[2 * l] | (((qh >> (2 * l)) & 1) << 8);
        const u32 g2 = qs[2 * l + 1] | (((qh >> (2 * l + 1)) & 1) << 8);
        r += grid4(IQ3S_GRID[g1], sg[l], x + 8 * l) + grid4(IQ3S_GRID[g2], sg[l] >> 4, x + 8 * l + 4);
    }
    return h2f(ld16(b)) * (float)(1 + 2 * sc) * r;
}

__device__ __forceinline__ float iq1_grid(u64 g, const float* x, float* sx) {
    float s = 0.f, t = 0.f;
    #pragma unroll
    for (int j = 0; j < 8; ++j) {
        s += (float)(signed char)((g >> (8 * j)) & 255) * x[j];
        t += x[j];
    }
    *sx = t;
    return s;
}

__device__ float dot_IQ1_S(const u8* b, int s, const float* x, const float* kv) {
    const u32 qh = ld16(b + 34 + 2 * s);
    const float dl = h2f(ld16(b)) * (float)(2 * ((qh >> 12) & 7) + 1);
    const float delta = (qh & 0x8000) ? -0.125f : 0.125f;
    float r = 0.f, ax = 0.f, t;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        r += iq1_grid(IQ1S_GRID[b[2 + 4 * s + l] | (((qh >> (3 * l)) & 7) << 8)], x + 8 * l, &t);
        ax += t;
    }
    return dl * (r + delta * ax);
}

__device__ float dot_IQ1_M(const u8* b, int s, const float* x, const float* kv) {
    const u32 s0 = ld16(b + 48), s1 = ld16(b + 50), s2 = ld16(b + 52), s3 = ld16(b + 54);
    const u32 dbits = (s0 >> 12) | ((s1 >> 8) & 0xF0) | ((s2 >> 4) & 0xF00) | (s3 & 0xF000);
    const float d = h2f((unsigned short)dbits);
    const u32 sw = ld16(b + 48 + 2 * (s >> 1));
    float r = 0.f, t;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const int g = 4 * s + l, k = 2 * s + (l >> 1);
        const u32 nib = (b[32 + (g >> 1)] >> (4 * (g & 1))) & 15;
        const float dl = d * (float)(2 * ((sw >> (3 * (k & 3))) & 7) + 1);
        const float v = iq1_grid(IQ1S_GRID[b[g] | ((nib & 7) << 8)], x + 8 * l, &t);
        r += dl * (v + ((nib & 8) ? -0.125f : 0.125f) * t);
    }
    return r;
}

__device__ float dot_IQ4_NL(const u8* b, int s, const float* x, const float* kv) {
    const u8* q = b + 2;
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[q[j] & 15] * x[j] + kv[q[j] >> 4] * x[j + 16];
    return h2f(ld16(b)) * r;
}

__device__ float dot_IQ4_XS(const u8* b, int s, const float* x, const float* kv) {
    const u32 sh = ld16(b + 2);
    const int ls = ((b[4 + (s >> 1)] >> (4 * (s & 1))) & 15) | (((sh >> (2 * s)) & 3) << 4);
    const u8* q = b + 8 + 16 * s;
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[q[j] & 15] * x[j] + kv[q[j] >> 4] * x[j + 16];
    return h2f(ld16(b)) * (float)(ls - 32) * r;
}

__device__ float dot_Q8_0(const u8* b, int s, const float* x, const float* kv) {
    const signed char* q = (const signed char*)(b + 2);
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 32; ++j) r += (float)q[j] * x[j];
    return h2f(ld16(b)) * r;
}

__device__ float dot_Q4_0(const u8* b, int s, const float* x, const float* kv) {
    const u8* q = b + 2;
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += (float)((q[j] & 15) - 8) * x[j] + (float)((q[j] >> 4) - 8) * x[j + 16];
    return h2f(ld16(b)) * r;
}

__device__ float dot_Q4_1(const u8* b, int s, const float* x, const float* kv) {
    const u8* q = b + 4;
    float r = 0.f, ax = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) {
        r += (float)(q[j] & 15) * x[j] + (float)(q[j] >> 4) * x[j + 16];
        ax += x[j] + x[j + 16];
    }
    return h2f(ld16(b)) * r + h2f(ld16(b + 2)) * ax;
}

__device__ float dot_Q5_0(const u8* b, int s, const float* x, const float* kv) {
    const u32 qh = ld32(b + 2);
    const u8* q = b + 6;
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) {
        const int lo = (q[j] & 15) | (((qh >> j) & 1) << 4), hi = (q[j] >> 4) | (((qh >> (j + 16)) & 1) << 4);
        r += (float)(lo - 16) * x[j] + (float)(hi - 16) * x[j + 16];
    }
    return h2f(ld16(b)) * r;
}

__device__ float dot_Q5_1(const u8* b, int s, const float* x, const float* kv) {
    const u32 qh = ld32(b + 4);
    const u8* q = b + 8;
    float r = 0.f, ax = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) {
        const int lo = (q[j] & 15) | (((qh >> j) & 1) << 4), hi = (q[j] >> 4) | (((qh >> (j + 16)) & 1) << 4);
        r += (float)lo * x[j] + (float)hi * x[j + 16];
        ax += x[j] + x[j + 16];
    }
    return h2f(ld16(b)) * r + h2f(ld16(b + 2)) * ax;
}

// 6-bit scale and min of sub-block s from the 12 packed bytes (ggml get_scale_min_k4)
__device__ __forceinline__ void skala_k4(const u8* sc, int s, float* d, float* m) {
    if (s < 4) { *d = (float)(sc[s] & 63); *m = (float)(sc[s + 4] & 63); }
    else {
        *d = (float)((sc[s + 4] & 15) | ((sc[s - 4] >> 6) << 4));
        *m = (float)((sc[s + 4] >> 4) | ((sc[s] >> 6) << 4));
    }
}

__device__ float dot_Q4_K(const u8* b, int s, const float* x, const float* kv) {
    float sc, mn;
    skala_k4(b + 4, s, &sc, &mn);
    const u8* q = b + 16 + 32 * (s >> 1);
    const int sh = 4 * (s & 1);
    float r = 0.f, ax = 0.f;
    #pragma unroll
    for (int t = 0; t < 32; ++t) { r += (float)((q[t] >> sh) & 15) * x[t]; ax += x[t]; }
    return h2f(ld16(b)) * sc * r - h2f(ld16(b + 2)) * mn * ax;
}

__device__ float dot_Q5_K(const u8* b, int s, const float* x, const float* kv) {
    float sc, mn;
    skala_k4(b + 4, s, &sc, &mn);
    const u8* qh = b + 16;
    const u8* q = b + 48 + 32 * (s >> 1);
    const int sh = 4 * (s & 1);
    float r = 0.f, ax = 0.f;
    #pragma unroll
    for (int t = 0; t < 32; ++t) {
        r += (float)(((q[t] >> sh) & 15) | (((qh[t] >> s) & 1) << 4)) * x[t];
        ax += x[t];
    }
    return h2f(ld16(b)) * sc * r - h2f(ld16(b + 2)) * mn * ax;
}

__device__ float dot_Q6_K(const u8* b, int s, const float* x, const float* kv) {
    const int n = s >> 2, g = s & 3;
    const u8* ql = b + 64 * n + 32 * (g & 1);
    const u8* qh = b + 128 + 32 * n;
    const signed char* sc = (const signed char*)(b + 192) + 8 * n + 2 * g;
    const int shl = 4 * (g >> 1), shh = 2 * g;
    float r0 = 0.f, r1 = 0.f;
    #pragma unroll
    for (int t = 0; t < 32; ++t) {
        const int q = (((ql[t] >> shl) & 15) | (((qh[t] >> shh) & 3) << 4)) - 32;
        if (t < 16) r0 += (float)q * x[t]; else r1 += (float)q * x[t];
    }
    return h2f(ld16(b + 208)) * ((float)sc[0] * r0 + (float)sc[1] * r1);
}

__device__ float dot_MXFP4(const u8* b, int s, const float* x, const float* kv) {
    const u32 e = b[0];
    const float d = __uint_as_float(e < 2 ? 0x00200000u << e : (e - 1) << 23);
    const u8* q = b + 1;
    float r = 0.f;
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[16 + (q[j] & 15)] * x[j] + kv[16 + (q[j] >> 4)] * x[j + 16];
    return d * r;
}
'''

# y[e, row] = W_ids[e][row, :] . x_e  with x_e = x + e * x_stride (x_stride 0: shared input)
_GERUEST = r'''
extern "C" __global__ void mv_{F}(const u8* __restrict__ raw, const long long* __restrict__ ids,
        const unsigned short* __restrict__ x, float* __restrict__ y, int n_out, int n_in,
        long long per_expert, int x_stride) {
    extern __shared__ float xs[];
    __shared__ float kv[32];                  // value tables: shared memory, not __constant__
    const int e = blockIdx.y, lane = threadIdx.x & 31;
    if (threadIdx.x < 32) kv[threadIdx.x] = threadIdx.x < 16 ? KV_IQ4NL[threadIdx.x] : KV_MXFP4[threadIdx.x - 16];
    const unsigned short* xe = x + (long long)e * x_stride;
    for (int i = threadIdx.x; i < n_in; i += blockDim.x) xs[i] = bf2f(xe[i]);
    __syncthreads();
    const int row = blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5);
    if (row >= n_out) return;
    const u8* r = raw + ids[e] * per_expert + (long long)row * (n_in / {BS}) * {TS};
    float sum = 0.f;
    for (int u = lane; u < n_in / 32; u += 32)
        sum += dot_{F}(r + (long long)(u / ({BS} / 32)) * {TS}, u % ({BS} / 32), xs + u * 32, kv);
    sum = warp_sum(sum);
    if (lane == 0) y[(long long)e * n_out + row] = sum;
}
'''


def _tabelle(name: str, typ: str, werte) -> str:
    breite = 16 if typ == "u64" else 8
    zahlen = ",".join(f"0x{v:0{breite}x}" + ("ull" if typ == "u64" else "u") for v in werte)
    return f"__device__ const {typ} {name}[{len(werte)}] = {{{zahlen}}};\n"


def _quelle() -> str:
    from . import iqtabellen as it
    tabellen = (_tabelle("IQ2XXS_GRID", "u64", it.IQ2XXS_GRID) + _tabelle("IQ2XS_GRID", "u64", it.IQ2XS_GRID)
                + _tabelle("IQ2S_GRID", "u64", it.IQ2S_GRID) + _tabelle("IQ3XXS_GRID", "u32", it.IQ3XXS_GRID)
                + _tabelle("IQ3S_GRID", "u32", it.IQ3S_GRID) + _tabelle("IQ1S_GRID", "u64", it.IQ1S_GRID))
    kernel = "".join(_GERUEST.replace("{F}", f).replace("{BS}", str(bs)).replace("{TS}", str(ts))
                     for f, (bs, ts) in FORMATE.items())
    return _GRUNDLAGEN + tabellen + _FORMATE + kernel


_SPERRE = threading.Lock()
_MODULE: dict = {}            # device index -> {format: CUfunction} or None (not available)
_cu = None


def _nvrtc_pfad() -> Optional[str]:
    lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    treffer = [p for p in glob.glob(os.path.join(lib, "nvrtc64_*.dll")) if "builtins" not in p]
    return sorted(treffer)[-1] if treffer else None


def _treiber():
    global _cu
    if _cu is None:
        if os.name == "nt":       # full path: a bare name is searched in the program folder first
            cu = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvcuda.dll"))
        else:
            cu = ctypes.CDLL("libcuda.so.1")
        cu.cuLaunchKernel.argtypes = [ctypes.c_void_p] + [ctypes.c_uint] * 6 + [
            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        _cu = cu
    return _cu


def _uebersetzen(device: torch.device) -> dict:
    pfad = _nvrtc_pfad()
    if pfad is None:
        raise RuntimeError("NVRTC nicht gefunden")
    nvrtc = ctypes.CDLL(pfad)
    prog = ctypes.c_void_p()
    if nvrtc.nvrtcCreateProgram(ctypes.byref(prog), _quelle().encode(), b"gguf.cu", 0, None, None):
        raise RuntimeError("nvrtcCreateProgram")
    maj, mi = torch.cuda.get_device_capability(device)
    opts = (ctypes.c_char_p * 2)(f"--gpu-architecture=sm_{maj}{mi}".encode(), b"--use_fast_math")
    if nvrtc.nvrtcCompileProgram(prog, 2, opts):
        n = ctypes.c_size_t()
        nvrtc.nvrtcGetProgramLogSize(prog, ctypes.byref(n))
        log = ctypes.create_string_buffer(n.value)
        nvrtc.nvrtcGetProgramLog(prog, log)
        raise RuntimeError("NVRTC: " + log.value.decode(errors="replace")[:500])
    n = ctypes.c_size_t()
    nvrtc.nvrtcGetCUBINSize(prog, ctypes.byref(n))
    cubin = ctypes.create_string_buffer(n.value)
    nvrtc.nvrtcGetCUBIN(prog, cubin)
    nvrtc.nvrtcDestroyProgram(ctypes.byref(prog))
    cu = _treiber()
    with torch.cuda.device(device):
        torch.zeros(1, device=device)                   # primary context of torch is current
        mod = ctypes.c_void_p()
        if cu.cuModuleLoadData(ctypes.byref(mod), cubin):
            raise RuntimeError("cuModuleLoadData")
        fns = {}
        for name in FORMATE:
            fn = ctypes.c_void_p()
            if cu.cuModuleGetFunction(ctypes.byref(fn), mod, f"mv_{name}".encode()):
                raise RuntimeError(f"cuModuleGetFunction mv_{name}")
            fns[name] = fn
    fns["_cubin"] = cubin                               # keep the image alive with the module
    return fns


def _funktionen(device: torch.device) -> Optional[dict]:
    if device.type != "cuda":
        return None
    idx = device.index if device.index is not None else torch.cuda.current_device()
    if idx not in _MODULE:
        with _SPERRE:
            if idx not in _MODULE:
                try:
                    _MODULE[idx] = _uebersetzen(torch.device("cuda", idx))
                    dbg.event("GGUF-Kernel übersetzt (NVRTC)")
                except Exception as exc:                # torch path instead
                    _MODULE[idx] = None
                    dbg.event(f"GGUF-Kernel nicht verfügbar: {exc}")
    return _MODULE[idx]


def verfuegbar(device, fmt: str, n_in: int) -> bool:
    """Kernel for this format and row length on this device."""
    device = torch.device(device)
    return fmt in FORMATE and n_in <= MAX_N_IN and n_in % FORMATE[fmt][0] == 0 \
        and _funktionen(device) is not None


def mv(fmt: str, raw: torch.Tensor, ids: torch.Tensor, x: torch.Tensor, n_out: int, n_in: int) -> torch.Tensor:
    """raw (n_exp, bytes per expert) on the GPU or in pinned RAM (read by the kernel directly
    over PCIe), ids (k,): expert ids.  x (n_in,) on the GPU is shared by all experts, x (k, n_in)
    is each expert's own input.  -> (k, n_out) float32."""
    geraet = x.device
    fn = _funktionen(geraet)[fmt]
    ids = ids.to(geraet, torch.int64).contiguous()
    x = x.to(torch.bfloat16).contiguous()
    k = ids.numel()
    y = torch.empty(k, n_out, dtype=torch.float32, device=geraet)
    args = [ctypes.c_void_p(raw.data_ptr()), ctypes.c_void_p(ids.data_ptr()), ctypes.c_void_p(x.data_ptr()),
            ctypes.c_void_p(y.data_ptr()), ctypes.c_int(n_out), ctypes.c_int(n_in),
            ctypes.c_longlong(raw.shape[1]), ctypes.c_int(n_in if x.dim() == 2 else 0)]
    werte = (ctypes.c_void_p * len(args))(*[ctypes.addressof(a) for a in args])
    strom = ctypes.c_void_p(torch.cuda.current_stream().cuda_stream)
    zeilen = THREADS // 32
    fehler = _treiber().cuLaunchKernel(fn, -(-n_out // zeilen), k, 1, THREADS, 1, 1, n_in * 4, strom, werte, None)
    if fehler:
        raise RuntimeError(f"cuLaunchKernel: CUDA-Fehler {fehler}")
    return y
