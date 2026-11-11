typedef unsigned int u32; typedef unsigned long long u64;
struct row { u32 live; u32 object[5]; };
struct extension { u64 magic; u32 version,ready; struct row rows[11][2]; u32 shadow[11][2]; };
struct parameter {u32 type,id,name_bytes,padding; u64 name;};
/* Nonzero cookie keeps the owned binding in the emitted data segment. */
static struct {struct extension *active; u64 cookie; u64 legacy[10]; const struct parameter *catalog;
               u64 old_name,old_descriptor,old_reverse,old_notify,scalar_notify,lookup,subscribers;
               } binding __attribute__((used))={0,0x31444e4942333246ULL,{0},0,0,0,0,0,0,0,0};
static const u64 addresses[11]={0x8d61704ULL,0x8d61884ULL,0x8d61a04ULL,0x8d61b84ULL,0x8d61c84ULL,0x8d61d84ULL,0x8d61e84ULL,0x8d61f84ULL,0x8d62084ULL,0x8d62184ULL,0x8d62284ULL};
static const u32 strides[11]={20,20,20,12,12,12,12,12,12,12,12};
static const u32 defaults[8]={0,0,0,0,0,4,3,1};
static const int lower[11]={0,0,0,-9,-9,-9,0,-9,0,1,0};
static const int upper[11]={11,11,11,9,9,9,9,9,9,5,9};
#define MAGIC 0x314e49414d333246ULL
int fl23_main_index(u32 value) {
    if(value<16)return value;
    if(value==17||value==18)return value-1;
    return -1;
}
u32 fl23_main_context_size(void) {return sizeof(struct extension);}
/* Fresh caller-owned RW memory only. No allocator, discovery or publication. */
int fl23_main_initialize(struct extension *e) {
    if(!e||((u64)e&15))return 1;
    u32 *words=(u32*)e;
    for(u32 i=0;i<sizeof(*e)/4;i++)words[i]=0;
    e->magic=MAGIC; e->version=2;
    for(u32 group=0;group<11;group++)for(u32 index=0;index<2;index++) {
        u32 value=group<3?10+index:defaults[group-3];
        e->rows[group][index].live=value;
        e->rows[group][index].object[0]=value;
        e->rows[group][index].object[1]=value;
        e->shadow[group][index]=value;
    }
    e->ready=1;
    return 0;
}
u64 fl23_main_cell(struct extension *e,u64 bias,u32 group,u32 selector,u32 object) {
    int index=fl23_main_index(selector);
    if(group>=11||object>1||index<0||!e||((u64)e&15)||(bias&4095)||
       bias>0x00007fffffffffffULL-0x8d62384ULL)return 0;
    if(e->magic!=MAGIC||e->version!=2||e->ready!=1)return 0;
    if(index<16)return bias+addresses[group]+(object?64+index*strides[group]:index*4);
    return (u64)(object?e->rows[group][index-16].object:&e->rows[group][index-16].live);
}
/* New slots must get explicit bindings; never alias the next group's IDs. */
u32 fl23_main_legacy_parameter(u32 group,u32 selector) {
    if(group>=11||selector>=16)return 0xffffffffU;
    return 0xea7+group*16+selector;
}
/* Input ABI consumed by the original A6700 0x34fde68 message builder. */
int fl23_main_message_input(struct extension *e,u64 bias,u32 selector,u32 token,u32 *out) {
    u64 cell=fl23_main_cell(e,bias,0,selector,0);
    if(!cell)return 1;
    out[0]=token; out[1]=selector; out[2]=*(const u32*)cell;
    return 0;
}
int fl23_main_bind(struct extension *e) {
    if(!e||((u64)e&15)||e->magic!=MAGIC||e->version!=2||e->ready!=1)return 1;
    binding.active=e; return 0;
}
void fl23_main_unbind(void) {binding.active=0;}
static struct row *bound_row(u32 group,u32 slot) {
    struct extension *e=binding.active;
    if(!e||e->magic!=MAGIC||e->version!=2||e->ready!=1)return 0;
    return &e->rows[group][slot];
}
int fl23_main_bind_paths(struct extension *e,const u64 *paths) {
    if(!paths)return 1;
    for(u32 i=0;i<10;i++)if(!paths[i]||(paths[i]&3))return 1;
    if(fl23_main_bind(e))return 1;
    for(u32 i=0;i<10;i++)binding.legacy[i]=paths[i];
    return 0;
}
int fl23_main_bind_catalog(const struct parameter *catalog,u64 names,u64 descriptors,u64 reverse) {
    if(!bound_row(0,0)||!catalog||((u64)catalog&7)||!names||!descriptors||!reverse||(names&3)||(descriptors&3)||(reverse&3))return 1;
    for(u32 i=0;i<22;i++)if(catalog[i].id!=10563U+i||catalog[i].type!=(i<6?0:1)||
        !catalog[i].name||!catalog[i].name_bytes||catalog[i].name_bytes>255)return 1;
    binding.catalog=catalog;binding.old_name=names;binding.old_descriptor=descriptors;binding.old_reverse=reverse;return 0;
}
u32 fl23_main_reverse_bridge(const char *name) {
    if(!name)return 0xffffffffU;
    if(bound_row(0,0)&&binding.catalog)for(u32 row=0;row<22;row++) {
        const struct parameter *p=&binding.catalog[row];const char *s=(const char*)p->name;
        u32 i=0;while(i<p->name_bytes&&name[i]==s[i]) {if(!s[i])return p->id; i++;}
    }
    return binding.old_reverse?((u32(*)(const char*))binding.old_reverse)(name):0xffffffffU;
}
u64 fl23_main_name_bridge(u32 id) {
    if(id>=10563U&&id<10563U+22)
        return bound_row(0,0)&&binding.catalog?binding.catalog[id-10563U].name:0;
    return binding.old_name?((u64(*)(u32))binding.old_name)(id):0;
}
u64 fl23_main_descriptor_bridge(u32 id) {
    if(id>=10563U&&id<10563U+22)
        return bound_row(0,0)&&binding.catalog?(u64)&binding.catalog[id-10563U]:0;
    return binding.old_descriptor?((u64(*)(u32))binding.old_descriptor)(id):0;
}
int fl23_main_bind_events(u64 original,u64 scalar,u64 lookup,u64 subscribers) {
    if(!bound_row(0,0)||!original||!scalar||!lookup||!subscribers||
       (original&3)||(scalar&3)||(lookup&3)||(subscribers&7))return 1;
    binding.old_notify=original;binding.scalar_notify=scalar;binding.lookup=lookup;binding.subscribers=subscribers;
    return 0;
}
void fl23_main_notify_bridge(u64 context,u32 id,const u32 *before,const u32 *after) {
    if(id>=10563U&&id<10563U+22) {
        if(!bound_row(0,0)||!binding.catalog||!binding.lookup||!binding.scalar_notify||!before||!after)return;
        u64 found=((u64(*)(u64,const u32*))binding.lookup)(binding.subscribers,&id);
        ((void(*)(u64,u32,u32,const u32*,const u32*))binding.scalar_notify)(context,found?3:0,id,before,after);
        return;
    }
    if(binding.old_notify)((void(*)(u64,u32,const u32*,const u32*))binding.old_notify)(context,id,before,after);
}
u32 fl23_main_parameter(u32 group,u32 selector) {
    if(group>=11||fl23_main_index(selector)<0)return 0xffffffffU;
    if(selector<16)return 0xea7+group*16+selector;
    return 10563U+group*2+selector-17;
}
int fl23_main_value_allowed(u32 group,u32 value) {
    if(group>=11)return 0;
    return (int)value>=lower[group]&&(int)value<=upper[group];
}
/* Whole-parameter mutation is distinct from low-level record/live accessors.
   Caller owns the model lock and supplies a valid native notification queue.
   No other group or old model cell is written by this additional-only API. */
