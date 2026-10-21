import SwiftUI

/// The wordmark, the page and its actions in one column that scrolls as a
/// whole. The actions follow the content they act on, so a checkbox that
/// gates a button always sits right above it, and a fade at the bottom edge
/// shows when more of the page is below.
struct InstallerPageLayout<Header: View, Content: View, Actions: View>: View {
  @ViewBuilder let header: () -> Header
  @ViewBuilder let content: () -> Content
  @ViewBuilder let actions: () -> Actions
  @State private var moreBelow = false

  var body: some View {
    GeometryReader { geometry in
      // The brand gives up room on short windows; the page needs it more.
      let topInset = min(96.0, max(32.0, geometry.size.height * 0.07))
      let wordmarkWidth = min(400.0, max(240.0, geometry.size.height * 0.5))
      let wordmarkHeight =
        wordmarkWidth * CGFloat(OmarchyWordmark.rows) / CGFloat(OmarchyWordmark.columns)

      ScrollView {
        VStack(spacing: 0) {
          OmarchyWordmark()
            .frame(width: wordmarkWidth, height: wordmarkHeight)
            .padding(.top, topInset)
            .padding(.bottom, 20)
          header()
            .frame(minHeight: 24)
            .padding(.bottom, 18)
          content()
            .frame(maxWidth: 560)
            .padding(.bottom, 24)
          actions()
            .frame(maxWidth: .infinity, minHeight: OmarchyTheme.buttonHeight)
            .padding(.bottom, 32)
        }
        .padding(.horizontal, 48)
        .frame(maxWidth: .infinity, alignment: .top)
      }
      .onScrollGeometryChange(for: Bool.self) { scroll in
        scroll.contentSize.height - scroll.contentOffset.y - scroll.containerSize.height > 1
      } action: { _, below in
        moreBelow = below
      }
      .overlay(alignment: .bottom) {
        LinearGradient(
          colors: [OmarchyTheme.window.opacity(0), OmarchyTheme.window],
          startPoint: .top, endPoint: .bottom
        )
        .frame(height: 40)
        .opacity(moreBelow ? 1 : 0)
        .animation(.easeOut(duration: 0.2), value: moreBelow)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
      }
    }
  }
}
