import AppKit

final class RecoveryPrimaryButton: NSButton {
  init(title: String, target: AnyObject?, action: Selector?) {
    super.init(frame: .zero)
    self.title = title.uppercased()
    self.target = target
    self.action = action
    bezelStyle = .rounded
    font = OmarchyTheme.button
    contentTintColor = OmarchyTheme.accentText
    wantsLayer = true
    layer?.backgroundColor = OmarchyTheme.accent.cgColor
    layer?.cornerRadius = OmarchyTheme.buttonRadius
    isBordered = false
    translatesAutoresizingMaskIntoConstraints = false
    heightAnchor.constraint(equalToConstant: OmarchyTheme.buttonHeight).isActive = true
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }
}
