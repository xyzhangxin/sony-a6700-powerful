typedef unsigned int u32;typedef unsigned long long u64;
static struct {u64 find,cell,native,owned;} binding;
static u64 created,bias_at;
int fl23_flags_create(u64 bias,u64 *owned,const u64 *begin,const u64 *end) {
 if(created||!bias||(bias&4095)||!owned||((u64)owned&7)||!begin||((u64)begin&7)||
    (u64)end-(u64)begin!=22*16)return 1;
 for(u32 i=0;i<7;i++)if(owned[i])return 2;
 if(*(const u64*)(bias+0x2fad25c)!=0x1e2e1000a9ba7bfdULL)return 3;
 for(u32 i=0;i<22;i++) {
  if(begin[i*2]!=10563U+i||!begin[i*2+1]||(begin[i*2+1]&7))return 4;
  const u64 *d=(const u64*)begin[i*2+1];
  if(!d[0]||d[1]!=(i<6?12:0))return 4;
 }
 ((void(*)(u64*,const u64*,const u64*,u64))(bias+0x2fad25c))(owned,begin,end,0);
 created=(u64)owned;bias_at=bias;return owned[3]==22?0:5;
}
void fl23_flags_disable(void){binding.owned=0;}
int fl23_flags_destroy(u64 bias,u64 *owned) {
 if(!owned||created!=(u64)owned||bias_at!=bias||owned[3]!=22)return 1;
 if(binding.owned==(u64)owned)return 2;
 if(*(const u64*)(bias+0x2f6c7e4)!=0x910003fda9be7bfdULL||
    *(const u64*)(bias+0x4e800e8)!=bias+0x2f6c7e4)return 3;
 ((void(*)(u64*))(bias+0x2f6c7e4))(owned);
 for(u32 i=0;i<7;i++)owned[i]=0;
 created=0;bias_at=0;return 0;
}
int fl23_flags_bind(u64 find,u64 cell,u64 native,u64 owned) {
 if(!find||!cell||!native||!owned)return 1;
 binding.find=find;binding.cell=cell;binding.native=native;binding.owned=owned;return 0;
}
static u64 route(u64 map,const u32 *key) {
 return binding.owned&&map==binding.native&&key&&*key>=10563U&&*key<10563U+22?binding.owned:map;
}
u64 fl23_flag_find(u64 map,const u32 *key) {
 return ((u64(*)(u64,const u32*))binding.find)(route(map,key),key);
}
u64 fl23_flag_cell(u64 map,const u32 *key) {
 return ((u64(*)(u64,const u32*))binding.cell)(route(map,key),key);
}
