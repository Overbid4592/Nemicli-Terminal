"""
Block-format matrices (Q4_K, Q6_K) for few tokens per step, after the MMVQ idea of llama.cpp (MIT):
the input is quantised once per 32 values, the kernel reads each weight byte once and multiplies four
values per dp4a.  No shared memory for the input, one warp per output row - many warps keep the memory
busy.  The input goes in two int8 stages (the rounding rest once more with 1/254 of the scale, about
15 bits together): one stage alone loses several percent on inputs with outliers.  The min term of
Q4_K uses the exact sum of each 32 values.

  Q4_K (256 values, 144 bytes): eight lanes per block, 16 quant bytes each; four blocks per warp at once
  Q6_K (256 values, 210 bytes): one lane takes half a block = 128 values (64 ql + 32 qh bytes)

Up to SPALTEN tokens share one pass over the weights, more go in groups (up to MMVQ_BIS); up to
MMQ_BIS tokens the MMQ kernel unpacks each weight block once into shared memory as fp16 and multiplies
all tokens on the tensor cores; beyond that the matrix is unpacked to bf16 for cuBLAS.  For one token per step there are fused small steps as well
(norm, activation, RoPE + KV cache, attention split over the keys) that hand their result straight to
the matrices.

Compiled with NVRTC like cudakern.py and launched through the driver API on torch's current stream
(CUDA-graph safe). Without NVRTC the callers keep their other paths.
"""
from __future__ import annotations

import contextlib
import ctypes
import threading
from typing import Optional

import torch

from . import cudakern
from . import debuglog as dbg

FORMATE = {"Q4_K": (256, 144), "Q6_K": (256, 210)}
WARPS = 4                     # rows per block (one warp each)
SPALTEN = 9                   # input rows (tokens) per kernel call, weights read once for all
# weight rows per warp by input rows (each loaded input value serves them all; measured)
ZEILEN_JE_WARP = {"Q4_K": lambda nc: 1 if nc == 1 else 2, "Q6_K": lambda nc: 1 if nc == 1 else 2}
# tokens per call: up to MMVQ_BIS int8 kernels in groups of SPALTEN, up to MMQ_BIS the tensor-core kernel
# (in groups of 8 * MMQ_NT), more: unpack to bf16 + cuBLAS (measured in a whole 8B model, short inputs)
MMVQ_BIS = {"Q4_K": 16, "Q6_K": 16}
MMQ_BIS = {"Q4_K": 112, "Q6_K": 112}
SCHRITT_KERNE = ("quant_q8", "norm_q8", "akt_q8", "rope_kv", "attn1_teil", "attn1_summe", "mmq_eingang")
MMQ_NT = 8                    # MMQ: bis 8 Kacheln zu 8 Token = 64 Token je Aufruf
MMQ_SKALA = 256.0             # Eingang je Token auf |x| <= 256 skaliert: fp16 ohne Überlauf, kaum Unterlauf
MMQ_BLOECKE = 4               # Split-K: mindestens so viele Blöcke je Multiprozessor

