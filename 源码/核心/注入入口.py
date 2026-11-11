"""相机RAM注入入口；默认只离线校验，仅接受固定只读载荷。"""
import argparse
import time

from .Pico通信 import Pico
from .记录协议 import boot_records, verified_payload


def transmit(device, records, timeout, *, clock=time.monotonic, sleep=time.sleep, say=print):
    device.ping()
    try:
        device.carrier(False)
        device.disable_pull()
        device.carrier(True)
        sleep(0.01)
        if device.sense():
            raise RuntimeError('Baseline already HIGH. Disconnect breakout from camera and cold-start again; nothing sent.')
        say('ARMED：低基线通过。现在插入测试板、安装电池、相机拨ON。', flush=True)
        started = clock()
        while not device.sense():
            if clock() - started >= timeout:
                raise TimeoutError('Camera activation not detected; no records sent')
            device.carrier(True)  # Keep the Pico 1200 ms starvation guard alive.
            sleep(0.02)
        say('检测到相机释放高电平，开始按顺序发送一次记录流。', flush=True)
        sleep(0.5)
        for index, record in enumerate(records, 1):
            device.send_record(record)
            say(f'RECORD {index}/{len(records)} ACK', flush=True)
            if index < len(records):
                sleep(0.0144)
    finally:
        # Release GP28 also on timeout, cancellation or a failed ACK. Never
        # resend a stream automatically. Pico's starvation guard is additional.
        try:
            device.carrier(False)
        finally:
            device.disable_pull()
    say('完整记录已发送一次。保持电池和ON，先从相机拔下整个测试板。', flush=True)
    say('再把Pico数据线移到相机，随后检查Fastboot身份和分区表。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--载荷','--payload',dest='payload', help='本地构建且哈希匹配的相机RAM引导程序')
    parser.add_argument('--串口','--port',dest='port', help='Pico实际串口，不自动选择其他设备')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--检查Pico','--ping',dest='ping', action='store_true', help='仅核对Pico版本，不启动载波')
    modes.add_argument('--开始注入','--arm',dest='arm', action='store_true', help='低基线和冷启动检测通过后发送固定RAM程序')
    parser.add_argument('--等待秒数','--activation-timeout',dest='activation_timeout', type=float, default=900)
    args = parser.parse_args()
    if not 1 <= args.activation_timeout <= 3600:
        parser.error('等待秒数必须为1..3600')
    if not args.ping and not args.payload:
        parser.error('校验和注入需要--载荷；仅检查Pico时只需--串口')
    records = boot_records(verified_payload(args.payload)) if args.payload else None
    if not args.ping and not args.arm:
        print(f'离线校验通过：共{len(records)}条记录；没有访问串口或USB。')
        return
    if not args.port:
        parser.error('检查Pico和开始注入都需要明确指定--串口')
    if args.arm:
        print('请先读硬件说明.md。相机OFF、取电池至少5秒、测试板先不要插相机。')
        input('保持Pico USB数据线连接电脑，并确认测试板由Pico VBUS供电。回车启动载波，看到ARMED后才给相机上电：')
    import serial  # Optional dependency, imported only for explicit device access.
    with serial.Serial(args.port, 115200, timeout=5, write_timeout=5, exclusive=True) as port:
        device = Pico(port)
        if args.ping:
            device.ping()
            print('PING a6700_pio_od 2；版本匹配，未启动载波。')
        else:
            transmit(device, records, args.activation_timeout)


if __name__ == '__main__':
    main()
