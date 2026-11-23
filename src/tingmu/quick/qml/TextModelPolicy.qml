import QtQuick
import QtQuick.Layouts

ColumnLayout {
    spacing: 5
    Text { text: qsTranslate("Tingmu", "当前工作区的使用权限"); color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
    Choice {
        objectName: "textModelPolicy"
        Layout.fillWidth: true
        translateOptions:true;model:["停用文本模型", "仅本机 API", "允许远程文本 API"]
        currentIndex: !backend.settings.text_models_enabled ? 0 : backend.settings.cloud_text_enabled ? 2 : 1
        onActivated: function(index) { backend.command("textModelPolicy", {mode: ["off", "local", "remote"][index]}) }
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
