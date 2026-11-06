static u32 dma_read(u64 base,u64 source,u32 length,u8 *out){
    typedef long (*alloc_fn)(u32,u32*);typedef long (*dma_fn)(int,u64,u64,u32);typedef long (*free_fn)(u32);
    alloc_fn alloc=(alloc_fn)(base+0x51e8);dma_fn transfer=(dma_fn)(base+0x50b8);free_fn release=(free_fn)(base+0x52fc);
    u32 id=0;long address=alloc(length,&id);
    if(address<=0||(u64)address>=(u64)-4095)return 1;
    long fd=t_sc(56,-100,(long)"/dev/stream",2,0,0,0);
    if(fd<0){if(release(id))a1_dma_cleanup|=4;return 2;}
    u64 delta=(u64)address&4095;u64 span=(u64)length+delta;
    long mapping=t_sc(222,0,(long)span,3,1,fd,(long)((u64)address-delta));
    if(mapping<0||(u64)mapping>=(u64)-4095){if(t_sc(57,fd,0,0,0,0,0))a1_dma_cleanup|=2;if(release(id))a1_dma_cleanup|=4;return 3;}
    long rc=transfer(0,source,(u64)address,length);u32 status=0;
    if(rc)status=4;
    else {const u8 *data=(const u8*)((u64)mapping+delta);for(u32 i=0;i<length;i++)out[i]=data[i];}
    if(t_sc(215,mapping,(long)span,0,0,0,0)){a1_dma_cleanup|=1;if(!status)status=5;}
    if(t_sc(57,fd,0,0,0,0,0)){a1_dma_cleanup|=2;if(!status)status=6;}
    if(release(id)){a1_dma_cleanup|=4;if(!status)status=7;}
    return status;
}
