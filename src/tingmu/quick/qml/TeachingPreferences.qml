import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
AppDialog {
    id:root
    objectName:"teachingPreferencesDialog"
    title:qsTranslate("Tingmu", "我的讲解偏好")
    parent:Overlay.overlay
    anchors.centerIn:parent
    width:Math.min(540,parent ? parent.width-32 : 540)
    height:Math.min(420,parent ? parent.height-32 : 420)
    modal:true
    onOpened:preferences.text=backend.settings.teaching_preferences || ""
    contentItem:ColumnLayout{
        spacing:12
        Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "告诉听幕你的目标、母语背景，以及希望重点解释什么。你也可以在对话里请伴侣修改这些偏好。");color:Theme.muted;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap}
        AppTextArea{id:preferences;Layout.fillWidth:true;Layout.fillHeight:true;objectName:"teachingPreferencesInput";placeholderText:qsTranslate("Tingmu", "例如：用普通话对比粤语，重点讲语气词和常见搭配；每个难点给一句日常例子。")}
        Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "保存后用于后续讲解和学习对话；不会自动重新处理已有材料。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
        Text{Layout.fillWidth:true;visible:!!backend.learningState.error;text:qsTranslate("TingmuStatus",backend.learningState.error || "");color:Theme.error;wrapMode:Text.Wrap;font.family:Theme.font}
        RowLayout{Layout.alignment:Qt.AlignRight
            TButton{text:qsTranslate("Tingmu", "取消");quiet:true;onClicked:root.close()}
            TButton{objectName:"saveTeachingPreferences";text:qsTranslate("Tingmu", "保存偏好");primary:true;onClicked:{backend.setting("teaching_preferences",preferences.text.trim());if(!backend.learningState.error)root.close()}}
        }
    }
}
