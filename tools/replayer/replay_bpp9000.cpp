// Rebuilds BPP-9000 ant-colony networks from public data with qubic/core's own
// reference scorer, and reads their output past the graded frame -- the colony's
// call for the coming hours. See docs/CONCEPT_BTC_PREDICTION.de.md and build.sh.
//
// Batch protocol on stdin, one command per line (hex is lower or upper case):
//
//   seed <hex32> [poolFile]              epoch spectrum digest; builds the 512 MB pool,
//                                        in memory or (with poolFile) in a mapped file
//   targets <0/1 string>                 the task's targets as far as they are known
//   load <id> <shift> <annHex>           a network replayed earlier (ANN layout)
//   walk <id> <parent|root> <pubkey> <nonce> <anchorDigest> <shiftLimit>
//        -> "res <id> <error> <shift>"   replays one solution on its parent
//   dump <id>      -> "ann <id> <shift> <annHex>"
//   calls <id> <n> -> "calls <id> <trits>"   outputs of emits WINDOW_WIDTH .. +n-1
//
// ANN layout (as qubic/core exports it): P*K uint16 wiring (LE), P start trits,
// P*27 LUT bytes. A walk whose parent is unknown answers "err <id> no-parent".
#include "bpp9000_settings_shim.h"
#include "score_bpp9000_reference.h"

#include <cstdio>
#include <fcntl.h>
#include <iostream>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

struct Params
{
    static constexpr unsigned long long numberOfInputNeurons = BPP9000_NUMBER_OF_INPUT_NEURONS;
    static constexpr unsigned long long numberOfOutputNeurons = BPP9000_NUMBER_OF_OUTPUT_NEURONS;
    static constexpr unsigned long long sequenceLength = BPP9000_SEQUENCE_LENGTH;
    static constexpr unsigned long long windowWidth = BPP9000_WINDOW_WIDTH;
    static constexpr unsigned long long maxNumberOfTicks = BPP9000_MAX_NUMBER_OF_TICKS;
    static constexpr unsigned long long numberOfNeighbors = BPP9000_NUMBER_OF_NEIGHBORS;
    static constexpr unsigned long long populationThreshold = BPP9000_POPULATION_THRESHOLD;
    static constexpr unsigned long long numberOfMutations = BPP9000_NUMBER_OF_MUTATIONS;
    static constexpr unsigned int solutionThreshold = BPP9000_SOLUTION_THRESHOLD_DEFAULT;
    static constexpr unsigned long long shiftCap = BPP9000_SHIFT_CAP;
};
using Miner = score_bpp9000_reference::Miner<Params>;
static constexpr unsigned long long P = Params::populationThreshold;
static constexpr unsigned long long LINKS = P * Params::numberOfNeighbors;
static constexpr unsigned long long LUT = P * 27;

struct Net
{
    std::vector<unsigned char> initial = std::vector<unsigned char>(P);
    std::vector<unsigned int> wiring = std::vector<unsigned int>(LINKS);
    std::vector<unsigned char> lut = std::vector<unsigned char>(LUT);
    unsigned int shift = 0;
};

