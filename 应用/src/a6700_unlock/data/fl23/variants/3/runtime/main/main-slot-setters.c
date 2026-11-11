typedef unsigned int u32;typedef unsigned long long u64;
struct frame {u64 live,object;u32 id;unsigned char changed,padding[3];u64 context;};
static struct {u64 context,bias,cell,allowed,base_original,reset_original;u64 cookie;} binding __attribute__((used))={0,0,0,0,0,0,0x31544553};
int fl23_adjust_bind(u64 context,u64 bias,u64 cell,u64 allowed,u64 base_original,u64 reset_original) {
 if(!context||(context&15)||!cell||!allowed||!base_original||!reset_original||
    (cell&3)||(allowed&3)||(base_original&3)||(reset_original&3))return 1;
 binding.context=context;binding.bias=bias;binding.cell=cell;binding.allowed=allowed;
 binding.base_original=base_original;binding.reset_original=reset_original;return 0;
}
static void adjust(struct frame *frame,u32 value,u32 group,u64 original) {
 u32 selector=*((u32*(*)(void))(binding.bias+0x2f77a04ULL))();
 if(selector==17||selector==18) {
  if(!binding.context||!binding.cell||!binding.allowed||
     !((int(*)(u32,u32))binding.allowed)(group,value))return;
  u64 live=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,0);
  u64 object=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,1);
  if(!live||!object)return;
  frame->live=live;frame->object=object;frame->id=10563U+group*2+selector-17;
 }
 ((void(*)(struct frame*,u32))(binding.bias+original))(frame,value);
}

void fl23_base_set(void *unused,u32 value) {
 u32 selector=*((u32*(*)(void))(binding.bias+0x2f77a04ULL))();
 if(selector!=17&&selector!=18) {
  /* Original base enum is0..9. This entry refuses invalid custom bases;
     it is not a guard for unrelated raw Backup/PTP/import writers. */
  if(selector>15||value>9)return;
  ((void(*)(void*,u32))binding.base_original)(unused,value);return;
 }
 if(!binding.context||!binding.cell||!binding.allowed||
    !((int(*)(u32,u32))binding.allowed)(0,value))return;
 struct frame f={0};u64 context[2]={0,0},lock=0;
 f.live=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,0,selector,0);
 f.object=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,0,selector,1);
 if(!f.live||!f.object)return;
 f.id=10563U+selector-17;
 ((void(*)(void*))(binding.bias+0x19cd584ULL))(context);
 ((void(*)(void*))(binding.bias+0x3137990ULL))(&lock);
 f.context=context[1];
 ((void(*)(struct frame*,u32))(binding.bias+0x2fb4ba0ULL))(&f,value);
 ((void(*)(u32))(binding.bias+0x2d0d52cULL))(f.id);
 /* Only the actual system_mode parameter0x14d chooses the active plane.
    0x2f7b924 is auto-customize-look0x711 and must never be used here. */
 u32 mode=*((u32*(*)(void))(binding.bias+0x2f6f630ULL))();
 if(mode==2||mode==3) {
  u32 group=mode-1;struct frame alias={0};
  alias.live=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,0);
  alias.object=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,1);
  alias.id=10563U+group*2+selector-17;alias.context=context[1];
  ((void(*)(struct frame*,u32))(binding.bias+0x2fb4ba0ULL))(&alias,value);
  ((void(*)(struct frame*))(binding.bias+0x2fb4b8cULL))(&alias);
 }
 ((void(*)(void*))(binding.bias+0x313759cULL))(&lock);
 ((void(*)(void*))(binding.bias+0x1904e68ULL))(context);
 ((void(*)(u32))(binding.bias+0x2d0c930ULL))(f.id);
}

void fl23_reset_options(u32 source) {
 u32 selector=*((u32*(*)(void))(binding.bias+0x2f77a04ULL))();
 if(selector!=17&&selector!=18) {
  if(selector<=15&&source<=15)((void(*)(u32))binding.reset_original)(source);
  return;
 }
 if(!binding.context||!binding.cell||!binding.allowed||source==16||source>18)return;
 u64 live[8],object[8];u32 values[8];
 for(u32 i=0;i<8;i++) {
  u32 group=i+3;
  u64 from=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,source,1);
  live[i]=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,0);
  object[i]=((u64(*)(u64,u64,u32,u32,u32))binding.cell)(binding.context,binding.bias,group,selector,1);
  if(!from||!live[i]||!object[i])return;
  values[i]=((const u32*)from)[1];
  if(!((int(*)(u32,u32))binding.allowed)(group,values[i]))return;
 }
 static const u64 helpers[8]={0x2dba584ULL,0x2dba71cULL,0x2dba8b4ULL,0x2dbaa4cULL,0x2dbabe4ULL,0x2dbad7cULL,0x2dbaf14ULL,0x2dbb0acULL};
 u64 context[2]={0,0},lock=0;
 ((void(*)(void*))(binding.bias+0x19cd584ULL))(context);
 ((void(*)(void*))(binding.bias+0x3137990ULL))(&lock);
 for(u32 i=0;i<8;i++) {
  struct frame f={live[i],object[i],10563U+(i+3)*2+selector-17,0,{0,0,0},context[1]};
  ((void(*)(struct frame*,u32))(binding.bias+helpers[i]))(&f,values[i]);
  if(f.changed)((void(*)(u32))(binding.bias+0x2d0d52cULL))(f.id);
 }
 ((void(*)(void*))(binding.bias+0x313759cULL))(&lock);
 ((void(*)(void*))(binding.bias+0x1904e68ULL))(context);
 /* Preserve the stock bulk operation's root notification, as do the
    eight original per-option bodies. No invented mode/alias copy here. */
 ((void(*)(u32))(binding.bias+0x2d0c930ULL))(0xea7);
}
void fl23_adjust_3(struct frame *f,u32 value){adjust(f,value,3,0x2dba584ULL);}
void fl23_adjust_4(struct frame *f,u32 value){adjust(f,value,4,0x2dba71cULL);}
void fl23_adjust_5(struct frame *f,u32 value){adjust(f,value,5,0x2dba8b4ULL);}
void fl23_adjust_6(struct frame *f,u32 value){adjust(f,value,6,0x2dbaa4cULL);}
void fl23_adjust_7(struct frame *f,u32 value){adjust(f,value,7,0x2dbabe4ULL);}
void fl23_adjust_8(struct frame *f,u32 value){adjust(f,value,8,0x2dbad7cULL);}
void fl23_adjust_9(struct frame *f,u32 value){adjust(f,value,9,0x2dbaf14ULL);}
void fl23_adjust_10(struct frame *f,u32 value){adjust(f,value,10,0x2dbb0acULL);}