_QUELLE = r'''
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define INFINITY __int_as_float(0x7f800000)
__device__ __forceinline__ float h2f(u16 u) { float f; asm("cvt.f32.f16 %0, %1;" : "=f"(f) : "h"(u)); return f; }
__device__ __forceinline__ float bf2f(u16 u) { return __uint_as_float(((u32)u) << 16); }
__device__ __forceinline__ float warp_sum(float v) {
    #pragma unroll
    for (int o = 16; o > 0; o >>= 1) v += __shfl_xor_sync(0xffffffffu, v, o);
    return v;
}
__device__ __forceinline__ u16 f2bf(float f) {
    u32 u = __float_as_uint(f); u += 0x7FFF + ((u >> 16) & 1); return (u16)(u >> 16);
}
__device__ __forceinline__ int ld32(const u8* p) { return *(const int*)p; }   // 4-byte aligned (Q4_K: 144 B)
__device__ __forceinline__ int ld32h(const u8* p) {                              // 2-byte aligned (Q6_K: 210 B)
    return (int)((u32)*(const u16*)p | ((u32)*(const u16*)(p + 2) << 16));
}

// warp-wide: value v of position i (group i / 32 = this warp) -> int8 q with scale d, the rest
// v - q d once more as int8 with scale d / 254 (xq2: about 15 bits together), and the exact sum
#define FEIN (1.f / 254.f)
__device__ __forceinline__ void quant32(float v, int i, signed char* __restrict__ xq, signed char* __restrict__ xq2,
                                        float* __restrict__ xd, float* __restrict__ xs) {
    float a = fabsf(v);
    #pragma unroll
    for (int o = 16; o > 0; o >>= 1) a = fmaxf(a, __shfl_xor_sync(0xffffffffu, a, o));
    const float d = a / 127.f;
    const int q = d > 0.f ? __float2int_rn(v / d) : 0;
    const int q2 = d > 0.f ? max(-127, min(127, __float2int_rn((v - q * d) / (d * FEIN)))) : 0;
    const float s = warp_sum(v);
    xq[i] = (signed char)q;
    xq2[i] = (signed char)q2;
    if ((threadIdx.x & 31) == 0) { xd[i >> 5] = d; xs[i >> 5] = s; }
}

// x (bf16 or float32, n; n % 32 == 0) -> xq, xq2 (int8, n), xd (float, n/32: scale), xs (float, n/32: sum)
extern "C" __global__ void quant_q8(const void* __restrict__ x, int f32, signed char* __restrict__ xq,
                                    signed char* __restrict__ xq2, float* __restrict__ xd, float* __restrict__ xs, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if ((i & ~31) >= n) return;                                  // whole warps only
    const float v = f32 ? ((const float*)x)[i] : bf2f(((const u16*)x)[i]);
    quant32(v, i, xq, xq2, xd, xs);
}

__device__ __forceinline__ float block_sum(float v, float* red) {
    __syncthreads();                                             // red may still be read from a previous call
    v = warp_sum(v);
    if ((threadIdx.x & 31) == 0) red[threadIdx.x >> 5] = v;
    __syncthreads();
    if (threadIdx.x < 32) {
        v = threadIdx.x < (blockDim.x >> 5) ? red[threadIdx.x] : 0.f;
        v = warp_sum(v);
        if (threadIdx.x == 0) red[0] = v;
    }
    __syncthreads();
    return red[0];
}

// ---- one token per step: fused small steps around the matrices ----------------------------------

// RMS norm of one row (one block): x = h (+ rs * r, written to hout) -> xn = x / rms * w and its int8 input
extern "C" __global__ void norm_q8(const float* __restrict__ h, const float* __restrict__ r, float rs,
        float* __restrict__ hout, const float* __restrict__ w, float eps, float* __restrict__ xn,
        signed char* __restrict__ xq, signed char* __restrict__ xq2, float* __restrict__ xd, float* __restrict__ xs,
        int n) {
    __shared__ float red[32];
    float ss = 0.f;
    for (int i = threadIdx.x; i < n; i += blockDim.x) {
        float v = h[i];
        if (r) { v += rs * r[i]; hout[i] = v; }
        ss += v * v;
    }
    const float inv = rsqrtf(block_sum(ss, red) / n + eps);
    const int nw = blockDim.x >> 5;
    for (int g = threadIdx.x >> 5; g < (n >> 5); g += nw) {
        const int i = (g << 5) + (threadIdx.x & 31);
        const float v = (r ? h[i] + rs * r[i] : h[i]) * inv * w[i];
        xn[i] = v;
        quant32(v, i, xq, xq2, xd, xs);
    }
}

// a = act(g) * u (u == 0: a = act(g)), float32, and its int8 input; act: silu or gelu (tanh)
extern "C" __global__ void akt_q8(const float* __restrict__ g, const float* __restrict__ u, int gelu,
        float* __restrict__ a, signed char* __restrict__ xq, signed char* __restrict__ xq2, float* __restrict__ xd,
        float* __restrict__ xs, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if ((i & ~31) >= n) return;
    const float x = g[i];
    float v = gelu ? 0.5f * x * (1.f + tanhf(0.7978845608f * (x + 0.044715f * x * x * x))) : x / (1.f + __expf(-x));
    if (u) v *= u[i];
    a[i] = v;
    quant32(v, i, xq, xq2, xd, xs);
}

// block b < H: query head b, < H + Hkv: key head, else value head; D threads.  Bias, RMS norm per head
// (query / key), rotary on the first rd features (rd = 0: none), query temperature (Ministral 3); keys
// and values go into the bf16 caches (Hkv, cap, D) at slot pos, the query to qo (float32)
extern "C" __global__ void rope_kv(const float* __restrict__ q, const float* __restrict__ k, const float* __restrict__ v,
        const float* __restrict__ qb, const float* __restrict__ kb, const float* __restrict__ vb,
        const float* __restrict__ cos_t, const float* __restrict__ sin_t, int rd, int neox,
        const long long* __restrict__ pos_p, const float* __restrict__ qn, const float* __restrict__ kn, float eps,
        float temp_beta, int temp_n, float* __restrict__ qo, u16* __restrict__ kc, u16* __restrict__ vc,
        int cap, int H, int Hkv, int D) {
    extern __shared__ float sh[];                                  // D values + 32 for the sum
    const int b = blockIdx.x, i = threadIdx.x;
    const long long pos = *pos_p;
    if (b >= H + Hkv) {
        const int hd = b - H - Hkv;
        const float x = v[hd * D + i] + (vb ? vb[hd * D + i] : 0.f);
        vc[((long long)hd * cap + pos) * D + i] = f2bf(x);
        return;
    }
    const bool ist_q = b < H;
    const int hd = ist_q ? b : b - H;
    const float* bias = ist_q ? qb : kb;
    const float* nw = ist_q ? qn : kn;
    float x = (ist_q ? q : k)[hd * D + i] + (bias ? bias[hd * D + i] : 0.f);
    if (nw) x = x * rsqrtf(block_sum(x * x, sh + D) / D + eps) * nw[i];
    sh[i] = x;
    __syncthreads();
    if (i < rd) {
        const int h2 = rd >> 1;
        if (neox) {
            const int j = i < h2 ? i : i - h2;
            const float c = cos_t[pos * h2 + j], s = sin_t[pos * h2 + j];
            x = i < h2 ? x * c - sh[i + h2] * s : sh[i - h2] * s + x * c;
        } else {
            const int j = i >> 1;
            const float c = cos_t[pos * h2 + j], s = sin_t[pos * h2 + j];
            x = (i & 1) == 0 ? x * c - sh[i + 1] * s : sh[i - 1] * s + x * c;
        }
    }
    if (ist_q) {
        if (temp_n > 0) x *= 1.f + temp_beta * log1pf((float)(pos / temp_n));
        qo[hd * D + i] = x;
    } else {
        kc[((long long)hd * cap + pos) * D + i] = f2bf(x);
    }
}

// attention of one query token, part 1: block (chunk c of `ac` keys, key head g), 128 threads; the
// G = H / Hkv query heads of g share each key and value read.  A key row (D = 64/128/256 bf16) is read
// by LK = D/8 lanes with 16-byte loads, four rows per lane in flight.  Keys lo .. pos (window > 0: the
// last `window` positions).  Per query head and chunk: max score m, sum l of exp(score - m), and the
// sum of exp(score - m) * value.
#define GB 4                                                   // query heads per pass over the values
__device__ __forceinline__ void bf8(uint4 r, float* f) {
    f[0] = __uint_as_float(r.x << 16); f[1] = __uint_as_float(r.x & 0xffff0000u);
    f[2] = __uint_as_float(r.y << 16); f[3] = __uint_as_float(r.y & 0xffff0000u);
    f[4] = __uint_as_float(r.z << 16); f[5] = __uint_as_float(r.z & 0xffff0000u);
    f[6] = __uint_as_float(r.w << 16); f[7] = __uint_as_float(r.w & 0xffff0000u);
}

extern "C" __global__ void attn1_teil(const float* __restrict__ q, const u16* __restrict__ kc,
        const u16* __restrict__ vc, const long long* __restrict__ pos_p, int cap, float scale, int window,
        float* __restrict__ po, float* __restrict__ pm, float* __restrict__ pl, int H, int Hkv, int D,
        int chunks, int ac) {
    extern __shared__ float sh[];              // max(G, 16) * D: query values, later partial sums; G * ac scores
    const int c = blockIdx.x, g = blockIdx.y, G = H / Hkv;
    const long long pos = *pos_p;
    const long long lo = window > 0 ? max(0LL, pos - window + 1) : 0LL;
    const int t0 = c * ac;
    const int tid = threadIdx.x, nt = blockDim.x, lane = tid & 31, warp = tid >> 5, nw = nt >> 5;
    const int tn = (int)min((long long)ac, pos + 1 - t0);          // keys of this chunk up to pos
    if (tn <= 0 || t0 + ac <= lo) {                               // nothing to read here
        for (int gi = tid; gi < G; gi += nt) {
            pm[(g * G + gi) * chunks + c] = -INFINITY;
            pl[(g * G + gi) * chunks + c] = 0.f;
        }
        return;
    }
    float* qs = sh;
    float* s = sh + max(G, 16) * D;
    for (int i = tid; i < G * D; i += nt) qs[i] = q[g * G * D + i] * scale;
    __syncthreads();
    const int LK = D >> 3, KW = 32 / LK;                           // lanes per row, rows per warp load
    const int sub = lane / LK, l8 = (lane % LK) * 8;
    const int schritt = nw * KW;                                   // rows per load of the whole block
    const u16* kb = kc + ((long long)g * cap + t0) * D + l8;
    const u16* vb = vc + ((long long)g * cap + t0) * D + l8;

    for (int tb = warp * KW; tb < tn; tb += 4 * schritt) {        // warp-uniform bounds (shuffles inside)
        uint4 r[4];
        #pragma unroll
        for (int u = 0; u < 4; ++u) {
            const int t = tb + sub + u * schritt;
            r[u] = t < tn ? *(const uint4*)(kb + (long long)t * D) : make_uint4(0u, 0u, 0u, 0u);
        }
        #pragma unroll
        for (int u = 0; u < 4; ++u) {
            const int t = tb + sub + u * schritt;
            float kf[8];
            bf8(r[u], kf);
            for (int gi = 0; gi < G; ++gi) {
                const float4 a = *(const float4*)(qs + gi * D + l8), b = *(const float4*)(qs + gi * D + l8 + 4);
                float d = kf[0] * a.x + kf[1] * a.y + kf[2] * a.z + kf[3] * a.w
                        + kf[4] * b.x + kf[5] * b.y + kf[6] * b.z + kf[7] * b.w;
                for (int o = LK >> 1; o > 0; o >>= 1) d += __shfl_xor_sync(0xffffffffu, d, o);
                if (lane % LK == 0 && t < tn) s[gi * ac + t] = t0 + t < lo ? -INFINITY : d;
            }
        }
    }
    __syncthreads();
    for (int gi = warp; gi < G; gi += nw) {                        // softmax part per query head
        float m = -INFINITY;
        for (int t = lane; t < tn; t += 32) m = fmaxf(m, s[gi * ac + t]);
        #pragma unroll
        for (int o = 16; o > 0; o >>= 1) m = fmaxf(m, __shfl_xor_sync(0xffffffffu, m, o));
        float l = 0.f;
        for (int t = lane; t < tn; t += 32) {
            const float e = m == -INFINITY ? 0.f : __expf(s[gi * ac + t] - m);
            s[gi * ac + t] = e;
            l += e;
        }
        l = warp_sum(l);
        if (lane == 0) { pm[(g * G + gi) * chunks + c] = m; pl[(g * G + gi) * chunks + c] = l; }
    }
    __syncthreads();
    float* red = sh;                                               // query values are no longer needed
    for (int gb = 0; gb < G; gb += GB) {
        float acc[GB][8];
        #pragma unroll
        for (int gi = 0; gi < GB; ++gi)
            #pragma unroll
            for (int j = 0; j < 8; ++j) acc[gi][j] = 0.f;
        for (int tb = warp * KW; tb < tn; tb += 4 * schritt) {
            uint4 r[4];
            #pragma unroll
            for (int u = 0; u < 4; ++u) {
                const int t = tb + sub + u * schritt;
                r[u] = t < tn ? *(const uint4*)(vb + (long long)t * D) : make_uint4(0u, 0u, 0u, 0u);
            }
            #pragma unroll
            for (int u = 0; u < 4; ++u) {
                const int t = tb + sub + u * schritt;
                if (t >= tn) continue;
                float vf[8];
                bf8(r[u], vf);
                #pragma unroll
                for (int gi = 0; gi < GB; ++gi) {
                    if (gb + gi >= G) break;
                    const float p = s[(gb + gi) * ac + t];
                    #pragma unroll
                    for (int j = 0; j < 8; ++j) acc[gi][j] += p * vf[j];
                }
            }
        }
        #pragma unroll
        for (int gi = 0; gi < GB; ++gi)                            // rows of one warp load: same values, other keys
            #pragma unroll
            for (int j = 0; j < 8; ++j)
                for (int o = LK; o < 32; o <<= 1) acc[gi][j] += __shfl_xor_sync(0xffffffffu, acc[gi][j], o);
        __syncthreads();
        if (lane < LK)
            #pragma unroll
            for (int gi = 0; gi < GB; ++gi)
                #pragma unroll
                for (int j = 0; j < 8; ++j) red[(warp * GB + gi) * D + l8 + j] = acc[gi][j];
        __syncthreads();
        for (int i = tid; i < GB * D; i += nt) {
            const int gi = i / D, d = i - gi * D;
            if (gb + gi >= G) break;
            float v = 0.f;
            for (int w = 0; w < nw; ++w) v += red[(w * GB + gi) * D + d];
            po[((long long)(g * G + gb + gi) * chunks + c) * D + d] = v;
        }
    }
}

// part 2: block = query head, D threads; joins the chunks (+ sink logit) -> out (float32) and its int8 input
extern "C" __global__ void attn1_summe(const float* __restrict__ po, const float* __restrict__ pm,
        const float* __restrict__ pl, const float* __restrict__ sinks, float* __restrict__ out,
        signed char* __restrict__ xq, signed char* __restrict__ xq2, float* __restrict__ xd, float* __restrict__ xs,
        int D, int chunks) {
    const int h = blockIdx.x, d = threadIdx.x;
    float M = sinks ? sinks[h] : -INFINITY;
    for (int c = 0; c < chunks; ++c) M = fmaxf(M, pm[h * chunks + c]);
    float L = sinks ? __expf(sinks[h] - M) : 0.f, acc = 0.f;
    for (int c = 0; c < chunks; ++c) {
        const float m = pm[h * chunks + c];
        if (m == -INFINITY) continue;
        const float w = __expf(m - M);
        L += w * pl[h * chunks + c];
        acc += w * po[((long long)h * chunks + c) * D + d];
    }
    const float v = L > 0.f ? acc / L : 0.f;
    out[h * D + d] = v;
    quant32(v, h * D + d, xq, xq2, xd, xs);
}

// 6-bit scale and min of sub-block j of a Q4_K block (ggml get_scale_min_k4)
__device__ __forceinline__ void skala_k4(const u8* sc, int j, int* d, int* m) {
    if (j < 4) { *d = sc[j] & 63; *m = sc[j + 4] & 63; }
    else { *d = (sc[j + 4] & 15) | ((sc[j - 4] >> 6) << 4); *m = (sc[j + 4] >> 4) | ((sc[j] >> 6) << 4); }
}

// NC input rows (tokens) at once: each weight byte is read once for all of them; input row c starts at
// c * n_in in xq (and c * n_in / 32 in xd, xs), output row c at c * n_out in y.  RW weight rows per
// warp: each input value loaded serves RW rows (for many tokens the loads of x are the limit).
template <int NC, int RW>
__device__ __forceinline__ void mvq_q4k(const u8* __restrict__ w, const signed char* __restrict__ xq,
        const signed char* __restrict__ xq2,
        const float* __restrict__ xd, const float* __restrict__ xs, float* __restrict__ y, int n_out, int n_in) {
    // a warp takes four blocks of a row at once, eight lanes per block, 16 quant bytes per lane:
    // piece q = lane % 8 -> pair p = q / 2 (sub-blocks 2p low nibbles, 2p+1 high nibbles), values t .. t+15
    const int lane = threadIdx.x & 31;
    const int row0 = (blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5)) * RW;
    if (row0 >= n_out) return;
    const int bloecke = n_in >> 8;
    const int q = lane & 7, p = q >> 1, t = 16 * (q & 1);
    float sum[RW][NC];
    #pragma unroll
    for (int rr = 0; rr < RW; ++rr)
        #pragma unroll
        for (int c = 0; c < NC; ++c) sum[rr][c] = 0.f;
    #pragma unroll 2
    for (int blk = lane >> 3; blk < bloecke; blk += 4) {
        int lo[RW][4], hi[RW][4], sc0[RW], m0[RW], sc1[RW], m1[RW];
        float d[RW], dm[RW];
        #pragma unroll
        for (int rr = 0; rr < RW; ++rr) {
            const int row = min(row0 + rr, n_out - 1);
            const u8* b = w + ((long long)row * bloecke + blk) * 144;
            const int4 v = *(const int4*)(b + 16 + 16 * q);
            lo[rr][0] = v.x & 0x0F0F0F0F; lo[rr][1] = v.y & 0x0F0F0F0F; lo[rr][2] = v.z & 0x0F0F0F0F; lo[rr][3] = v.w & 0x0F0F0F0F;
            hi[rr][0] = (v.x >> 4) & 0x0F0F0F0F; hi[rr][1] = (v.y >> 4) & 0x0F0F0F0F;
            hi[rr][2] = (v.z >> 4) & 0x0F0F0F0F; hi[rr][3] = (v.w >> 4) & 0x0F0F0F0F;
            skala_k4(b + 4, 2 * p, &sc0[rr], &m0[rr]);
            skala_k4(b + 4, 2 * p + 1, &sc1[rr], &m1[rr]);
            const u32 dd = *(const u32*)b;                        // d, dmin
            d[rr] = h2f((u16)dd);
            dm[rr] = h2f((u16)(dd >> 16));
        }
        const int x0 = blk * 256 + 64 * p;                       // sub-block 2p starts here
        #pragma unroll
        for (int c = 0; c < NC; ++c) {
            const signed char* xc = xq + (long long)c * n_in;
            const int4 xa = *(const int4*)(xc + x0 + t);
            const int4 xb = *(const int4*)(xc + x0 + 32 + t);
            const signed char* xc2 = xq2 + (long long)c * n_in;
            const int4 xa2 = *(const int4*)(xc2 + x0 + t);
            const int4 xb2 = *(const int4*)(xc2 + x0 + 32 + t);
            const int j = (c * n_in + x0) >> 5;
            const float2 xdj = *(const float2*)(xd + j);
            const float2 xsj = t == 0 ? *(const float2*)(xs + j) : make_float2(0.f, 0.f);
            #pragma unroll
            for (int rr = 0; rr < RW; ++rr) {
                int s0 = __dp4a(lo[rr][0], xa.x, 0);
                s0 = __dp4a(lo[rr][1], xa.y, s0);
                s0 = __dp4a(lo[rr][2], xa.z, s0);
                s0 = __dp4a(lo[rr][3], xa.w, s0);
                int s1 = __dp4a(hi[rr][0], xb.x, 0);
                s1 = __dp4a(hi[rr][1], xb.y, s1);
                s1 = __dp4a(hi[rr][2], xb.z, s1);
                s1 = __dp4a(hi[rr][3], xb.w, s1);
                int r0 = __dp4a(lo[rr][0], xa2.x, 0);              // the rest of the input, scale d / 254
                r0 = __dp4a(lo[rr][1], xa2.y, r0);
                r0 = __dp4a(lo[rr][2], xa2.z, r0);
                r0 = __dp4a(lo[rr][3], xa2.w, r0);
                int r1 = __dp4a(hi[rr][0], xb2.x, 0);
                r1 = __dp4a(hi[rr][1], xb2.y, r1);
                r1 = __dp4a(hi[rr][2], xb2.z, r1);
                r1 = __dp4a(hi[rr][3], xb2.w, r1);
                const float f0 = s0 + FEIN * r0, f1 = s1 + FEIN * r1;
                // the min term once per sub-block (lanes with t == 0; the others add zero)
                sum[rr][c] += d[rr] * (xdj.x * sc0[rr] * f0 + xdj.y * sc1[rr] * f1)
                            - dm[rr] * (m0[rr] * xsj.x + m1[rr] * xsj.y);
            }
        }
    }
    #pragma unroll
    for (int rr = 0; rr < RW; ++rr)
        #pragma unroll
        for (int c = 0; c < NC; ++c) {
            const float s = warp_sum(sum[rr][c]);
            if (lane == 0 && row0 + rr < n_out) y[(long long)c * n_out + row0 + rr] = s;
        }
}

template <int NC, int RW>
__device__ __forceinline__ void mvq_q6k(const u8* __restrict__ w, const signed char* __restrict__ xq,
        const signed char* __restrict__ xq2,
        const float* __restrict__ xd, const float* __restrict__ xs, float* __restrict__ y, int n_out, int n_in) {
    // lane i reads ql int i: half h = i / 16, byte j = 4 (i % 16) of that half's 64 ql bytes;
    // j < 32: sub-blocks g = 0 (low nibble) and g = 2 (high nibble), else g = 1 and 3; t = j % 32
    const int lane = threadIdx.x & 31;
    const int row0 = (blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5)) * RW;
    if (row0 >= n_out) return;
    const int bloecke = n_in >> 8;
    const int h = lane >> 4, j = 4 * (lane & 15);
    const int g0 = j < 32 ? 0 : 1, t = j & 31;
    float sum[RW][NC];
    #pragma unroll
    for (int rr = 0; rr < RW; ++rr)
        #pragma unroll
        for (int c = 0; c < NC; ++c) sum[rr][c] = 0.f;
    for (int blk = 0; blk < bloecke; ++blk) {
        int vq[RW][2], skala[RW][2];
        float d[RW];
        #pragma unroll
        for (int rr = 0; rr < RW; ++rr) {
            const int row = min(row0 + rr, n_out - 1);
            const u8* b = w + ((long long)row * bloecke + blk) * 210;
            const int l = ld32h(b + 64 * h + j);
            const int qh = ld32h(b + 128 + 32 * h + t);
            const signed char* sc = (const signed char*)(b + 192) + 8 * h;
            d[rr] = h2f(*(const u16*)(b + 208));
            #pragma unroll
            for (int z = 0; z < 2; ++z) {                       // g = g0 (low nibble), g0 + 2 (high nibble)
                const int g = g0 + 2 * z;
                vq[rr][z] = __vsub4(((l >> (4 * z)) & 0x0F0F0F0F) | (((qh >> (2 * g)) & 0x03030303) << 4), 0x20202020);
                skala[rr][z] = sc[2 * g + (t >> 4)];
            }
        }
        const int x0 = blk * 256 + 128 * h;
        #pragma unroll
        for (int c = 0; c < NC; ++c) {
            const signed char* xc = xq + (long long)c * n_in;
            const int xa = ld32((const u8*)(xc + x0 + 32 * g0 + t));
            const int xb = ld32((const u8*)(xc + x0 + 32 * (g0 + 2) + t));
            const signed char* xc2 = xq2 + (long long)c * n_in;
            const int xa2 = ld32((const u8*)(xc2 + x0 + 32 * g0 + t));
            const int xb2 = ld32((const u8*)(xc2 + x0 + 32 * (g0 + 2) + t));
            const int jx = ((c * n_in + x0) >> 5) + g0;
            const float da = xd[jx], db = xd[jx + 2];
            #pragma unroll
            for (int rr = 0; rr < RW; ++rr) {
                const float fa = __dp4a(vq[rr][0], xa, 0) + FEIN * __dp4a(vq[rr][0], xa2, 0);
                const float fb = __dp4a(vq[rr][1], xb, 0) + FEIN * __dp4a(vq[rr][1], xb2, 0);
                sum[rr][c] += d[rr] * (da * skala[rr][0] * fa + db * skala[rr][1] * fb);
            }
        }
    }
    #pragma unroll
    for (int rr = 0; rr < RW; ++rr)
        #pragma unroll
        for (int c = 0; c < NC; ++c) {
            const float s = warp_sum(sum[rr][c]);
            if (lane == 0 && row0 + rr < n_out) y[(long long)c * n_out + row0 + rr] = s;
        }
}

#define MVQ_ARGS const u8* __restrict__ w, const signed char* __restrict__ xq, const signed char* __restrict__ xq2, \
                 const float* __restrict__ xd, const float* __restrict__ xs, float* __restrict__ y, int n_out, int n_in
#define MVQ(NC) \
extern "C" __global__ void mvq_Q4_K_##NC(MVQ_ARGS) { mvq_q4k<NC, RW_Q4_K(NC)>(w, xq, xq2, xd, xs, y, n_out, n_in); } \
extern "C" __global__ void mvq_Q6_K_##NC(MVQ_ARGS) { mvq_q6k<NC, RW_Q6_K(NC)>(w, xq, xq2, xd, xs, y, n_out, n_in); }
MVQ(1) MVQ(2) MVQ(3) MVQ(4) MVQ(5) MVQ(6) MVQ(7) MVQ(8) MVQ(9)

// unpacking to bf16 for many input rows (then cuBLAS): one thread per 4 quant bytes of a block
__device__ __forceinline__ void schreib4(u16* o, float a, float b, float c, float d) {
    uint2 v; v.x = f2bf(a) | ((u32)f2bf(b) << 16); v.y = f2bf(c) | ((u32)f2bf(d) << 16); *(uint2*)o = v;
}

extern "C" __global__ void aus_Q4_K(const u8* __restrict__ w, u16* __restrict__ out, long long n_bl) {
    const long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n_bl * 32) return;
    const long long blk = i >> 5;
    const int lane = (int)(i & 31), p = lane >> 3, t = 4 * (lane & 7);
    const u8* b = w + blk * 144;
    const int v = ld32(b + 16 + 4 * lane);
    int sc0, m0, sc1, m1;
    skala_k4(b + 4, 2 * p, &sc0, &m0);
    skala_k4(b + 4, 2 * p + 1, &sc1, &m1);
    const u32 dd = *(const u32*)b;
    const float d = h2f((u16)dd), dm = h2f((u16)(dd >> 16));
    const float a0 = d * sc0, b0 = dm * m0, a1 = d * sc1, b1 = dm * m1;
    u16* o = out + blk * 256 + 64 * p + t;
    schreib4(o, a0 * (v & 15) - b0, a0 * ((v >> 8) & 15) - b0, a0 * ((v >> 16) & 15) - b0, a0 * ((v >> 24) & 15) - b0);
    schreib4(o + 32, a1 * ((v >> 4) & 15) - b1, a1 * ((v >> 12) & 15) - b1, a1 * ((v >> 20) & 15) - b1,
             a1 * ((v >> 28) & 15) - b1);
}

extern "C" __global__ void aus_Q6_K(const u8* __restrict__ w, u16* __restrict__ out, long long n_bl) {
    const long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n_bl * 32) return;
    const long long blk = i >> 5;
    const int lane = (int)(i & 31), h = lane >> 4, j = 4 * (lane & 15);
    const int g0 = j < 32 ? 0 : 1, t = j & 31;
    const u8* b = w + blk * 210;
    const int l = ld32h(b + 64 * h + j);
    const int qh = ld32h(b + 128 + 32 * h + t);
    const signed char* sc = (const signed char*)(b + 192) + 8 * h;
    const float d = h2f(*(const u16*)(b + 208));
    #pragma unroll
    for (int z = 0; z < 2; ++z) {
        const int g = g0 + 2 * z;
        const int vq = ((l >> (4 * z)) & 0x0F0F0F0F) | (((qh >> (2 * g)) & 0x03030303) << 4);
        const float f = d * sc[2 * g + (t >> 4)];
        schreib4(out + blk * 256 + 128 * h + 32 * g + t, f * ((vq & 63) - 32), f * (((vq >> 8) & 63) - 32),
                 f * (((vq >> 16) & 63) - 32), f * (((vq >> 24) & 63) - 32));
    }
}

// ---- MMQ: several tokens at once on the tensor cores ------------------------------------------------
// A block takes 64 weight rows; per K step of 128 values it unpacks them once to fp16 in shared memory
// and multiplies them with all tokens (fp16, scaled per token) via mma.sync m16n8k16, fp32 sums.
#define MMQ_BM 64
#define MMQ_BK 128
#define MMQ_LD 136                          // halves per shared row: +8 against bank conflicts
__device__ __forceinline__ u16 f2h(float f) { u16 h; asm("cvt.rn.f16.f32 %0, %1;" : "=h"(h) : "f"(f)); return h; }
__device__ __forceinline__ void halb4(u16* o, float a, float b, float c, float d) {
    *(uint2*)o = make_uint2((u32)f2h(a) | ((u32)f2h(b) << 16), (u32)f2h(c) | ((u32)f2h(d) << 16));
}
__device__ __forceinline__ void mma16816(float* c, const u32* a, u32 b0, u32 b1) {
    asm volatile("mma.sync.aligned.m16n8k16.row.col.f32.f16.f16.f32 {%0,%1,%2,%3}, {%4,%5,%6,%7}, {%8,%9}, "
                 "{%0,%1,%2,%3};"
                 : "+f"(c[0]), "+f"(c[1]), "+f"(c[2]), "+f"(c[3])
                 : "r"(a[0]), "r"(a[1]), "r"(a[2]), "r"(a[3]), "r"(b0), "r"(b1));
}
// Raw bytes of one slot (8 values of a half block) - loaded a K step ahead, unpacked after the next
// barrier, so the loads of step k+1 run while the tensor cores work on step k.
struct Roh { u32 a, b, c, d, e; };
__device__ __forceinline__ Roh laden_q4k(const u8* b, int hb, int s) {
    const int lane = 16 * hb + s;
    Roh r;
    r.a = (u32)ld32(b + 16 + 4 * lane);
    r.b = *(const u32*)b;
    r.c = *(const u32*)(b + 4); r.d = *(const u32*)(b + 8); r.e = *(const u32*)(b + 12);
    return r;
}
__device__ __forceinline__ int sc_byte(const Roh& r, int k) {
    const u32 w = k < 4 ? r.c : (k < 8 ? r.d : r.e);
    return (int)((w >> (8 * (k & 3))) & 0xFF);
}
// ggml get_scale_min_k4 on the scale words of the slot
__device__ __forceinline__ void skala_roh(const Roh& r, int j, int* d, int* m) {
    if (j < 4) { *d = sc_byte(r, j) & 63; *m = sc_byte(r, j + 4) & 63; }
    else {
        *d = (sc_byte(r, j + 4) & 0xF) | ((sc_byte(r, j - 4) >> 6) << 4);
        *m = (sc_byte(r, j + 4) >> 4) | ((sc_byte(r, j) >> 6) << 4);
    }
}
__device__ __forceinline__ void schreib_q4k(const Roh& r, int hb, int s, u16* dst) {
    const int lane = 16 * hb + s, p = lane >> 3, t = 4 * (lane & 7);
    const int v = (int)r.a;
    int sc0, m0, sc1, m1;
    skala_roh(r, 2 * p, &sc0, &m0);
    skala_roh(r, 2 * p + 1, &sc1, &m1);
    const float d = h2f((u16)r.b), dm = h2f((u16)(r.b >> 16));
    const float a0 = d * sc0, b0 = dm * m0, a1 = d * sc1, b1 = dm * m1;
    u16* o = dst + 64 * (p - 2 * hb) + t;
    halb4(o, a0 * (v & 15) - b0, a0 * ((v >> 8) & 15) - b0, a0 * ((v >> 16) & 15) - b0, a0 * ((v >> 24) & 15) - b0);
    halb4(o + 32, a1 * ((v >> 4) & 15) - b1, a1 * ((v >> 12) & 15) - b1, a1 * ((v >> 20) & 15) - b1,
          a1 * ((v >> 28) & 15) - b1);
}
__device__ __forceinline__ Roh laden_q6k(const u8* b, int hb, int s) {
    const int j = 4 * s, g0 = j < 32 ? 0 : 1, t = j & 31;
    const signed char* sc = (const signed char*)(b + 192) + 8 * hb;
    const int i1 = 2 * g0 + (t >> 4);
    Roh r;
    r.a = (u32)ld32h(b + 64 * hb + j);
    r.b = (u32)ld32h(b + 128 + 32 * hb + t);
    r.c = ((u32)(u8)sc[i1]) | ((u32)(u8)sc[i1 + 4] << 8) | ((u32)*(const u16*)(b + 208) << 16);
    r.d = r.e = 0;
    return r;
}
__device__ __forceinline__ void schreib_q6k(const Roh& r, int hb, int s, u16* dst) {
    const int j = 4 * s, g0 = j < 32 ? 0 : 1, t = j & 31;
    const int l = (int)r.a, qh = (int)r.b;
    const float d = h2f((u16)(r.c >> 16));
    #pragma unroll
    for (int z = 0; z < 2; ++z) {
        const int g = g0 + 2 * z;
        const int vq = ((l >> (4 * z)) & 0x0F0F0F0F) | (((qh >> (2 * g)) & 0x03030303) << 4);
        const float f = d * (float)(signed char)((r.c >> (8 * z)) & 0xFF);
        halb4(dst + 32 * g + t, f * ((vq & 63) - 32), f * (((vq >> 8) & 63) - 32), f * (((vq >> 16) & 63) - 32),
              f * (((vq >> 24) & 63) - 32));
    }
}
__device__ __forceinline__ float warp_max(float v) {
    #pragma unroll
    for (int o = 16; o > 0; o >>= 1) v = fmaxf(v, __shfl_xor_sync(0xffffffffu, v, o));
    return v;
}
// input for MMQ in one kernel: one block per token row -> xs = max|x| / skala, xh = fp16(x / xs)
extern "C" __global__ void mmq_eingang(const void* __restrict__ x, int f32, u16* __restrict__ xh,
                                       float* __restrict__ xs, int n, float skala) {
    __shared__ float teil[32];
    const long long z = (long long)blockIdx.x * n;
    float m = 0.f;
    for (int i = threadIdx.x; i < n; i += blockDim.x) {
        const float v = f32 ? ((const float*)x)[z + i] : bf2f(((const u16*)x)[z + i]);
        m = fmaxf(m, fabsf(v));
    }
    m = warp_max(m);
    if ((threadIdx.x & 31) == 0) teil[threadIdx.x >> 5] = m;
    __syncthreads();
    if (threadIdx.x < 32) {
        m = threadIdx.x < (blockDim.x >> 5) ? teil[threadIdx.x] : 0.f;
        m = warp_max(m);
        if (threadIdx.x == 0) teil[0] = fmaxf(m, 1e-30f) / skala;
    }
    __syncthreads();
    const float s = teil[0], inv = 1.f / s;
    if (threadIdx.x == 0) xs[blockIdx.x] = s;
    for (int i = threadIdx.x; i < n; i += blockDim.x) {
        const float v = f32 ? ((const float*)x)[z + i] : bf2f(((const u16*)x)[z + i]);
        xh[z + i] = f2h(v * inv);
    }
}
#define MMQ_JOBS (MMQ_BM * 16 / 128)        // weight slots per thread and K step
// w: (n_out, n_in) block format; x: (N, n_in) fp16 already divided by xs; y: (N, n_out) float32, zeroed:
// blockIdx.y takes k_teil values of the sum (split-K, enough blocks for small matrices), results add up.
template <int NT, int Q6>
__device__ __forceinline__ void mmq_kern(const u8* __restrict__ w, const u16* __restrict__ x,
                                         const float* __restrict__ xs, float* __restrict__ y,
                                         int n_out, int n_in, int N, int k_teil) {
    __shared__ __align__(16) u16 sw[MMQ_BM * MMQ_LD];
    __shared__ __align__(16) u16 sx[NT * 8 * MMQ_LD];
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31, g = lane >> 2, tig = lane & 3;
    const int row0 = blockIdx.x * MMQ_BM;
    const int bs = Q6 ? 210 : 144, nbl = n_in >> 8;
    const int k0s = blockIdx.y * k_teil, k_ende = min(n_in, k0s + k_teil);
    float acc[NT][4];
    #pragma unroll
    for (int nt = 0; nt < NT; ++nt) acc[nt][0] = acc[nt][1] = acc[nt][2] = acc[nt][3] = 0.f;
    Roh roh[MMQ_JOBS];
    uint4 xr[NT];
    // load step kc into registers (weights of 64 rows, NT*8 token rows of x)
    auto laden = [&](int kc) {
        const int blk = kc >> 8, hb = (kc >> 7) & 1;
        #pragma unroll
        for (int i = 0; i < MMQ_JOBS; ++i) {
            const int job = threadIdx.x + 128 * i, r = job >> 4, s = job & 15, row = row0 + r;
            if (row < n_out) {
                const u8* b = w + ((long long)row * nbl + blk) * bs;
                roh[i] = Q6 ? laden_q6k(b, hb, s) : laden_q4k(b, hb, s);
            }
        }
        #pragma unroll
        for (int i = 0; i < NT; ++i) {
            const int job = threadIdx.x + 128 * i, n = job >> 4, q = job & 15;
            xr[i] = n < N ? *(const uint4*)(x + (long long)n * n_in + kc + 8 * q) : make_uint4(0, 0, 0, 0);
        }
    };
    if (k0s < k_ende) laden(k0s);
    for (int kc = k0s; kc < k_ende; kc += MMQ_BK) {
        const int hb = (kc >> 7) & 1;
        #pragma unroll
        for (int i = 0; i < MMQ_JOBS; ++i) {
            const int job = threadIdx.x + 128 * i, r = job >> 4, s = job & 15;
            u16* dst = sw + r * MMQ_LD;
            if (row0 + r < n_out) {
                if (Q6) schreib_q6k(roh[i], hb, s, dst); else schreib_q4k(roh[i], hb, s, dst);
            } else {
                *(uint4*)(dst + 8 * s) = make_uint4(0, 0, 0, 0);
            }
        }
        #pragma unroll
        for (int i = 0; i < NT; ++i) {
            const int job = threadIdx.x + 128 * i;
            *(uint4*)(sx + (job >> 4) * MMQ_LD + 8 * (job & 15)) = xr[i];
        }
        __syncthreads();
        if (kc + MMQ_BK < k_ende) laden(kc + MMQ_BK);      // next step in flight while the mma run
        const u16* ab = sw + (warp * 16) * MMQ_LD;
        #pragma unroll
        for (int k0 = 0; k0 < MMQ_BK; k0 += 16) {
            u32 a[4];
            a[0] = *(const u32*)(ab + g * MMQ_LD + k0 + 2 * tig);
            a[1] = *(const u32*)(ab + (g + 8) * MMQ_LD + k0 + 2 * tig);
            a[2] = *(const u32*)(ab + g * MMQ_LD + k0 + 8 + 2 * tig);
            a[3] = *(const u32*)(ab + (g + 8) * MMQ_LD + k0 + 8 + 2 * tig);
            #pragma unroll
            for (int nt = 0; nt < NT; ++nt) {
                const u16* bp = sx + (nt * 8 + g) * MMQ_LD + k0 + 2 * tig;
                mma16816(acc[nt], a, *(const u32*)bp, *(const u32*)(bp + 8));
            }
        }
        __syncthreads();
    }
    const int r = row0 + warp * 16 + g;
    #pragma unroll
    for (int nt = 0; nt < NT; ++nt) {
        const int n0 = nt * 8 + 2 * tig;
        #pragma unroll
        for (int e = 0; e < 4; ++e) {
            const int n = n0 + (e & 1), rr = r + (e >> 1) * 8;
            if (n < N && rr < n_out) atomicAdd(y + (long long)n * n_out + rr, acc[nt][e] * xs[n]);
        }
    }
}
'''

