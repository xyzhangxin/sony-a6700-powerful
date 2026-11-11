"""本地构建相机 RAM 引导：历史实机只读版。"""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
import argparse,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from 上游校验 import checked_upstream
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--上游目录','--upstream',dest='upstream',type=Path,required=True)
parser.add_argument('--输出目录','--output',dest='output',type=Path)
parser.add_argument('--cc',default=shutil.which('clang'))
parser.add_argument('--ld',default=shutil.which('aarch64-elf-ld') or shutil.which('aarch64-linux-gnu-ld'))
parser.add_argument('--objcopy',default=shutil.which('aarch64-elf-objcopy') or shutil.which('aarch64-linux-gnu-objcopy'))
parser.add_argument('--nm',default=shutil.which('aarch64-elf-nm') or shutil.which('aarch64-linux-gnu-nm'))
args=parser.parse_args()
if not all((args.cc,args.ld,args.objcopy,args.nm)):
    parser.error('需要PATH中的clang和AArch64 GNU binutils（ld、objcopy、nm），也可通过参数指定')
REF=checked_upstream(args.upstream)/'payloads/fastboot'
OUT=(args.output or Path('构建产物/只读')).resolve(); SRC=OUT/'生成源码'
if OUT.exists(): parser.error('输出目录已存在，请使用新目录')
SRC.mkdir(parents=True)

def replace_fn(text,name,new):
    m=re.search(r'^static void '+name+r'\([^\n]*\)\n\{',text,re.M)
    assert m,name
    start=m.end()-1;depth=0
    for end in range(start,len(text)):
        depth += (text[end]=='{')-(text[end]=='}')
        if not depth:
            return text[:m.start()]+new+text[end+1:]
    raise ValueError(name)

