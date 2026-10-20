#include "digit_queue.h"
#include <array>
#include <algorithm>
#include <new>
using Report = std::array<std::uint8_t, 8>;
struct FixedFifo {
    std::array<Report,16> reports{};
    std::size_t head=0, count=0;
    bool empty() const { return count==0; }
    std::size_t size() const { return count; }
    void clear() { head=0; count=0; }
    void push_back(const Report& r) { reports[(head+count)%reports.size()]=r; ++count; }
    Report front() const { return reports[head]; }
    void pop_front() { head=(head+1)%reports.size(); --count; }
};
struct Queue {
    bool eligible=false, release=false, in_flight=false;
    int state=0;
    std::uint64_t next=0, active=0;
    FixedFifo fifo;
};
static Queue& q(void* p) { return *static_cast<Queue*>(p); }
static void halt(Queue& s, int state) {
    if (s.state==1 || s.in_flight || !s.fifo.empty()) s.release=true;
    s.fifo.clear(); s.state=state;
    // Keep in-flight token: its completion cannot pop a replacement report.
}
DIGIT_API void* digit_create() { try { return new Queue; } catch (...) { return nullptr; } }
DIGIT_API void digit_destroy(void* p) { delete static_cast<Queue*>(p); }
DIGIT_API int digit_gate(void* p,int eligible) {
    auto& s=q(p); s.eligible=eligible!=0;
    if (!s.eligible && s.state==1) { halt(s,2); return -1; } return 1;
}
DIGIT_API int digit_arm(void* p) {
    auto& s=q(p);
    if (!s.eligible || s.state==1 || s.in_flight || s.release || !s.fifo.empty()) return 0;
    s.state=1; s.fifo.push_back(Report{}); return 1; // neutral baseline first
}
DIGIT_API int digit_encode(std::uint8_t modifier,const std::uint8_t* usages,int count,std::uint8_t* out) {
    if (!out || count<0 || count>6 || (count && !usages)) return 0;
    Report r{}; r[0]=modifier;
    for (int i=0;i<count;++i) {
        if (usages[i]<4 || usages[i]>0x65) return 0;
        for(int j=0;j<i;++j) if(usages[i]==usages[j]) return 0;
        r[i+2]=usages[i];
    }
    std::copy(r.begin(),r.end(),out); return 1;
}
DIGIT_API int digit_fixed_test(void* p) {
    auto& s=q(p); if(s.state!=1 || !s.eligible) return 0;
    // Exactly 'digit' in US keyboard layout. No Enter, shortcuts or arbitrary input.
    constexpr std::uint8_t keys[]={7,12,10,12,23};
    constexpr std::size_t cap=16;
    if(s.fifo.size()+10+(s.in_flight?1:0)>cap) { halt(s,2); return -1; }
    for(auto key:keys) { Report r{}; r[2]=key; s.fifo.push_back(r); s.fifo.push_back(Report{}); }
    return 1;
}
DIGIT_API int digit_take(void* p,std::uint8_t* out,std::uint64_t* token) {
    auto& s=q(p); if(!out || !token || s.state!=1 || !s.eligible || s.in_flight || s.fifo.empty()) return 0;
    if(s.next==UINT64_MAX) { halt(s,2); return -1; }
    const auto r=s.fifo.front(); s.fifo.pop_front(); s.active=++s.next; s.in_flight=true;
    std::copy(r.begin(),r.end(),out); *token=s.active; return 1;
}
DIGIT_API int digit_complete(void* p,std::uint64_t token,int success) {
    auto& s=q(p); if(!s.in_flight || s.active!=token) return 0;
    s.in_flight=false;
    if(!success) { halt(s,2); s.release=true; return -1; }
    return 1;
}
DIGIT_API void digit_stop(void* p) { halt(q(p),0); }
DIGIT_API void digit_fault(void* p) { halt(q(p),2); }
DIGIT_API int digit_state(void* p) { return q(p).state; }
DIGIT_API int digit_pending(void* p) { return static_cast<int>(q(p).fifo.size())+(q(p).in_flight?1:0); }
DIGIT_API int digit_release_needed(void* p) {
    auto& s=q(p); if(s.in_flight || !s.release) return 0;
    s.release=false; return 1;
}
