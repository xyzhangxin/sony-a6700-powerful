typedef unsigned int u32;typedef unsigned long long u64;
static struct {u64 group_lookup,enum_lookup,group_original,enum_original,group_owned,enum_owned;} binding;
static int prologue(u64 at,u32 first,u32 second) {
 return *(const u32*)at==first&&*(const u32*)(at+4)==second;
}
static int native_guards(u64 bias) {
 if((bias&4095)||bias>0x00007fffffffffffULL-0x2cb1f3cULL)return 0;
 return prologue(bias+0x2cb1efcULL,0xd10103ffU,0xaa0503e7U)&&
  prologue(bias+0x2cb1f34ULL,0xa9ba7bfdU,0x1e2e1000U)&&
  prologue(bias+0x2cb1828ULL,0xa9be7bfdU,0x910003fdU)&&
  prologue(bias+0x2cb1888ULL,0xa9bd7bfdU,0x910003fdU);
}
struct pair {u32 key,value;};
struct base_source {u32 id,pad;struct pair values[12];};
struct entry {u32 id,pad;u64 header[7];};
/* Fresh zeroed caller-owned frames. Input pairs are validated before the
   first native allocation; temporary children are destroyed after copying. */
int fl23_inputs_create(u64 bias,u64 *groups,u64 *enums,struct entry *temporary,
 const struct pair *source,const struct base_source *bases) {
 u64 ptrs[5]={(u64)groups,(u64)enums,(u64)temporary,(u64)source,(u64)bases};
 u64 widths[5]={56,56,128,144,208};
 if(!native_guards(bias))return 1;
 for(u32 i=0;i<5;i++) {
  if(!ptrs[i]||(ptrs[i]&7)||ptrs[i]>~0ULL-widths[i])return 2;
  for(u32 j=0;j<i;j++)if(ptrs[i]<ptrs[j]+widths[j]&&ptrs[j]<ptrs[i]+widths[i])return 2;
 }
 for(u32 i=0;i<7;i++)if(groups[i]||enums[i])return 3;
 for(u32 i=0;i<16;i++)if(((u64*)temporary)[i])return 3;
 for(u32 i=0;i<18;i++)if(source[i].key!=10563U+(i<2?i:i+4)||source[i].value!=3393+i)return 4;
 for(u32 i=0;i<2;i++) {
  if(bases[i].id!=10563U+i||bases[i].pad)return 4;
  for(u32 v=0;v<12;v++)if(bases[i].values[v].key!=v||bases[i].values[v].value!=(i?24818U:24805U)+v)return 4;
 }
 u64 stateless[3]={0,0,0};
 typedef void(*simple_ctor)(void*,const struct pair*,u64,u64,void*,void*,void*);
 typedef void(*outer_ctor)(void*,const void*,const void*,u64,void*,void*,void*);
 simple_ctor make=(simple_ctor)(bias+0x2cb1efcULL);
 make(groups,source,18,0,stateless,stateless+1,stateless+2);
 for(u32 i=0;i<2;i++) {
  temporary[i].id=bases[i].id;
  make(temporary[i].header,bases[i].values,12,0,stateless,stateless+1,stateless+2);
 }
 ((outer_ctor)(bias+0x2cb1f34ULL))(enums,temporary,temporary+2,0,stateless,stateless+1,stateless+2);
 for(u32 i=0;i<2;i++)((void(*)(void*))(bias+0x2cb1828ULL))(temporary[i].header);
 return 0;
}
/* All readers must be quiesced and the owned adapters disabled first. */
int fl23_inputs_destroy(u64 bias,u64 *groups,u64 *enums) {
 if(!native_guards(bias)||!groups||!enums||((u64)groups&7)||((u64)enums&7))return 1;
 if(binding.group_owned==(u64)groups||binding.enum_owned==(u64)enums)return 2;
 if(groups[3]!=18||enums[3]!=2)return 3;
 ((void(*)(void*))(bias+0x2cb1888ULL))(enums);
 ((void(*)(void*))(bias+0x2cb1828ULL))(groups);return 0;
}
int fl23_inputs_bind(u64 g,u64 e,u64 gm,u64 em,u64 go,u64 eo) {
 if(!g||!e||!gm||!em||!go||!eo)return 1;
 if((g&3)||(e&3)||(gm&7)||(em&7)||(go&7)||(eo&7)||go==eo)return 1;
 if(binding.group_owned||binding.enum_owned)return 2;
 if(((const u64*)go)[3]!=18||((const u64*)eo)[3]!=2)return 3;
 binding.group_lookup=g;binding.enum_lookup=e;binding.group_original=gm;binding.enum_original=em;
 binding.group_owned=go;binding.enum_owned=eo;return 0;
}
/* Caller must quiesce all readers before disabling/freeing these maps. */
void fl23_inputs_disable(void) {binding.group_owned=0;binding.enum_owned=0;}
u64 fl23_input_group_lookup(u64 map,const u32 *key) {
 if(binding.group_owned&&key&&map==binding.group_original&&*key>=10563U&&*key<10563U+22&&
    !(*key>=10563U+2&&*key<10563U+6))map=binding.group_owned;
 return ((u64(*)(u64,const u32*))binding.group_lookup)(map,key);
}
u64 fl23_input_enum_lookup(u64 map,const u32 *key) {
 if(binding.enum_owned&&key&&map==binding.enum_original&&(*key==10563U||*key==10563U+1))map=binding.enum_owned;
 return ((u64(*)(u64,const u32*))binding.enum_lookup)(map,key);
}
