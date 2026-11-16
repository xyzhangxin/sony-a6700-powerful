"""Observe named reads and validate stores before their real instruction.

Unicorn 2.1.4 pre-read hooks locally misexecute CMP/branch after a Thumb IT
load (retained minimal repro). Pre-write hooks similarly affect conditional
owner-lookup stores. Keep original write validation through decoded stores,
and code instrumentation for the three named read-fixture regions.
This adapts only offline emulators. It does not patch the library or firmware.
"""
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_MEM,ARM_REG_PC,ARM_SFT_LSL,ARM_SFT_LSR,ARM_SFT_ASR
from unicorn import Uc,UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
from types import MethodType
import unicorn.arm_const as arm


class FixtureReadUc:
    def __new__(cls,*args,**kwargs):
        # Uc itself is an architecture-dispatch factory, not a normal base
        # class. Wrap only this initialized instance's public registration.
        instance=Uc(*args,**kwargs)
        instance.lab_original_hook_add=instance.hook_add
        instance.hook_add=MethodType(cls.hook_add,instance)
        return instance

    def hook_add(self,htype,callback,user_data=None,begin=1,end=0,*args):
        if htype not in (UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE):
            return self.lab_original_hook_add(htype,callback,user_data,begin,end,*args)
        if begin!=1 or end!=0:
            raise ValueError('Only the original global offline fixture read hook is supported')
        decoder=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN)
        decoder.detail=True
        cache={}
        observer=getattr(callback,'__self__',None)
        if observer is None:
            raise ValueError('Expected original bound fixture observer')
        from emulate_cp_photo_mlut_switch import PARAM,RESOURCE_STATE
        def register(uc,capstone_register,at):
            if capstone_register==ARM_REG_PC:return (at+4)&~3
            name=decoder.reg_name(capstone_register).upper()
            return uc.reg_read(getattr(arm,'UC_ARM_REG_'+name))
        def before_load(uc,at,size,user):
            if uc.reg_read(arm.UC_ARM_REG_PC)!=at:return
            if at not in cache:
                decoded=list(decoder.disasm(bytes(uc.mem_read(at,size)),at,count=1))
                cache[at]=decoded[0] if decoded else None
            instruction=cache[at]
            if instruction is None:return
            operation=instruction.mnemonic.split('.')[0]
            if htype==UC_HOOK_MEM_WRITE and operation.startswith(('push','stm')):
                operands=instruction.operands
                if operation.startswith('push'):
                    values=[register(uc,op.reg,at) for op in operands]
                    address=uc.reg_read(arm.UC_ARM_REG_SP)-4*len(values)
                else:
                    values=[register(uc,op.reg,at) for op in operands[1:]]
                    address=register(uc,operands[0].reg,at)
                    if operation.startswith('stmdb'):address-=4*len(values)
                    elif operation.startswith('stmda'):address-=4*(len(values)-1)
                    elif operation.startswith('stmib'):address+=4
                for index,value in enumerate(values):callback(uc,17,(address+4*index)&0xffffffff,4,value,user_data)
                return
            if htype==UC_HOOK_MEM_READ and not operation.startswith('ldr'):return
            if htype==UC_HOOK_MEM_WRITE and not operation.startswith(('str','strex')):
                if operation.startswith(('st','vst','vpush','swp','srs')):raise AssertionError('Unplanned native store '+operation)
                return
            memory=next((operand for operand in instruction.operands if operand.type==ARM_OP_MEM),None)
            if memory is None:return
            address=register(uc,memory.mem.base,at) if memory.mem.base else 0
            index=register(uc,memory.mem.index,at) if memory.mem.index else 0
            if memory.shift.type==ARM_SFT_LSL:index=(index<<memory.shift.value)&0xffffffff
            elif memory.shift.type==ARM_SFT_LSR:index>>=memory.shift.value
            elif memory.shift.type==ARM_SFT_ASR:
                index=((index if index<0x80000000 else index-0x100000000)>>memory.shift.value)&0xffffffff
            elif memory.shift.type:raise AssertionError('Unplanned memory index shift')
            address=(address+(-index if memory.subtracted else index)+memory.mem.disp)&0xffffffff
            if htype==UC_HOOK_MEM_WRITE:
                sources=[operand.reg for operand in instruction.operands if operand.type!=ARM_OP_MEM]
                if operation.startswith('strex'):sources=sources[1:]
                width=1 if operation.startswith(('strb','strexb')) else 2 if operation.startswith(('strh','strexh')) else 4
                count=2 if operation.startswith(('strd','strexd')) else 1
                for index,source in enumerate(sources[:count]):
                    callback(uc,17,address+4*index,width,register(uc,source,at)&((1<<(width*8))-1),user_data)
                return
            selected=(0xf1058000<=address<0xf105a000 or
                address==RESOURCE_STATE+observer.resource*4 or address==PARAM+0x1bc or
                getattr(observer,'waiting_channel',None) is not None and
                address==observer.registers[observer.waiting_channel]+4)
            if not selected:return
            width=1 if operation.startswith(('ldrb','ldrsb')) else 2 if operation.startswith(('ldrh','ldrsh')) else 4
            callback(uc,16,address,width,0,user_data)
            if operation.startswith('ldrd'):callback(uc,16,address+4,4,0,user_data)
        return self.lab_original_hook_add(UC_HOOK_CODE,before_load,user_data)
