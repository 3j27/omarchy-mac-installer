import AppKit

/// The complete Recovery interface uses only AppKit. The model owns installation
/// work; this controller renders its state and keeps credentials in native fields.
final class InstallerView: NSViewController, NSTextFieldDelegate {
  private let model: InstallerModel
  private let log = InstallerLog.shared
  private let titleLabel = NSTextField(labelWithString: "")
  private let content = NSView()
  private let footer = NSStackView()
  private let attribution = NSTextField(labelWithString: "Made with love by Aurora Silicon")
  private var renderedStage: Stage?
  private var primaryButton: NSButton?
  private var debugButton: NSButton?
  private var proxyButton: NSButton?
  private var progressIndicator: NSProgressIndicator?
  private var operationLabel: NSTextField?
  private var accountUsernameField: NSTextField?
  private var accountPasswordField: NSSecureTextField?
  private var accountConfirmationField: NSSecureTextField?
  private var accountButton: NSButton?
  private var skipButton: NSButton?
  private var advancedMachO: NSButton?
  private var advancedNested: NSButton?
  private var issueShowing = false
  private var authorizationRepair = false

  init(model: InstallerModel) {
    self.model = model
    super.init(nibName: nil, bundle: nil)
    model.onChange = { [weak self] in self?.refresh() }
    log.onChange = { [weak self] in self?.refresh() }
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }

  deinit {
    model.onChange = nil
    log.onChange = nil
  }

