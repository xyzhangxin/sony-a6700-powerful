typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;

#define STATE_BYTES 4096u
#define STATE_FILE_BYTES (STATE_BYTES*2u)
#define PAGE_BYTES 120u
#define STATE_MAGIC "FL23ST01"

int fl23_main_pack_shadow(const void *extension,u8 *page,u32 bytes);
int fl23_main_unpack_shadow(void *extension,const u8 *page,u32 bytes);

static const char STATE_PATH[]="/setting/fl23nbk";
static const u8 STATE_MAGIC_BYTES[8]={'F','L','2','3','S','T','0','1'};
static const void *state_extension;
static u32 state_selector;
static u8 state_saved_page[PAGE_BYTES];
static u32 state_saved_selector;
static int state_saved_valid;

static long state_sc(long n,long a,long b,long c,long d,long e,long f) {
    register long x8 __asm__("x8")=n,x0 __asm__("x0")=a,x1 __asm__("x1")=b,
        x2 __asm__("x2")=c,x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f;
    __asm__ volatile("svc #0":"+r"(x0):"r"(x8),"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5):"memory","cc");
    return x0;
}

static void state_zero(u8 *p,u32 n) {for(u32 i=0;i<n;i++)p[i]=0;}
static void state_copy(u8 *p,const u8 *q,u32 n) {for(u32 i=0;i<n;i++)p[i]=q[i];}
static int state_same(const u8 *p,const u8 *q,u32 n) {
    u8 diff=0;for(u32 i=0;i<n;i++)diff|=p[i]^q[i];return !diff;
}
static int state_all_zero(const u8 *p,u32 n) {
    u8 value=0;for(u32 i=0;i<n;i++)value|=p[i];return !value;
}
static void state_put32(u8 *p,u32 v) {
    for(u32 i=0;i<4;i++)p[i]=(u8)(v>>(8*i));
}
static u32 state_get32(const u8 *p) {
    return (u32)p[0]|(u32)p[1]<<8|(u32)p[2]<<16|(u32)p[3]<<24;
}
static int state_selector_valid(u32 selector) {
    return selector<16u||selector==17u||selector==18u;
}
static u32 state_crc_update(u32 crc,const u8 *p,u32 n) {
    for(u32 i=0;i<n;i++) {
        crc^=p[i];
        for(u32 bit=0;bit<8;bit++)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
    }
    return crc;
}
static u32 state_record_crc(const u8 *record) {
    u32 crc=0xffffffffu;
    crc=state_crc_update(crc,record,24);
    crc=state_crc_update(crc,record+28,STATE_BYTES-28);
    return ~crc;
}
static int state_io(long nr,long fd,u8 *p,u32 n,u64 at) {
    for(u32 tries=0;n&&tries<1024;tries++) {
        long got=state_sc(nr,fd,(long)p,n,at,0,0);
        if(got==-4)continue;
        if(got<=0||(u64)got>n)return 0;
        p+=got;n-=got;at+=got;
    }
    return !n;
}
static int state_sync(long fd) {
    for(u32 i=0;i<16;i++) {
        long rc=state_sc(82,fd,0,0,0,0,0);
        if(!rc)return 1;
        if(rc!=-4)return 0;
    }
    return 0;
}
static long state_close(long fd) {return state_sc(57,fd,0,0,0,0,0);}
static int state_record_valid(const u8 *record) {
    if(!record||!state_same(record,STATE_MAGIC_BYTES,8)||state_get32(record+8)!=1u||
       state_get32(record+12)!=STATE_BYTES||!state_get32(record+16)||!state_selector_valid(state_get32(record+20))||
       state_get32(record+28)||state_get32(record+24)!=state_record_crc(record))return 0;
    for(u32 i=152;i<STATE_BYTES;i++)if(record[i])return 0;
    return 1;
}
static void state_record_make(u8 *record,u32 selector,u32 generation,const u8 *page) {
    state_zero(record,STATE_BYTES);
    state_copy(record,STATE_MAGIC_BYTES,8);
    state_put32(record+8,1);
    state_put32(record+12,STATE_BYTES);
    state_put32(record+16,generation);
    state_put32(record+20,selector);
    state_put32(record+24,0);
    state_put32(record+28,0);
    state_copy(record+32,page,PAGE_BYTES);
    state_put32(record+24,state_record_crc(record));
}

int fl23_state_bind_extension(const void *extension) {
    if(!extension||((u64)extension&15))return -22;
    state_extension=extension;state_saved_valid=0;return 0;
}

void fl23_state_set_selector(u32 selector) {
    state_selector=selector;
}

static int state_page_same(const u8 *page,u32 selector) {
    return state_saved_valid && state_saved_selector==selector && state_same(page,state_saved_page,PAGE_BYTES);
}

static void state_cache_update(const u8 *page,u32 selector) {
    state_copy(state_saved_page,page,PAGE_BYTES);state_saved_selector=selector;state_saved_valid=1;
}

static int state_write(const void *extension,u32 selector) {
    if(!extension||!state_selector_valid(selector))return -22;
    u8 page[PAGE_BYTES],records[2][STATE_BYTES];
    if(fl23_main_pack_shadow(extension,page,PAGE_BYTES))return -22;
    long fd=state_sc(56,-100,(long)STATE_PATH,0x88042,0600,0,0);
    if(fd<0)return fd;
    long size=state_sc(62,fd,0,2,0,0,0);
    if(size<0){long close_rc=state_close(fd);return close_rc<0?close_rc:size;}
    u32 generation=1,target=0;
    if(size==0) {
        state_record_make(records[0],selector,generation,page);
        state_zero(records[1],STATE_BYTES);
        if(!state_io(68,fd,records[0],STATE_BYTES,0)||!state_io(68,fd,records[1],STATE_BYTES,STATE_BYTES)||!state_sync(fd)) {
            long close_rc=state_close(fd);return close_rc<0?close_rc:-5;
        }
    } else if(size==STATE_FILE_BYTES) {
        if(!state_io(67,fd,records[0],STATE_BYTES,0)||!state_io(67,fd,records[1],STATE_BYTES,STATE_BYTES)) {
            long close_rc=state_close(fd);return close_rc<0?close_rc:-5;
        }
        int valid0=state_record_valid(records[0]),valid1=state_record_valid(records[1]);
        if(!valid0&&!valid1&&!state_all_zero(records[0],STATE_BYTES)&&!state_all_zero(records[1],STATE_BYTES)) {
            long close_rc=state_close(fd);return close_rc<0?close_rc:-1;
        }
        u32 gen0=valid0?state_get32(records[0]+16):0,gen1=valid1?state_get32(records[1]+16):0;
        generation=(gen0>gen1?gen0:gen1)+1;
        if(!generation)generation=1;
        if(!valid0)target=0;
        else if(!valid1)target=1;
        else target=gen0<=gen1?0:1;
        state_record_make(records[target],selector,generation,page);
        if(!state_io(68,fd,records[target],STATE_BYTES,target*STATE_BYTES)||!state_sync(fd)) {
            long close_rc=state_close(fd);return close_rc<0?close_rc:-5;
        }
    } else {
        long close_rc=state_close(fd);return close_rc<0?close_rc:-1;
    }
    long close_rc=state_close(fd);return close_rc<0?close_rc:0;
}

int fl23_state_save(const void *extension,u32 selector) {
    if(!extension||!state_selector_valid(selector))return -22;
    u8 page[PAGE_BYTES];
    if(fl23_main_pack_shadow(extension,page,PAGE_BYTES))return -22;
    if(state_page_same(page,selector))return 0;
    int result=state_write(extension,selector);
    if(!result)state_cache_update(page,selector);
    return result;
}

int fl23_state_save_bound(void) {
    if(!state_extension)return -19;
    return fl23_state_save(state_extension,state_selector);
}

int fl23_state_load(void *extension,u32 *selector) {
    if(!extension||!selector)return -22;
    long fd=state_sc(56,-100,(long)STATE_PATH,0x88000,0,0,0);
    if(fd<0)return fd;
    long size=state_sc(62,fd,0,2,0,0,0);
    if(size<0){long close_rc=state_close(fd);return close_rc<0?close_rc:size;}
    if(size!=STATE_FILE_BYTES) {
        long close_rc=state_close(fd);return close_rc<0?close_rc:-1;
    }
    u8 records[2][STATE_BYTES];
    if(!state_io(67,fd,records[0],STATE_BYTES,0)||!state_io(67,fd,records[1],STATE_BYTES,STATE_BYTES)) {
        long close_rc=state_close(fd);return close_rc<0?close_rc:-5;
    }
    long close_rc=state_close(fd);if(close_rc<0)return close_rc;
    int valid0=state_record_valid(records[0]),valid1=state_record_valid(records[1]);
    if(!valid0&&!valid1)return -1;
    u32 gen0=valid0?state_get32(records[0]+16):0,gen1=valid1?state_get32(records[1]+16):0;
    const u8 *chosen=(valid1&&(!valid0||gen1>gen0))?records[1]:records[0];
    u8 page[PAGE_BYTES];state_copy(page,chosen+32,PAGE_BYTES);
    *selector=state_get32(chosen+20);
    if(fl23_main_unpack_shadow(extension,page,PAGE_BYTES))return -22;
    if(extension==state_extension)state_cache_update(page,*selector);
    return 0;
}
