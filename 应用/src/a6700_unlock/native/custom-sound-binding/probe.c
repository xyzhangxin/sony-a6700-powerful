/* Fixed FL23 runtime capability probe. No firmware/parameter/WBI write API.
 * The verified arena constructor writes only one owned anonymous page.
 * Existing app mappings and files are compared through checked pipe copies.
 * Service result/config files are restored by the host ScratchTransaction.
 */
typedef unsigned char u8;typedef unsigned int u32;typedef unsigned long long u64;
static long sc(long n,long a,long b,long c,long d,long e,long f) {
register long x0 __asm__("x0")=a;register long x1 __asm__("x1")=b;
register long x2 __asm__("x2")=c;register long x3 __asm__("x3")=d;
register long x4 __asm__("x4")=e;register long x5 __asm__("x5")=f;
register long x8 __asm__("x8")=n;
__asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;}
#include "fl23_a1_loader.c"

#define DATA_SIZE 16384
static unsigned char request[40],result[44+DATA_SIZE],maps[524288],file_page[4096];
static const char exchange[]="/tmp/jiritsu/tmmc.jit";
static const u64 pages[4]={0,0x378b000,0x378c000,0x378d000};
static u64 words[32];
static int io(long nr,long fd,u8 *data,u64 size,u64 offset) {
    u64 done=0;
    for(u32 attempt=0;done<size&&attempt<1024;attempt++) {
        long n=sc(nr,fd,(long)(data+done),size-done,offset+done,0,0);
        if(n==-4)continue;
        if(n<=0||(u64)n>size-done)return 0;
        done+=n;
    }
    return done==size;
}
static u32 get32(const u8 *p) {u32 n=0;for(u32 i=0;i<4;i++)n|=(u32)p[i]<<(i*8);return n;}
static void put32(u8 *p,u32 n) {for(u32 i=0;i<4;i++)p[i]=(u8)(n>>(i*8));}
static int same(const u8 *a,const u8 *b,u64 n) {for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
static u64 hex(const u8 **p,const u8 *end) {
    u64 value=0;u32 count=0;
    while(*p<end) {
        u8 c=**p;u32 d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
        if(d==16)break;
        if(++count>16)return 0;
        value=(value<<4)|d;(*p)++;
    }
    return count?value:0;
}
static int locate(u64 length,u64 *bias,u64 *hint) {
    const u8 *at=maps,*end=maps+length;u32 found=0;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        if(at==end)return 0;
        const u8 *p=line;u64 start=hex(&p,at);if(p==at||*p++!='-')return 0;
        u64 stop=hex(&p,at);if(!start||stop<=start||p+6>=at||*p++!=' ')return 0;
        int rx=same(p,(const u8*)"r-xp ",5);p+=5;
        u64 offset=hex(&p,at);if(p==at||*p!=' ')return 0;
        const char suffix[]="/usr/lib/appFw.so";
        if(rx&&!offset&&at-line>=sizeof(suffix)-1&&same(at-(sizeof(suffix)-1),(const u8*)suffix,sizeof(suffix)-1)) {
            *bias=start;found++;
            if(stop-start!=0x4489000)return 0;
        }
        at++;
    }
    if(found!=1||(*bias&4095))return 0;
    u64 candidate=*bias+0x20000000;
    at=maps;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        const u8 *p=line;u64 start=hex(&p,at);p++;u64 stop=hex(&p,at);
        if(candidate<stop&&candidate+8192>start)candidate=(stop+4095)&~4095ull;
        at++;
    }
    if(candidate>=0x8000000000ull||candidate-*bias>=0x100000000ull)return 0;
    *hint=candidate;return 1;
}
static int checked_copy(u64 address,u8 *dest,u64 n) {
    int fd[2];if(n>4096||sc(59,(long)fd,0x80000,0,0,0,0)<0)return 0;
    long written=sc(64,fd[1],address,n,0,0,0);
    int ok=written==(long)n&&io(63,fd[0],dest,n,0);
    long a=sc(57,fd[1],0,0,0,0,0),b=sc(57,fd[0],0,0,0,0,0);
    return ok&&!a&&!b;
}
/* Read only the existing boot context. Never allocate, signal, ptrace,
 * execute a worker, or fault in unresident candidate pages deliberately. */
