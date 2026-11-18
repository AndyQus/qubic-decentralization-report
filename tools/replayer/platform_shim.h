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
