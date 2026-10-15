import Foundation

/// Deferring the separate account never marks its ownership as completed.
/// Keep the normal Recovery resume path available after the restart.
enum RecoveryAccountRestart {
  static func perform(
    receipt: [String: Any], hasPendingInstallation: Bool,
    persist: ([String: Any]) throws -> Void, restart: () throws -> Void
  ) throws {
    guard receipt["status"] as? String == "installed", !hasPendingInstallation else {
      throw RecoveryFailure(message: "Finish installing the startup software before restarting.")
    }
    var deferred = receipt
    deferred["recoveryAccountDeferred"] = true
    try persist(deferred)
    try restart()
  }
}
