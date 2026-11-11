typedef unsigned int u32;typedef unsigned long long u64;
static struct {u64 original,native,owned,cookie;} binding __attribute__((used))={0,0,0,0x3153424f};
static u64 created,bias_at;
int fl23_observer_create(u64 bias,u64 owned,u64 begin,u64 end) {
 if(created||!bias||(bias&4095)||!owned||(owned&7)||!begin||(begin&7)||end-begin!=18*32)return 1;
 for(unsigned i=0;i<7;i++)if(((const u64*)owned)[i])return 2;
 if(*(const u64*)(bias+0x2d0e100)!=0x1e2e1000a9ba7bfdULL)return 3;
 ((void(*)(u64,u64,u64,u64))(bias+0x2d0e100))(owned,begin,end,0);
 created=owned;bias_at=bias;return ((const u64*)owned)[3]==18?0:4;
}
static u64 observed_node(u64 bias,u64 key) {
 u64 *map=(u64*)(bias+0x8d22a70),count=map[1];u32 value=key;
 if(!count||count>1000000)return 0;
 u64 previous=((u64(*)(u64,u64,const u32*))(bias+0x2cc1cc0))((u64)map,key%count,&value);
 u64 node=previous?*(const u64*)previous:0;
 return node&&*(const u32*)(node+8)==key?node:0;
}
static int vector_shape(u64 begin,u64 end,u64 capacity,u64 stride,u64 maximum) {
 return !(begin&7)&&(!capacity||begin)&&begin<=end&&end<=capacity&&capacity<0x8000000000ULL&&
  end-begin<=maximum&&!((end-begin)%stride);
}
/* Read the current application's registrations, not historical callback
   addresses supplied by a host snapshot. Only caller-owned input is written. */
int fl23_observer_clone(u64 bias,u64 owned,u64 memory,u64 bytes) {
 if(created||!bias||(bias&4095)||bias>=0x8000000000ULL-0x15000000ULL||
    !owned||(owned&7)||!memory||(memory&7)||bytes<576||bytes>0x10000||
    owned>=0x8000000000ULL-56||memory>=0x8000000000ULL-bytes||
    (owned<memory+bytes&&memory<owned+56))return 10;
 if(*(const u32*)(bias+0x2cc1cc0)!=0xaa0003e6U)return 11;
 u64 cursor=memory+576,end=memory+bytes;u32 records=0;
 for(u32 group=0;group<11;group++) {
  u64 representative=observed_node(bias,0xea7+group*16),lo=0,hi=0,cap=0;
  if(representative) {const u64 *v=(const u64*)(representative+16);lo=v[0];hi=v[1];cap=v[2];}
  if(!vector_shape(lo,hi,cap,24,240)||!!(hi-lo)!=(group!=1&&group!=2))return 12;
  for(u64 at=lo;at<hi;at+=24) {
   const u64 *v=(const u64*)at;
   if(!vector_shape(v[0],v[1],v[2],16,160)||v[0]==v[1])return 12;
  }
  for(u32 slot=1;slot<16;slot++) {
   u64 node=observed_node(bias,0xea7+group*16+slot),a=0,b=0,c=0;
   if(node) {const u64 *v=(const u64*)(node+16);a=v[0];b=v[1];c=v[2];}
   if(!vector_shape(a,b,c,24,240)||b-a!=hi-lo)return 13;
   for(u64 offset=0;offset<hi-lo;offset+=24) {
    const u64 *left=(const u64*)(lo+offset),*right=(const u64*)(a+offset);
    if(!vector_shape(right[0],right[1],right[2],16,160)||left[1]-left[0]!=right[1]-right[0])return 13;
    for(u64 i=0;i<left[1]-left[0];i++)if(((const unsigned char*)left[0])[i]!=((const unsigned char*)right[0])[i])return 13;
   }
  }
  if(lo==hi)continue;
  for(u32 slot=0;slot<2;slot++) {
   u64 extent=hi-lo;if(extent>end-cursor)return 14;
   u64 vectors=cursor;cursor+=extent;
   for(u64 offset=0;offset<extent;offset+=24) {
    const u64 *source=(const u64*)(lo+offset);u64 count=source[1]-source[0];
    if(count>end-cursor)return 14;
    u64 *target=(u64*)(vectors+offset);target[0]=cursor;target[1]=cursor+count;target[2]=cursor+count;
    for(u64 i=0;i<count;i++)((unsigned char*)cursor)[i]=((const unsigned char*)source[0])[i];
    cursor+=count;
   }
   u64 *record=(u64*)(memory+records*32);record[0]=10563U+group*2+slot;
   record[1]=vectors;record[2]=vectors+extent;record[3]=vectors+extent;records++;
  }
 }
 if(records!=18)return 15;
 return fl23_observer_create(bias,owned,memory,memory+576);
}
int fl23_observer_disable(u64 owned) {
 if(!owned||binding.owned!=owned)return 1;
 binding.original=0;binding.native=0;binding.owned=0;return 0;
}
int fl23_observer_destroy(u64 bias,u64 owned) {
 if(!owned||created!=owned||bias_at!=bias)return 1;
 if(binding.owned==owned)return 2;
 if(*(const u64*)(bias+0x2ce6fa4)!=0x910003fda9bd7bfdULL)return 3;
 ((void(*)(u64))(bias+0x2ce6fa4))(owned);
 for(unsigned i=0;i<7;i++)((u64*)owned)[i]=0;
 created=0;bias_at=0;return 0;
}
int fl23_observer_bind(u64 original,u64 native,u64 owned) {
 if(!original||!native||!owned||(original&3)||(native&7)||(owned&7))return 1;
 if(!((const u64*)owned)[1])return 2;
 if(binding.owned)return 3;
 if(created&&created!=owned)return 4;
 binding.original=original;binding.native=native;binding.owned=owned;return 0;
}
u64 fl23_observer_lookup(u64 header,u64 bucket,const u32 *key) {
 if(!binding.original)return 0;
 if(header==binding.native&&key&&*key>=10563U&&*key<10563U+22) {
  header=binding.owned;bucket=*key%((const u64*)header)[1];
 }
 return ((u64(*)(u64,u64,const u32*))binding.original)(header,bucket,key);
}
