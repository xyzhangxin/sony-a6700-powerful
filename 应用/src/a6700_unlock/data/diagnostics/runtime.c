/* Read-only appFw runtime inspection. No ptrace, signals or firmware writes.
 * Only /tmp/jiritsu/tmmc.jit is opened writable for USB result retrieval.
 */
typedef unsigned long U;
static long sc(long n, long a, long b, long c, long d) {
    register long x8 __asm__("x8")=n, x0 __asm__("x0")=a;
    register long x1 __asm__("x1")=b, x2 __asm__("x2")=c, x3 __asm__("x3")=d;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8),"r"(x1),"r"(x2),"r"(x3) : "memory");
    return x0;
}
static char out[24000], maps[65536], directory[8192];
static U used;
static void add(const char *s) { while (*s && used<sizeof(out)-1) out[used++]=*s++; }
static void hex(U v) {
    char b[17]; U n=0;
    do { b[n++]="0123456789abcdef"[v&15]; v>>=4; } while(v && n<16);
    while(n && used<sizeof(out)-1) out[used++]=b[--n];
}
static void number(long v) { if(v<0){add("-");v=-v;} add("0x");hex(v); }
static void append(char *s,U *at,const char *p) { while(*p && *at<95)s[(*at)++]=*p++;s[*at]=0; }
static void path(char *s,const char *pid,const char *file) {
    U at=0;append(s,&at,"/proc/");append(s,&at,pid);append(s,&at,"/");append(s,&at,file);
}
static int digit(char c) { return c>='0' && c<='9'; }
static int hx(char c) {
    if(digit(c))return c-'0'; if(c>='a' && c<='f')return c-'a'+10;return -1;
}
static U parse_hex(const char **s) { U v=0;int n;while((n=hx(**s))>=0){v=(v<<4)|n;(*s)++;}return v; }
static U app_base(long n) {
    const char *p=maps,*end=maps+n;
    while(p<end) {
        const char *line=p;U start=parse_hex(&p);
        if(p>=end || *p!='-')break;
        p++;parse_hex(&p);while(p<end && *p==' ')p++;
        while(p<end && *p!=' ')p++;while(p<end && *p==' ')p++;
        U offset=parse_hex(&p);int found=0;
        while(p<end && *p!='\n') {
            const char *q=p,*name="/appFw.so";
            while(q<end && *name && *q==*name){q++;name++;}
            if(!*name && (q==end || *q=='\n' || *q==' '))found=1;
            p++;
        }
        if(found && offset==0)return start;
        if(p<end)p++;if(p<=line)break;
    }
    return 0;
}
static long read_at(long fd,U at,void *buf,U n) { return sc(67,fd,(long)buf,n,at); }
static U pointer(long fd,U at) { U v=0;return read_at(fd,at,&v,8)==8?v:0; }
static void probe(long fd,const char *tag,U at,U n) {
    unsigned char b[256];if(n>sizeof(b))return;
    long got=read_at(fd,at,b,n);
    add("READ ");add(tag);add(" addr=0x");hex(at);add(" result=");number(got);add(" data=");
    if(got>0 && (U)got<=sizeof(b))for(long i=0;i<got;i++) {
        char s[3]={"0123456789abcdef"[b[i]>>4],"0123456789abcdef"[b[i]&15],0};add(s);
    }
    add("\n");
}
static void inspect(const char *pid,U base) {
    char p[96];path(p,pid,"mem");long fd=sc(56,-100,(long)p,0,0);
    add("PROCESS pid=");add(pid);add(" base=0x");hex(base);add(" mem_fd=");number(fd);add("\n");
    if(fd<0)return;
    probe(fd,"fl23_base_hook",base+0x3107340,8u);
    probe(fd,"fl23_cave_0",base+0x378d800,256u);
    probe(fd,"fl23_cave_1",base+0x378d900,256u);
    probe(fd,"fl23_cave_2",base+0x378da00,256u);
    probe(fd,"fl23_cave_3",base+0x378db00,256u);
    probe(fd,"fl23_cave_4",base+0x378dc00,256u);
    probe(fd,"fl23_cave_5",base+0x378dd00,120u);

    probe(fd,"product",base+0x8d22f98,4);
    probe(fd,"profile_index",base+0x4f21310,4);
    probe(fd,"steadyshot_model",base+0x8d374f8,16);
    probe(fd,"leaf_template",base+0x4b8b918,160);
    probe(fd,"row_template",base+0x4b85e88,176);
    probe(fd,"menu_vtable_meta",base+0x4e5b430,8);
    probe(fd,"leaf_map_holder",base+0x4e490c8,8);
    probe(fd,"value_support_initializer",base+0x3ed6548,40);
    probe(fd,"active_plus_counter",base+0x8cf60c0+0x349f*4,4);
    /* Original nested unordered-map layout: outer key is model parameter,
     * inner key is numeric setting value. No model getter is called. */
    U support_node=pointer(fd,base+0x138bd930+16);
    for(unsigned depth=0;support_node && depth<8192;depth++) {
        unsigned parameter=0;
        if(read_at(fd,support_node+8,&parameter,4)!=4)break;
        if(parameter==0x4d1) {
            U choice=pointer(fd,support_node+32);
            for(unsigned j=0;choice && j<64;j++) {
                unsigned value=0;
                if(read_at(fd,choice+8,&value,4)!=4)break;
                if(value==0) {
                    add("VALUE_LOOKUP flag=0x");hex(choice+12);add("\n");
                    probe(fd,"active_plus_value_support",choice+12,4);break;
                }
                choice=pointer(fd,choice);
            }
            break;
        }
        support_node=pointer(fd,support_node);
    }
    U holder=pointer(fd,base+0x4e490c8), node=holder?pointer(fd,holder+16):0;
    for(unsigned depth=0;node && depth<64;depth++) {
        unsigned id=0;
        if(read_at(fd,node+32,&id,4)!=4)break;
        if(id==0x3a3) {
            U leaf=pointer(fd,node+40);
            add("LOOKUP leaf=0x");hex(leaf);add("\n");
            if(leaf)probe(fd,"leaf_lookup",leaf,160);
            break;
        }
        node=pointer(fd,node+(id<0x3a3?24:16));
    }
    sc(57,fd,0,0,0);
}
struct dirent64 { U ino;long off;unsigned short reclen;unsigned char type;char name[]; };
__attribute__((constructor)) static void run(void) {
    add("A6700-DYNAMIC-RUNTIME-v1\n");
    char request[81];
    long input=sc(56,-100,(long)"/tmp/jiritsu/tmmc.jit",0,0);
    if(input>=0) {
        long n=sc(63,input,(long)request,80,0);
        sc(57,input,0,0,0);
        if(n>0 && n<=80){request[n]=0;add(request);add("\n");}
    }
    /* O_RDONLY is sufficient for getdents64. ARM64 directory/direct-I/O flag
     * values differ from x86; do not copy host O_DIRECTORY constants. */
    long dir=sc(56,-100,(long)"/proc",0,0);unsigned tested=0,found=0;
    add("PROC open=");number(dir);add("\n");
    if(dir>=0)for(unsigned batch=0;batch<64 && tested<512 && found<4;batch++) {
        long n=sc(61,dir,(long)directory,sizeof(directory),0);
        if(n<0){add("PROC getdents=");number(n);add("\n");}if(n<=0)break;
        for(U off=0;off+19<=(U)n && tested<512 && found<4;) {
            struct dirent64 *entry=(struct dirent64 *)(directory+off);
            if(entry->reclen<20 || entry->reclen>(U)n-off)break;
            U j=0;while(19+j<entry->reclen && digit(entry->name[j]))j++;
            if(j && j<16 && 19+j<entry->reclen && !entry->name[j]) {
                char p[96];path(p,entry->name,"maps");tested++;
                long fd=sc(56,-100,(long)p,0,0);
                if(fd>=0) {
                    U total=0;
                    while(total<sizeof(maps)-1) {
                        long got=sc(63,fd,(long)(maps+total),sizeof(maps)-1-total,0);
                        if(got<=0)break;total+=got;
                    }
                    sc(57,fd,0,0,0);maps[total]=0;
                    U base=app_base(total);if(base){inspect(entry->name,base);found++;}
                }
            }
            off+=entry->reclen;
        }
    }
    if(dir>=0)sc(57,dir,0,0,0);
    add("DONE tested=");number(tested);add(" found=");number(found);add("\n");
    long fd=sc(56,-100,(long)"/tmp/jiritsu/tmmc.jit",0x241,0600);
    if(fd>=0) {
        U done=0;for(unsigned tries=0;done<used && tries<64;tries++) {
            long n=sc(64,fd,(long)(out+done),used-done,0);if(n<=0)break;done+=n;
        }
        sc(57,fd,0,0,0);
    }
}
