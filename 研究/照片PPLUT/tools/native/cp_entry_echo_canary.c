/* SPDX-License-Identifier: GPL-3.0-only
 * Fixed private entry echo. Ordinary selectors retain stock query behavior.
 * No IRQ API, allocator, LUT setter, hook, thread or arbitrary memory write.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
struct message {u8 selector,status;unsigned short reserved;u32 marker,lo,hi,bytes,tag;};
_Static_assert(sizeof(struct message)==24,"original fixed message ABI");
typedef int (*read_fn)(u32,void*,u32);
typedef void (*cache_fn)(u32,u32);

__attribute__((used)) void cp_entry_echo_canary(struct message *p){
 u64 address=0,bytes=0;
 u32 selector=p->selector;
 p->status=0x80;p->lo=p->hi=p->bytes=0;
 if(!selector||selector>4)return;
 if(selector==1){
  u32 cfg=*(volatile u32*)0x19ca488u;
  address=cfg;bytes=*(const u32*)(cfg+0x38)+60u;
 }else{
  u32 key=0xfa000bu;
  if(selector==3)key+=0x14;
  if(selector==4)key+=0x63;
  read_fn read=(read_fn)0x55bc65u;
  if(read(key,&address,8)||read(key+(selector==4?0x17:0x35),&bytes,8)){
   p->status=0x81;return;
  }
 }
 u32 lo=(u32)address,n=(u32)bytes;
 ((cache_fn)0x5fa164u)(lo&~63u,(n+(lo&63u)+63u)&~63u);
 p->lo=lo;p->hi=(u32)(address>>32);p->bytes=n;p->status=1;
 if(selector==1&&p->marker==0x31475043u&&p->tag==0x31515249u){
  p->marker=0x31455043u; /* CPE1: fixed entry result, not an address. */
  p->tag=0x314b4341u;    /* ACK1: independent of any native API result. */
 }
}