int fl23_main_set_value(u64 context,u32 id,u32 value) {
    if(id<10563U||id>=10563U+22)return 1;
    u32 index=id-10563U,group=index/2,slot=index%2;
    struct row *r=bound_row(group,slot);
    if(!r||!binding.catalog||!binding.lookup||!binding.scalar_notify)return 2;
    if(!fl23_main_value_allowed(group,value))return 3;
    u32 previous=r->object[0];
    if(previous==value&&r->live==value)return 0;
    fl23_main_notify_bridge(context,id,&previous,&value);
    r->object[0]=value;r->live=value;
    return 0;
}
int fl23_main_reset_value(u64 context,u32 id) {
    if(id<10563U||id>=10563U+22)return 1;
    u32 index=id-10563U;struct row *r=bound_row(index/2,index%2);
    if(!r)return 2;
    return fl23_main_set_value(context,id,r->object[1]);
}
int fl23_main_model_projection(struct extension *e,u64 bias,u32 selector,u32 *out) {
    if(!out||((u64)out&3)||selector>18)return 1;
    u64 at=(u64)out,owner=(u64)e;
    if(at>~0ULL-16||owner>~0ULL-sizeof(*e)||
       (owner&&at<owner+sizeof(*e)&&owner<at+16))return 1;
    u32 value[9],route=selector==16?0:selector;
    for(u32 item=0;item<9;item++) {
        u32 group=item?item+2:0;u64 cell=fl23_main_cell(e,bias,group,route,0);
        if(!cell)return 2;
        value[item]=*(const u32*)cell;
        if(selector>=17&&!fl23_main_value_allowed(group,value[item]))return 3;
    }
    /* Exact16B native mirror payload. Invalid16 retains the original
       index0 fallback; new selectors17/18 read only the owned extra cells. */
    out[0]=selector;out[1]=value[0];
    unsigned char *options=(unsigned char*)out+8;
    for(u32 item=1;item<9;item++)options[item-1]=(unsigned char)value[item];
    return 0;
}
static int page_valid(const struct extension *e,const u32 *page,u32 bytes) {
    if(!e||((u64)e&15)||!page||((u64)page&3)||bytes!=120||
       e->magic!=MAGIC||e->version!=2||e->ready!=1)return 0;
    u64 p=(u64)page,c=(u64)e;
    if(p>~0ULL-bytes||c>~0ULL-sizeof(*e))return 0;
    return !(p<c+sizeof(*e)&&c<p+bytes);
}
static u32 page_crc(const u32 *payload) {
    const unsigned char *bytes=(const unsigned char*)payload;u32 crc=0xffffffffU;
    for(u32 i=0;i<88;i++) {
        crc^=bytes[i];for(u32 bit=0;bit<8;bit++)crc=(crc>>1)^((0U-(crc&1U))&0xedb88320U);
    }
    return ~crc;
}
/* Offline storage contract only: no Backup key, path, SAVE or device API.
   Pack the snapshots supplied by native-style per-parameter callbacks.
   Restoring a validated page updates the owned live/record/shadow values,
   preserving every default, old slot, identity and unrelated setting. */
