#include "digit_queue.h"
#include <cassert>
#include <array>
#include <iostream>
#include <random>
using Bytes=std::array<std::uint8_t,8>;
int main() {
  int checks=0;
  auto check=[&](bool v){assert(v);++checks;};
  void* q=digit_create(); check(q!=nullptr); Bytes out{}; std::uint64_t token=0;
  check(digit_arm(q)==0); check(digit_fixed_test(q)==0); check(digit_take(q,out.data(),&token)==0);
  check(digit_gate(q,1)==1); check(digit_arm(q)==1); check(digit_arm(q)==0);
  check(digit_take(q,out.data(),&token)==1); check(out==Bytes{});
  check(digit_take(q,out.data(),&token)==0); check(digit_complete(q,token+1,1)==0);
  check(digit_complete(q,token,1)==1); check(digit_complete(q,token,1)==0);
  check(digit_fixed_test(q)==1);
  const int keys[]={7,12,10,12,23};
  for(int k:keys) {
    check(digit_take(q,out.data(),&token)==1); check(out[0]==0&&out[1]==0&&out[2]==k);
    check(digit_complete(q,token,1)==1); check(digit_take(q,out.data(),&token)==1);
    check(out==Bytes{}); check(digit_complete(q,token,1)==1);
  }
  check(digit_pending(q)==0); check(digit_fixed_test(q)==1); check(digit_fixed_test(q)==-1);
  check(digit_state(q)==2); check(digit_pending(q)==0); check(digit_arm(q)==0);
  check(digit_release_needed(q)==1); check(digit_release_needed(q)==0); check(digit_arm(q)==1);
  check(digit_take(q,out.data(),&token)==1); digit_stop(q);
  check(digit_state(q)==0); check(digit_release_needed(q)==0); check(digit_arm(q)==0);
  check(digit_complete(q,token,1)==1); check(digit_release_needed(q)==1); check(digit_arm(q)==1);
  check(digit_take(q,out.data(),&token)==1); check(digit_complete(q,token,0)==-1);
  check(digit_release_needed(q)==1); check(digit_state(q)==2); check(digit_arm(q)==1);
  check(digit_gate(q,0)==-1); check(digit_state(q)==2); check(digit_release_needed(q)==1);
  check(digit_arm(q)==0); check(digit_take(q,out.data(),&token)==0);
  std::uint8_t keys6[]={4,5,6,7,8,9}; check(digit_encode(2,keys6,6,out.data())==1);
  check(out[0]==2&&out[1]==0&&out[7]==9); check(digit_encode(0,keys6,7,out.data())==0);
  std::uint8_t duplicates[]={4,4}; check(digit_encode(0,duplicates,2,out.data())==0);
  std::uint8_t invalid[]={1}; check(digit_encode(0,invalid,1,out.data())==0);
  check(digit_encode(0,nullptr,0,out.data())==1); check(out==Bytes{});
  check(digit_encode(0,nullptr,1,out.data())==0); digit_destroy(q);
  q=digit_create(); std::mt19937 rng(0xD1617); bool flying=false; std::uint64_t active=0;
  for(int i=0;i<10000;++i) {
    switch(rng()%8) {
      case 0: digit_gate(q,static_cast<int>(rng()%2)); break;
      case 1: digit_arm(q); break;
      case 2: digit_fixed_test(q); break;
      case 3: if(digit_take(q,out.data(),&token)==1) {check(!flying);flying=true;active=token;} break;
      case 4: if(flying) {digit_complete(q,active,static_cast<int>(rng()%2));flying=false;} break;
      case 5: digit_stop(q);check(digit_pending(q)<=(flying?1:0));break;
      case 6: digit_fault(q);check(digit_pending(q)<=(flying?1:0));break;
      case 7: if(flying)check(digit_release_needed(q)==0);else digit_release_needed(q);break;
    }
    check(digit_pending(q)>=0 && digit_pending(q)<=16);
    check(digit_state(q)>=0 && digit_state(q)<=2);
    const int before=digit_pending(q);
    check(digit_complete(q,UINT64_MAX,1)==0);check(digit_pending(q)==before);
    if(digit_state(q)!=1)check(digit_take(q,out.data(),&token)==0);
  }
  digit_destroy(q);
  std::cout<<"PASS "<<checks<<" portable assertions (including 10,000 seeded state transitions)\n";
}
