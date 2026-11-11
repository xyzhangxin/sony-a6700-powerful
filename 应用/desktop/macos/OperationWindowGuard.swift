import AppKit
import SwiftUI

/// Keep the worker alive until an operation has returned an explicit result.
/// Preserve SwiftUI's window delegate for every other window callback.
struct OperationWindowGuard: NSViewRepresentable {
    let model: PreviewModel

    final class Anchor: NSView {
        var guardDelegate: GuardDelegate?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window = window, let guardDelegate = guardDelegate else { return }
            if window.delegate !== guardDelegate {
                guardDelegate.previous = window.delegate
                window.delegate = guardDelegate
            }
        }
    }

    final class GuardDelegate: NSObject, NSWindowDelegate {
        weak var model: PreviewModel?
        weak var previous: NSWindowDelegate?

        func windowShouldClose(_ sender: NSWindow) -> Bool {
            guard model?.allowWindowExit() != false else { return false }
            return previous?.windowShouldClose?(sender) ?? true
        }

        override func responds(to selector: Selector!) -> Bool {
            super.responds(to: selector) || previous?.responds(to: selector) == true
        }

        override func forwardingTarget(for selector: Selector!) -> Any? {
            previous?.responds(to: selector) == true ? previous : super.forwardingTarget(for: selector)
        }
    }

    func makeCoordinator() -> GuardDelegate {
        let delegate = GuardDelegate()
        delegate.model = model
        return delegate
    }

    func makeNSView(context: Context) -> Anchor {
        let anchor = Anchor()
        anchor.guardDelegate = context.coordinator
        return anchor
    }

    func updateNSView(_ view: Anchor, context: Context) {}
}