int fl23_main_pack_shadow(const struct extension *e,u32 *page,u32 bytes) {
    if(!page_valid(e,page,bytes))return 1;
    for(u32 g=0;g<11;g++)for(u32 s=0;s<2;s++)
        if(!fl23_main_value_allowed(g,e->shadow[g][s]))return 2;
    page[0]=0x33324c46U;page[1]=0x31304353U;page[2]=1;page[3]=120;
    page[4]=11;page[5]=2;page[7]=0;
    for(u32 g=0;g<11;g++)for(u32 s=0;s<2;s++)page[8+g*2+s]=e->shadow[g][s];
    page[6]=page_crc(page+8);return 0;
}
int fl23_main_unpack_shadow(struct extension *e,const u32 *page,u32 bytes) {
    if(!page_valid(e,page,bytes))return 1;
    if(page[0]!=0x33324c46U||page[1]!=0x31304353U||page[2]!=1||page[3]!=120||
       page[4]!=11||page[5]!=2||page[7]||page[6]!=page_crc(page+8))return 2;
    for(u32 g=0;g<11;g++)for(u32 s=0;s<2;s++)
        if(!fl23_main_value_allowed(g,page[8+g*2+s]))return 3;
    /* Every header/value is checked before the first target mutation. */
    for(u32 g=0;g<11;g++)for(u32 s=0;s<2;s++) {
        u32 value=page[8+g*2+s];e->rows[g][s].live=value;
        e->rows[g][s].object[0]=value;e->shadow[g][s]=value;
    }
    return 0;
}
struct saved_bank {u64 magic;u32 version,bytes;u32 pages[14][30];};
#define BANK_MAGIC 0x314b424e33324c46ULL
u32 fl23_main_bank_size(void) {return sizeof(struct saved_bank);}
static int bank_buffer(const struct extension *e,const struct saved_bank *bank,u32 bytes) {
    if(!e||!bank||((u64)e&15)||((u64)bank&7)||bytes!=sizeof(*bank)||
       e->magic!=MAGIC||e->version!=2||e->ready!=1)return 0;
    u64 p=(u64)bank,c=(u64)e;
    if(p>~0ULL-bytes||c>~0ULL-sizeof(*e))return 0;
    return !(p<c+sizeof(*e)&&c<p+bytes);
}
/* Bank0 is normal. Banks1..13 hold the thirteen independent MR snapshots.
   These are private-buffer indices, not assumed camera setting attributes. */
int fl23_main_bank_initialize(const struct extension *e,struct saved_bank *bank,u32 bytes) {
    if(!bank_buffer(e,bank,bytes))return 1;
    for(u32 g=0;g<11;g++)for(u32 s=0;s<2;s++)
        if(!fl23_main_value_allowed(g,e->shadow[g][s]))return 2;
    for(u32 index=0;index<14;index++) {
        int rc=fl23_main_pack_shadow(e,bank->pages[index],120);if(rc)return rc;
    }
    bank->magic=BANK_MAGIC;bank->version=1;bank->bytes=sizeof(*bank);return 0;
}
int fl23_main_bank_store(const struct extension *e,struct saved_bank *bank,u32 bytes,u32 index) {
    if(!bank_buffer(e,bank,bytes)||index>=14||bank->magic!=BANK_MAGIC||bank->version!=1||bank->bytes!=bytes)return 1;
    return fl23_main_pack_shadow(e,bank->pages[index],120);
}
int fl23_main_bank_load(struct extension *e,const struct saved_bank *bank,u32 bytes,u32 index) {
    if(!bank_buffer(e,bank,bytes)||index>=14||bank->magic!=BANK_MAGIC||bank->version!=1||bank->bytes!=bytes)return 1;
    return fl23_main_unpack_shadow(e,bank->pages[index],120);
}
/* Public IDs and dense row indices are separate. Original lifecycle events
   7520..10562 are never treated as the extra primitive parameters. */