_SPERRE = threading.Lock()
_MODULE: dict = {}


def _makros() -> str:
    """RW_<Format>(nc): Gewichtszeilen je Warp im Kern, aus ZEILEN_JE_WARP (eine Stelle für beide Seiten)."""
    zeilen = []
    for f in FORMATE:
        zwei = [n for n in range(1, SPALTEN + 1) if ZEILEN_JE_WARP[f](n) == 2]
        bed = " || ".join(f"(nc) == {n}" for n in zwei) or "0"
        zeilen.append(f"#define RW_{f}(nc) (({bed}) ? 2 : 1)")
    return "".join(z + "\n" for z in zeilen)


def _mmq_huellen() -> str:
    """extern-"C"-Einstiege je Format und Kachelzahl (das Template steht in _QUELLE)."""
    return "".join(
        f'extern "C" __global__ void __launch_bounds__(128) mmq_{f}_{nt}(const u8* w, const u16* x, '
        f'const float* xs, float* y, int n_out, int n_in, int N, int k_teil) {{ mmq_kern<{nt}, {q6}>(w, x, xs, y, '
        f'n_out, n_in, N, k_teil); }}\n'
        for f, q6 in (("Q4_K", 0), ("Q6_K", 1)) for nt in range(1, MMQ_NT + 1))