#include "boot-state-profile.h"
static void finish(u32 status);
static u8 bs_maps[262144],bs_after[262144],bs_unix[131072],bs_vector[1024],bs_candidate[4096],bs_second[4096];
static u64 bs_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static long bs_close(long fd){long rc=sc(57,fd,0,0,0,0,0);if(rc)words[13]++;return rc;}
static long bs_file(const char *path,u8 *dst,u32 cap){
 long fd=sc(56,-100,(long)path,0x80000,0,0,0);if(fd<0)return fd;
 u32 n=0;long rc=0;
 for(u32 i=0;i<1024;i++){
  if(n==cap){rc=-7;break;}
  long got=sc(63,fd,(long)(dst+n),cap-n,0,0,0);if(got==-4)continue;if(got<0){rc=got;break;}if(!got){rc=n;break;}
  if((u64)got>cap-n){rc=-5;break;}n+=got;rc=-110;
 }long closed=bs_close(fd);return closed?closed:rc;
}
static u32 bs_decimal(char *p,u64 v){char buf[20];u32 n=0;do{buf[n++]='0'+v%10;v/=10;}while(v);u32 count=n;while(n)*p++=buf[--n];return count;}
static int bs_number(const u8 **p,const u8 *end,u64 *value){u64 v=0;u32 n=0;while(*p<end&&**p>='0'&&**p<='9'){u32 d=*(*p)++-'0';if(v>(~0ULL-d)/10)return 0;v=v*10+d;n++;}*value=v;return n>0;}
static int bs_owner(const u8 *p,u64 bias,u64 pid){
 if(bs_u64(p)!=1||bs_u64(p+8)!=bias||!bs_u64(p+16)||bs_u64(p+16)>=0x8000000000ULL||bs_u64(p+24)!=pid||!bs_u64(p+32)||bs_u64(p+32)>=0x80000000ULL)return 0;
 const u8 *g=p+72;
 if(bs_u64(g)!=1||bs_u64(g+8)!=1||bs_u64(g+16)!=pid||bs_u64(g+24)>1048576||bs_u64(g+32)!=bs_u64(g+24)||bs_u64(g+40)||bs_u64(g+48)!=(u64)-999||bs_u64(g+176)||bs_u64(g+184))return 0;
 u8 expected[112]={1,0,0};const char name[]="sony.fl23.a1.boot.";u32 n=3;
 for(u32 i=0;i<sizeof(name)-1;i++)expected[n++]=name[i];n+=bs_decimal((char*)expected+n,pid);
 return bs_u64(g+56)==n&&same(g+64,expected,112);
}
static int bs_self_page(u64 page){
 const u8 *arrays[]={bs_maps,bs_after,bs_unix,bs_vector,bs_candidate,bs_second,result};
 const u32 sizes[]={sizeof(bs_maps),sizeof(bs_after),sizeof(bs_unix),sizeof(bs_vector),4096,4096,sizeof(result)};
 for(u32 i=0;i<7;i++)if(page<(u64)arrays[i]+sizes[i]&&(u64)arrays[i]<page+4096)return 1;return 0;
}
static int bs_socket(u64 pid,u64 *inode){
 char name[80]="@sony.fl23.a1.boot.";u32 n=19;n+=bs_decimal(name+n,pid);name[n]=0;
 long bytes=bs_file("/proc/net/unix",bs_unix,sizeof(bs_unix));if(bytes<0)return -1;u32 matches=0;
 const u8 *p=bs_unix,*end=p+bytes;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;const u8 *e=p;if(p<end)p++;
  const u8 *at=line,*token[8]={0},*limit[8]={0};u32 count=0;
  while(at<e&&count<8){while(at<e&&(*at==' '||*at=='\t'))at++;if(at==e)break;token[count]=at;while(at<e&&*at!=' '&&*at!='\t')at++;limit[count++]=at;}
  if(count==8&&limit[7]-token[7]==n&&same(token[7],(const u8*)name,n)){
   const u8 *x=token[6];u64 v=0;if(!bs_number(&x,limit[6],&v)||x!=limit[6]||!v)return -1;*inode=v;matches++;
  }
 }return matches==1?1:matches? -1:0;
}
static int bs_fd_link(const u8 *p,u64 inode){
 char path[80]="/proc/self/fd/",expected[80]="socket:[";u32 a=14,b=8;
 a+=bs_decimal(path+a,bs_u64(p+72+24));path[a]=0;b+=bs_decimal(expected+b,inode);expected[b++]=']';
 u8 value[80];long n=sc(78,-100,(long)path,(long)value,80,0,0);
 return n==b&&same(value,(const u8*)expected,b);
}
static long bs_pread(long fd,u8 *p,u32 bytes,u64 at){
 for(u32 i=0,done=0;i<1024;i++){
  long n=sc(67,fd,(long)(p+done),bytes-done,at+done,0,0);if(n==-4)continue;if(n<=0||(u64)n>bytes-done)return -5;
  done+=n;if(done==bytes)return bytes;
 }return -110;
}
/* Observe the fixed active sound host and its pinned menu spans. */
#include "host-probe-profile.h"

