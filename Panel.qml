import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "jordan.steam-frame"
  ipcTarget: "jordan.steam-frame"
  manageIpc: false

  readonly property string refreshHelper: Qt.resolvedUrl("bin/refresh.py").toString().replace(/^file:\/\//, "")
  readonly property string windowHelper: Qt.resolvedUrl("bin/window.py").toString().replace(/^file:\/\//, "")
  readonly property color fg: bar ? bar.barForeground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // The bar sizes the slot from this item. The button is anchored to fill it,
  // so without these the slot height stays 0 and the icon never appears.
  implicitWidth: Math.max(button.implicitWidth, Style.bar.iconSlot)
  implicitHeight: Math.max(button.implicitHeight, Style.bar.iconSlot)

  // SteamVR's own Frame visor. The VR badge is only there when that file is absent.
  readonly property string frameIconPath: Quickshell.env("HOME") + "/.local/share/Steam/steamapps/common/SteamVR/drivers/vrlink/resources/icons/headset_steamframe_ready_2x.png"
  readonly property bool frameIconReady: frameProbe.status === Image.Ready

  Image {
    id: frameProbe
    visible: false
    asynchronous: true
    source: root.frameIconPath
    sourceSize.width: 80
    sourceSize.height: 64
  }

  Component {
    id: frameMark
    Item {
      Image {
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        source: root.frameIconPath
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
      }
    }
  }

  property string verdict: "Open to read the Frame."
  property string linkText: ""
  property string chargeText: ""
  property string steamvrText: ""
  property string audioText: ""
  property string findText: ""
  property string bridgeText: ""
  property string extraText: ""
  property string errorText: ""
  property string windowState: "absent"
  property bool busy: false
  property string refreshBuf: ""

  function refresh() {
    if (refreshProc.running) return
    root.busy = true
    root.refreshBuf = ""
    refreshProc.running = true
    if (!windowProc.running) windowProc.running = true
  }

  function toggleWindow() {
    if (toggleProc.running) return
    toggleProc.running = true
  }

  function applyReport(line) {
    var report
    try {
      report = JSON.parse(line)
    } catch (e) {
      root.errorText = "Frame status did not parse."
      return
    }
    root.verdict = String(report.verdict || "No verdict.")
    root.linkText = report.link === "picture" ? "picture" : (report.link === "down" ? "radio down" : "radio")
    root.chargeText = String(report.charge || "unknown")
    root.steamvrText = String(report.steamvr || "unknown")
    root.audioText = String(report.audio || "unknown")
    root.findText = String(report.find || "")
    root.bridgeText = String(report.bridge || "")
    root.extraText = report.extra instanceof Array ? report.extra.join("\n") : ""
    root.errorText = String(report.error || "")
  }

  function applyWindow(line) {
    var state = String(line || "").trim()
    if (state === "shown" || state === "hidden" || state === "absent")
      root.windowState = state
  }

  Process {
    id: refreshProc
    command: ["/usr/bin/python3", "-I", "-S", root.refreshHelper, "refresh"]
    stdout: SplitParser {
      onRead: function (line) {
        root.refreshBuf += line
        if (root.refreshBuf.length > 20000) {
          refreshProc.signal(15)
          root.refreshBuf = ""
          root.errorText = "Frame status was too long."
        }
      }
    }
    onExited: function () {
      root.busy = false
      if (root.refreshBuf === "") root.errorText = "Frame status did not return."
      else root.applyReport(root.refreshBuf)
      root.refreshBuf = ""
    }
  }

  Process {
    id: windowProc
    command: ["/usr/bin/python3", "-I", "-S", root.windowHelper, "state"]
    stdout: SplitParser {
      onRead: function (line) { root.applyWindow(line) }
    }
  }

  Process {
    id: toggleProc
    command: ["/usr/bin/python3", "-I", "-S", root.windowHelper, "toggle"]
    stdout: SplitParser {
      onRead: function (line) { root.applyWindow(line) }
    }
  }

  Timer {
    id: refreshWatch
    interval: 35000
    repeat: false
    running: refreshProc.running
    onTriggered: if (refreshProc.running) refreshProc.signal(15)
  }

  Component.onDestruction: {
    if (refreshProc.running) refreshProc.signal(15)
    if (windowProc.running) windowProc.signal(15)
    if (toggleProc.running) toggleProc.signal(15)
  }

  onOpenedChanged: if (opened) root.refresh()

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.frameIconReady ? "" : "󰢔"
    iconComponent: root.frameIconReady ? frameMark : null
    tooltipText: "Steam Frame"
    onPressed: function () { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(body.implicitHeight)

    Column {
      id: body
      width: parent.width
      spacing: Style.space(8)

      Text {
        text: "Steam Frame"
        textFormat: Text.PlainText
        color: fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
      }

      Text {
        width: parent.width
        text: root.busy ? "Reading status, find, and bridge." : root.verdict
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.linkText !== ""
        text: "Link: " + root.linkText
        textFormat: Text.PlainText
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.chargeText !== ""
        text: "Charge: " + root.chargeText
        textFormat: Text.PlainText
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.steamvrText !== ""
        text: "SteamVR: " + root.steamvrText
        textFormat: Text.PlainText
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.audioText !== ""
        text: "Audio: " + root.audioText
        textFormat: Text.PlainText
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.findText !== ""
        text: "Controllers: " + root.findText
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Text {
        width: parent.width
        visible: root.bridgeText !== ""
        text: "Bridge: " + root.bridgeText
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Text {
        width: parent.width
        visible: root.extraText !== ""
        text: root.extraText
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Text {
        width: parent.width
        visible: root.errorText !== ""
        text: root.errorText
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Toggle {
        width: parent.width
        label: "Frame window"
        description: "Show or hide the SteamVR status window."
        checked: root.windowState === "shown"
        onClicked: root.toggleWindow()
      }

      MouseArea {
        width: parent.width
        height: refreshLabel.implicitHeight + Style.space(8)
        onClicked: root.refresh()

        Text {
          id: refreshLabel
          text: "Refresh"
          textFormat: Text.PlainText
          color: fg
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
      }
    }
  }
}
