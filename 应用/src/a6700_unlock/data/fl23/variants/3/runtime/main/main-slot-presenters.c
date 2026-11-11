typedef unsigned int u32;typedef unsigned long long u64;
static struct {u64 original[2];u64 cookie;} binding __attribute__((used))={{0,0},0x31525350};
int fl23_presenters_bind(u64 first,u64 second) {
 if(!first||!second||(first&3)||(second&3))return 1;
 binding.original[0]=first;binding.original[1]=second;return 0;
}
int fl23_presenter_extend(u64 *mask) {
 if(!mask||((u64)mask&7))return 1;
 for(u32 group=0;group<11;group++) {
  u32 present=0;
  for(u32 slot=0;slot<16;slot++) {
   u32 id=0xea7+group*16+slot;
   present|=(mask[id/64]>>(id%64))&1;
  }
  if(present)for(u32 slot=0;slot<2;slot++) {
   u32 id=10563U+group*2+slot;mask[id/64]|=1ULL<<(id%64);
  }
 }
 return 0;
}
void fl23_presenter_main(u64 root,u64 *mask) {
 if(!binding.original[0])return;
 ((void(*)(u64,u64*))binding.original[0])(root,mask);fl23_presenter_extend(mask);
}
void fl23_presenter_options(u64 root,u64 *mask) {
 if(!binding.original[1])return;
 ((void(*)(u64,u64*))binding.original[1])(root,mask);fl23_presenter_extend(mask);
}