static int owned_image(long fd, u64 base) {
    u8 code[64];
    for (u32 i = 0; i < 2; ++i) {
        const struct code_guard *guard = code_guards + i;
        if (bs_pread(fd, code, sizeof(code), base + guard->offset) != sizeof(code) ||
            !same(code, guard->bytes, sizeof(code))) return 0;
    }
    return 1;
}

#if GUI_GUARDS
/* This path observes an exact native guard chain. It never calls a target
 * function and never uses a sampled pointer as an unrestricted read source. */
static int gui_extent(u64 at, u32 bytes, int dynamic, u64 base) {
    if (!bytes || at < 4096 || at >= 0x8000000000ULL ||
        bytes > 0x8000000000ULL-at || (at & 3)) return 0;
    if (dynamic && ((at & 7) || (at < APP_BIAS+0x4489000ULL && APP_BIAS < at+bytes) ||
                    (at < base+HOST_BYTES && base < at+bytes))) return 0;
    for (u64 page=at&~4095ULL;page<at+bytes;page+=4096)
        if (bs_self_page(page)) return 0;
    const u8 *p=bs_maps,*end=p+words[5];
    while (p<end) {
        const u8 *line=p;
        while (p<end && *p!='\n') ++p;
        const u8 *e=p;if(p<end)++p;
        const u8 *q=line;u64 lo=hex(&q,e);
        if(q==e || *q++!='-')return 0;
        u64 hi=hex(&q,e);
        if(q+5>=e || *q++!=' ')return 0;
        if(lo<=at && at+bytes<=hi && q[0]=='r' && (!dynamic || same(q,(const u8*)"rw-p",4)))return 1;
    }
    return 0;
}
static int gui_read(long fd,u8 *dst,u32 bytes,u64 at,int dynamic,u64 base) {
    if(bytes>64 || !gui_extent(at,bytes,dynamic,base))return 0;
    /* At most two resident pages for one fixed <=64-byte read. */
    u64 first=at&~4095ULL,last=(at+bytes-1)&~4095ULL;
    u8 resident[2]={0,0};u64 count=(last-first)/4096+1;
    if(count>2 || sc(232,first,count*4096,(long)resident,0,0,0))return 0;
    for(u32 i=0;i<count;++i)if(!(resident[i]&1))return 0;
    return bs_pread(fd,dst,bytes,at)==bytes;
}
static void gui_take(long fd,u64 *snapshot,u32 bit,u32 index,u32 bytes,u64 address,int dynamic,u64 base) {
    if(gui_read(fd,(u8 *)(snapshot+index),bytes,address,dynamic,base)) snapshot[0]|=1ULL<<bit;
    else snapshot[1]|=1ULL<<bit;
}
#if MAIN_TEXT_GUARDS
static void main_text_snapshot(long fd,u64 *h,const u64 *g,u64 base) {
    u8 scene[8]={0},wrapper[24]={0},after[64]={0},presenter[24]={0};
    u64 receiver_after=0,expected=0;
    h[0]=3;h[1]=g[2];h[2]=g[8];h[3]=g[11];h[6]=g[9];h[13]=1;
    if ((g[0]&0x39)!=0x39 || g[2]!=g[13] || !g[15]) return;
    h[0]=2;
    if (g[5] || g[7]!=APP_BIAS+0x4d11c90ULL || g[9]!=APP_BIAS+0x4c6d018ULL) return;
    h[0]=3;h[13]=2;
    if(!gui_read(fd,scene,4,g[8]+0x18,1,base)) return;
    h[5]=bs_u64(scene);h[0]=2;if(h[5]!=0x292)return;
    h[0]=3;h[13]=3;
    if(!gui_extent(g[11],MAIN_WRAPPER_OFFSET+24,1,base) || !gui_read(fd,wrapper,24,g[11]+MAIN_WRAPPER_OFFSET,1,base))return;
    h[7]=bs_u64(wrapper);h[4]=bs_u64(wrapper+16);
    /* Original 28ce2d8 checks the wrapper tag against this relocated ID. */
    if(!gui_read(fd,(u8*)&expected,8,APP_BIAS+MAIN_TAG_OFFSET,0,base))return;
    h[8]=expected;h[13]=4;if(h[7]!=expected || !h[4])return;
    h[13]=5;
    if(!gui_extent(h[4],0xd8,1,base))return;
    for(u32 i=0;i<84;i+=64) {
        u32 n=84-i<64?84-i:64;
        if(!gui_read(fd,(u8*)h+128+i,n,h[4]+0x30+i,0,base) ||
           !gui_read(fd,(u8*)h+216+i,n,h[4]+0x84+i,0,base))return;
    }
#if ITEM_TEXT_GUARDS
    h[13]=7;
    if(h[4]<0x1d0)return;
    h[14]=h[4]-0x1d0;
    u64 tp[8]={0},vp[8]={0},check[8]={0},vptr_after=0;
    if(!gui_extent(h[14],0x49208,1,base) ||
       !gui_read(fd,(u8*)&h[15],8,h[14],1,base) || h[15]!=APP_BIAS+0x4a00d78ULL)return;
    h[13]=11;
    if(!gui_read(fd,(u8*)h+496,4,h[14]+0x4188,0,base))return;
    h[13]=8;
    if(!gui_read(fd,(u8*)tp,64,h[14]+0x490c8,1,base) ||
       !gui_read(fd,(u8*)vp,64,h[14]+0x491c8,1,base))return;
    for(u32 i=0;i<8;++i) {
        u8 t[16]={0},v[16]={0};
        h[13]=9;
        if(!gui_extent(tp[i],0x40,1,base) || !gui_extent(vp[i],0x40,1,base) ||
           !gui_read(fd,t,16,tp[i]+0x30,0,base) || !gui_read(fd,v,16,vp[i]+0x30,0,base))return;
        u8 *out=(u8*)h+304+i*24;
        for(u32 j=0;j<4;++j){out[j]=t[j];out[4+j]=v[j];}
        for(u32 j=0;j<8;++j){out[8+j]=t[8+j];out[16+j]=v[8+j];}
    }
    h[13]=10;
    if(!gui_read(fd,(u8*)check,64,h[14]+0x490c8,1,base))return;
    h[0]=4;if(!same((u8*)check,(u8*)tp,64))return;
    h[0]=3;
    if(!gui_read(fd,(u8*)check,64,h[14]+0x491c8,1,base) ||
       !gui_read(fd,(u8*)&vptr_after,8,h[14],1,base))return;
    h[0]=4;if(!same((u8*)check,(u8*)vp,64) || vptr_after!=h[15])return;
    h[0]=3;
#endif
    h[13]=6;
    if(!gui_read(fd,(u8*)&receiver_after,8,APP_BIAS+0x8338710ULL,0,base) ||
       !gui_read(fd,after,64,g[2]+0x718+0x278,1,base) ||
       !gui_read(fd,presenter,24,g[8],1,base) ||
       !gui_read(fd,wrapper,24,g[11]+MAIN_WRAPPER_OFFSET,1,base) ||
       !gui_read(fd,scene,4,g[8]+0x18,1,base))return;
    h[9]=receiver_after;h[10]=bs_u64(after);h[11]=bs_u64(presenter+16);h[12]=bs_u64(wrapper+16);
    h[0]=4;
    if(h[9]!=h[1] || h[10]!=h[2] || h[11]!=h[3] || h[12]!=h[4] ||
       bs_u64(presenter)!=h[6] || bs_u64(wrapper)!=h[7] || bs_u64(scene)!=h[5] ||
       bs_u64(after+48)!=g[6] || bs_u64(after+56)!=g[5]) return;
    long n=bs_file("/proc/self/maps",bs_after,sizeof(bs_after));
    if(n!=(long)words[5] || !same(bs_maps,bs_after,(u32)n))return;
    h[0]=1;h[13]=0;
}
#endif
static void gui_snapshot(long fd,u64 *g,u64 base) {
    /* All three fixed data cells are in the hash-bound ELF's RW PT_LOAD,
     * [0x4499aa0,0x148e8bc0); hook/App-code scope remains unchanged. */
    gui_take(fd,g,0,2,8,APP_BIAS+0x8338710ULL,0,base);
    gui_take(fd,g,1,3,4,APP_BIAS+0x8d34960ULL,0,base);
    gui_take(fd,g,6,12,8,APP_BIAS+0x4c6d100ULL,0,base);
    u8 chain[64]={0};
    if((g[0]&1) && gui_extent(g[2],0xa64,1,base)) {
        gui_take(fd,g,2,4,4,g[2]+0xa20,0,base);
        gui_take(fd,g,10,16,8,g[2],1,base);
        gui_take(fd,g,11,17,8,g[2]+0x718,1,base);
        gui_take(fd,g,12,18,4,g[2]+0x8e0,0,base);
        /* Byte flags are extracted from bounded aligned four-byte reads. */
        gui_take(fd,g,13,19,4,g[2]+0xa10,0,base);
        gui_take(fd,g,14,20,4,g[2]+0xa60,0,base);
        if(gui_read(fd,chain,64,g[2]+0x718+0x278,1,base)) {
            g[0]|=8;g[8]=bs_u64(chain);g[6]=bs_u64(chain+48);g[5]=bs_u64(chain+56);
            gui_take(fd,g,4,7,8,g[6],1,base);
            gui_take(fd,g,5,9,24,g[8],1,base);
        } else g[1]|=8;
    } else g[1]|=12|0x7c00;
    gui_take(fd,g,15,21,4,APP_BIAS+0x8394ce8ULL,0,base);
    gui_take(fd,g,7,13,8,APP_BIAS+0x8338710ULL,0,base);
    gui_take(fd,g,8,14,4,APP_BIAS+0x8d34960ULL,0,base);
    long n=bs_file("/proc/self/maps",bs_after,sizeof(bs_after));
    if(n==(long)words[5] && same(bs_maps,bs_after,(u32)n)) {g[0]|=512;g[15]=1;}
    else g[1]|=512;
}
#endif