  override func loadView() {
    let root = NSView(frame: NSRect(x: 0, y: 0, width: 780, height: 660))
    root.wantsLayer = true
    root.layer?.backgroundColor = OmarchyTheme.window.cgColor
    root.appearance = NSAppearance(named: .darkAqua)
    view = root

    let wordmark = OmarchyWordmark(frame: .zero)
    wordmark.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(wordmark)

    titleLabel.font = OmarchyTheme.title
    titleLabel.textColor = OmarchyTheme.text
    titleLabel.alignment = .center
    titleLabel.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(titleLabel)

    content.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(content)

    attribution.font = OmarchyTheme.detail
    attribution.textColor = OmarchyTheme.secondaryText
    attribution.alignment = .center
    attribution.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(attribution)

    footer.orientation = .horizontal
    footer.alignment = .centerY
    footer.spacing = 12
    footer.distribution = .gravityAreas
    footer.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(footer)

    NSLayoutConstraint.activate([
      wordmark.topAnchor.constraint(equalTo: root.topAnchor, constant: 62),
      wordmark.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      wordmark.widthAnchor.constraint(equalToConstant: 400),
      wordmark.heightAnchor.constraint(equalToConstant: 94),
      titleLabel.topAnchor.constraint(equalTo: wordmark.bottomAnchor, constant: 28),
      titleLabel.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      titleLabel.widthAnchor.constraint(equalToConstant: 600),
      content.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 24),
      content.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      content.widthAnchor.constraint(equalToConstant: 510),
      content.heightAnchor.constraint(equalToConstant: 292),
      footer.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 32),
      footer.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -32),
      footer.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -26),
      footer.heightAnchor.constraint(equalToConstant: 52),
      attribution.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      attribution.bottomAnchor.constraint(equalTo: footer.topAnchor, constant: -8),
    ])
    refresh()
  }

  private func refresh() {
    if !Thread.isMainThread {
      DispatchQueue.main.async { [weak self] in self?.refresh() }
      return
    }
    guard isViewLoaded else { return }
    if renderedStage != model.stage {
      renderedStage = model.stage
      buildStage()
    }
    titleLabel.stringValue = title
    attribution.isHidden = model.stage != .welcome
    progressIndicator?.doubleValue = model.progress * 100
    operationLabel?.stringValue = model.currentOperation + "…"
    primaryButton?.isEnabled = !model.checkingEnvironment
    debugButton?.title = model.debugEnabled ? "Disable Debug" : "Enable Debug"
    proxyButton?.title = model.proxyEnabled ? "Disable Proxy" : "Enable Proxy"
    accountButton?.title = model.accountAlreadyStarted ? "CONTINUE" : "CREATE ACCOUNT"
    accountButton?.isEnabled = model.canCreateAccount
    skipButton?.title = model.restartingWithoutAccount ? "Restarting…" : "Skip, Restart Now"
    skipButton?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    if let field = accountUsernameField {
      field.isEnabled = !model.accountAlreadyStarted && !model.creatingAccount
      if field.currentEditor() == nil && field.stringValue != model.accountUsername {
        field.stringValue = model.accountUsername
      }
    }
    accountPasswordField?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    accountConfirmationField?.isEnabled = !model.creatingAccount && !model.restartingWithoutAccount
    if (model.environmentIssue != nil || model.failure != nil) && !issueShowing {
      presentIssue()
    }
  }

  private var title: String {
    switch model.stage {
    case .welcome: return model.recoveryInterface ? "Repair startup" : "Finish setup"
    case .installing:
      return model.recoveryInterface ? "Restoring startup software" : "Setting up Omarchy"
    case .account:
      return model.accountAlreadyStarted ? "Resume recovery setup" : "Create a recovery account"
    case .complete:
      if model.accountNeedsAuthorization { return "Authorize recovery account" }
      return model.recoveryInterface ? "Recovery complete" : "Installation complete"
    }
  }

  private func buildStage() {
    content.subviews.forEach { $0.removeFromSuperview() }
    footer.arrangedSubviews.forEach { footer.removeArrangedSubview($0); $0.removeFromSuperview() }
    primaryButton = nil
    debugButton = nil
    proxyButton = nil
    progressIndicator = nil
    operationLabel = nil
    accountUsernameField = nil
    accountPasswordField = nil
    accountConfirmationField = nil
    accountButton = nil
    skipButton = nil

    switch model.stage {
    case .welcome: buildWelcome()
    case .installing: buildInstalling()
    case .account: buildAccount()
    case .complete: buildComplete()
    }
    buildFooter()
  }

  private func buildWelcome() {
    let stack = vertical(spacing: 9)
    stack.addArrangedSubview(label(
      model.recoveryInterface
        ? "Restore the startup software for your Mac."
        : "Finish setting up Omarchy on your Mac.", size: 15))
    stack.addArrangedSubview(label(
      model.recoveryInterface
        ? "Use the saved recovery files to repair your\nstartup software."
        : "The final step installs the startup software for your\nselected Omarchy volume.",
      size: 13))
    let icon = NSImageView(image: DestinationVolume.icon)
    icon.imageScaling = .scaleProportionallyUpOrDown
    icon.translatesAutoresizingMaskIntoConstraints = false
    icon.widthAnchor.constraint(equalToConstant: 96).isActive = true
    icon.heightAnchor.constraint(equalToConstant: 96).isActive = true
    stack.setCustomSpacing(30, after: stack.arrangedSubviews[1])
    stack.addArrangedSubview(icon)
    stack.addArrangedSubview(label(DestinationVolume.name, size: 14))
    place(stack)
  }

  private func buildInstalling() {
    let stack = vertical(spacing: 15)
    stack.addArrangedSubview(label(
      (model.recoveryInterface ? "Restoring " : "Setting up ") + DestinationVolume.name,
      size: 14))
    let progress = NSProgressIndicator()
    progress.isIndeterminate = false
    progress.minValue = 0
    progress.maxValue = 100
    progress.controlSize = .regular
    progress.style = .bar
    progress.translatesAutoresizingMaskIntoConstraints = false
    progress.widthAnchor.constraint(equalToConstant: 380).isActive = true
    stack.addArrangedSubview(progress)
    progressIndicator = progress
    let operation = label("", size: 12)
    stack.addArrangedSubview(operation)
    operationLabel = operation
    place(stack)
  }

  private func buildAccount() {
    let stack = vertical(spacing: 11)
    stack.alignment = .leading
    let message = model.accountAlreadyStarted
      ? "Verify your recovery account’s password to resume authorization.\n\nYour Linux login is separate."
      : "Create a separate recovery account for your Linux volume. Next, authorize it as a volume owner to manage Linux boot settings independently of your macOS account.\n\nYour Linux login is separate."
    let explanation = label(message, size: 12, alignment: .left)
    explanation.maximumNumberOfLines = 0
    explanation.preferredMaxLayoutWidth = 380
    explanation.widthAnchor.constraint(equalToConstant: 380).isActive = true
    stack.addArrangedSubview(explanation)

    let username = NSTextField(string: model.accountUsername)
    username.placeholderString = "omarchy-owner"
    username.delegate = self
    username.identifier = NSUserInterfaceItemIdentifier("accountUsername")
    stack.addArrangedSubview(fieldRow("Username", field: username))
    accountUsernameField = username

    let password = NSSecureTextField()
    password.placeholderString = "Password"
    password.delegate = self
    password.identifier = NSUserInterfaceItemIdentifier("accountPassword")
    stack.addArrangedSubview(fieldRow("Password", field: password))
    accountPasswordField = password

    let confirmation = NSSecureTextField()
    confirmation.placeholderString = "Retype password"
    confirmation.delegate = self
    confirmation.identifier = NSUserInterfaceItemIdentifier("accountConfirmation")
    stack.addArrangedSubview(fieldRow("Retype password", field: confirmation))
    accountConfirmationField = confirmation
    place(stack, top: 0)
    DispatchQueue.main.async { [weak self] in
      guard let self else { return }
      self.view.window?.makeFirstResponder(
        self.model.accountAlreadyStarted ? self.accountPasswordField : self.accountUsernameField)
    }
  }

  private func buildComplete() {
    let stack = vertical(spacing: 13)
    stack.addArrangedSubview(label(
      model.accountNeedsAuthorization
        ? DestinationVolume.name + " is ready for authorization."
        : DestinationVolume.name + " is ready to start.", size: 15))
    let icon = NSImageView(image: DestinationVolume.icon)
    icon.imageScaling = .scaleProportionallyUpOrDown
    icon.translatesAutoresizingMaskIntoConstraints = false
    icon.widthAnchor.constraint(equalToConstant: 96).isActive = true
    icon.heightAnchor.constraint(equalToConstant: 96).isActive = true
    stack.addArrangedSubview(icon)
    stack.addArrangedSubview(label(
      "Select “" + DestinationVolume.name + "”, click “Restart…”, then “Authorize Users…”.",
      size: 12))
    stack.addArrangedSubview(label(
      "Enter the credentials for your macOS account and the recovery account you just created.",
      size: 12))
    place(stack)
  }

  private func buildFooter() {
    let leading = NSStackView()
    leading.orientation = .horizontal
    leading.spacing = 12
    leading.alignment = .centerY

    switch model.stage {
    case .welcome:
      let start = RecoveryPrimaryButton(
        title: model.recoveryInterface ? "Restore" : "Continue",
        target: self, action: #selector(startPressed))
      leading.addArrangedSubview(start)
      primaryButton = start
      if model.recoveryInterface {
        let debug = NSButton(title: "", target: self, action: #selector(debugPressed))
        let proxy = NSButton(title: "", target: self, action: #selector(proxyPressed))
        leading.addArrangedSubview(debug)
        leading.addArrangedSubview(proxy)
        debugButton = debug
        proxyButton = proxy
      }
    case .installing: break
    case .account:
      let account = RecoveryPrimaryButton(
        title: "Create Account", target: self, action: #selector(accountPressed))
      leading.addArrangedSubview(account)
      accountButton = account
      let skip = NSButton(title: "Skip, Restart Now", target: self, action: #selector(skipPressed))
      skip.isBordered = false
      skip.font = OmarchyTheme.control
      leading.addArrangedSubview(skip)
      skipButton = skip
    case .complete:
      leading.addArrangedSubview(RecoveryPrimaryButton(
        title: "Restart", target: self, action: #selector(restartPressed)))
    }
    footer.addArrangedSubview(leading)
    let spacer = NSView()
    spacer.translatesAutoresizingMaskIntoConstraints = false
    spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
    footer.addArrangedSubview(spacer)
    if model.stage == .welcome && !model.recoveryInterface {
      let advanced = NSButton(title: "Advanced Options", target: self, action: #selector(advancedPressed))
      advanced.isBordered = false
      advanced.font = OmarchyTheme.control
      footer.addArrangedSubview(advanced)
    }
    if model.stage != .welcome || !log.text.isEmpty {
      let details = NSButton(title: "Show details", target: self, action: #selector(detailsPressed))
      details.isBordered = false
      details.font = OmarchyTheme.control
      footer.addArrangedSubview(details)
    }
  }

  private func fieldRow(_ title: String, field: NSTextField) -> NSStackView {
    let row = vertical(spacing: 4)
    row.alignment = .leading
    row.addArrangedSubview(label(title, size: 12, alignment: .left))
    field.font = OmarchyTheme.body
    field.translatesAutoresizingMaskIntoConstraints = false
    field.widthAnchor.constraint(equalToConstant: 380).isActive = true
    row.addArrangedSubview(field)
    return row
  }

  private func vertical(spacing: CGFloat) -> NSStackView {
    let stack = NSStackView()
    stack.orientation = .vertical
    stack.alignment = .centerX
    stack.spacing = spacing
    stack.translatesAutoresizingMaskIntoConstraints = false
    return stack
  }

  private func label(
    _ text: String, size: CGFloat, alignment: NSTextAlignment = .center
  ) -> NSTextField {
    let field = NSTextField(labelWithString: text)
    field.font = OmarchyTheme.mono(size)
    field.textColor = OmarchyTheme.secondaryText
    field.alignment = alignment
    field.lineBreakMode = .byWordWrapping
    field.maximumNumberOfLines = 0
    return field
  }

  private func place(_ stack: NSStackView, top: CGFloat = 8) {
    content.addSubview(stack)
    NSLayoutConstraint.activate([
      stack.topAnchor.constraint(equalTo: content.topAnchor, constant: top),
      stack.centerXAnchor.constraint(equalTo: content.centerXAnchor),
      stack.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor),
    ])
  }

  func controlTextDidChange(_ notification: Notification) {
    guard let field = notification.object as? NSTextField else { return }
    switch field.identifier?.rawValue {
    case "accountUsername": model.accountUsername = field.stringValue
    case "accountPassword": model.accountPassword = field.stringValue
    case "accountConfirmation": model.accountConfirmation = field.stringValue
    default: break
    }
  }

  @objc private func startPressed() {
    if model.recoveryInterface {
      model.restore()
    } else {
      model.checkEnrollmentEnvironment { [weak self] in self?.showAuthorization(repair: false) }
    }
  }

  @objc private func debugPressed() { model.toggleBootFlag("debug") }
  @objc private func proxyPressed() { model.toggleBootFlag("proxy") }
  @objc private func accountPressed() {
    model.createRecoveryAccount()
    accountPasswordField?.stringValue = ""
    accountConfirmationField?.stringValue = ""
  }
  @objc private func skipPressed() { model.skipRecoveryAccountAndRestart() }
  @objc private func restartPressed() { model.openStartupDisk() }

  private func showAuthorization(repair: Bool) {
    guard let window = view.window else { return }
    authorizationRepair = repair
    let alert = NSAlert()
    alert.messageText = repair ? "Authorize startup disk" : "Authorize installation"
    alert.informativeText = "Enter password for "
      + (RecoveryActions.inRecovery
        ? (model.authorizationOwner?.username ?? "the volume owner") : NSUserName())
      + (repair ? "" : "\n\nUse the installation owner’s password to set up Omarchy.")
    let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 26))
    field.placeholderString = "Password"
    alert.accessoryView = field
    alert.addButton(withTitle: repair ? "Shut Down" : "Install")
    alert.addButton(withTitle: "Cancel")
    alert.beginSheetModal(for: window) { [weak self] response in
      guard let self else { return }
      let credential = field.stringValue
      field.stringValue = ""
      guard response == .alertFirstButtonReturn, !credential.isEmpty else {
        self.model.password.removeAll(keepingCapacity: false)
        return
      }
      if self.authorizationRepair {
        self.model.repairRecoveryBoot(password: credential) { [weak self] in
          self?.showAuthorization(repair: true)
        }
      } else {
        self.model.password = credential
        self.model.startDemo()
      }
    }
    window.makeFirstResponder(field)
  }

  @objc private func advancedPressed() {
    guard let window = view.window else { return }
    let alert = NSAlert()
    alert.messageText = "Advanced Options"
    alert.informativeText =
      "Nested virtualization can reduce normal OS performance by up to 20%."
    let stack = vertical(spacing: 12)
    stack.alignment = .leading
    let macho = NSButton(checkboxWithTitle: "Enable Mach-O Boot Mode",
                         target: self, action: #selector(machoChanged))
    macho.state = model.machoBootMode ? .on : .off
    macho.isEnabled = RecoveryActions.supportsMachO
    stack.addArrangedSubview(macho)
    let nested = NSButton(checkboxWithTitle: "Enable Nested Virtualization",
                          target: self, action: #selector(nestedChanged))
    nested.state = model.nestedVirtualization ? .on : .off
    nested.isEnabled = RecoveryActions.supportsMachO && model.machoBootMode
    stack.addArrangedSubview(nested)
    advancedMachO = macho
    advancedNested = nested
    stack.frame = NSRect(x: 0, y: 0, width: 360, height: 64)
    alert.accessoryView = stack
    alert.addButton(withTitle: "Done")
    alert.beginSheetModal(for: window) { [weak self] _ in
      self?.advancedMachO = nil
      self?.advancedNested = nil
    }
  }

  @objc private func machoChanged() {
    model.machoBootMode = advancedMachO?.state == .on
    advancedNested?.isEnabled = RecoveryActions.supportsMachO && model.machoBootMode
    advancedNested?.state = model.nestedVirtualization ? .on : .off
  }

  @objc private func nestedChanged() {
    model.nestedVirtualization = advancedNested?.state == .on
  }

  @objc private func detailsPressed() {
    guard let window = view.window else { return }
    let alert = NSAlert()
    alert.messageText = "Installation details"
    alert.informativeText =
      "Boot security is reduced only for this Omarchy installation. Your macOS security settings are unchanged."
    let viewer = LogTextView(text: log.text.isEmpty
      ? model.operations.joined(separator: "\n") : log.text)
    viewer.frame = NSRect(x: 0, y: 0, width: 474, height: 200)
    alert.accessoryView = viewer
    alert.addButton(withTitle: "Copy Log")
    alert.addButton(withTitle: "Done")
    alert.beginSheetModal(for: window) { [weak self] response in
      guard response == .alertFirstButtonReturn else { return }
      DispatchQueue.global(qos: .userInitiated).async {
        let result = Result { try self?.log.fullText() ?? "" }
        DispatchQueue.main.async {
          switch result {
          case .success(let text):
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
          case .failure(let error): self?.model.failure = error.localizedDescription
          }
        }
      }
    }
  }

  private func presentIssue() {
    guard let window = view.window else { return }
    issueShowing = true
    let issue = model.environmentIssue
    let alert = NSAlert()
    alert.messageText = issue?.title ?? "Setup could not finish"
    alert.informativeText = issue?.message ?? model.failure ?? ""
    if issue?.canRepairStartupSelection == true {
      alert.addButton(withTitle: "Set Startup Disk and Shut Down")
      alert.addButton(withTitle: "Cancel")
    } else {
      alert.addButton(withTitle: "Show details")
      alert.addButton(withTitle: "OK")
    }
    alert.beginSheetModal(for: window) { [weak self] response in
      guard let self else { return }
      self.model.environmentIssue = nil
      self.model.failure = nil
      self.issueShowing = false
      if response == .alertFirstButtonReturn {
        if issue?.canRepairStartupSelection == true {
          self.model.repairRecoveryBoot { [weak self] in
            self?.showAuthorization(repair: true)
          }
        } else {
          self.detailsPressed()
        }
      }
    }
  }
}
