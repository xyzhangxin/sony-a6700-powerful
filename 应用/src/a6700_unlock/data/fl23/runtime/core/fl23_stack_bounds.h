/* Bounds from the stopped process's complete maps, not a guessed SP window.
 * No memory access, syscalls or permission to skip incomplete frame chains.
 */
#ifndef FL23_STACK_BOUNDS_H
#define FL23_STACK_BOUNDS_H
#define FL23_STACK_MAP_LIMIT 131072U
#define FL23_STACK_MAX_BYTES 8388608ULL
#define FL23_STACK_USER_LIMIT 0x8000000000ULL
typedef unsigned long long fl23_sb_word;

static int fl23_sb_number(const unsigned char *s,unsigned int *at,unsigned int end,
                         unsigned int base,fl23_sb_word *out){
    fl23_sb_word value=0;unsigned int count=0;
    while(*at<end){
        unsigned int c=s[*at],digit=255;
        if(c>='0'&&c<='9')digit=c-'0';
        else if(c>='a'&&c<='f')digit=c-'a'+10;
        else if(c>='A'&&c<='F')digit=c-'A'+10;
        if(digit>=base)break;
        if(value>(~0ULL-digit)/base)return 0;
        value=value*base+digit;(*at)++;count++;
    }
    if(!count)return 0;*out=value;return 1;
}
static int fl23_sb_space(const unsigned char *s,unsigned int *at,unsigned int end){
    unsigned int before=*at;while(*at<end&&s[*at]==' ')(*at)++;
    return *at>before;
}
static int fl23_stack_bounds_many(const unsigned char *maps,unsigned int bytes,
                                 const fl23_sb_word *sp,unsigned int count,
                                 fl23_sb_word *lower,fl23_sb_word *upper){
    if(!maps||!sp||!lower||!upper||!bytes||bytes>FL23_STACK_MAP_LIMIT||!count||count>512)return 0;
    for(unsigned int i=0;i<count;i++){
        if(!sp[i]||(sp[i]&15)||sp[i]>FL23_STACK_USER_LIMIT-16)return 0;
        lower[i]=upper[i]=0;
    }
    unsigned int pos=0;fl23_sb_word previous=0;
    while(pos<bytes){
        unsigned int end=pos;
        while(end<bytes&&maps[end]!='\n'){if(!maps[end])return 0;end++;}
        if(end==bytes||end==pos)return 0;
        unsigned int at=pos;fl23_sb_word lo,hi,offset,major,minor,inode;
        if(!fl23_sb_number(maps,&at,end,16,&lo)||at>=end||maps[at++]!='-'||
           !fl23_sb_number(maps,&at,end,16,&hi)||lo>=hi||lo<previous||hi>FL23_STACK_USER_LIMIT||
           !fl23_sb_space(maps,&at,end)||end-at<4)return 0;
        previous=hi;const unsigned char *perm=maps+at;at+=4;
        if((perm[0]!='r'&&perm[0]!='-')||(perm[1]!='w'&&perm[1]!='-')||
           (perm[2]!='x'&&perm[2]!='-')||(perm[3]!='p'&&perm[3]!='s')||
           !fl23_sb_space(maps,&at,end)||!fl23_sb_number(maps,&at,end,16,&offset)||
           !fl23_sb_space(maps,&at,end)||!fl23_sb_number(maps,&at,end,16,&major)||
           at>=end||maps[at++]!=':'||!fl23_sb_number(maps,&at,end,16,&minor)||
           !fl23_sb_space(maps,&at,end)||!fl23_sb_number(maps,&at,end,10,&inode)||
           (at<end&&maps[at]!=' '))return 0;
        for(unsigned int i=0;i<count;i++)if(lo<=sp[i]&&sp[i]<=hi-16){
            if(perm[0]!='r'||perm[1]!='w'||perm[2]!='-'||perm[3]!='p'||
               offset||major||minor||inode||upper[i])return 0;
            lower[i]=lo;upper[i]=hi;
        }
        pos=end+1;
    }
    for(unsigned int i=0;i<count;i++)if(!upper[i])return 0;
    return 1;
}
static int fl23_stack_bounds(const unsigned char *maps,unsigned int bytes,fl23_sb_word sp,
                            fl23_sb_word *lower,fl23_sb_word *upper){
    return fl23_stack_bounds_many(maps,bytes,&sp,1,lower,upper);
}
static int fl23_stack_frame(fl23_sb_word sp,fl23_sb_word fp,fl23_sb_word lower,fl23_sb_word upper){
    return sp>=lower&&sp<upper&&upper>lower&&upper<=FL23_STACK_USER_LIMIT&&
           fp&&!(fp&15)&&fp>=sp&&fp-sp<=FL23_STACK_MAX_BYTES-16&&upper>=16&&fp<=upper-16;
}
#endif
