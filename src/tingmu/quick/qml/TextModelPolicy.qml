import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: policy
    spacing: 5
    function selectedIndex() {
        return !backend.settings.text_models_enabled ? 0 : backend.settings.cloud_text_enabled ? 2 : 1
    }
    function choosePolicy() { choice.forceActiveFocus(); choice.popup.open() }
    function requestRemote() { consent.active = true; consent.item.review() }
    Loader {
        id: consent
        active: false
        sourceComponent: RemoteTextPermissionDialog { origin: policy }
    }
    Text { text: qsTranslate("Tingmu", "当前工作区的使用权限"); color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
    Choice {
        id: choice
        objectName: "textModelPolicy"
        Layout.fillWidth: true
        translateOptions:true;model:["停用文本模型", "仅本机 API", "允许远程文本 API"]
        currentIndex: policy.selectedIndex()
        onActivated: function(index) {
            if (index === 2 && policy.selectedIndex() !== 2) policy.requestRemote()
            else if (index !== policy.selectedIndex()) backend.command("textModelPolicy", {mode: ["off", "local", "remote"][index]})
            currentIndex = Qt.binding(policy.selectedIndex)
        }
    }
    Text {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: !backend.settings.text_models_enabled ? qsTranslate("Tingmu", "使用本地字幕与词典，暂停模型复核、讲解和对话。") :
            backend.settings.cloud_text_enabled ? qsTranslate("Tingmu", "字幕和学习文本发送至分配给各任务的 API。") :
            qsTranslate("Tingmu", "仅连接本机回环地址。请选择本地运行的模型，服务端的转发由该服务配置。")
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
    }
}
