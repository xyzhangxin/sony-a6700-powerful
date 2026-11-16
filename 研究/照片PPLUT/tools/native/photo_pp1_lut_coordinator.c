/* SPDX-License-Identifier: GPL-3.0-only
 * OFFLINE PROTOTYPE. No firmware addresses, DMA, LUT setter or device entry.
 * Native adapters must supply separately validated current-task observations
 * and completion receipts. A receipt here is not a hardware-drain proof.
 * All calls must be serialized by the future adapter; this is not a mutex.
 */
typedef unsigned int u32;
enum {IDLE=0, HELD=1, LOADING=2, CALLBACK=3, READY=4, RENDERING=5,
      CLOSING=6, POISONED=7, RELEASING=8, TRANSFERRING=9};
enum {IRQ_RETURNED=1, WAIT_SUCCESS=2, MESSAGE_DISPOSED=4, DRIVER_QUIET=8,
      ALL_RECEIPTS=15};
struct observation {
 u32 task,handle,photo_owner,pin_owner,policy,mode,references;
 u32 photo_active,pin_active,owner_nodes,asset_generation;
 u32 bank_handle[4],bank_kind[4],bank_flags[4];
};
struct coordinator {
 u32 magic,phase,epoch,last_epoch,task,handle,photo_owner,pin_owner;
 u32 asset_generation,callback_count,callback_status,receipts,cancelled,photo_ended;
};
static struct coordinator state;
_Static_assert(sizeof(struct observation)==92,"observation ABI");
_Static_assert(sizeof(struct coordinator)==56,"state ABI");

static u32 same(const struct observation *o,u32 active){
 if(!o||o->task!=state.task||o->handle!=state.handle||
    o->photo_owner!=state.photo_owner||o->pin_owner!=state.pin_owner||
    o->policy!=9||o->mode||!o->pin_active||!o->references||
    o->asset_generation!=state.asset_generation)return 0;
 for(u32 i=0;i<4;i++)if(o->bank_handle[i]!=state.handle||o->bank_kind[i]!=1||
    (active&&!(o->bank_flags[i]&(1u<<21))))return 0;
 return 1;
}

u32 pp1_bind(const struct observation *o,u32 epoch){
 if(!o||state.phase!=IDLE||!epoch||epoch<=state.last_epoch||
    !o->task||!o->handle||o->handle==0xffffffffu||!o->photo_owner||!o->pin_owner||
    o->photo_owner==o->pin_owner||o->policy!=9||o->mode||
    !o->photo_active||!o->pin_active||o->references!=2||o->owner_nodes!=2)return 0;
 for(u32 i=0;i<4;i++)if(o->bank_kind[i]!=1||o->bank_handle[i]!=o->handle)return 0;
 state.magic=0x50314c43;state.phase=HELD;state.epoch=epoch;state.last_epoch=epoch;
 state.task=o->task;state.handle=o->handle;state.photo_owner=o->photo_owner;
 state.pin_owner=o->pin_owner;state.asset_generation=o->asset_generation;
 state.callback_count=state.callback_status=state.receipts=state.cancelled=state.photo_ended=0;
 return 1;
}

u32 pp1_arm(const struct observation *o,u32 epoch){
 if(state.phase!=HELD||state.cancelled||state.photo_ended||epoch!=state.epoch||!same(o,1)||!o->photo_active||
    o->references!=2||o->owner_nodes!=2)return 0;
 state.phase=LOADING;return 1;
}

/* Signature matches the stock MLUT status-only callback ABI. It never frees. */
void pp1_completion(u32 status){
 if(state.phase!=TRANSFERRING||state.callback_count){state.phase=POISONED;return;}
 state.callback_count=1;state.callback_status=status;
 state.phase=status?POISONED:CALLBACK;
}

u32 pp1_transfer_started(u32 epoch){
 if(epoch!=state.epoch||state.phase!=LOADING)return 0;
 state.phase=TRANSFERRING;return 1;
}

u32 pp1_consumer_boundary(u32 epoch,u32 receipts){
 if(epoch!=state.epoch||state.phase!=CALLBACK||receipts&~ALL_RECEIPTS)return 0;
 state.receipts|=receipts;
 if(state.receipts!=ALL_RECEIPTS)return 0;
 state.phase=state.photo_ended?CLOSING:READY;return 1;
}

u32 pp1_main_gate(const struct observation *o,u32 epoch){
 if(epoch!=state.epoch||(state.phase!=READY&&state.phase!=RENDERING)||
    state.cancelled||state.photo_ended||state.callback_count!=1||state.callback_status||
    state.receipts!=ALL_RECEIPTS||!same(o,1)||!o->photo_active||
    o->references!=2||o->owner_nodes!=2)return 0;
 return 1;
}

u32 pp1_render_begin(const struct observation *o,u32 epoch){
 if(!pp1_main_gate(o,epoch))return 0;
 state.phase=RENDERING;return 1;
}

u32 pp1_cancel(u32 epoch){
 if(epoch!=state.epoch||state.phase==IDLE||state.phase==POISONED)return 0;
 state.cancelled=1;return 1;
}

u32 pp1_photo_released(u32 epoch,u32 original_release_succeeded){
 if(epoch!=state.epoch||state.phase==IDLE||state.photo_ended)return 0;
 if(!original_release_succeeded){state.phase=POISONED;return 0;}
 state.photo_ended=1;
 if(state.phase!=RENDERING)state.cancelled=1;
 if(state.phase==READY||state.phase==RENDERING)state.phase=CLOSING;
 return 1;
}

u32 pp1_release_gate(const struct observation *o,u32 epoch){
 if(epoch!=state.epoch||state.phase!=CLOSING||!state.photo_ended||
    state.callback_count!=1||state.callback_status||state.receipts!=ALL_RECEIPTS||
    !same(o,0)||o->photo_active||o->references!=1||o->owner_nodes!=1)return 0;
 return 1;
}

u32 pp1_pin_released(const struct observation *o,u32 epoch,u32 succeeded){
 if(epoch!=state.epoch||state.phase!=RELEASING)return 0;
 if(!succeeded||!o||o->references||o->owner_nodes||o->pin_active||
    o->handle!=0xffffffffu||o->task!=state.task||o->photo_owner!=state.photo_owner||
    o->pin_owner!=state.pin_owner||o->asset_generation!=state.asset_generation||
    o->policy!=9||o->mode){state.phase=POISONED;return 0;}
 for(u32 i=0;i<4;i++)if(o->bank_kind[i]){state.phase=POISONED;return 0;}
 state.phase=IDLE;return 1;
}

u32 pp1_release_begin(const struct observation *o,u32 epoch){
 if(!pp1_release_gate(o,epoch))return 0;
 state.phase=RELEASING;return 1;
}

u32 pp1_fault(u32 epoch){
 if(epoch!=state.epoch||state.phase==IDLE)return 0;
 state.phase=POISONED;return 1;
}

u32 pp1_state_word(u32 index){
 const u32 *words=(const u32 *)&state;
 return index<sizeof(state)/sizeof(u32)?words[index]:0xffffffffu;
}
