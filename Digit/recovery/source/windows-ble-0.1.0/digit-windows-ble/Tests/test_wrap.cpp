#include "digit_queue.h"
#include <array>
#include <cassert>
#include <iostream>
int main(){
 void* q=digit_create();assert(q);std::array<std::uint8_t,8> r{};std::uint64_t t=0,last=0;
 assert(digit_gate(q,1)==1);assert(digit_arm(q)==1);
 assert(digit_take(q,r.data(),&t)==1);assert(r==decltype(r){});assert(digit_complete(q,t,1)==1);last=t;
 constexpr int keys[]={7,12,10,12,23};
 for(int batch=0;batch<10000;batch++){
  assert(digit_fixed_test(q)==1);
  for(int key:keys){
   assert(digit_take(q,r.data(),&t)==1);assert(t>last);last=t;
   decltype(r) expected{};expected[2]=static_cast<std::uint8_t>(key);assert(r==expected);
   assert(digit_complete(q,t,1)==1);
   assert(digit_take(q,r.data(),&t)==1);assert(t>last);last=t;assert(r==decltype(r){});
   assert(digit_complete(q,t,1)==1);
  }
  assert(digit_pending(q)==0);
 }
 digit_stop(q);assert(digit_release_needed(q)==1);digit_destroy(q);
 std::cout<<"PASS 10000 repeated batches / 100000 report completions; ring wrap and monotonically increasing tokens\n";
}
