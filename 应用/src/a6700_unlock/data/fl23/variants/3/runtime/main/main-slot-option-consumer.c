typedef unsigned int u32;typedef unsigned long long u64;
struct binding {u64 context,bias,cell,allowed,errors;};
struct binding fl23_option_binding __attribute__((visibility("hidden")))={0,0,0,0,0};
int fl23_option_bytes(u32 selector,unsigned char *out) {
 struct binding *b=&fl23_option_binding;u32 value[8];
 if((selector!=17&&selector!=18)||!out||!b->context||!b->cell||!b->allowed)return 1;
 for(u32 i=0;i<8;i++) {
  u64 at=((u64(*)(u64,u64,u32,u32,u32))b->cell)(b->context,b->bias,i+3,selector,0);
  if(!at)return 2;value[i]=*(const u32*)at;
  if(!((int(*)(u32,u32))b->allowed)(i+3,value[i]))return 3;
 }
 for(u32 i=0;i<8;i++)out[i]=(unsigned char)value[i];return 0;
}
static const unsigned char refused_record[12]={0,0,0,0,0,0,0,0,1,0,0,0};
u64 fl23_osd_record(u32 selector,u32 group) {
 struct binding *b=&fl23_option_binding;
 if((selector==17||selector==18)&&group>=3&&group<11&&b->context&&b->cell) {
  u64 at=((u64(*)(u64,u64,u32,u32,u32))b->cell)(b->context,b->bias,group,selector,1);
  if(at)return at;
 }
 b->errors++;return (u64)refused_record;
}
