typedef unsigned int u32;typedef unsigned long long u64;typedef unsigned char u8;
static struct {u64 ordinal,flags;const u8 *ready;u64 cookie,leaf,parent,extra[2],names,help;} binding __attribute__((used))={0,0,0,0x314e454d,0,0,{0,0},0,0};
const u32 fl23_menu_base_names[12]={0x21c658eu,0x21c6654u,0x21c6434u,0x21c687bu,0x21c689au,0x21c6cc9u,0x21c6857u,0x21c6961u,0x21c65f8u,0x21c658au,0x21c70dfu,0x21c7124u};
static const u32 custom_short_names[10]={0x21c658eu,0x21c6654u,0x21c6434u,0x21c687bu,0x21c689au,0x21c6cc9u,0x21c6857u,0x21c6961u,0x21c65f8u,0x21c658au};
int fl23_menu_bind(u64 ordinal,u64 flags,const u8 *ready) {
 if(!ordinal||!flags||!ready||(ordinal&3)||(flags&3))return 1;
 binding.ordinal=ordinal;binding.flags=flags;binding.ready=ready;binding.help=ordinal+(0x2a15454ULL-0x22d2fc8ULL);return 0;
}
u32 fl23_menu_inhibit2(void){return !binding.ready||binding.ready[0]?1:((u32(*)(u32,u32))binding.flags)(0x530,17);}
u32 fl23_menu_inhibit3(void){return !binding.ready||binding.ready[1]?1:((u32(*)(u32,u32))binding.flags)(0x530,18);}
int fl23_menu_bind_leaves(u64 leaf,u64 parent,u64 first,u64 second) {
 if(!leaf||!parent||(leaf&3)||(parent&3)||first<6*160||(first&7)||second!=first+160)return 1;
 if(*(const u32*)first!=0x23c3||*(const u32*)second!=0x23c4||
    *(const u32*)(first+0x48)!=17||*(const u32*)(second+0x48)!=18)return 2;
 u64 names=first-6*160;
 for(u32 i=0;i<12;i++){
  u32 visible=i<6?i:i<10?i+2:i-4;
  u32 id=i<10?0x6ab+i:0x23c3+i-10;
  u32 selector=i<10?i:17+i-10;
  const u8 *row=(const u8*)(names+visible*160);
  if(*(const u32*)row!=id||*(const u32*)(row+4)!=fl23_menu_base_names[i]||
     *(const u32*)(row+0x48)!=selector)return 3;
 }
 binding.leaf=leaf;binding.parent=parent;binding.extra[0]=first;binding.extra[1]=second;binding.names=names;return 0;
}
/* Preserve the original per-look explanation switch after localizing the
   custom caption. This is the one original callsite, not a global text hook. */
u32 fl23_menu_caption_help(u64 object,u32 caption) {
 if(!binding.help)return 0x20025ed;
 for(u32 i=0;i<10;i++)if(caption==fl23_menu_base_names[i])return ((u32(*)(u64,u32))binding.help)(object,custom_short_names[i]);
 if(caption==fl23_menu_base_names[10])return 0x21c71e8;
 if(caption==fl23_menu_base_names[11])return 0x21c7279;
 return ((u32(*)(u64,u32))binding.help)(object,caption);
}
u64 fl23_menu_leaf(u64 object,u32 id) {
 if(id>=0x23c3&&id<=0x23c4&&binding.extra[id-0x23c3])return binding.extra[id-0x23c3];
 if(id>=0x6ab&&id<=0x6b4&&binding.names){u32 i=id-0x6ab;return binding.names+(i<6?i:i+2)*160;}
 return binding.leaf?((u64(*)(u64,u32))binding.leaf)(object,id):0;
}
u32 fl23_menu_parent(u64 object,u32 id) {
 if(id>=0x23c3&&id<=0x23c4&&binding.extra[id-0x23c3])return 0x197;
 return binding.parent?((u32(*)(u64,u32))binding.parent)(object,id):0x820;
}
/* The shipped custom-caption consumer indexes its old16-slot array by a
   difference of visible ordinals. Remove only the two inserted visible main
   choices before its selected old custom leaf; keep the original scan. */
int fl23_menu_slot_ordinal(u64 object,u32 value) {
 if(!binding.ordinal)return -1;
 int result=((int(*)(u64,u32))binding.ordinal)(object,value);
 if(value>=0x6b5&&value<=0x6ba&&result>=0) {
  int removed=0;
  for(u32 extra=0x23c3;extra<=0x23c4;extra++) {
   int index=((int(*)(u64,u32))binding.ordinal)(object,extra);
   if(index>=0&&index<result)removed++;
  }
  return result-removed;
 }
 return result;
}
