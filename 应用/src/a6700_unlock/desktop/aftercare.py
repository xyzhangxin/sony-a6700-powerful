"""Post-operation camera checks. No firmware values or device access here.

Paths follow the project's A6700 2.00 validation records; these unlocked
entries are not all described in Sony's unmodified A6700 help guide.
"""
from __future__ import annotations

from ..operation_workflow import build_evidence, build_workflow


FL23_STARTUP_NOTICE = ('每次取出电池并重新装入后，开机需等待约 40 秒，'
                       '待 FL2／FL3 自动加载后再使用。刚开机时未显示这两项，请先等待加载。')


MENU_CHECKS = {
    'fl23': dict(
        path='MENU → 曝光/颜色 → 色调 → 创意外观',
        unlock='主列表出现独立 FL2、FL3；图标、预览、说明、拍摄保存和回放正常。',
        close='断电重启后不再出现 FL2、FL3，原有外观仍可使用；未关闭的中文名称保持。',
        close_note='完整卸载后立即按步骤断电重启，先不要继续选择RAM中尚未退出的新增外观。其他未选功能保持原状；本操作不是整机恢复出厂。',
        note='名称由“创意外观中文名称”选项独立控制。开机后等待约 40 秒加载，再检查图标、说明和拍摄。',
        unlock_notice=FL23_STARTUP_NOTICE),
    'creative_look_names': dict(
        path='MENU → 曝光/颜色 → 色调 → 创意外观（含编号 1～6）',
        unlock='中文界面显示 ST(标准)、FL(胶片) 等完整名称，编号 1～6 的列表与调节页一致。',
        close='恢复原有简称；现有创意外观、图标、调整值和说明保持可用。',
        note='名称使用相机当前语言的原生文字；此选项不强制更改相机语言或成像。'),
    'ftp': dict(
        path='MENU → 网络 → 传输/远程 → FTP传输功能',
        unlock='能打开 FTP传输功能，并看到 FTP功能、服务器设置等项目。',
        close='新增的 FTP传输功能入口不再显示。',
        note='解锁不会自动配置服务器或开启传输；菜单检查不等于已完成网络传输测试。',
        close_note='确认新增入口已隐藏即可，无需重新配置服务器。'),
    'shutter': dict(
        path='MENU → 设置 → 设置选项 → 防尘功能 → 关机时快门',
        unlock='出现“关机时快门”选项。设为“开”后正常关机，快门帘关闭；再次开机时打开。',
        close='新增的“关机时快门”选项不再显示。',
        note='请遵守机身静音等相关设置的限制，不要触摸快门帘。无需拆下镜头检查。'),
    'exposure': dict(
        path='MENU → 拍摄 → 照相模式 → 曝光控制类型',
        unlock='出现并可选择“灵活曝光模式”；在 曝光/颜色 → 曝光 → 自动/手动切设 中可分别切换 Av、Tv、ISO。',
        close='“曝光控制类型 / 灵活曝光模式”新增入口不再显示。',
        note='请切到视频模式、停止录像，模式转盘使用 P/A/S/M；调用存储的 1/2/3 模式可能使选项变灰。'),
    'sound': dict(
        path='MENU → 设置 → 声音选项 → 音频信号',
        unlock='出现“开：仅电子快门”和“开：除快门以外”。选“开：仅电子快门”后，用电子快门拍照可听到提示音。',
        close='音频信号仅保留原有“开 / 关”，两个新增选项不再显示。',
        note='试听：照片模式，MENU → 拍摄 → 快门/静音，将静音模式设为关、快门类型设为电子快门。',
        close_note='若菜单停在已移除的选项、无法上下切换或高亮异常，用触摸选回“开”或“关”。'),
}

RESTART_STEPS = [
    dict(id='off', title='关机', detail='正常关闭相机，等待存储指示灯熄灭。'),
    dict(id='usb-out', title='断开 USB', detail='拔掉 USB 数据线，并断开其他外接供电。'),
    dict(id='battery-out', title='拔出电池', detail='取出相机电池，确保机身完全断电。'),
    dict(id='wait', title='等待 10 秒', detail='从断开所有供电后开始，至少等待 10 秒。'),
    dict(id='battery-in', title='插入电池', detail='重新装入电池。'),
    dict(id='on', title='开机检查', detail='按下方路径检查本次功能。'),
]


def build_aftercare(action, report, selected, simulation):
    """Only verified unlock/close results receive a camera check guide."""
    if (action not in ('unlock', 'close') or not report.get('verified')
            or report.get('status') not in ('completed', 'no-op', 'settings-prepared')):
        return None
    target = 'enabled' if action == 'unlock' else 'disabled'
    before = {row['id']: row for row in report.get('plan', {}).get('features', [])}
    final = {row['id']: row for row in report.get('final_features', [])}
    checks = []
    changed_ids = set()
    # Follow the catalog/preflight display order, not request sorting.
    ordered = [identifier for identifier in before if identifier in (selected or [])]
    ordered.extend(identifier for identifier in selected or [] if identifier not in ordered)
    for identifier in ordered:
        menu = MENU_CHECKS.get(identifier)
        if menu is None:
            continue
        updating=action=='unlock' and before.get(identifier,{}).get('update_available',False)
        unchanged = report['status'] == 'no-op' or (before.get(identifier, {}).get('state') == target and not updating)
        verb = '更新' if updating else '解锁' if action == 'unlock' else '关闭'
        row = final.get(identifier, before.get(identifier, {}))
        if not unchanged:
            changed_ids.add(identifier)
        checks.append(dict(id=identifier, name=row.get('name', identifier), path=menu['path'],
                           expected=menu[action], note=(menu.get('close_note', menu['note'])
                                                       if action == 'close' else menu['note']),
                           outcome=('原已%s，未重复写入' % verb if unchanged
                                    else '已%s · 回读通过' % verb)))
    steps = [dict(step) for step in RESTART_STEPS]
    startup_notice = FL23_STARTUP_NOTICE if action == 'unlock' and 'fl23' in changed_ids else None
    restart_required = report['status'] != 'no-op'
    return dict(simulation=simulation, restart_required=restart_required,
                steps=steps, features=checks, startup_notice=startup_notice,
                workflow=build_workflow(action, steps, checks, changed_ids, restart_required),
                evidence=build_evidence(action, report, checks, restart_required))
