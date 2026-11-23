import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Window {
    id:root
    objectName:"developerWindow"
    title:qsTranslate("Tingmu", "开发者日志 · 听幕")
    width:Math.min(960,Screen.desktopAvailableWidth-32)
    height:Math.min(680,Screen.desktopAvailableHeight-64)
    minimumWidth:640
    minimumHeight:420
    visible:false
    color:Theme.bg
    property int page:1
    property bool follow:true
    property var visibleLogs:(backend.logs || []).filter(function(entry){
        var level=String(entry.level || "").toUpperCase()
        var matchesLevel=levelFilter.currentIndex===0 || level===levelFilter.currentText || (levelFilter.currentText==="WARNING" && level==="WARN")
        var term=search.text.trim().toLowerCase()
        return matchesLevel && (!term || [entry.event,entry.message,entry.details].join(" ").toLowerCase().indexOf(term)>=0)
    })
    function open(){
        if(!backend.settings.developer_mode)return
        backend.command("refreshDiagnostics",{})
        show();raise();requestActivate()
    }
    function describeDiagnostics(){
        var d=backend.diagnostics || {},media=d.media || {},queue=d.queue || {},models=d.models || {},privacy=d.privacy || {}
        var counts=queue.counts || {},states=[]
        for(var key in counts)states.push(S.status(key)+" "+counts[key])
        return [
            qsTranslate("Tingmu", "运行环境"),
            qsTranslate("Tingmu", "版本      ") + (d.version || "—"),
            qsTranslate("Tingmu", "前端      ") + (d.frontend || "—"),
            "Python    " + (d.python || "—"),
            qsTranslate("Tingmu", "系统      ") + (d.platform || "—"),
            qsTranslate("Tingmu", "日志级别  ") + (d.logLevel || "—"),
            "", qsTranslate("Tingmu", "本地目录"),
            qsTranslate("Tingmu", "数据  ") + (d.dataDir || "—"),
            qsTranslate("Tingmu", "日志  ") + (d.logDir || "—"),
            "", qsTranslate("Tingmu", "媒体与任务"),
            qsTranslate("Tingmu", "媒体      ") + (media.loaded ? qsTranslate("Tingmu", "已加载") : qsTranslate("Tingmu", "未加载")),
            qsTranslate("Tingmu", "字幕      ") + (media.cues || 0) + qsTranslate("Tingmu", " 句"),
            qsTranslate("Tingmu", "播放      ") + S.time(media.position) + " / " + S.time(media.duration),
            qsTranslate("Tingmu", "队列      ") + (queue.running ? qsTranslate("Tingmu", "运行中") : qsTranslate("Tingmu", "未运行")),
            qsTranslate("Tingmu", "任务      ") + (states.join(" · ") || qsTranslate("Tingmu", "无任务")),
            "", qsTranslate("Tingmu", "模型配置"),
            qsTranslate("Tingmu", "字幕识别  ") + (models.asr || "—"),
            qsTranslate("Tingmu", "识别设备  ") + (models.device || "—"),
            qsTranslate("Tingmu", "文本复核  ") + (models.review || "—"),
            qsTranslate("Tingmu", "批量讲解  ") + (models.teaching || "—"),
            qsTranslate("Tingmu", "学习对话  ") + (models.companion || "—"),
            "", qsTranslate("Tingmu", "日志内容策略"),
            qsTranslate("Tingmu", "云端文本分析  ") + (privacy.cloudText ? qsTranslate("Tingmu", "已启用") : qsTranslate("Tingmu", "未启用")),
            qsTranslate("Tingmu", "字幕与对话正文  ") + (privacy.logsIncludeContent ? qsTranslate("Tingmu", "包含") : qsTranslate("Tingmu", "不记录"))
        ].join("\n")
    }
    Connections { target:backend;function onSettingsChanged(){if(!backend.settings.developer_mode)root.hide()} }
    ColumnLayout {
        anchors.fill:parent;anchors.margins:18;spacing:12
        RowLayout {
            Layout.fillWidth:true;spacing:8
            Text { textFormat:Text.PlainText;text:qsTranslate("Tingmu", "开发者模式");color:Theme.text;font.family:Theme.font;font.pixelSize:19;font.weight:Font.DemiBold }
            Text { textFormat:Text.PlainText;text:(backend.diagnostics || {}).logLevel || "";color:Theme.accent;font.family:Theme.mono;font.pixelSize:11 }
            Item { Layout.fillWidth:true }
            TButton { objectName:"refreshDiagnostics";text:qsTranslate("Tingmu", "刷新");onClicked:backend.command("refreshDiagnostics",{}) }
            TButton { objectName:"exportDiagnostics";text:qsTranslate("Tingmu", "导出诊断");onClicked:backend.command("exportDiagnostics",{}) }
            TButton { objectName:"openLogFolder";text:qsTranslate("Tingmu", "日志文件夹");onClicked:backend.command("openLogFolder",{}) }
        }
        RowLayout {
            Layout.fillWidth:true;spacing:4
            TButton { text:qsTranslate("Tingmu", "运行信息");quiet:true;checked:root.page===0;onClicked:root.page=0 }
            TButton { text:qsTranslate("Tingmu", "日志  ") + (backend.logs || []).length;quiet:true;checked:root.page===1;onClicked:root.page=1 }
            Item { Layout.fillWidth:true }
            Text { textFormat:Text.PlainText;text:qsTranslate("Tingmu", "每 2 秒更新");color:Theme.muted;font.family:Theme.font;font.pixelSize:10 }
        }
        Rectangle {
            Layout.fillWidth:true;Layout.fillHeight:true;visible:root.page===0
            color:Theme.panel;radius:7;border.color:Theme.line
            ScrollView {
                anchors.fill:parent;anchors.margins:14
                clip:true;contentWidth:availableWidth
                TextArea {
                    objectName:"developerDiagnostics"
                    text:root.describeDiagnostics()
                    textFormat:TextEdit.PlainText
                    readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap
                    color:Theme.text;font.family:Theme.mono;font.pixelSize:12
                    background:null
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth:true;Layout.fillHeight:true;visible:root.page===1;spacing:10
            RowLayout {
                Layout.fillWidth:true;spacing:8
                Choice { id:levelFilter;implicitWidth:116;translateOptions:true;model:["全部","INFO","WARNING","ERROR","DEBUG"] }
                Field { id:search;Layout.fillWidth:true;placeholderText:qsTranslate("Tingmu", "搜索事件、消息或详情") }
                Toggle { text:qsTranslate("Tingmu", "跟随最新");checked:root.follow;onToggled:{root.follow=checked;if(checked)logList.positionViewAtEnd()} }
            }
            ListView {
                id:logList;objectName:"developerLogList"
                Layout.fillWidth:true;Layout.fillHeight:true
                clip:true;spacing:7;model:root.visibleLogs
                ScrollBar.vertical:ScrollBar{}
                onCountChanged:if(root.follow)Qt.callLater(function(){logList.positionViewAtEnd()})
                delegate:Rectangle {
                    id:entry
                    required property var modelData
                    property bool expanded:false
                    width:logList.width;height:entryContent.implicitHeight+20
                    color:Theme.panel;radius:6;border.color:Theme.line
                    ColumnLayout {
                        id:entryContent
                        anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top;anchors.margins:10;spacing:7
                        RowLayout {
                            Layout.fillWidth:true;spacing:9
                            Text { textFormat:Text.PlainText;text:entry.modelData.time || "";color:Theme.muted;font.family:Theme.mono;font.pixelSize:10 }
                            Text { textFormat:Text.PlainText;text:entry.modelData.level || "";color:text==="ERROR" || text==="CRITICAL" ? Theme.error : text==="WARNING" || text==="WARN" ? Theme.warning : Theme.accent;font.family:Theme.mono;font.pixelSize:10 }
                            Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:entry.modelData.event || "";color:Theme.muted;font.family:Theme.mono;font.pixelSize:10;elide:Text.ElideRight }
                            TButton { visible:!!entry.modelData.details;text:entry.expanded ? qsTranslate("Tingmu", "收起") : qsTranslate("Tingmu", "详情");quiet:true;implicitHeight:24;onClicked:entry.expanded=!entry.expanded }
                        }
                        TextEdit { Layout.fillWidth:true;text:qsTranslate("TingmuStatus", entry.modelData.message || "");textFormat:TextEdit.PlainText;readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;color:Theme.text;font.family:Theme.mono;font.pixelSize:12 }
                        TextEdit { Layout.fillWidth:true;visible:entry.expanded;text:entry.modelData.details || "";textFormat:TextEdit.PlainText;readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;color:Theme.muted;font.family:Theme.mono;font.pixelSize:11 }
                    }
                }
                Text { textFormat:Text.PlainText;anchors.centerIn:parent;visible:logList.count===0;text:search.text || levelFilter.currentIndex>0 ? qsTranslate("Tingmu", "没有符合条件的日志") : qsTranslate("Tingmu", "暂无日志");color:Theme.muted;font.family:Theme.font;font.pixelSize:12 }
            }
        }
        Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:qsTranslate("Tingmu", "运行日志保存在本机。导出诊断包含环境、任务状态与日志；不包含字幕或对话正文。");color:Theme.muted;font.family:Theme.font;font.pixelSize:10;wrapMode:Text.Wrap }
    }
}