text=(REF/'src/fastboot.c').read_text()
text=replace_fn(text,'cmd_help','''static void cmd_help(void)
{
    fb_result("A6700 AP-only read candidate: getvar, upload");
    fb_result("oem partition; oem partition dump nflasha[B0/B1] [off [size]]; oem dumpbrom");
    fb_okay("");
}''')
text=replace_fn(text,'cmd_oem','''static void cmd_oem(const char *args)
{
    const char *rest;
    if (str_eq(args, "help")) { cmd_help(); return; }
    if (str_eq(args, "partition")) { cmd_partition(); return; }
    if (str_eq(args, "dumpbrom")) { cmd_dumpbrom(); return; }
    rest = str_after(args, "partition dump ");
    if (rest && (str_eq(rest,"nflasha") || str_after(rest,"nflasha ") ||
                 str_eq(rest,"nflashaB0") || str_after(rest,"nflashaB0 ") ||
                 str_eq(rest,"nflashaB1") || str_after(rest,"nflashaB1 "))) {
        cmd_partition_dump(rest); return;
    }
    fb_fail("AP-only read candidate: command unavailable");
}''')
text=replace_fn(text,'fastboot_command','''static void fastboot_command(const char *cmd)
{
    const char *rest;
    if ((rest = str_after(cmd, "getvar:")) != 0) cmd_getvar(rest);
    else if ((rest = str_after(cmd, "oem ")) != 0) cmd_oem(rest);
    else if (str_eq(cmd, "upload")) cmd_upload();
    else fb_fail("AP-only read candidate: command unavailable");
}''')
text=text.replace('fb_okay("ILCE-7M4");','fb_okay("A6700-AP-read-candidate");')
text=text.replace('fb_okay("ILCE7M4-fastboot");','fb_okay("A6700-RAM-fastboot");')
(SRC/'fastboot.c').write_text(text)
(SRC/'main.c').write_text('''#include "io.h"
#include "led.h"
#include "timer.h"
#include "usb_phy.h"
#include "dwc3.h"
#include "usb.h"
static u32 diag_phase, diag_epoch;
void diag_set(u32 phase) {
    if (phase > diag_phase) { diag_phase=phase; diag_epoch=timer_ticks(); }
}
void diag_tick(void) {
    u32 now=timer_ticks();
    u32 ms=(now-diag_epoch)/(TIMER0_HZ/1000);
    if (ms>=3000) {diag_epoch=now;ms=0;}
    if (ms<diag_phase*300 && ms%300<150) led_on(); else led_off();
}
int main(void) {
    led_init(); timer_init(); led_blip();
    /* A6700 kernel gpio_set_mode_bit(17,16,0x11): official DPDM init.
     * Reproduce only this known switch GPIO; no Type-C mode-trigger pulses.
     * Board branch truth table remains to be established by USB reception. */
    write32(0xF102E034ull, 1u<<16); /* FUNC_SET */
    write32(0xF102E024ull, 1u<<16); /* INEN_SET */
    write32(0xF102E048ull, 1u<<16); /* WDATA_CLR */
    write32(0xF102E004ull, 1u<<16); /* DIR_SET */
    write32(0xF102E044ull, 1u<<16); /* Official dpdm_connect_to(2): HIGH */
    dsb();
    if (usb_phy_init() != USB_PHY_OK) led_fail(6);
    if (dwc3_core_init() != DWC3_OK) led_fail(7);
    if (usb_gadget_init() != 0) led_fail(8);
    if (dwc3_connect() != DWC3_OK) led_fail(9);
    diag_set(1);
    usb_gadget_run();
    return 0;
}
''')
usb=(REF/'src/usb.c').read_text()
usb='extern void diag_tick(void);\nextern void diag_set(unsigned int phase);\n'+usb
usb=usb.replace('led_activity_tick();','diag_tick();').replace('led_activity();','/* heartbeat is independent of event activity */')
usb=usb.replace('static void on_reset(void)\n{','static void on_reset(void)\n{\n    diag_set(2);')
usb=usb.replace('static int handle_get_descriptor(void)\n{','static int handle_get_descriptor(void)\n{\n    diag_set(3);')
usb=usb.replace('configured = 1;','diag_set(4);\n            configured = 1;')
(SRC/'usb.c').write_text(usb)
(SRC/'no_cp.c').write_text('''#include "io.h"
#include "cetus.h"
void log_init(void) {}
void mira_log(const char *s) {(void)s;}
void mira_logx(const char *s,u64 v) {(void)s;(void)v;}
int cetus_read(u32 a,u8 *b,u32 n,u32 w) {(void)a;(void)b;(void)n;(void)w;return CETUS_E_ARG;}
int cetus_nor_read(u32 a,u8 *b,u32 n) {(void)a;(void)b;(void)n;return CETUS_E_ARG;}
''')
sources=[SRC/'main.c',SRC/'fastboot.c',SRC/'no_cp.c',SRC/'usb.c']
sources += [REF/'src'/n for n in ('start.S','delay.c','led.c','usb_phy.c','dwc3.c','sdhci.c')]
sources += [REF/'src/spacc.c']
flags=['-target','aarch64-none-elf','-march=armv8-a','-mgeneral-regs-only','-mstrict-align','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-pic','-fno-common','-ffunction-sections','-fdata-sections','-O2','-g','-I'+str(REF/'include')]
objects=[]
for source in sources:
    obj=OUT/(source.stem+'.o');objects.append(obj)
    subprocess.run([args.cc,*flags,'-c',str(source),'-o',str(obj)],check=True)
stem='相机只读引导'
elf=OUT/(stem+'.elf')
subprocess.run([args.ld,'-T',str(REF/'fastboot.ld'),'--gc-sections','--build-id=none','-Map='+str(OUT/'fastboot-read.map'),'-o',str(elf),*map(str,objects)],check=True)
binary=OUT/(stem+'.bin')
subprocess.run([args.objcopy,'-O','binary',str(elf),str(binary)],check=True)
symbols=subprocess.check_output([args.nm,'--defined-only',str(elf)],text=True)
(OUT/'symbols.txt').write_text(symbols)
for name in ('mmc_write_blocks','cmd_flash','cmd_download','cetus_bring_up','cmd_exec','cmd_poke','system_reset'):
    if re.search(r'\b'+name+r'$',symbols,re.M):
        raise RuntimeError('不应链接的符号：'+name)
r={'upstream_commit':'2328d18ce640e6c8cd9341efcf0043ce9042a135',
   'variant':'AP-only 只读, GPIO17 bit16 HIGH', 'bytes':binary.stat().st_size,
   'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
   'load_entry':'0xfe020000','storage_write_symbols_absent':True,
   'mode':'只读','real_device_write_verified':False,
   'build_is_not_hardware_validation':True}
if r['bytes']>0x10000: raise RuntimeError('Payload exceeds fixed RAM window')
(OUT/'manifest.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r,indent=2))
catalog=json.loads((Path(__file__).resolve().parents[1]/'核心/载荷清单.json').read_text())
expected=catalog['只读']['sha256']
if r['sha256'] != expected:
    raise SystemExit('构建结果与载荷清单不同，可能是工具链差异；注入仍被阻止，不要绕过哈希。')
