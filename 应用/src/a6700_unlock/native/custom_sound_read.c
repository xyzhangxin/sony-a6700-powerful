/* Fixed-path slot observation. Never writes /setting or follows a symlink. */
#define MAX_RECORD (128u + 255u + 512u * 1024u)
static unsigned char request[72], result[44 + MAX_RECORD + 1];
static const char exchange[] = "/tmp/jiritsu/tmmc.jit";
static char path[] = "/setting/csnd/slot10.bin";
static long sys(long n, long a, long b, long c, long d) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    register long x3 __asm__("x3") = d;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3) : "memory");
    return x0;
}
static unsigned get(unsigned at) {
    unsigned v = 0;
    for (unsigned i=0;i<4;i++) v |= (unsigned)request[at+i] << (8*i);
    return v;
}
static void put(unsigned at,unsigned v) {
    for (unsigned i=0;i<4;i++) result[at+i] = v >> (8*i);
}
static int exact(long operation,long fd,unsigned char *buffer,unsigned bytes) {
    unsigned done=0;
    for (unsigned i=0;i<1024 && done<bytes;i++) {
        long n=sys(operation,fd,(long)(buffer+done),bytes-done,0);
        if(n==-4) continue;
        if(n<=0 || (unsigned long)n>bytes-done) return 0;
        done+=(unsigned)n;
    }
    return done==bytes;
}
static void finish(unsigned status,unsigned slot,unsigned bytes,unsigned present) {
    result[0]='C';result[1]='S';result[2]='N';result[3]='R';put(4,1);
    for(unsigned i=0;i<16;i++) result[8+i]=request[8+i];
    put(24,status);put(28,slot);put(32,bytes);put(36,0);put(40,present);
    long fd=sys(56,-100,(long)exchange,0x241,0600);
    if(fd<0)return;
    if(exact(64,fd,result,44+bytes)) {
        for(unsigned i=0;i<16;i++) if(sys(82,fd,0,0,0)!=-4)break;
    }
    sys(57,fd,0,0,0);
}
__attribute__((constructor)) static void run(void) {
    long fd=sys(56,-100,(long)exchange,0,0);
    if(fd<0)return;
    unsigned char extra;
    int ok=exact(63,fd,request,72);
    if(ok && sys(63,fd,(long)&extra,1,0)!=0)ok=0;
    if(sys(57,fd,0,0,0))ok=0;
    unsigned slot=get(56);
    for(unsigned i=24;i<56;i++) if(request[i])ok=0;
    if(!ok || request[0]!='C'||request[1]!='S'||request[2]!='N'||request[3]!='I'
       || get(4)!=1 || slot<1 || slot>10 || get(60)||get(64)||get(68)) {
        finish(2,slot,0,0);return;
    }
    if(slot!=10) {
        path[18]='0'+slot;path[19]='.';path[20]='b';path[21]='i';path[22]='n';path[23]=0;
    }
    /* AArch64 O_NOFOLLOW; the only persistent file open is read-only. */
    fd=sys(56,-100,(long)path,0x20000,0);
    if(fd==-2) {finish(0,slot,0,0);return;}
    if(fd<0) {finish(3,slot,0,0);return;}
    unsigned bytes=0;int eof=0;
    for(unsigned i=0;i<1024 && bytes<=MAX_RECORD;i++) {
        long n=sys(63,fd,(long)(result+44+bytes),MAX_RECORD+1-bytes,0);
        if(n==-4)continue;
        if(n==0){eof=1;break;}
        if(n<0 || (unsigned long)n>MAX_RECORD+1-bytes)break;
        bytes+=(unsigned)n;
    }
    long closed=sys(57,fd,0,0,0);
    if(!eof || bytes>MAX_RECORD || closed) {finish(4,slot,0,0);return;}
    finish(0,slot,bytes,1);
}
