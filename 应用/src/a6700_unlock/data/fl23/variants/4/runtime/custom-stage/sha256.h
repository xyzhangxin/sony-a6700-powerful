/* SHA-256, FIPS 180-4. Standalone, no external calls or allocations. */
typedef struct {u32 h[8];u64 bytes;u8 block[64];u32 used;} SHA256;
static u32 rr(u32 x,u32 n) {return (x>>n)|(x<<(32-n));}
static void sha_block(SHA256 *s,const u8 *p) {
    static const u32 k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    u32 w[64];
    for(u32 i=0;i<16;i++)w[i]=((u32)p[4*i]<<24)|((u32)p[4*i+1]<<16)|((u32)p[4*i+2]<<8)|p[4*i+3];
    for(u32 i=16;i<64;i++)w[i]=w[i-16]+(rr(w[i-15],7)^rr(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(rr(w[i-2],17)^rr(w[i-2],19)^(w[i-2]>>10));
    u32 a=s->h[0],b=s->h[1],c=s->h[2],d=s->h[3],e=s->h[4],f=s->h[5],g=s->h[6],h=s->h[7];
    for(u32 i=0;i<64;i++) {
        u32 t=h+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^((~e)&g))+k[i]+w[i];
        u32 v=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+v;
    }
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
static void sha_init(SHA256 *s) {
    static const u32 h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    for(u32 i=0;i<8;i++)s->h[i]=h[i];s->bytes=0;s->used=0;
}
static void sha_add(SHA256 *s,const u8 *p,u32 n) {
    s->bytes+=n;
    while(n) {
        u32 count=64-s->used;if(count>n)count=n;
        for(u32 i=0;i<count;i++)s->block[s->used+i]=p[i];
        s->used+=count;p+=count;n-=count;
        if(s->used==64) {sha_block(s,s->block);s->used=0;}
    }
}
static void sha_end(SHA256 *s,u8 *out) {
    u64 bits=s->bytes*8;u8 pad[64]={0x80};u32 n=s->used<56?56-s->used:120-s->used;
    sha_add(s,pad,n);
    u8 tail[8];for(u32 i=0;i<8;i++)tail[7-i]=(u8)(bits>>(i*8));sha_add(s,tail,8);
    for(u32 i=0;i<8;i++)for(u32 j=0;j<4;j++)out[4*i+j]=(u8)(s->h[i]>>(24-8*j));
}