static u32 host_probe(void) {
    u64 *diagnostic = (void *)(result + 44 + 4608);
    diagnostic[0] = 1;
    const u8 *page = result + 44 + 512;
    const u8 *startup = page + 1664;
    u64 base = bs_u64(page + 328), api = bs_u64(page + 360);
#if INSTALLED_OBSERVATION
    if (same(result+300,expected_boot_id,36)) return 8;
    for (u32 i=0;i<36;++i) {
        u8 c=result[300+i];
        if (i==8 || i==13 || i==18 || i==23) {if(c!='-')return 8;}
        else if (!((c>='0' && c<='9') || (c>='a' && c<='f'))) return 8;
    }
#else
    if (!same(result+300,expected_boot_id,36)) return 8;
#endif
    int matched_receipt = 0;
    for (u32 i = 0; i < 4; ++i) {
        if (same(page + 1120, host_receipts[i], 512)) { matched_receipt = 1; break; }
    }
    if (!matched_receipt || words[6] != APP_BIAS ||
        bs_u64(page + 264) != 6 || bs_u64(page + 272) != EXPECT_STARTUP_STATUS || bs_u64(page + 312) != EXPECT_STARTUP_STATUS ||
        bs_u64(startup) != 0x314c444e554f5343ULL || bs_u64(startup + 8) != 1 ||
        bs_u64(startup + 16) != EXPECT_STARTUP_STATUS || bs_u64(startup + 24) != 6 ||
        bs_u64(startup + 176) != PUBLISHED_MASK || bs_u64(startup + 256) != EXPECT_STARTUP_STATE ||
        base < 4096 || (base & 4095) || base > 0x8000000000ULL - HOST_BYTES ||
        bs_u64(page + 336) != HOST_BYTES || api <= base || api >= base + HOST_BYTES) return 8;
    if (base < APP_BIAS + 0x4489000ULL && APP_BIAS < base + HOST_BYTES) return 8;
    diagnostic[3] = base;
    long fd = sc(56, -100, (long)"/proc/self/mem", 0x80000, 0, 0, 0);
    if (fd < 0) return 9;
    u32 status = 10;
    if (!owned_image(fd, base)) goto done;
    for (u32 pass = 0; pass < 2; ++pass) {
        u8 *scalar = result + 44 + SCALARS_OFFSET + pass * FIELD_COUNT * 8;
        for (u32 i = 0; i < FIELD_COUNT; ++i) {
            const struct extent *field = fields + i;
            if (bs_pread(fd, scalar + 8 * i, field->bytes, base + field->offset) != field->bytes) {
                status = 11;
                goto done;
            }
        }
        if (bs_u64(scalar + OWNER_BASE_FIELD * 8) != base ||
            bs_u64(scalar + OWNER_SIZE_FIELD * 8) != HOST_BYTES) {
            status = 12;
            goto done;
        }
        u8 *app = result + 44 + SPANS_OFFSET + pass * SPANS_SIZE;
        for (u32 i = 0; i < SPAN_COUNT; ++i) {
            const struct extent *span = spans + i;
            if (bs_pread(fd, app, span->bytes, APP_BIAS + span->offset) != span->bytes) {
                status = 13;
                goto done;
            }
            app += span->bytes;
        }
        u8 *entry = result + 44 + ENTRIES_OFFSET + pass * ENTRY_BYTES;
        for (u32 i = 0; i < ENTRY_COUNT; ++i) {
            const struct extent *item = entries + i;
            if (bs_pread(fd, entry, item->bytes, APP_BIAS + item->offset) != item->bytes) {
                status = 15;
                goto done;
            }
            entry += item->bytes;
        }
#if GUI_GUARDS
        gui_snapshot(fd,(u64 *)(result+44+GUI_OFFSET+pass*GUI_STRIDE),base);
#if MAIN_TEXT_GUARDS
        main_text_snapshot(fd,(u64 *)(result+44+GUI_OFFSET+pass*GUI_STRIDE+256),
                           (const u64 *)(result+44+GUI_OFFSET+pass*GUI_STRIDE),base);
#endif
#endif
        ++diagnostic[1];
    }
    if (!owned_image(fd, base) || bs_pread(fd, bs_second, 4096, words[11]) != 4096 ||
        !same(bs_second, page, 4096)) goto done;
    status = 0;
done:
    diagnostic[2] = bs_close(fd);
    return diagnostic[2] ? 14 : status;
}

