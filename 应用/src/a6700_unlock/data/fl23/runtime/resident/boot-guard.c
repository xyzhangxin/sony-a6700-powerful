/* One activation reservation per process, without writable App text state.
 * Abstract AF_UNIX names are kernel-local and disappear with their last fd.
 * A successful boot caller deliberately retains ONE fd until process exit.
 * A probe nonce selects a disjoint test namespace which the probe releases.
 * No bind/listen on an IP address, filesystem path, thread or App write.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
struct a1_guard_result {
 u64 version,state,pid;long fd,socket_rc,bind_rc,close_rc;
 u64 address_bytes;u8 address[112];u64 reserved[2];
};
_Static_assert(sizeof(struct a1_guard_result)==192,"Boot guard ABI");
static long g_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;
}
long fl23_a1_guard_claim(struct a1_guard_result *r,const u8 *probe_nonce){
 if(!r)return -22;
 volatile u8 *zero=(void*)r;for(u32 i=0;i<sizeof(*r);i++)zero[i]=0;
 r->version=1;r->fd=-1;r->socket_rc=-999;r->bind_rc=-999;r->close_rc=-999;
 long pid=g_sc(172,0,0,0,0,0,0);r->pid=pid;
 if(pid<=0||pid>=0x80000000L){r->state=4;return -22;}
 /* sockaddr_un: little-endian family1, first path byte zero (abstract). */
 r->address[0]=1;u32 at=3;const char *prefix=probe_nonce?"sony.fl23.a1.probe.":"sony.fl23.a1.boot.";
 for(u32 i=0;prefix[i];i++)r->address[at++]=prefix[i];
 if(probe_nonce){const char hex[]="0123456789abcdef";
  for(u32 i=0;i<16;i++){r->address[at++]=hex[probe_nonce[i]>>4];r->address[at++]=hex[probe_nonce[i]&15];}
  r->address[at++]='.';
 }
 char digits[10];u32 n=0;do{digits[n++]='0'+pid%10;pid/=10;}while(pid);
 while(n)r->address[at++]=digits[--n];r->address_bytes=at;
 long fd=g_sc(198,1,0x80002,0,0,0,0);r->socket_rc=fd;
 if(fd<0){r->state=4;return fd;}
 r->fd=fd;long rc=g_sc(200,fd,(long)r->address,at,0,0,0);r->bind_rc=rc;
 if(!rc){r->state=1;return 1;}
 r->close_rc=g_sc(57,fd,0,0,0,0,0);
 if(r->close_rc){r->state=5;return r->close_rc;}
 r->fd=-1;r->state=rc==-98?2:4;return rc==-98?0:rc;
}
long fl23_a1_guard_release(struct a1_guard_result *r){
 if(!r||r->version!=1||r->state!=1||r->fd<0||r->fd!=r->socket_rc||r->bind_rc)return -22;
 /* Never retry close: the fd may already have been reused after an error. */
 long rc=g_sc(57,r->fd,0,0,0,0,0);r->close_rc=rc;
 if(!rc){r->state=3;r->fd=-1;}else r->state=5;
 return rc;
}
