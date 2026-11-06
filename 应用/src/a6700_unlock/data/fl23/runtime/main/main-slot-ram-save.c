typedef unsigned char u8;typedef unsigned int u32;typedef unsigned long long u64;
static struct {u64 normal,mr,queued;u32 fallback,ready;} binding __attribute__((used))={0,0,0,0,0x32564153};
int fl23_ram_save_bind(u64 normal,u64 mr,u32 fallback) {
    if(!normal||!mr||(normal&3)||(mr&3)||fallback!=0)return 1;
    binding.normal=normal;binding.mr=mr;binding.fallback=fallback;binding.ready=1;return 0;
}
/* The original internal save-worker supplies its descriptor width here.
   This is NOT the public Backup ABI: its normal adapter ignores x2, and
   its MR adapter ignores x3. Common-entry adapters below derive a width
   only for the two separately pinned selector items. */
static int fl23_ram_save_mr_bytes(const u8 *data,u8 *out) {
    if(!data)return -22;
    for(u32 i=0;i<13;i++) {
        u32 value=data[i];if(value==16||value>18)return -22;
        out[i]=(value==17||value==18)?0:value;
    }
    return 0;
}
int fl23_ram_save_normal(u32 key,const u8 *data,u64 width) {
    if(binding.ready!=1)return -19;
    if(key==0x02cf0370U) {
        u8 out[13];if(width!=13||fl23_ram_save_mr_bytes(data,out))return -22;
        return ((int(*)(u32,const u8*,u64))binding.normal)(key,out,width);
    }
    if(key!=0x02cf036fU)return ((int(*)(u32,const u8*,u64))binding.normal)(key,data,width);
    if(!data||width!=1)return -22;
    u32 value=*data;if(value==16||value>18)return -22;
    /* A crash must not make the next original boot select an extended look.
       Always persist ST, even if a legacy selection was saved in between. */
    u8 saved=0;
    const u8 *out=(value==17||value==18)?&saved:data;
    int result=((int(*)(u32,const u8*,u64))binding.normal)(key,out,width);
    return result;
}
int fl23_ram_save_mr(u32 attr,u32 key,const u8 *data,u64 width) {
    if(binding.ready!=1)return -19;
    if(key!=0x02cf0370U)return ((int(*)(u32,u32,const u8*,u64))binding.mr)(attr,key,data,width);
    if(!data||width!=1||attr>=13)return -22;
    u32 value=*data;if(value==16||value>18)return -22;
    /* Native worker passes one setting-attribute cell, not13 bytes.
       ST is the explicit temporary fallback for a new MR selection. */
    u8 saved=0;const u8 *out=(value==17||value==18)?&saved:data;
    return ((int(*)(u32,u32,const u8*,u64))binding.mr)(attr,key,out,width);
}
/* Original adapter ABIs have no width argument. The target main item is
   independently pinned to one byte; non-target items retain native behavior. */
int fl23_ram_save_normal_adapter(u32 key,const u8 *data) {
    return fl23_ram_save_normal(key,data,key==0x02cf0370U?13:1);
}
int fl23_ram_save_mr_adapter(u32 attr,u32 key,const u8 *data) {
    return fl23_ram_save_mr(attr,key,data,1);
}
int fl23_ram_save_queue_bind(u64 original) {
    if(!original||(original&3))return 1;
    binding.queued=original;return 0;
}
int fl23_ram_save_queued(u32 section,u32 key,const u8 *data) {
    if(binding.ready!=1||!binding.queued)return -19;
    if(section==0x2cf&&key==0x02cf0370U) {
        u8 out[13];if(fl23_ram_save_mr_bytes(data,out))return -22;
        return ((int(*)(u32,u32,const u8*))binding.queued)(section,key,out);
    }
    if(section!=0x2cf||key!=0x02cf036fU)
        return ((int(*)(u32,u32,const u8*))binding.queued)(section,key,data);
    if(!data||*data==16||*data>18)return -22;
    static const u8 standard=0;
    return ((int(*)(u32,u32,const u8*))binding.queued)(section,key,(*data==17||*data==18)?&standard:data);
}