__attribute__((constructor)) static void run(void){
 long fd=sc(56,-100,(long)exchange,0,0,0,0);u8 tail;if(fd<0)return;
 int ok=io(63,fd,request,40,0);long extra=sc(63,fd,(long)&tail,1,0,0,0);sc(57,fd,0,0,0,0,0);
 if(!ok||extra||!same(request,(const u8*)"A67P",4)||get32(request+4)!=1||get32(request+24)!=71||get32(request+28)||get32(request+32)||get32(request+36)!=DATA_SIZE){finish(2);return;}
 words[0]=1;words[1]=sc(172,0,0,0,0,0,0);words[2]=sc(178,0,0,0,0,0,0);
 long n=bs_file("/proc/sys/kernel/random/boot_id",result+44+256,38);if(n!=37){finish(3);return;}
 words[14]=n;
 u64 inode=0;int present=bs_socket(words[1],&inode);words[3]=present;words[4]=inode;
 if(present<0){finish(3);return;}if(!present){finish(8);return;}
 n=bs_file("/proc/self/maps",bs_maps,sizeof(bs_maps));if(n<=0){finish(3);return;}words[5]=n;
 const u8 *p=bs_maps,*end=p+n;u64 bias=0;u32 apps=0;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;const u8 *e=p;if(p<end)p++;
  const u8 *q=line;u64 lo=hex(&q,e);if(q==e||*q++!='-'){finish(3);return;}u64 hi=hex(&q,e);
  if(hi<=lo||q+6>=e||*q++!=' '){finish(3);return;}const u8 *perm=q;q+=5;u64 off=hex(&q,e);
  const char path[]="/usr/lib/appFw.so";
  if(e-line>=sizeof(path)-1&&same(e-(sizeof(path)-1),(const u8*)path,sizeof(path)-1)&&same(perm,(const u8*)"r-xp",4)&&!off){if(hi-lo!=0x4489000){finish(3);return;}bias=lo;apps++;}
 }
 if(apps!=1||!bias||(bias&4095)||bias>=0x8000000000ULL-0x15000000){finish(3);return;}words[6]=bias;
 fd=sc(56,-100,(long)"/proc/self/mem",0x80000,0,0,0);if(fd<0){finish(3);return;}
 u32 status=0;u64 matches=0;p=bs_maps;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;const u8 *e=p;if(p<end)p++;
  const u8 *q=line;u64 lo=hex(&q,e);q++;u64 hi=hex(&q,e);q++;const u8 *perm=q;q+=5;
  for(u32 col=0;col<3;col++){while(q<e&&*q!=' ')q++;while(q<e&&*q==' ')q++;}
  if(q!=e||!same(perm,(const u8*)"rw-p",4)||!lo||(lo&4095)||(hi&4095)||hi>=0x8000000000ULL)continue;
  words[7]++;
  /* mincore examines virtual reservations without faulting them in. Keep
   * its bound separate from the number of resident pages actually read. */
  if((hi-lo)/4096>4194304-words[8]){status=4;goto done;}
  for(u64 at=lo;at<hi;){u64 span=hi-at;if(span>sizeof(bs_vector)*4096ULL)span=sizeof(bs_vector)*4096ULL;
   words[8]+=span/4096;
   long rc=sc(232,at,span,(long)bs_vector,0,0,0);if(rc){status=5;goto done;}
   for(u32 i=0;i<span/4096;i++){
    u64 page=at+i*4096;if(!(bs_vector[i]&1)||bs_self_page(page))continue;
    if(words[9]>=131072){status=4;goto done;}words[9]++;
    if(bs_pread(fd,bs_candidate,32,page)!=32){status=5;goto done;}
    if(bs_u64(bs_candidate)!=1||bs_u64(bs_candidate+8)!=bias||bs_u64(bs_candidate+24)!=words[1])continue;
    if(bs_pread(fd,bs_candidate,4096,page)!=4096){status=5;goto done;}
    if(!bs_owner(bs_candidate,bias,words[1])||!bs_fd_link(bs_candidate,inode))continue;
    if(bs_pread(fd,bs_second,4096,page)!=4096||!same(bs_candidate,bs_second,4096)){status=5;goto done;}
    matches++;words[10]=matches;words[11]=page;
    for(u32 j=0;j<4096;j++)result[44+512+j]=bs_candidate[j];
   }at+=span;
  }
 }
 if(matches!=1)status=6;
 n=bs_file("/proc/self/maps",bs_after,sizeof(bs_after));if(n!=(long)words[5]||!same(bs_maps,bs_after,(u32)n))status=5;
 if(!status)words[12]=1;
done:
 if(bs_close(fd))status=7;
 if(words[13])status=7;if(!status)status=host_probe();finish(status);
}

static void finish(u32 status) {
    result[0]='A';result[1]='6';result[2]='7';result[3]='B';put32(result+4,1);
    for(u32 i=0;i<16;i++)result[8+i]=request[8+i];
    put32(result+24,status);put32(result+28,71);
    for(u32 i=0;i<8;i++)result[32+i]=request[28+i];
    put32(result+40,DATA_SIZE);
    for(u32 i=0;i<32;i++)for(u32 j=0;j<8;j++)result[44+i*8+j]=(u8)(words[i]>>(8*j));
    long fd=sc(56,-100,(long)exchange,0x241,0600,0,0);
    if(fd<0)return;
    if(io(64,fd,result,sizeof(result),0))sc(82,fd,0,0,0,0,0);
    sc(57,fd,0,0,0,0,0);
}