def _uebersetzen(device: torch.device) -> dict:
    """NVRTC -> CUBIN -> Modul im torch-Kontext (wie cudakern)."""
    pfad = cudakern._nvrtc_pfad()
    if pfad is None:
        raise RuntimeError("NVRTC fehlt")
    cap = torch.cuda.get_device_capability(device)
    arch = f"--gpu-architecture=sm_{cap[0]}{cap[1]}"
    nvrtc = ctypes.CDLL(pfad)
    prog = ctypes.c_void_p()
    makros = _makros()
    if nvrtc.nvrtcCreateProgram(ctypes.byref(prog), (makros + _QUELLE + _mmq_huellen()).encode(), b"mmvq.cu", 0,
                                None, None):
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
    cu = cudakern._treiber()
    with torch.cuda.device(device):
        torch.zeros(1, device=device)
        mod = ctypes.c_void_p()
        if cu.cuModuleLoadData(ctypes.byref(mod), cubin):
            raise RuntimeError("cuModuleLoadData")
        fns = {"_cubin": cubin}
        for name in (*SCHRITT_KERNE, *(f"aus_{f}" for f in FORMATE),
                     *(f"mvq_{f}_{nc}" for f in FORMATE for nc in range(1, SPALTEN + 1)),
                     *(f"mmq_{f}_{nt}" for f in FORMATE for nt in range(1, MMQ_NT + 1))):
            fn = ctypes.c_void_p()
            if cu.cuModuleGetFunction(ctypes.byref(fn), mod, name.encode()):
                raise RuntimeError(f"cuModuleGetFunction {name}")
            fns[name] = fn
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
                    dbg.event("MMVQ-Kernel übersetzt (NVRTC)")
                except Exception as exc:
                    _MODULE[idx] = None
                    dbg.event(f"MMVQ-Kernel nicht verfügbar: {exc}")
    return _MODULE[idx]