static int nib(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static bool hexBytes(const std::string& s, unsigned char* out, size_t n)
{
    if (s.size() < 2 * n) return false;
    for (size_t i = 0; i < n; ++i)
    {
        const int hi = nib(s[2 * i]), lo = nib(s[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (unsigned char)(hi * 16 + lo);
    }
    return true;
}

static std::string toHex(const unsigned char* p, size_t n)
{
    static const char* d = "0123456789abcdef";
    std::string s(2 * n, '0');
    for (size_t i = 0; i < n; ++i) { s[2 * i] = d[p[i] >> 4]; s[2 * i + 1] = d[p[i] & 15]; }
    return s;
}

static void toMiner(const Net& net, Miner& m)
{
    memcpy(m.curInitial, net.initial.data(), P);
    for (unsigned long long i = 0; i < LINKS; ++i) m.neighborIndices[i] = net.wiring[i];
    memcpy(m.curLut, net.lut.data(), LUT);
}

static Net fromBest(const Miner& m, unsigned int shift)
{
    Net net;
    memcpy(net.initial.data(), m.bestInitial, P);
    for (unsigned long long i = 0; i < LINKS; ++i) net.wiring[i] = m.bestNeighborIndices[i];
    memcpy(net.lut.data(), m.bestLut, LUT);
    net.shift = shift;
    return net;
}

static std::string annHex(const Net& net)
{
    std::vector<unsigned char> b;
    b.reserve(LINKS * 2 + P + LUT);
    for (unsigned long long i = 0; i < LINKS; ++i) { b.push_back(net.wiring[i] & 0xFF); b.push_back((net.wiring[i] >> 8) & 0xFF); }
    b.insert(b.end(), net.initial.begin(), net.initial.end());
    b.insert(b.end(), net.lut.begin(), net.lut.end());
    return toHex(b.data(), b.size());
}

static bool parseAnn(const std::string& h, Net& net)
{
    std::vector<unsigned char> b(LINKS * 2 + P + LUT);
    if (!hexBytes(h, b.data(), b.size())) return false;
    size_t o = 0;
    for (unsigned long long i = 0; i < LINKS; ++i, o += 2) net.wiring[i] = (unsigned int)(b[o] | (b[o + 1] << 8));
    for (unsigned long long n = 0; n < P; ++n) net.initial[n] = b[o++];
    for (unsigned long long i = 0; i < LUT; ++i) net.lut[i] = b[o++];
    return true;
}

// The network's output trit for every emit from WINDOW_WIDTH on, `n` of them.
static std::string calls(Miner& m, const Net& net, unsigned long long n)
{
    toMiner(net, m);
    for (unsigned long long k = 0; k < P; ++k) m.neuronOut[k] = m.curInitial[k];
    const unsigned long long want = Params::windowWidth + n;
    std::string out;
    unsigned long long counter = 0, ticks = 0;
    while (counter < want)
    {
        if (++ticks >= Params::maxNumberOfTicks * 2) break;     // a stalled clock: no call
        memcpy(m.neuronPrev, m.neuronOut, P);
        for (unsigned long long k = 0; k < P; ++k)
        {
            const unsigned long long t0 = m.neuronPrev[m.neighborIndices[k * 3 + 0]];
            const unsigned long long t1 = m.neuronPrev[m.neighborIndices[k * 3 + 1]];
            const unsigned long long t2 = m.neuronPrev[m.neighborIndices[k * 3 + 2]];
            m.neuronOut[k] = m.curLut[k * 27 + (t0 + 3 * t1 + 9 * t2)];
        }
        if (m.neuronOut[m.controlIndex] != Miner::TRIT_UNKNOWN)
        {
            if (counter >= Params::windowWidth) out.push_back((char)('0' + m.neuronOut[m.outputIndex]));
            counter++;
        }
    }
    return out;
}

// The pool, written to `path` once (regenerated when its size is wrong) and
// mapped read-only. generateRandom2Pool, chunk by chunk, so no 512 MB buffer
// ever exists in memory.
static unsigned char* mapPool(const unsigned char seed[32], const std::string& path)
{
    const size_t size = score_reference::POOL_VEC_PADDING_SIZE;
    struct stat st;
    if (stat(path.c_str(), &st) != 0 || (size_t)st.st_size != size)
    {
        const std::string tmp = path + ".tmp";
        FILE* f = std::fopen(tmp.c_str(), "wb");
        if (!f) return nullptr;
        unsigned char state[200];
        memcpy(state, seed, 32);
        memset(state + 32, 0, sizeof(state) - 32);
        std::vector<unsigned char> buf(200 * 4096);
        size_t done = 0;
        while (done < size)
        {
            size_t inBuf = 0;
            while (inBuf < buf.size() && done + inBuf < size)
            {
                KeccakP1600_Permute_12rounds(state);
                memcpy(buf.data() + inBuf, state, 200);
                inBuf += 200;
            }
            if (std::fwrite(buf.data(), 1, inBuf, f) != inBuf) { std::fclose(f); return nullptr; }
            done += inBuf;
        }
        if (std::fclose(f) != 0 || std::rename(tmp.c_str(), path.c_str()) != 0) return nullptr;
    }
    const int fd = open(path.c_str(), O_RDONLY);
    if (fd < 0) return nullptr;
    void* p = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    return p == MAP_FAILED ? nullptr : (unsigned char*)p;
}

int main()
{
    std::ios::sync_with_stdio(false);
    auto m = std::make_unique<Miner>();
    bool seeded = false;
    std::unordered_map<std::string, Net> nets;
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "seed")
        {
            std::string h, poolFile; in >> h >> poolFile;
            unsigned char seed[32];
            if (!hexBytes(h, seed, 32)) { std::printf("err seed bad-hex\n"); return 2; }
            if (poolFile.empty())
            {
                m->initialize(seed);
            }
            else
            {
                unsigned char* pool = mapPool(seed, poolFile);
                if (!pool) { std::printf("err seed pool-file\n"); std::fflush(stdout); continue; }
                m->poolVec.mapped = pool;
                m->deriveControlOutput(seed);
            }
            seeded = true;
            std::printf("ok seed\n");
        }
        else if (cmd == "targets")
        {
            std::string t; in >> t;
            memset(m->targetOutputs, 0, sizeof(m->targetOutputs));
            for (size_t i = 0; i < t.size() && i < Params::sequenceLength; ++i)
                m->targetOutputs[i] = (unsigned char)(t[i] == '1' ? 1 : 0);
            std::printf("ok targets %zu\n", t.size());
        }
        else if (cmd == "load")
        {
            std::string id, h; unsigned int shift = 0; in >> id >> shift >> h;
            Net net; net.shift = shift;
            if (!parseAnn(h, net)) { std::printf("err %s bad-ann\n", id.c_str()); continue; }
            nets[id] = std::move(net);
        }
        else if (cmd == "walk")
        {
            std::string id, parent, pkH, nonceH, anchorH; unsigned long long limit = 0;
            in >> id >> parent >> pkH >> nonceH >> anchorH >> limit;
            unsigned char pk[32], nonce[32], anchor[32];
            if (!seeded || !hexBytes(pkH, pk, 32) || !hexBytes(nonceH, nonce, 32) || !hexBytes(anchorH, anchor, 32))
            {
                std::printf("err %s bad-input\n", id.c_str());
                continue;
            }
            unsigned int parentShift = 0;
            if (parent == "root")
            {
                m->deriveRootMaterial(pk);
                m->applyRootMaterial();
            }
            else
            {
                auto it = nets.find(parent);
                if (it == nets.end()) { std::printf("err %s no-parent\n", id.c_str()); continue; }
                toMiner(it->second, *m);
                parentShift = it->second.shift;
            }
            m->deriveMutationSeeds(pk, nonce, anchor);
            const score_engine::Rating r = m->computeScoreFromParentShift(parentShift, limit,
                Miner::changesPerStep(nonce), nonce[2], Miner::modeOf(nonce));
            nets[id] = fromBest(*m, r.shift);
            std::printf("res %s %u %u\n", id.c_str(), r.error, r.shift);
        }
        else if (cmd == "dump")
        {
            std::string id; in >> id;
            auto it = nets.find(id);
            if (it == nets.end()) { std::printf("err %s unknown\n", id.c_str()); continue; }
            std::printf("ann %s %u %s\n", id.c_str(), it->second.shift, annHex(it->second).c_str());
        }
        else if (cmd == "calls")
        {
            std::string id; unsigned long long n = 0; in >> id >> n;
            auto it = nets.find(id);
            if (it == nets.end() || !seeded) { std::printf("err %s unknown\n", id.c_str()); continue; }
            std::printf("calls %s %s\n", id.c_str(), calls(*m, it->second, n).c_str());
        }
        else if (!cmd.empty())
        {
            std::printf("err unknown-command %s\n", cmd.c_str());
        }
        std::fflush(stdout);
    }
    return 0;
}
