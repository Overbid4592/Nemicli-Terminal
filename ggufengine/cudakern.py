"""
Own CUDA kernels: matrix-vector products directly on GGUF block data for every block format
(legacy Q4/Q5/Q8, K-quants, the IQ family, MXFP4).  Nothing is unpacked, each weight byte is
read once.  Used for one token per step: MoE experts (also from pinned RAM, read directly over
PCIe) and dense layers in formats without the int4 path (IMMER_GEPACKT).

Each format contributes one device function "32 weights of a block times 32 inputs";
one kernel skeleton per format turns it into y[e, row] = W_ids[e][row, :] . x_e, where
x_e is either one input shared by all experts or each expert's own input.  A second one
(`mm`) multiplies one matrix with a few input rows, reading the weights once.

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

THREADS = 256                 # 8 warps per block, one output row per warp at a time
BLOECKE_JE_SM = 2             # mv: blocks per multiprocessor (each walks over several rows)
MM_ZEILEN = 16                # input rows of one `mm` call (weights read once for all)
MM_GROESSEN = tuple(range(2, MM_ZEILEN + 1))   # one kernel per row count
MM_SPALTEN = 1024             # mm: columns of X per shared-memory tile
MM_JE_WARP = 4                # mm: rows per warp (one tile of X serves 8 * 4 rows)
_MAX_DYNAMIC_SHARED = 8       # CU_FUNC_ATTRIBUTE_MAX_DYNAMIC_SHARED_SIZE_BYTES


def _mm_bytes(m: int) -> int:
    return m * (MM_SPALTEN + MM_SPALTEN // 32) * 4

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
// Inputs of the format functions: a float pointer (one row, result float) or XS<M> (M rows
// in shared memory, result V<M>) - each weight is decoded once for all rows.
template <int M> struct V {
    float v[M];
    __device__ __forceinline__ V() {
        #pragma unroll
        for (int m = 0; m < M; ++m) v[m] = 0.f;
    }
};
template <int M> __device__ __forceinline__ V<M> operator*(float a, const V<M>& b) {
    V<M> r;
    #pragma unroll
    for (int m = 0; m < M; ++m) r.v[m] = a * b.v[m];
    return r;
}
template <int M> __device__ __forceinline__ V<M> operator+(const V<M>& a, const V<M>& b) {
    V<M> r;
    #pragma unroll
    for (int m = 0; m < M; ++m) r.v[m] = a.v[m] + b.v[m];
    return r;
}
template <int M> __device__ __forceinline__ V<M> operator-(const V<M>& a, const V<M>& b) {
    V<M> r;
    #pragma unroll
    for (int m = 0; m < M; ++m) r.v[m] = a.v[m] - b.v[m];
    return r;
}
template <int M> __device__ __forceinline__ V<M>& operator+=(V<M>& a, const V<M>& b) {
    #pragma unroll
    for (int m = 0; m < M; ++m) a.v[m] += b.v[m];
    return a;
}
template <int M> struct XS {
    const float* p;
    int stride;
    __device__ __forceinline__ V<M> operator[](int i) const {
        V<M> r;
        #pragma unroll
        for (int m = 0; m < M; ++m) r.v[m] = p[m * stride + i];
        return r;
    }
    __device__ __forceinline__ XS operator+(int o) const { XS r = {p + o, stride}; return r; }
};
template <class X> struct Res { typedef float T; };
template <int M> struct Res<XS<M> > { typedef V<M> T; };
#define RT typename Res<X>::T

// 8 unsigned grid bytes with signs (bit j of sg set: negative) times x[0..7]
template <class X> __device__ __forceinline__ RT grid8(u64 g, u32 sg, X x) {
    RT s = RT();
    #pragma unroll
    for (int j = 0; j < 8; ++j) {
        const float v = (float)((g >> (8 * j)) & 255);
        s += ((sg >> j) & 1 ? -v : v) * x[j];
    }
    return s;
}
template <class X> __device__ __forceinline__ RT grid4(u32 g, u32 sg, X x) {
    RT s = RT();
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
template <class X> __device__ __forceinline__ RT dot_Q2_K(const u8* b, int s, X x, const float* kv) {
    const float d = h2f(ld16(b + 80)), dmin = h2f(ld16(b + 82));
    const u8* q = b + 16 + (s >> 2) * 32;
    const int sh = 2 * (s & 3);
    RT r = RT();
    #pragma unroll
    for (int h = 0; h < 2; ++h) {
        const u32 sc = b[2 * s + h];
        RT aq = RT(), ax = RT();
        #pragma unroll
        for (int t = 0; t < 16; ++t) {
            aq += (float)((q[16 * h + t] >> sh) & 3) * x[16 * h + t];
            ax += x[16 * h + t];
        }
        r += d * (float)(sc & 15) * aq - dmin * (float)(sc >> 4) * ax;
    }
    return r;
}

template <class X> __device__ __forceinline__ RT dot_Q3_K(const u8* b, int s, X x, const float* kv) {
    const u8* hm = b;
    const u8* q = b + 32 + (s >> 2) * 32;
    const u8* sc = b + 96;
    const float d = h2f(ld16(b + 108));
    const int sh = 2 * (s & 3);
    RT r = RT();
    #pragma unroll
    for (int h = 0; h < 2; ++h) {
        const int k = 2 * s + h;
        const int lo = k < 8 ? (sc[k] & 15) : (sc[k - 8] >> 4);
        const int hi = (sc[8 + (k & 3)] >> (2 * (k >> 2))) & 3;
        RT a = RT();
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

template <class X> __device__ __forceinline__ RT dot_IQ2_XXS(const u8* b, int s, X x, const float* kv) {
    const u8* q = b + 2 + 8 * s;
    const u32 aux0 = ld32(q), aux1 = ld32(q + 4);
    RT r = RT();
    #pragma unroll
    for (int l = 0; l < 4; ++l)
        r += grid8(IQ2XXS_GRID[(aux0 >> (8 * l)) & 255], ksign7((aux1 >> (7 * l)) & 127), x + 8 * l);
    return h2f(ld16(b)) * (0.125f + 0.25f * (float)(aux1 >> 28)) * r;
}

template <class X> __device__ __forceinline__ RT dot_IQ2_XS(const u8* b, int s, X x, const float* kv) {
    const float d = h2f(ld16(b));
    const u32 sc = b[66 + s];
    RT r = RT();
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 q = ld16(b + 2 + 2 * (4 * s + l));
        const float db = d * (0.125f + 0.25f * (float)(l < 2 ? sc & 15 : sc >> 4));
        r += db * grid8(IQ2XS_GRID[q & 511], ksign7(q >> 9), x + 8 * l);
    }
    return r;
}

template <class X> __device__ __forceinline__ RT dot_IQ2_S(const u8* b, int s, X x, const float* kv) {
    const float d = h2f(ld16(b));
    const u8* qs = b + 2; const u8* sg = b + 34; const u32 qh = b[66 + s], sc = b[74 + s];
    RT r = RT();
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 g = qs[4 * s + l] | ((qh << (8 - 2 * l)) & 0x300);
        const float db = d * (0.125f + 0.25f * (float)(l < 2 ? sc & 15 : sc >> 4));
        r += db * grid8(IQ2S_GRID[g], sg[4 * s + l], x + 8 * l);
    }
    return r;
}

template <class X> __device__ __forceinline__ RT dot_IQ3_XXS(const u8* b, int s, X x, const float* kv) {
    const u8* qs = b + 2 + 8 * s;
    const u32 aux = ld32(b + 66 + 4 * s);
    RT r = RT();
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 sg = ksign7((aux >> (7 * l)) & 127);
        r += grid4(IQ3XXS_GRID[qs[2 * l]], sg, x + 8 * l) + grid4(IQ3XXS_GRID[qs[2 * l + 1]], sg >> 4, x + 8 * l + 4);
    }
    return h2f(ld16(b)) * (0.25f + 0.5f * (float)(aux >> 28)) * r;
}

template <class X> __device__ __forceinline__ RT dot_IQ3_S(const u8* b, int s, X x, const float* kv) {
    const u8* qs = b + 2 + 8 * s; const u32 qh = b[66 + s]; const u8* sg = b + 74 + 4 * s;
    const u32 sc = (b[106 + (s >> 1)] >> (4 * (s & 1))) & 15;
    RT r = RT();
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const u32 g1 = qs[2 * l] | (((qh >> (2 * l)) & 1) << 8);
        const u32 g2 = qs[2 * l + 1] | (((qh >> (2 * l + 1)) & 1) << 8);
        r += grid4(IQ3S_GRID[g1], sg[l], x + 8 * l) + grid4(IQ3S_GRID[g2], sg[l] >> 4, x + 8 * l + 4);
    }
    return h2f(ld16(b)) * (float)(1 + 2 * sc) * r;
}

template <class X> __device__ __forceinline__ RT iq1_grid(u64 g, X x, RT* sx) {
    RT s = RT(), t = RT();
    #pragma unroll
    for (int j = 0; j < 8; ++j) {
        s += (float)(signed char)((g >> (8 * j)) & 255) * x[j];
        t += x[j];
    }
    *sx = t;
    return s;
}

template <class X> __device__ __forceinline__ RT dot_IQ1_S(const u8* b, int s, X x, const float* kv) {
    const u32 qh = ld16(b + 34 + 2 * s);
    const float dl = h2f(ld16(b)) * (float)(2 * ((qh >> 12) & 7) + 1);
    const float delta = (qh & 0x8000) ? -0.125f : 0.125f;
    RT r = RT(), ax = RT(), t;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        r += iq1_grid(IQ1S_GRID[b[2 + 4 * s + l] | (((qh >> (3 * l)) & 7) << 8)], x + 8 * l, &t);
        ax += t;
    }
    return dl * (r + delta * ax);
}

template <class X> __device__ __forceinline__ RT dot_IQ1_M(const u8* b, int s, X x, const float* kv) {
    const u32 s0 = ld16(b + 48), s1 = ld16(b + 50), s2 = ld16(b + 52), s3 = ld16(b + 54);
    const u32 dbits = (s0 >> 12) | ((s1 >> 8) & 0xF0) | ((s2 >> 4) & 0xF00) | (s3 & 0xF000);
    const float d = h2f((unsigned short)dbits);
    const u32 sw = ld16(b + 48 + 2 * (s >> 1));
    RT r = RT(), t;
    #pragma unroll
    for (int l = 0; l < 4; ++l) {
        const int g = 4 * s + l, k = 2 * s + (l >> 1);
        const u32 nib = (b[32 + (g >> 1)] >> (4 * (g & 1))) & 15;
        const float dl = d * (float)(2 * ((sw >> (3 * (k & 3))) & 7) + 1);
        const RT v = iq1_grid(IQ1S_GRID[b[g] | ((nib & 7) << 8)], x + 8 * l, &t);
        r += dl * (v + ((nib & 8) ? -0.125f : 0.125f) * t);
    }
    return r;
}

template <class X> __device__ __forceinline__ RT dot_IQ4_NL(const u8* b, int s, X x, const float* kv) {
    const u8* q = b + 2;
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[q[j] & 15] * x[j] + kv[q[j] >> 4] * x[j + 16];
    return h2f(ld16(b)) * r;
}

template <class X> __device__ __forceinline__ RT dot_IQ4_XS(const u8* b, int s, X x, const float* kv) {
    const u32 sh = ld16(b + 2);
    const int ls = ((b[4 + (s >> 1)] >> (4 * (s & 1))) & 15) | (((sh >> (2 * s)) & 3) << 4);
    const u8* q = b + 8 + 16 * s;
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[q[j] & 15] * x[j] + kv[q[j] >> 4] * x[j + 16];
    return h2f(ld16(b)) * (float)(ls - 32) * r;
}

template <class X> __device__ __forceinline__ RT dot_Q8_0(const u8* b, int s, X x, const float* kv) {
    const signed char* q = (const signed char*)(b + 2);
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 32; ++j) r += (float)q[j] * x[j];
    return h2f(ld16(b)) * r;
}

template <class X> __device__ __forceinline__ RT dot_Q4_0(const u8* b, int s, X x, const float* kv) {
    const u8* q = b + 2;
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += (float)((q[j] & 15) - 8) * x[j] + (float)((q[j] >> 4) - 8) * x[j + 16];
    return h2f(ld16(b)) * r;
}

template <class X> __device__ __forceinline__ RT dot_Q4_1(const u8* b, int s, X x, const float* kv) {
    const u8* q = b + 4;
    RT r = RT(), ax = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) {
        r += (float)(q[j] & 15) * x[j] + (float)(q[j] >> 4) * x[j + 16];
        ax += x[j] + x[j + 16];
    }
    return h2f(ld16(b)) * r + h2f(ld16(b + 2)) * ax;
}

template <class X> __device__ __forceinline__ RT dot_Q5_0(const u8* b, int s, X x, const float* kv) {
    const u32 qh = ld32(b + 2);
    const u8* q = b + 6;
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) {
        const int lo = (q[j] & 15) | (((qh >> j) & 1) << 4), hi = (q[j] >> 4) | (((qh >> (j + 16)) & 1) << 4);
        r += (float)(lo - 16) * x[j] + (float)(hi - 16) * x[j + 16];
    }
    return h2f(ld16(b)) * r;
}

template <class X> __device__ __forceinline__ RT dot_Q5_1(const u8* b, int s, X x, const float* kv) {
    const u32 qh = ld32(b + 4);
    const u8* q = b + 8;
    RT r = RT(), ax = RT();
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

template <class X> __device__ __forceinline__ RT dot_Q4_K(const u8* b, int s, X x, const float* kv) {
    float sc, mn;
    skala_k4(b + 4, s, &sc, &mn);
    const u8* q = b + 16 + 32 * (s >> 1);
    const int sh = 4 * (s & 1);
    RT r = RT(), ax = RT();
    #pragma unroll
    for (int t = 0; t < 32; ++t) { r += (float)((q[t] >> sh) & 15) * x[t]; ax += x[t]; }
    return h2f(ld16(b)) * sc * r - h2f(ld16(b + 2)) * mn * ax;
}

template <class X> __device__ __forceinline__ RT dot_Q5_K(const u8* b, int s, X x, const float* kv) {
    float sc, mn;
    skala_k4(b + 4, s, &sc, &mn);
    const u8* qh = b + 16;
    const u8* q = b + 48 + 32 * (s >> 1);
    const int sh = 4 * (s & 1);
    RT r = RT(), ax = RT();
    #pragma unroll
    for (int t = 0; t < 32; ++t) {
        r += (float)(((q[t] >> sh) & 15) | (((qh[t] >> s) & 1) << 4)) * x[t];
        ax += x[t];
    }
    return h2f(ld16(b)) * sc * r - h2f(ld16(b + 2)) * mn * ax;
}

template <class X> __device__ __forceinline__ RT dot_Q6_K(const u8* b, int s, X x, const float* kv) {
    const int n = s >> 2, g = s & 3;
    const u8* ql = b + 64 * n + 32 * (g & 1);
    const u8* qh = b + 128 + 32 * n;
    const signed char* sc = (const signed char*)(b + 192) + 8 * n + 2 * g;
    const int shl = 4 * (g >> 1), shh = 2 * g;
    RT r0 = RT(), r1 = RT();
    #pragma unroll
    for (int t = 0; t < 32; ++t) {
        const int q = (((ql[t] >> shl) & 15) | (((qh[t] >> shh) & 3) << 4)) - 32;
        if (t < 16) r0 += (float)q * x[t]; else r1 += (float)q * x[t];
    }
    return h2f(ld16(b + 208)) * ((float)sc[0] * r0 + (float)sc[1] * r1);
}

template <class X> __device__ __forceinline__ RT dot_MXFP4(const u8* b, int s, X x, const float* kv) {
    const u32 e = b[0];
    const float d = __uint_as_float(e < 2 ? 0x00200000u << e : (e - 1) << 23);
    const u8* q = b + 1;
    RT r = RT();
    #pragma unroll
    for (int j = 0; j < 16; ++j) r += kv[16 + (q[j] & 15)] * x[j] + kv[16 + (q[j] >> 4)] * x[j + 16];
    return d * r;
}
'''

# y[e, row] = W_ids[e][row, :] . x_e  with x_e = x + e * x_stride (x_stride 0: shared input).
# One warp per row, a block walks over several rows (x is loaded once per block); in shared
# memory x[i] sits at i + i / 32, so the 32 lanes (one 32-value unit each) hit 32 banks.
_GERUEST = r'''
extern "C" __global__ void mv_{F}(const u8* __restrict__ raw, const long long* __restrict__ ids,
        const unsigned short* __restrict__ x, float* __restrict__ y, int n_out, int n_in,
        long long per_expert, int x_stride) {
    extern __shared__ float xs[];
    __shared__ float kv[32];                  // value tables: shared memory, not __constant__
    const int e = blockIdx.y, lane = threadIdx.x & 31, warps = blockDim.x >> 5;
    if (threadIdx.x < 32) kv[threadIdx.x] = threadIdx.x < 16 ? KV_IQ4NL[threadIdx.x] : KV_MXFP4[threadIdx.x - 16];
    const unsigned short* xe = x + (long long)e * x_stride;
    for (int i = threadIdx.x; i < n_in; i += blockDim.x) xs[i + (i >> 5)] = bf2f(xe[i]);
    __syncthreads();
    // per_expert < 0: raw is a table with the address of every expert (VRAM slot or pinned RAM)
    const u8* basis = per_expert >= 0 ? raw + ids[e] * per_expert
                                      : (const u8*)((const long long*)raw)[ids[e]];
    const long long zeile = (long long)(n_in / {BS}) * {TS};
    for (int row = blockIdx.x * warps + (threadIdx.x >> 5); row < n_out; row += gridDim.x * warps) {
        const u8* r = basis + row * zeile;
        float sum = 0.f;
        for (int u = lane; u < n_in / 32; u += 32)
            sum += dot_{F}(r + (long long)(u / ({BS} / 32)) * {TS}, u % ({BS} / 32), (const float*)(xs + u * 33), kv);
        sum = warp_sum(sum);
        if (lane == 0) y[(long long)e * n_out + row] = sum;
    }
}

// Y[m, row] = W[row, :] . X[m] for m < Mreal <= M: each warp takes MM_JE_WARP rows; X (Mreal,
// n_in) bf16 goes through shared memory in tiles of MM_SPALTEN columns (one 32-value unit
// per lane), each weight is decoded once for all M rows.
template <int M> __device__ __forceinline__ void mm_kern_{F}(const u8* __restrict__ raw,
        const unsigned short* __restrict__ x, float* __restrict__ y, int n_out, int n_in, int Mreal) {
    extern __shared__ float xs[];
    __shared__ float kv[32];
    const int lane = threadIdx.x & 31, warps = blockDim.x >> 5;
    if (threadIdx.x < 32) kv[threadIdx.x] = threadIdx.x < 16 ? KV_IQ4NL[threadIdx.x] : KV_MXFP4[threadIdx.x - 16];
    const int row0 = blockIdx.x * warps * MM_JE_WARP + (threadIdx.x >> 5);
    const int stride = MM_SPALTEN + MM_SPALTEN / 32;
    const long long zeile = (long long)(n_in / {BS}) * {TS};
    V<M> sum[MM_JE_WARP];
    for (int c0 = 0; c0 < n_in; c0 += MM_SPALTEN) {
        const int breite = min(MM_SPALTEN, n_in - c0);
        __syncthreads();
        for (int m = 0; m < M; ++m)
            for (int c = threadIdx.x; c < MM_SPALTEN; c += blockDim.x)
                xs[m * stride + c + (c >> 5)] = (m < Mreal && c < breite) ? bf2f(x[(long long)m * n_in + c0 + c]) : 0.f;
        __syncthreads();
        if (lane < breite / 32) {
            const int u = c0 / 32 + lane;
            const XS<M> xa = {xs + lane * 33, stride};
            #pragma unroll
            for (int j = 0; j < MM_JE_WARP; ++j) {
                const int row = row0 + j * warps;
                if (row < n_out)
                    sum[j] += dot_{F}(raw + row * zeile + (long long)(u / ({BS} / 32)) * {TS}, u % ({BS} / 32), xa, kv);
            }
        }
    }
    #pragma unroll
    for (int j = 0; j < MM_JE_WARP; ++j) {
        const int row = row0 + j * warps;
        if (row >= n_out) break;
        #pragma unroll
        for (int m = 0; m < M; ++m) {
            const float v = warp_sum(sum[j].v[m]);
            if (lane == 0 && m < Mreal) y[(long long)m * n_out + row] = v;
        }
    }
}
'''
_MM_HUELLE = r'''
extern "C" __global__ void mm{M}_{F}(const u8* __restrict__ raw, const unsigned short* __restrict__ x,
        float* __restrict__ y, int n_out, int n_in, int Mreal) {
    mm_kern_{F}<{M}>(raw, x, y, n_out, n_in, Mreal);
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
                     + "".join(_MM_HUELLE.replace("{F}", f).replace("{M}", str(m)) for m in MM_GROESSEN)
                     for f, (bs, ts) in FORMATE.items())
    return f"#define MM_SPALTEN {MM_SPALTEN}\n#define MM_JE_WARP {MM_JE_WARP}\n" + _GRUNDLAGEN + tabellen + _FORMATE + kernel


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


def _cache_ordner() -> Optional[str]:
    """Fertige Kernel (CUBIN) liegen je Quelle/Karte/NVRTC auf der Platte: einmal übersetzen (mehrere
    Minuten für 320 Kernel), danach nur laden."""
    basis = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP")
    if not basis:
        return None
    ordner = os.path.join(basis, "NemiCli", "kernel-cache")
    try:
        os.makedirs(ordner, exist_ok=True)
        return ordner
    except OSError:
        return None


def _uebersetzen(device: torch.device) -> dict:
    import hashlib
    pfad = _nvrtc_pfad()
    if pfad is None:
        raise RuntimeError("NVRTC nicht gefunden")
    quelle = _quelle()
    maj, mi = torch.cuda.get_device_capability(device)
    arch = f"--gpu-architecture=sm_{maj}{mi}"
    schluessel = hashlib.sha256((quelle + arch + os.path.basename(pfad) + str(torch.version.cuda)).encode()).hexdigest()
    ordner = _cache_ordner()
    datei = os.path.join(ordner, schluessel + ".cubin") if ordner else None
    cubin = None
    if datei and os.path.isfile(datei):
        try:
            with open(datei, "rb") as f:
                daten = f.read()
            cubin = ctypes.create_string_buffer(daten, len(daten))
            dbg.event(f"GGUF-Kernel aus dem Cache: {os.path.basename(datei)}")
        except OSError:
            cubin = None
    if cubin is None:
        nvrtc = ctypes.CDLL(pfad)
        prog = ctypes.c_void_p()
        if nvrtc.nvrtcCreateProgram(ctypes.byref(prog), quelle.encode(), b"gguf.cu", 0, None, None):
            raise RuntimeError("nvrtcCreateProgram")
        opts = (ctypes.c_char_p * 2)(arch.encode(), b"--use_fast_math")
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
        if datei:
            try:
                tmp = datei + ".teil"
                with open(tmp, "wb") as f:
                    f.write(cubin.raw[:n.value])
                os.replace(tmp, datei)
            except OSError:
                pass
    cu = _treiber()
    eig = torch.cuda.get_device_properties(device)
    # dynamic shared memory beyond the 48 KiB default (opt-in), minus the static tables
    geteilt = int(getattr(eig, "shared_memory_per_block_optin", 0) or 49152) - 1024
    with torch.cuda.device(device):
        torch.zeros(1, device=device)                   # primary context of torch is current
        mod = ctypes.c_void_p()
        if cu.cuModuleLoadData(ctypes.byref(mod), cubin):
            raise RuntimeError("cuModuleLoadData")

        def holen(name: str, bytes_: int):
            fn = ctypes.c_void_p()
            if cu.cuModuleGetFunction(ctypes.byref(fn), mod, name.encode()):
                raise RuntimeError(f"cuModuleGetFunction {name}")
            if bytes_ > 49152 and cu.cuFuncSetAttribute(fn, _MAX_DYNAMIC_SHARED, ctypes.c_int(bytes_)):
                raise RuntimeError(f"cuFuncSetAttribute {name}")
            return fn
        fns = {"_max_n_in": geteilt // 4 * 32 // 33 // 256 * 256, "_sm": int(eig.multi_processor_count)}
        mm_bis = max((m for m in MM_GROESSEN if _mm_bytes(m) <= geteilt), default=1)
        fns["_mm_bis"] = mm_bis
        for name in FORMATE:
            fns[name] = holen(f"mv_{name}", geteilt)
            for m in MM_GROESSEN:
                if m <= mm_bis:
                    fns[f"mm{m}_{name}"] = holen(f"mm{m}_{name}", _mm_bytes(m))
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
    if fmt not in FORMATE or n_in % FORMATE[fmt][0]:
        return False
    fns = _funktionen(device)
    return fns is not None and n_in <= fns["_max_n_in"]


def mm_bis(device) -> int:
    """Most input rows of one `mm` call on this device (1: none)."""
    fns = _funktionen(torch.device(device))
    return fns["_mm_bis"] if fns is not None else 1


def _starten(fn, grid: tuple, bytes_: int, args: list) -> None:
    werte = (ctypes.c_void_p * len(args))(*[ctypes.addressof(a) for a in args])
    strom = ctypes.c_void_p(torch.cuda.current_stream().cuda_stream)
    fehler = _treiber().cuLaunchKernel(fn, grid[0], grid[1], 1, THREADS, 1, 1, bytes_, strom, werte, None)
    if fehler:
        raise RuntimeError(f"cuLaunchKernel: CUDA-Fehler {fehler}")


def mv(fmt: str, raw: torch.Tensor, ids: torch.Tensor, x: torch.Tensor, n_out: int, n_in: int,
       adressen: torch.Tensor | None = None) -> torch.Tensor:
    """raw (n_exp, bytes per expert) on the GPU or in pinned RAM (read by the kernel directly
    over PCIe), ids (k,): expert ids.  x (n_in,) on the GPU is shared by all experts, x (k, n_in)
    is each expert's own input.  adressen (n_exp,) int64 on the GPU: where each expert's bytes
    are (VRAM slot or RAM) – then raw is not read.  -> (k, n_out) float32."""
    geraet = x.device
    fns = _funktionen(geraet)
    ids = ids.to(geraet, torch.int64).contiguous()
    x = x.to(torch.bfloat16).contiguous()
    k = ids.numel()
    y = torch.empty(k, n_out, dtype=torch.float32, device=geraet)
    quelle, per = (raw, raw.shape[1]) if adressen is None else (adressen, -1)
    args = [ctypes.c_void_p(quelle.data_ptr()), ctypes.c_void_p(ids.data_ptr()), ctypes.c_void_p(x.data_ptr()),
            ctypes.c_void_p(y.data_ptr()), ctypes.c_int(n_out), ctypes.c_int(n_in),
            ctypes.c_longlong(per), ctypes.c_int(n_in if x.dim() == 2 else 0)]
    bloecke = min(-(-n_out // (THREADS // 32)), BLOECKE_JE_SM * fns["_sm"])
    _starten(fns[fmt], (bloecke, k), (n_in + n_in // 32) * 4, args)
    return y


def mm(fmt: str, raw: torch.Tensor, x: torch.Tensor, n_out: int, n_in: int) -> torch.Tensor:
    """raw: one matrix (n_out rows) in block format on the GPU, x (M, n_in) with
    2 <= M <= mm_bis(device) -> (M, n_out) float32; each weight is read and decoded once."""
    geraet = x.device
    fns = _funktionen(geraet)
    M = x.shape[0]
    if not 2 <= M <= fns["_mm_bis"]:
        raise ValueError(f"mm: {M} rows (2..{fns['_mm_bis']})")
    x = x.to(torch.bfloat16).contiguous()
    y = torch.empty(M, n_out, dtype=torch.float32, device=geraet)
    args = [ctypes.c_void_p(raw.data_ptr()), ctypes.c_void_p(x.data_ptr()), ctypes.c_void_p(y.data_ptr()),
            ctypes.c_int(n_out), ctypes.c_int(n_in), ctypes.c_int(M)]
    _starten(fns[f"mm{M}_{fmt}"], (-(-n_out // (THREADS // 32 * MM_JE_WARP)), 1), _mm_bytes(M), args)
    return y