def verfuegbar(device, fmt: str, n_in: int) -> bool:
    device = torch.device(device)
    return fmt in FORMATE and n_in % 256 == 0 and _funktionen(device) is not None


def _starten(fn, grid, threads: int, args: list, shared: int = 0) -> None:
    gx, gy = grid if isinstance(grid, tuple) else (grid, 1)
    werte = (ctypes.c_void_p * len(args))(*[ctypes.addressof(a) for a in args])
    strom = ctypes.c_void_p(torch.cuda.current_stream().cuda_stream)
    if cudakern._treiber().cuLaunchKernel(fn, gx, gy, 1, threads, 1, 1, shared, strom, werte, None):
        raise RuntimeError("cuLaunchKernel (mmvq)")


def _z(t: Optional[torch.Tensor]) -> ctypes.c_void_p:
    return ctypes.c_void_p(0 if t is None else t.data_ptr())


class Eingang:
    """Der int8-Eingang eines Vektors – einmal je Eingabe, auch wenn mehrere Matrizen ihn lesen.
    Ohne x nur die Puffer (ein verschmolzener Kern füllt sie)."""

    def __init__(self, x: Optional[torch.Tensor] = None, n: int = 0, device=None):
        if x is not None:
            x = x.reshape(-1)
            if x.dtype != torch.float32:
                x = x.to(torch.bfloat16)
            x = x.contiguous()
            n, device = x.numel(), x.device
        self.n = n
        self.xq = torch.empty(n, dtype=torch.int8, device=device)
        self.xq2 = torch.empty(n, dtype=torch.int8, device=device)          # Rest, Skala xd / 254
        self.xd = torch.empty(n // 32, dtype=torch.float32, device=device)
        self.xs = torch.empty(n // 32, dtype=torch.float32, device=device)
        if x is not None:
            _starten(_funktionen(x.device)["quant_q8"], -(-n // 256), 256,
                     [_z(x), ctypes.c_int(x.dtype == torch.float32), *self.zeiger(), ctypes.c_int(n)])

    def zeiger(self, ab: int = 0) -> list:
        """Zeiger auf xq, xq2, xd, xs ab Wert `ab` (Anfang einer Zeile)."""
        if not ab:
            return [_z(self.xq), _z(self.xq2), _z(self.xd), _z(self.xs)]
        return [_z(self.xq[ab:]), _z(self.xq2[ab:]), _z(self.xd[ab // 32:]), _z(self.xs[ab // 32:])]


def mv(fmt: str, raw: torch.Tensor, x, n_out: int, n_in: int) -> torch.Tensor:
    """raw: (n_out, n_in) im Blockformat auf der GPU, x: (M, n_in) oder (n_in,) bf16/float32 oder ein
    Eingang mit M Zeilen -> (M, n_out) float32 bzw. (n_out,) für einen Vektor."""
    e = x if isinstance(x, Eingang) else Eingang(x)
    M = e.n // n_in
    fns = _funktionen(raw.device)
    y = torch.empty(M, n_out, dtype=torch.float32, device=raw.device)
    for c0 in range(0, M, SPALTEN):
        nc = min(SPALTEN, M - c0)
        rw = ZEILEN_JE_WARP[fmt](nc)
        _starten(fns[f"mvq_{fmt}_{nc}"], -(-n_out // (WARPS * rw)), 32 * WARPS,
                 [_z(raw), *e.zeiger(c0 * n_in), _z(y[c0]), ctypes.c_int(n_out), ctypes.c_int(n_in)])
    vektor = M == 1 if isinstance(x, Eingang) else x.dim() == 1
    return y[0] if vektor else y


_TEILEN = threading.local()


@contextlib.contextmanager
def eingang_teilen():
    """Matrizen mit demselben Eingang (q/k/v, gate/up): der MMQ-Eingang wird darin nur einmal vorbereitet."""
    alt = getattr(_TEILEN, "werte", None)
    _TEILEN.werte = {}
    try:
        yield
    finally:
        _TEILEN.werte = alt


def _mmq_eingang(x2: torch.Tensor) -> tuple:
    """(xh fp16, xs float32) für x2 (M, n_in) – ein Kern; in eingang_teilen() einmal je Eingang."""
    geteilt = getattr(_TEILEN, "werte", None)
    schluessel = (x2.data_ptr(), tuple(x2.shape), x2.dtype)
    if geteilt is not None and schluessel in geteilt:
        return geteilt[schluessel]
    if x2.dtype not in (torch.float32, torch.bfloat16):
        x2 = x2.to(torch.bfloat16)
    x2 = x2.contiguous()
    M, n = x2.shape
    xh = torch.empty(M, n, dtype=torch.float16, device=x2.device)
    xs = torch.empty(M, dtype=torch.float32, device=x2.device)
    _starten(_funktionen(x2.device)["mmq_eingang"], M, 256,
             [_z(x2), ctypes.c_int(x2.dtype == torch.float32), _z(xh), _z(xs), ctypes.c_int(n),
              ctypes.c_float(MMQ_SKALA)])
    if geteilt is not None:
        geteilt[schluessel] = (xh, xs)
    return xh, xs


def mm(fmt: str, raw: torch.Tensor, x: torch.Tensor, n_out: int, n_in: int) -> torch.Tensor:
    """Mehrere Token auf den Tensor-Kernen: x (M, n_in) -> (M, n_out) float32 (je Aufruf 8 * MMQ_NT Token).
    Die Gewichte werden je Block einmal nach fp16 entpackt; der Eingang je Token skaliert in fp16."""
    x2 = x.reshape(-1, n_in)
    M = x2.shape[0]
    xh, s = _mmq_eingang(x2)
    y = torch.zeros(M, n_out, dtype=torch.float32, device=raw.device)
    kacheln = -(-n_out // 64)
    teile = max(1, min(n_in // 128, -(-MMQ_BLOECKE * _sm_zahl(raw.device) // kacheln)))
    k_teil = -(-(n_in // 128) // teile) * 128
    fns = _funktionen(raw.device)
    for a in range(0, M, 8 * MMQ_NT):                  # mehr als 64 Token: in Gruppen
        m = min(8 * MMQ_NT, M - a)
        _starten(fns[f"mmq_{fmt}_{-(-m // 8)}"], (kacheln, -(-n_in // k_teil)), 128,
                 [_z(raw), _z(xh[a]), _z(s[a:]), _z(y[a]), ctypes.c_int(n_out), ctypes.c_int(n_in), ctypes.c_int(m),
                  ctypes.c_int(k_teil)])
    return y


_SM: dict = {}


def _sm_zahl(device) -> int:
    i = device.index if device.index is not None else torch.cuda.current_device()
    if i not in _SM:
        _SM[i] = torch.cuda.get_device_properties(i).multi_processor_count
    return _SM[i]


def entpacken(fmt: str, raw: torch.Tensor, n_out: int, n_in: int) -> torch.Tensor:
    """raw: (n_out, n_in) im Blockformat -> (n_out, n_in) bf16."""
    bs, _ = FORMATE[fmt]
    n_bl = n_out * n_in // bs
    out = torch.empty(n_out, n_in, dtype=torch.bfloat16, device=raw.device)
    _starten(_funktionen(raw.device)[f"aus_{fmt}"], -(-n_bl * 32 // 256), 256,
             [_z(raw), _z(out), ctypes.c_longlong(n_bl)])
    return out


# ---- ein Token je Schritt: verschmolzene Kleinschritte (Eingaben float32, zusammenhängend) ---------

SHARED_MAX = 48 * 1024        # ohne Freischaltung größerer Shared-Memory-Blöcke


def norm_q8(h: torch.Tensor, w: torch.Tensor, eps: float, rest: Optional[torch.Tensor] = None,
            rs: float = 1.0):
    """RMSNorm einer Zeile. Mit `rest` zuerst h = h + rs * rest. -> (normiert float32, Eingang, neues h)."""
    n = h.numel()
    e = Eingang(n=n, device=h.device)
    xn = torch.empty(n, dtype=torch.float32, device=h.device)
    hn = torch.empty(n, dtype=torch.float32, device=h.device) if rest is not None else None
    _starten(_funktionen(h.device)["norm_q8"], 1, 1024,
             [_z(h), _z(rest), ctypes.c_float(rs), _z(hn), _z(w), ctypes.c_float(eps), _z(xn), *e.zeiger(),
              ctypes.c_int(n)])
    return xn, e, (h if hn is None else hn)


def akt_q8(g: torch.Tensor, u: Optional[torch.Tensor], gelu: bool):
    """act(g) * u (ohne u: act(g)) -> (float32, Eingang)."""
    n = g.numel()
    e = Eingang(n=n, device=g.device)
    a = torch.empty(n, dtype=torch.float32, device=g.device)
    _starten(_funktionen(g.device)["akt_q8"], -(-n // 256), 256,
             [_z(g), _z(u), ctypes.c_int(int(gelu)), _z(a), *e.zeiger(), ctypes.c_int(n)])
    return a, e


def rope_kv(q, k, v, bias: tuple, rope, pos: torch.Tensor, norms: tuple, eps: float, q_temp,
            kc: torch.Tensor, vc: torch.Tensor, H: int, Hkv: int, D: int) -> torch.Tensor:
    """Bias, Norm je Kopf, RoPE, Abfrage-Temperatur; Schlüssel/Werte an Platz pos der bf16-Caches
    (Hkv, cap, D). -> Abfrage (H*D) float32."""
    qo = torch.empty(H * D, dtype=torch.float32, device=q.device)
    beta, tn = q_temp if q_temp is not None else (0.0, 0)
    cos = sin = None
    rd, neox = 0, 1
    if rope is not None:
        cos, sin, rd, neox = rope.cos, rope.sin, rope.dim, int(rope.neox)
    _starten(_funktionen(q.device)["rope_kv"], H + 2 * Hkv, D,
             [_z(q), _z(k), _z(v), *[_z(b) for b in bias], _z(cos), _z(sin), ctypes.c_int(rd), ctypes.c_int(neox),
              _z(pos), *[_z(n) for n in norms], ctypes.c_float(eps), ctypes.c_float(beta), ctypes.c_int(int(tn)),
              _z(qo), _z(kc), _z(vc), ctypes.c_int(kc.shape[1]), ctypes.c_int(H), ctypes.c_int(Hkv),
              ctypes.c_int(D)], shared=(D + 32) * 4)
    return qo


def attention1(q: torch.Tensor, kc: torch.Tensor, vc: torch.Tensor, pos: torch.Tensor, kv_len: int,
               scale: float, window: int, sinks: Optional[torch.Tensor], H: int, Hkv: int, D: int):
    """Attention eines Tokens über die Plätze 0..pos (höchstens kv_len) -> (H*D float32, Eingang)."""
    dev = q.device
    n = min(kv_len, kc.shape[1])
    ac = 32 if n <= 512 else 64 if n <= 1024 else 128           # Schlüssel je Block (gemessen)
    chunks = -(-n // ac)
    G = H // Hkv
    po = torch.empty(H * chunks * D, dtype=torch.float32, device=dev)
    pm = torch.empty(H * chunks, dtype=torch.float32, device=dev)
    pl = torch.empty(H * chunks, dtype=torch.float32, device=dev)
    fns = _funktionen(dev)
    _starten(fns["attn1_teil"], (chunks, Hkv), 128,
             [_z(q), _z(kc), _z(vc), _z(pos), ctypes.c_int(kc.shape[1]), ctypes.c_float(scale),
              ctypes.c_int(window), _z(po), _z(pm), _z(pl), ctypes.c_int(H), ctypes.c_int(Hkv), ctypes.c_int(D),
              ctypes.c_int(chunks), ctypes.c_int(ac)], shared=(max(G, 16) * D + G * ac) * 4)
    out = torch.empty(H * D, dtype=torch.float32, device=dev)
    e = Eingang(n=H * D, device=dev)
    _starten(fns["attn1_summe"], H, D,
             [_z(po), _z(pm), _z(pl), _z(sinks), _z(out), *e.zeiger(), ctypes.c_int(D), ctypes.c_int(chunks)])
    return out, e


def schritt_moeglich(device, H: int, Hkv: int, D: int) -> bool:
    """Passen Kopfzahl und -größe zu den verschmolzenen Kernen?"""
    G = H // Hkv if Hkv else 0
    return (_funktionen(torch.device(device)) is not None and Hkv > 0 and H % Hkv == 0 and D in (64, 128, 256)
            and (max(G, 16) * D + G * 128) * 4 <= SHARED_MAX)
