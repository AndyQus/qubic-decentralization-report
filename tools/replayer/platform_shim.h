#pragma once
#include <immintrin.h>
#include <cstring>
#include <cstdint>
static inline void copyMem(void* d, const void* s, unsigned long long n) { memcpy(d, s, (size_t)n); }
static inline void setMem(void* d, unsigned long long n, unsigned char v) { memset(d, v, (size_t)n); }
#define ASSERT(x) ((void)0)
namespace score_task_file {
static inline unsigned long long packedBytes(unsigned long long n) { return (n + 4) / 5; }
static inline bool unpackTrits(const unsigned char*, unsigned long long, unsigned char*) { return false; }
}

#include <vector>
// The reference keeps its 512 MB random pool in a std::vector. The replayer may
// point it at a memory-mapped file instead (see replay_bpp9000.cpp, "seed ...
// <file>"): file pages are cache the kernel can drop under pressure, where an
// anonymous 512 MB buffer gets the process OOM-killed on a small host.
struct PoolView
{
    std::vector<unsigned char> own;
    unsigned char* mapped = nullptr;
    unsigned char* data() { return mapped ? mapped : own.data(); }
    const unsigned char* data() const { return mapped ? mapped : own.data(); }
    void resize(size_t n) { mapped = nullptr; own.resize(n); }
};
