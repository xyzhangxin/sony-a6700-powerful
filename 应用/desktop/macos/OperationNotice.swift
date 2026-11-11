import SwiftUI

/// The service owns risk and scope copy; the view only groups it for reading.
struct OperationNoticeView: View {
    let state: Snapshot?
    let onRecheck: () -> Void
    let onLicense: () -> Void
    var reviewing = false
    @State private var diagnostics = false
    @State private var validation = false
    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            PageTitle(title: state?.notice.title ?? "操作前须知", subtitle: "请先确认机型与操作风险，再准备相机。")
            if !reviewing { startupCheck }
            if let sections = state?.notice.sections {
                if let scope = sections.first(where: { $0.id == "scope" }) {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: "camera").font(.system(size: 21)).foregroundColor(UI.accent)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(scope.title).font(.system(size: 14, weight: .semibold))
                            Text(scope.body).font(.system(size: 11)).foregroundColor(UI.muted)
                                .fixedSize(horizontal: false, vertical: true).lineSpacing(3)
                        }
                    }.padding(17).frame(maxWidth: .infinity, alignment: .leading)
                        .background(UI.accent.opacity(0.045)).cornerRadius(10)
                }
                HStack(alignment: .top, spacing: 14) {
                    ForEach(sections.filter { $0.level == "risk" }) { section in
                        noticeCard(section, equalHeight: true)
                    }
                }.fixedSize(horizontal: false, vertical: true)
                if let disclaimer = sections.first(where: { $0.id == "disclaimer" }) { noticeCard(disclaimer) }
                if let progress = sections.first(where: { $0.id == "validation" }) {
                    DisclosureGroup("当前验证范围", isExpanded: $validation) {
                        Text(progress.body).font(.system(size: 11)).foregroundColor(UI.muted)
                            .fixedSize(horizontal: false, vertical: true).lineSpacing(3).padding(.top, 7)
                    }.font(.system(size: 11)).foregroundColor(UI.muted)
                }
            }
        }
    }
    private var startupCheck: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                if state?.checked != true { ProgressView().controlSize(.small) }
                else { Image(systemName: state?.environment.ready == true ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                    .foregroundColor(state?.environment.ready == true ? UI.accent : UI.amber) }
                Text(state?.checked != true ? "正在检查本机组件…" : state?.environment.ready == true ? "本机组件已就绪" : "本机组件检查未通过")
                    .font(.system(size: 12, weight: .medium))
                Spacer()
                Button(diagnostics ? "收起详情" : "检查详情") { diagnostics.toggle() }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundColor(UI.muted)
                Button("重新检查", action: onRecheck).buttonStyle(.plain).font(.system(size: 11))
                    .foregroundColor(UI.accent).disabled(state?.checked != true)
            }
            if state?.checked == true && state?.environment.ready != true {
                ForEach(state?.environment.checks.filter { !$0.ok } ?? []) { row in
                    Text(row.name + "：" + row.remediation).font(.system(size: 11)).foregroundColor(UI.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if diagnostics {
                ForEach(state?.environment.checks ?? []) { row in
                    Label(row.name + " · " + row.detail, systemImage: row.ok ? "checkmark.circle" : "exclamationmark.circle")
                        .font(.system(size: 10)).foregroundColor(UI.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("这里只检查本机组件，相机兼容性在连接后检查。")
                    .font(.system(size: 10)).foregroundColor(UI.muted)
            }
        }.padding(13).background(.white).cornerRadius(9)
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(UI.line))
    }
    private func noticeCard(_ section: NoticeSection, equalHeight: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(section.title, systemImage: section.icon).font(.system(size: 12, weight: .semibold))
                .foregroundColor(section.level == "risk" ? UI.amber : UI.ink)
            Text(section.body).font(.system(size: 11)).foregroundColor(UI.muted)
                .lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            if !section.source_url.isEmpty, let url = URL(string: section.source_url) {
                Link("Sony 服务政策 ↗", destination: url).font(.system(size: 10)).foregroundColor(UI.accent)
            }
            if section.id == "disclaimer" {
                Button("查看开源许可", action: onLicense).buttonStyle(.plain)
                    .font(.system(size: 10)).foregroundColor(UI.accent)
            }
        }.padding(16).frame(maxWidth: .infinity, maxHeight: equalHeight ? .infinity : nil, alignment: .topLeading)
            .background(.white).cornerRadius(10)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(UI.line))
    }
}
