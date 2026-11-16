"""Add ARM store validation to the bound Thumb-only offline observer.

Used by the query/GIC interworking harness only. No device or firmware edits.
"""
from capstone import Cs,CS_ARCH_ARM,CS_MODE_ARM
from capstone.arm import ARM_OP_MEM,ARM_REG_PC
from unicorn import UC_HOOK_CODE
import unicorn.arm_const as arm
from cp_thumb_fixture_reads import FixtureReadUc


def ArmThumbFixtureUc(*args,**kwargs):
    instance=FixtureReadUc(*args,**kwargs)
    original=instance.lab_original_hook_add
    decoder=Cs(CS_ARCH_ARM,CS_MODE_ARM);decoder.detail=True
    def add(kind,callback,user_data=None,begin=1,end=0,*rest):
        if kind!=UC_HOOK_CODE or callback.__name__!='before_load':
            return original(kind,callback,user_data,begin,end,*rest)
        captured=next((cell.cell_contents for cell in callback.__closure__ or ()
                       if callable(cell.cell_contents) and hasattr(cell.cell_contents,'__self__')),None)
        assert captured is not None,'Missing original store validator'
        def observe(u,at,size,user):
            if u.reg_read(arm.UC_ARM_REG_CPSR)&0x20:
                return callback(u,at,size,user)
            if u.reg_read(arm.UC_ARM_REG_PC)!=at:return
            ins=next(decoder.disasm(bytes(u.mem_read(at,size)),at,count=1))
            def reg(r):
                if r==ARM_REG_PC:return at+8
                return u.reg_read(getattr(arm,'UC_ARM_REG_'+decoder.reg_name(r).upper()))
            if ins.mnemonic=='push':
                values=[reg(v.reg) for v in ins.operands]
                address=u.reg_read(arm.UC_ARM_REG_SP)-len(values)*4
                for n,value in enumerate(values):captured(u,17,address+4*n,4,value,user)
            elif ins.mnemonic=='str':
                mem=next(v for v in ins.operands if v.type==ARM_OP_MEM)
                assert not mem.mem.index and not ins.writeback
                captured(u,17,(reg(mem.mem.base)+mem.mem.disp)&0xffffffff,4,reg(ins.operands[0].reg),user)
            elif ins.mnemonic.startswith(('st','vst','vpush','swp','srs')):
                raise AssertionError('Unplanned ARM store '+ins.mnemonic)
        return original(kind,observe,user_data,begin,end,*rest)
    instance.lab_original_hook_add=add
    return instance