static u64 dispatch_bridge(u32 op,u32 id,u32 value) {
    if(id>=10563U&&id<10563U+22) {
        u32 index=id-10563U,group=index/2,slot=index%2;
        struct row *r=bound_row(group,slot);
        if(!r)return 0;
        switch(op) {
        case 0:return group!=1&&group!=2;
        case 1:return r->object[1];
        case 2:r->object[0]=value;if(group==1||group==2)r->live=value;return value;
        case 3:r->live=value;if(group==1||group==2)r->object[0]=value;return value;
        case 4:return r->object[0];
        case 5:return r->live;
        case 6:binding.active->shadow[group][slot]=r->object[0];return 0;
        case 7:value=binding.active->shadow[group][slot];r->object[0]=value;r->live=value;return 0;
        case 8:return (u64)((group==1||group==2)?&r->live:r->object);
        case 9:return 4;
        }
    }
    if(id<7520U&&op<10&&binding.legacy[op])
        return ((u64(*)(u32,u32))binding.legacy[op])(id,value);
    return 0;
}
u64 fl23_bridge_flag(u32 id,u32 value){return dispatch_bridge(0,id,value);}
u64 fl23_bridge_default(u32 id,u32 value){return dispatch_bridge(1,id,value);}
u64 fl23_bridge_record_set(u32 id,u32 value){return dispatch_bridge(2,id,value);}
u64 fl23_bridge_live_set(u32 id,u32 value){return dispatch_bridge(3,id,value);}
u64 fl23_bridge_record_get(u32 id,u32 value){return dispatch_bridge(4,id,value);}
u64 fl23_bridge_live_get(u32 id,u32 value){return dispatch_bridge(5,id,value);}
u64 fl23_bridge_snapshot(u32 id,u32 value){return dispatch_bridge(6,id,value);}
u64 fl23_bridge_restore(u32 id,u32 value){return dispatch_bridge(7,id,value);}
u64 fl23_bridge_object(u32 id,u32 value){return dispatch_bridge(8,id,value);}
u64 fl23_bridge_width(u32 id,u32 value){return dispatch_bridge(9,id,value);}
u32 fl23_slot_00_0_flag(void){return bound_row(0,0)!=0 && 1;}
u32 fl23_slot_00_0_default(void){struct row *r=bound_row(0,0); return r?r->object[1]:0;}
u32 fl23_slot_00_0_record_set(u32 v){struct row *r=bound_row(0,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_00_0_live_set(u32 v){struct row *r=bound_row(0,0); if(r){r->live=v; } return v;}
u32 fl23_slot_00_0_record_get(void){struct row *r=bound_row(0,0); return r?r->object[0]:0;}
u32 fl23_slot_00_0_live_get(void){struct row *r=bound_row(0,0); return r?r->live:0;}
void fl23_slot_00_0_snapshot(void){struct row *r=bound_row(0,0); if(r)binding.active->shadow[0][0]=r->object[0];}
void fl23_slot_00_0_restore(void){struct row *r=bound_row(0,0); if(r){u32 v=binding.active->shadow[0][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_00_0_object(void){struct row *r=bound_row(0,0); return r?(u64)r->object:0;}
u32 fl23_slot_00_0_width(void){return 4;}
u32 fl23_slot_00_1_flag(void){return bound_row(0,1)!=0 && 1;}
u32 fl23_slot_00_1_default(void){struct row *r=bound_row(0,1); return r?r->object[1]:0;}
u32 fl23_slot_00_1_record_set(u32 v){struct row *r=bound_row(0,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_00_1_live_set(u32 v){struct row *r=bound_row(0,1); if(r){r->live=v; } return v;}
u32 fl23_slot_00_1_record_get(void){struct row *r=bound_row(0,1); return r?r->object[0]:0;}
u32 fl23_slot_00_1_live_get(void){struct row *r=bound_row(0,1); return r?r->live:0;}
void fl23_slot_00_1_snapshot(void){struct row *r=bound_row(0,1); if(r)binding.active->shadow[0][1]=r->object[0];}
void fl23_slot_00_1_restore(void){struct row *r=bound_row(0,1); if(r){u32 v=binding.active->shadow[0][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_00_1_object(void){struct row *r=bound_row(0,1); return r?(u64)r->object:0;}
u32 fl23_slot_00_1_width(void){return 4;}
u32 fl23_slot_01_0_flag(void){return bound_row(1,0)!=0 && 0;}
u32 fl23_slot_01_0_default(void){struct row *r=bound_row(1,0); return r?r->object[1]:0;}
u32 fl23_slot_01_0_record_set(u32 v){struct row *r=bound_row(1,0); if(r){r->object[0]=v; r->live=v;} return v;}
u32 fl23_slot_01_0_live_set(u32 v){struct row *r=bound_row(1,0); if(r){r->live=v; r->object[0]=v;} return v;}
u32 fl23_slot_01_0_record_get(void){struct row *r=bound_row(1,0); return r?r->object[0]:0;}
u32 fl23_slot_01_0_live_get(void){struct row *r=bound_row(1,0); return r?r->live:0;}
void fl23_slot_01_0_snapshot(void){struct row *r=bound_row(1,0); if(r)binding.active->shadow[1][0]=r->object[0];}
void fl23_slot_01_0_restore(void){struct row *r=bound_row(1,0); if(r){u32 v=binding.active->shadow[1][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_01_0_object(void){struct row *r=bound_row(1,0); return r?(u64)&r->live:0;}
u32 fl23_slot_01_0_width(void){return 4;}
u32 fl23_slot_01_1_flag(void){return bound_row(1,1)!=0 && 0;}
u32 fl23_slot_01_1_default(void){struct row *r=bound_row(1,1); return r?r->object[1]:0;}
u32 fl23_slot_01_1_record_set(u32 v){struct row *r=bound_row(1,1); if(r){r->object[0]=v; r->live=v;} return v;}
u32 fl23_slot_01_1_live_set(u32 v){struct row *r=bound_row(1,1); if(r){r->live=v; r->object[0]=v;} return v;}
u32 fl23_slot_01_1_record_get(void){struct row *r=bound_row(1,1); return r?r->object[0]:0;}
u32 fl23_slot_01_1_live_get(void){struct row *r=bound_row(1,1); return r?r->live:0;}
void fl23_slot_01_1_snapshot(void){struct row *r=bound_row(1,1); if(r)binding.active->shadow[1][1]=r->object[0];}
void fl23_slot_01_1_restore(void){struct row *r=bound_row(1,1); if(r){u32 v=binding.active->shadow[1][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_01_1_object(void){struct row *r=bound_row(1,1); return r?(u64)&r->live:0;}
u32 fl23_slot_01_1_width(void){return 4;}
u32 fl23_slot_02_0_flag(void){return bound_row(2,0)!=0 && 0;}
u32 fl23_slot_02_0_default(void){struct row *r=bound_row(2,0); return r?r->object[1]:0;}
u32 fl23_slot_02_0_record_set(u32 v){struct row *r=bound_row(2,0); if(r){r->object[0]=v; r->live=v;} return v;}
u32 fl23_slot_02_0_live_set(u32 v){struct row *r=bound_row(2,0); if(r){r->live=v; r->object[0]=v;} return v;}
u32 fl23_slot_02_0_record_get(void){struct row *r=bound_row(2,0); return r?r->object[0]:0;}
u32 fl23_slot_02_0_live_get(void){struct row *r=bound_row(2,0); return r?r->live:0;}
void fl23_slot_02_0_snapshot(void){struct row *r=bound_row(2,0); if(r)binding.active->shadow[2][0]=r->object[0];}
void fl23_slot_02_0_restore(void){struct row *r=bound_row(2,0); if(r){u32 v=binding.active->shadow[2][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_02_0_object(void){struct row *r=bound_row(2,0); return r?(u64)&r->live:0;}
u32 fl23_slot_02_0_width(void){return 4;}
u32 fl23_slot_02_1_flag(void){return bound_row(2,1)!=0 && 0;}
u32 fl23_slot_02_1_default(void){struct row *r=bound_row(2,1); return r?r->object[1]:0;}
u32 fl23_slot_02_1_record_set(u32 v){struct row *r=bound_row(2,1); if(r){r->object[0]=v; r->live=v;} return v;}
u32 fl23_slot_02_1_live_set(u32 v){struct row *r=bound_row(2,1); if(r){r->live=v; r->object[0]=v;} return v;}
u32 fl23_slot_02_1_record_get(void){struct row *r=bound_row(2,1); return r?r->object[0]:0;}
u32 fl23_slot_02_1_live_get(void){struct row *r=bound_row(2,1); return r?r->live:0;}
void fl23_slot_02_1_snapshot(void){struct row *r=bound_row(2,1); if(r)binding.active->shadow[2][1]=r->object[0];}
void fl23_slot_02_1_restore(void){struct row *r=bound_row(2,1); if(r){u32 v=binding.active->shadow[2][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_02_1_object(void){struct row *r=bound_row(2,1); return r?(u64)&r->live:0;}
u32 fl23_slot_02_1_width(void){return 4;}
u32 fl23_slot_03_0_flag(void){return bound_row(3,0)!=0 && 1;}
u32 fl23_slot_03_0_default(void){struct row *r=bound_row(3,0); return r?r->object[1]:0;}
u32 fl23_slot_03_0_record_set(u32 v){struct row *r=bound_row(3,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_03_0_live_set(u32 v){struct row *r=bound_row(3,0); if(r){r->live=v; } return v;}
u32 fl23_slot_03_0_record_get(void){struct row *r=bound_row(3,0); return r?r->object[0]:0;}
u32 fl23_slot_03_0_live_get(void){struct row *r=bound_row(3,0); return r?r->live:0;}
void fl23_slot_03_0_snapshot(void){struct row *r=bound_row(3,0); if(r)binding.active->shadow[3][0]=r->object[0];}
void fl23_slot_03_0_restore(void){struct row *r=bound_row(3,0); if(r){u32 v=binding.active->shadow[3][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_03_0_object(void){struct row *r=bound_row(3,0); return r?(u64)r->object:0;}
u32 fl23_slot_03_0_width(void){return 4;}
u32 fl23_slot_03_1_flag(void){return bound_row(3,1)!=0 && 1;}
u32 fl23_slot_03_1_default(void){struct row *r=bound_row(3,1); return r?r->object[1]:0;}
u32 fl23_slot_03_1_record_set(u32 v){struct row *r=bound_row(3,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_03_1_live_set(u32 v){struct row *r=bound_row(3,1); if(r){r->live=v; } return v;}
u32 fl23_slot_03_1_record_get(void){struct row *r=bound_row(3,1); return r?r->object[0]:0;}
u32 fl23_slot_03_1_live_get(void){struct row *r=bound_row(3,1); return r?r->live:0;}
void fl23_slot_03_1_snapshot(void){struct row *r=bound_row(3,1); if(r)binding.active->shadow[3][1]=r->object[0];}
void fl23_slot_03_1_restore(void){struct row *r=bound_row(3,1); if(r){u32 v=binding.active->shadow[3][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_03_1_object(void){struct row *r=bound_row(3,1); return r?(u64)r->object:0;}
u32 fl23_slot_03_1_width(void){return 4;}
u32 fl23_slot_04_0_flag(void){return bound_row(4,0)!=0 && 1;}
u32 fl23_slot_04_0_default(void){struct row *r=bound_row(4,0); return r?r->object[1]:0;}
u32 fl23_slot_04_0_record_set(u32 v){struct row *r=bound_row(4,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_04_0_live_set(u32 v){struct row *r=bound_row(4,0); if(r){r->live=v; } return v;}
u32 fl23_slot_04_0_record_get(void){struct row *r=bound_row(4,0); return r?r->object[0]:0;}
u32 fl23_slot_04_0_live_get(void){struct row *r=bound_row(4,0); return r?r->live:0;}
void fl23_slot_04_0_snapshot(void){struct row *r=bound_row(4,0); if(r)binding.active->shadow[4][0]=r->object[0];}
void fl23_slot_04_0_restore(void){struct row *r=bound_row(4,0); if(r){u32 v=binding.active->shadow[4][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_04_0_object(void){struct row *r=bound_row(4,0); return r?(u64)r->object:0;}
u32 fl23_slot_04_0_width(void){return 4;}
u32 fl23_slot_04_1_flag(void){return bound_row(4,1)!=0 && 1;}
u32 fl23_slot_04_1_default(void){struct row *r=bound_row(4,1); return r?r->object[1]:0;}
u32 fl23_slot_04_1_record_set(u32 v){struct row *r=bound_row(4,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_04_1_live_set(u32 v){struct row *r=bound_row(4,1); if(r){r->live=v; } return v;}
u32 fl23_slot_04_1_record_get(void){struct row *r=bound_row(4,1); return r?r->object[0]:0;}
u32 fl23_slot_04_1_live_get(void){struct row *r=bound_row(4,1); return r?r->live:0;}
void fl23_slot_04_1_snapshot(void){struct row *r=bound_row(4,1); if(r)binding.active->shadow[4][1]=r->object[0];}
void fl23_slot_04_1_restore(void){struct row *r=bound_row(4,1); if(r){u32 v=binding.active->shadow[4][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_04_1_object(void){struct row *r=bound_row(4,1); return r?(u64)r->object:0;}
u32 fl23_slot_04_1_width(void){return 4;}
u32 fl23_slot_05_0_flag(void){return bound_row(5,0)!=0 && 1;}
u32 fl23_slot_05_0_default(void){struct row *r=bound_row(5,0); return r?r->object[1]:0;}
u32 fl23_slot_05_0_record_set(u32 v){struct row *r=bound_row(5,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_05_0_live_set(u32 v){struct row *r=bound_row(5,0); if(r){r->live=v; } return v;}
u32 fl23_slot_05_0_record_get(void){struct row *r=bound_row(5,0); return r?r->object[0]:0;}
u32 fl23_slot_05_0_live_get(void){struct row *r=bound_row(5,0); return r?r->live:0;}
void fl23_slot_05_0_snapshot(void){struct row *r=bound_row(5,0); if(r)binding.active->shadow[5][0]=r->object[0];}
void fl23_slot_05_0_restore(void){struct row *r=bound_row(5,0); if(r){u32 v=binding.active->shadow[5][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_05_0_object(void){struct row *r=bound_row(5,0); return r?(u64)r->object:0;}
u32 fl23_slot_05_0_width(void){return 4;}
u32 fl23_slot_05_1_flag(void){return bound_row(5,1)!=0 && 1;}
u32 fl23_slot_05_1_default(void){struct row *r=bound_row(5,1); return r?r->object[1]:0;}
u32 fl23_slot_05_1_record_set(u32 v){struct row *r=bound_row(5,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_05_1_live_set(u32 v){struct row *r=bound_row(5,1); if(r){r->live=v; } return v;}
u32 fl23_slot_05_1_record_get(void){struct row *r=bound_row(5,1); return r?r->object[0]:0;}
u32 fl23_slot_05_1_live_get(void){struct row *r=bound_row(5,1); return r?r->live:0;}
void fl23_slot_05_1_snapshot(void){struct row *r=bound_row(5,1); if(r)binding.active->shadow[5][1]=r->object[0];}
void fl23_slot_05_1_restore(void){struct row *r=bound_row(5,1); if(r){u32 v=binding.active->shadow[5][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_05_1_object(void){struct row *r=bound_row(5,1); return r?(u64)r->object:0;}
u32 fl23_slot_05_1_width(void){return 4;}
u32 fl23_slot_06_0_flag(void){return bound_row(6,0)!=0 && 1;}
u32 fl23_slot_06_0_default(void){struct row *r=bound_row(6,0); return r?r->object[1]:0;}
u32 fl23_slot_06_0_record_set(u32 v){struct row *r=bound_row(6,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_06_0_live_set(u32 v){struct row *r=bound_row(6,0); if(r){r->live=v; } return v;}
u32 fl23_slot_06_0_record_get(void){struct row *r=bound_row(6,0); return r?r->object[0]:0;}
u32 fl23_slot_06_0_live_get(void){struct row *r=bound_row(6,0); return r?r->live:0;}
void fl23_slot_06_0_snapshot(void){struct row *r=bound_row(6,0); if(r)binding.active->shadow[6][0]=r->object[0];}
void fl23_slot_06_0_restore(void){struct row *r=bound_row(6,0); if(r){u32 v=binding.active->shadow[6][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_06_0_object(void){struct row *r=bound_row(6,0); return r?(u64)r->object:0;}
u32 fl23_slot_06_0_width(void){return 4;}
u32 fl23_slot_06_1_flag(void){return bound_row(6,1)!=0 && 1;}
u32 fl23_slot_06_1_default(void){struct row *r=bound_row(6,1); return r?r->object[1]:0;}
u32 fl23_slot_06_1_record_set(u32 v){struct row *r=bound_row(6,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_06_1_live_set(u32 v){struct row *r=bound_row(6,1); if(r){r->live=v; } return v;}
u32 fl23_slot_06_1_record_get(void){struct row *r=bound_row(6,1); return r?r->object[0]:0;}
u32 fl23_slot_06_1_live_get(void){struct row *r=bound_row(6,1); return r?r->live:0;}
void fl23_slot_06_1_snapshot(void){struct row *r=bound_row(6,1); if(r)binding.active->shadow[6][1]=r->object[0];}
void fl23_slot_06_1_restore(void){struct row *r=bound_row(6,1); if(r){u32 v=binding.active->shadow[6][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_06_1_object(void){struct row *r=bound_row(6,1); return r?(u64)r->object:0;}
u32 fl23_slot_06_1_width(void){return 4;}
u32 fl23_slot_07_0_flag(void){return bound_row(7,0)!=0 && 1;}
u32 fl23_slot_07_0_default(void){struct row *r=bound_row(7,0); return r?r->object[1]:0;}
u32 fl23_slot_07_0_record_set(u32 v){struct row *r=bound_row(7,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_07_0_live_set(u32 v){struct row *r=bound_row(7,0); if(r){r->live=v; } return v;}
u32 fl23_slot_07_0_record_get(void){struct row *r=bound_row(7,0); return r?r->object[0]:0;}
u32 fl23_slot_07_0_live_get(void){struct row *r=bound_row(7,0); return r?r->live:0;}
void fl23_slot_07_0_snapshot(void){struct row *r=bound_row(7,0); if(r)binding.active->shadow[7][0]=r->object[0];}
void fl23_slot_07_0_restore(void){struct row *r=bound_row(7,0); if(r){u32 v=binding.active->shadow[7][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_07_0_object(void){struct row *r=bound_row(7,0); return r?(u64)r->object:0;}
u32 fl23_slot_07_0_width(void){return 4;}
u32 fl23_slot_07_1_flag(void){return bound_row(7,1)!=0 && 1;}
u32 fl23_slot_07_1_default(void){struct row *r=bound_row(7,1); return r?r->object[1]:0;}
u32 fl23_slot_07_1_record_set(u32 v){struct row *r=bound_row(7,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_07_1_live_set(u32 v){struct row *r=bound_row(7,1); if(r){r->live=v; } return v;}
u32 fl23_slot_07_1_record_get(void){struct row *r=bound_row(7,1); return r?r->object[0]:0;}
u32 fl23_slot_07_1_live_get(void){struct row *r=bound_row(7,1); return r?r->live:0;}
void fl23_slot_07_1_snapshot(void){struct row *r=bound_row(7,1); if(r)binding.active->shadow[7][1]=r->object[0];}
void fl23_slot_07_1_restore(void){struct row *r=bound_row(7,1); if(r){u32 v=binding.active->shadow[7][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_07_1_object(void){struct row *r=bound_row(7,1); return r?(u64)r->object:0;}
u32 fl23_slot_07_1_width(void){return 4;}
u32 fl23_slot_08_0_flag(void){return bound_row(8,0)!=0 && 1;}
u32 fl23_slot_08_0_default(void){struct row *r=bound_row(8,0); return r?r->object[1]:0;}
u32 fl23_slot_08_0_record_set(u32 v){struct row *r=bound_row(8,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_08_0_live_set(u32 v){struct row *r=bound_row(8,0); if(r){r->live=v; } return v;}
u32 fl23_slot_08_0_record_get(void){struct row *r=bound_row(8,0); return r?r->object[0]:0;}
u32 fl23_slot_08_0_live_get(void){struct row *r=bound_row(8,0); return r?r->live:0;}
void fl23_slot_08_0_snapshot(void){struct row *r=bound_row(8,0); if(r)binding.active->shadow[8][0]=r->object[0];}
void fl23_slot_08_0_restore(void){struct row *r=bound_row(8,0); if(r){u32 v=binding.active->shadow[8][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_08_0_object(void){struct row *r=bound_row(8,0); return r?(u64)r->object:0;}
u32 fl23_slot_08_0_width(void){return 4;}
u32 fl23_slot_08_1_flag(void){return bound_row(8,1)!=0 && 1;}
u32 fl23_slot_08_1_default(void){struct row *r=bound_row(8,1); return r?r->object[1]:0;}
u32 fl23_slot_08_1_record_set(u32 v){struct row *r=bound_row(8,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_08_1_live_set(u32 v){struct row *r=bound_row(8,1); if(r){r->live=v; } return v;}
u32 fl23_slot_08_1_record_get(void){struct row *r=bound_row(8,1); return r?r->object[0]:0;}
u32 fl23_slot_08_1_live_get(void){struct row *r=bound_row(8,1); return r?r->live:0;}
void fl23_slot_08_1_snapshot(void){struct row *r=bound_row(8,1); if(r)binding.active->shadow[8][1]=r->object[0];}
void fl23_slot_08_1_restore(void){struct row *r=bound_row(8,1); if(r){u32 v=binding.active->shadow[8][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_08_1_object(void){struct row *r=bound_row(8,1); return r?(u64)r->object:0;}
u32 fl23_slot_08_1_width(void){return 4;}
u32 fl23_slot_09_0_flag(void){return bound_row(9,0)!=0 && 1;}
u32 fl23_slot_09_0_default(void){struct row *r=bound_row(9,0); return r?r->object[1]:0;}
u32 fl23_slot_09_0_record_set(u32 v){struct row *r=bound_row(9,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_09_0_live_set(u32 v){struct row *r=bound_row(9,0); if(r){r->live=v; } return v;}
u32 fl23_slot_09_0_record_get(void){struct row *r=bound_row(9,0); return r?r->object[0]:0;}
u32 fl23_slot_09_0_live_get(void){struct row *r=bound_row(9,0); return r?r->live:0;}
void fl23_slot_09_0_snapshot(void){struct row *r=bound_row(9,0); if(r)binding.active->shadow[9][0]=r->object[0];}
void fl23_slot_09_0_restore(void){struct row *r=bound_row(9,0); if(r){u32 v=binding.active->shadow[9][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_09_0_object(void){struct row *r=bound_row(9,0); return r?(u64)r->object:0;}
u32 fl23_slot_09_0_width(void){return 4;}
u32 fl23_slot_09_1_flag(void){return bound_row(9,1)!=0 && 1;}
u32 fl23_slot_09_1_default(void){struct row *r=bound_row(9,1); return r?r->object[1]:0;}
u32 fl23_slot_09_1_record_set(u32 v){struct row *r=bound_row(9,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_09_1_live_set(u32 v){struct row *r=bound_row(9,1); if(r){r->live=v; } return v;}
u32 fl23_slot_09_1_record_get(void){struct row *r=bound_row(9,1); return r?r->object[0]:0;}
u32 fl23_slot_09_1_live_get(void){struct row *r=bound_row(9,1); return r?r->live:0;}
void fl23_slot_09_1_snapshot(void){struct row *r=bound_row(9,1); if(r)binding.active->shadow[9][1]=r->object[0];}
void fl23_slot_09_1_restore(void){struct row *r=bound_row(9,1); if(r){u32 v=binding.active->shadow[9][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_09_1_object(void){struct row *r=bound_row(9,1); return r?(u64)r->object:0;}
u32 fl23_slot_09_1_width(void){return 4;}
u32 fl23_slot_10_0_flag(void){return bound_row(10,0)!=0 && 1;}
u32 fl23_slot_10_0_default(void){struct row *r=bound_row(10,0); return r?r->object[1]:0;}
u32 fl23_slot_10_0_record_set(u32 v){struct row *r=bound_row(10,0); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_10_0_live_set(u32 v){struct row *r=bound_row(10,0); if(r){r->live=v; } return v;}
u32 fl23_slot_10_0_record_get(void){struct row *r=bound_row(10,0); return r?r->object[0]:0;}
u32 fl23_slot_10_0_live_get(void){struct row *r=bound_row(10,0); return r?r->live:0;}
void fl23_slot_10_0_snapshot(void){struct row *r=bound_row(10,0); if(r)binding.active->shadow[10][0]=r->object[0];}
void fl23_slot_10_0_restore(void){struct row *r=bound_row(10,0); if(r){u32 v=binding.active->shadow[10][0]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_10_0_object(void){struct row *r=bound_row(10,0); return r?(u64)r->object:0;}
u32 fl23_slot_10_0_width(void){return 4;}
u32 fl23_slot_10_1_flag(void){return bound_row(10,1)!=0 && 1;}
u32 fl23_slot_10_1_default(void){struct row *r=bound_row(10,1); return r?r->object[1]:0;}
u32 fl23_slot_10_1_record_set(u32 v){struct row *r=bound_row(10,1); if(r){r->object[0]=v; } return v;}
u32 fl23_slot_10_1_live_set(u32 v){struct row *r=bound_row(10,1); if(r){r->live=v; } return v;}
u32 fl23_slot_10_1_record_get(void){struct row *r=bound_row(10,1); return r?r->object[0]:0;}
u32 fl23_slot_10_1_live_get(void){struct row *r=bound_row(10,1); return r?r->live:0;}
void fl23_slot_10_1_snapshot(void){struct row *r=bound_row(10,1); if(r)binding.active->shadow[10][1]=r->object[0];}
void fl23_slot_10_1_restore(void){struct row *r=bound_row(10,1); if(r){u32 v=binding.active->shadow[10][1]; r->object[0]=v; r->live=v;}}
u64 fl23_slot_10_1_object(void){struct row *r=bound_row(10,1); return r?(u64)r->object:0;}
u32 fl23_slot_10_1_width(void){return 4;}
