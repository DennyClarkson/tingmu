import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
ScrollView {
    id: root
    property bool displayImported: true
    readonly property bool shown: visible && !!Window.window && Window.window.visible
    readonly property string track: !shown ? "" : backend.activeCue.id ? "learning"
        : displayImported && backend.sources.importedCue.id ? "imported" : "embedded"
    property var cue: track === "learning" ? backend.activeCue
        : track === "imported" ? backend.sources.importedCue : track === "embedded" ? backend.playbackDetails.embeddedCue || ({}) : ({})
    readonly property string sourceId: cue.sourceIds && cue.sourceIds.length ? cue.sourceIds[0] : cue.id || ""
    readonly property string sentence: cue.sourceText || cue.text || ""
    readonly property bool sentenceSaved: !!sourceId && backend.entriesRevision > 0
        && backend.sentenceCollected(sourceId, sentence)
    property string collectionMessage: ""
    property bool collectionSuccess: true
    property bool tokenFeedback:false
    readonly property string wordContext: shown ? JSON.stringify([backend.applicationState.dataDir, backend.mediaState.mediaPath,
        backend.settings.source_language, backend.settings.target_language, track, cue.id, cue.text]) : ""
    property string armedWord: ""
    readonly property var wordStates: {
        if (track !== "learning" || !interactive || !cue.id || backend.entriesRevision === 0) return ({})
        const words = (cue.tokens || []).map(token => token.text)
        const selected = sourceText.selectedText
        if (selected && selected.length <= 200 && words.indexOf(selected) < 0) words.push(selected)
        return words.length ? backend.wordCollectionStates(cue.id, words) : ({})
    }
    function captureWord(text, index) {
        return {context: wordContext, cueId: cue.id, cueText: cue.text, text: text, tokenIndex: index,
            sourceLanguage: backend.settings.source_language, targetLanguage: backend.settings.target_language}
    }
    function isArmed(text, index) {
        return !!armedWord && armedWord === JSON.stringify(captureWord(text, index))
    }
    function collectWord(captured, current) {
        const key = JSON.stringify(current)
        if (captured && JSON.stringify(captured) !== key) { clearConfirmation(); return }
        current.confirm = armedWord === key
        clearConfirmation()
        tokenFeedback = current.tokenIndex >= 0
        if (backend.mediaState.mediaPath) current.mediaPath = backend.mediaState.mediaPath
        const outcome = backend.collectWord(current)
        if (outcome === "confirm") {
            armedWord = key
            feedbackTimer.restart()
        }
    }
    function clearConfirmation() {
        if (armedWord) { armedWord = ""; collectionMessage = "" }
    }
    function stateLabel(status, armed) {
        return armed ? qsTranslate("Tingmu", "再次点击确认") : status === "pending" ? qsTranslate("Tingmu", "已收藏") : status === "organized" ? qsTranslate("Tingmu", "已整理") : ""
    }
    function stateColor(status) {
        return status === "organized" ? Theme.organizedWord : Theme.accent
    }
    onWordContextChanged: { clearConfirmation(); collectionMessage = "" }
    onVisibleChanged: if (!visible) clearConfirmation()
    onInteractiveChanged: if (!interactive) clearConfirmation()
    Connections {
        target: root.Window.window
        function onActiveChanged() { if (!root.Window.window.active) root.clearConfirmation() }
    }
    Connections {
        target: backend
        function onEntriesChanged() { root.clearConfirmation() }
        function onCollectionFeedback(success, message) {
            root.collectionSuccess = success
            root.collectionMessage = message
            feedbackTimer.restart()
        }
    }
    Timer { id: feedbackTimer; interval: root.armedWord ? 8000 : 3200
        onTriggered: { root.clearConfirmation(); root.collectionMessage = "" } }
    readonly property string currentId: cue.id || ""
    onCurrentIdChanged: if(contentItem)contentItem.contentY=0
    property int textSize: 23
    property bool showReading: true
    property bool showTranslation: true
    property bool showExplanation: true
    property bool compact: false
    property bool interactive:!compact || !backend.playbackDetails.playing
    readonly property bool mediaOpened:!!backend.mediaState.mediaPath
    readonly property bool preparing:mediaOpened && (backend.queueState.runningMediaPaths || []).indexOf(backend.mediaState.mediaPath)>=0
    readonly property string emptyTitle:!shown || cue.text ? "" : !mediaOpened ? qsTranslate("Tingmu", "开始观看") : preparing ? qsTranslate("Tingmu", "正在准备学习材料") : backend.cueCount===0 ? qsTranslate("Tingmu", "还没有学习字幕") : qsTranslate("Tingmu", "当前是字幕空档")
    readonly property string emptyMessage:!shown || cue.text ? "" : !mediaOpened ? qsTranslate("Tingmu", "打开一个视频，开始观看与学习。") : preparing ? qsTranslate("Tingmu", "正在处理这个视频，完成后会显示逐句内容。")
        : backend.playbackDetails.embeddedImage ? qsTranslate("Tingmu", "当前是图像字幕。切换到文字字幕、导入字幕文件或准备学习字幕后，即可收藏整句。")
        : backend.cueCount===0 ? qsTranslate("Tingmu", "视频已打开。可以准备当前视频，也可以从字幕建立学习文档。") : qsTranslate("Tingmu", "这里没有当前字幕，播放到下一句时会自动显示。")
    signal learningRequested()
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: root.availableWidth
        spacing: root.compact ? 8 : 10
        Item { Layout.preferredHeight: 1 }
        RowLayout {
            Layout.fillWidth:true
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; elide:Text.ElideRight
                text: root.cue.text ? S.time(root.cue.start*1000) + "  /  " + ((root.track === "embedded" ? qsTranslate("TingmuStatus", root.cue.trackName || "") : root.cue.trackName) || root.cue.speaker || qsTranslate("Tingmu", "当前句")) : root.emptyTitle
                color:Theme.accent; font.family:Theme.font; font.pixelSize:11 }
            TButton { objectName:"correctCompanionSubtitle"; text:qsTranslate("Tingmu", "纠正此句"); quiet:true; implicitHeight:28
                visible:root.track==="learning"; onClicked:backend.requestDialog("correction",root,{cueId:root.cue.id}) }
        }
        TextEdit {
            id:sourceText;objectName:"selectableCueText"
            onSelectedTextChanged:root.clearConfirmation()
            Accessible.name:qsTranslate("Tingmu", "当前字幕原文")
            Accessible.description:qsTranslate("Tingmu", "用鼠标选择原文，再点击收藏选中文字；也可直接收藏整句。")
            Layout.fillWidth:true
            text:root.cue.text || root.emptyMessage
            textFormat:TextEdit.PlainText;readOnly:true;selectByMouse:root.interactive;persistentSelection:true
            color:Theme.text;font.family:Theme.font;font.pixelSize:root.textSize
            wrapMode:TextEdit.Wrap;selectionColor:Theme.selection;selectedTextColor:Theme.text
        }
        Flow {
            Layout.fillWidth:true;spacing:6;visible:!!root.cue.id
            TButton {
                objectName:"saveSelectionButton"; focusPolicy:Qt.TabFocus
                property var pressedWord: null
                readonly property string collectionState: root.wordStates[sourceText.selectedText] || "unsaved"
                readonly property string statusLabel: root.stateLabel(collectionState, root.isArmed(sourceText.selectedText, -1))
                text:statusLabel || qsTranslate("Tingmu", "收藏选中文字"); quiet:true; implicitHeight:28
                tip:qsTranslate("Tingmu", "收藏所选原文"); Accessible.name:qsTranslate("Tingmu", "收藏选中文字 ") + statusLabel
                visible:root.interactive && root.track==="learning"; enabled:sourceText.selectedText.length>0
                labelColor: statusLabel ? root.stateColor(collectionState) : Theme.text
                onPressed:pressedWord=root.captureWord(sourceText.selectedText, -1)
                onCanceled:pressedWord=null
                onClicked:{root.collectWord(pressedWord, root.captureWord(sourceText.selectedText, -1));pressedWord=null}
            }
            TButton {
                objectName:"saveSentenceButton";text:root.sentenceSaved ? qsTranslate("Tingmu", "已收藏整句") : qsTranslate("Tingmu", "收藏整句")
                checked:root.sentenceSaved;quiet:true;implicitHeight:28
                property var pressedCue: null
                function capture(){return {mediaPath:backend.mediaState.mediaPath,cueId:root.cue.id,text:root.sentence,track:root.track}}
                onPressed:{root.clearConfirmation();root.tokenFeedback=false;pressedCue=capture()}
                onCanceled:pressedCue=null
                onClicked:{backend.command("saveSentence",pressedCue || capture());pressedCue=null}
            }
            TButton{objectName:"openLearningButton";text:qsTranslate("Tingmu", "进入学习");quiet:true;implicitHeight:28;onClicked:root.learningRequested()}
        }
        ColumnLayout {
            id:sentenceFeedbackSlot;Layout.fillWidth:true;visible:!!root.collectionMessage && !root.tokenFeedback
        }
        LessonPreparation { showWhenReady:false }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 4
            visible: root.showTranslation && !!root.cue.translation
            Text { text: qsTranslate("TingmuLanguage", backend.lessonModel.language || "") + (root.cue.translationProvided ? qsTranslate("Tingmu", "译文 · 来自字幕") : root.cue.translationVerified ? qsTranslate("Tingmu", "译文 · 已复核") : qsTranslate("Tingmu", "译文 · 待复核")); color: Theme.muted; font.family: Theme.font; font.pixelSize: 11 }
            Text { objectName:"companionTranslation"; textFormat:Text.PlainText; Layout.fillWidth: true; text: root.cue.translation || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: root.compact ? Math.max(12,root.textSize-5) : 14; wrapMode: Text.Wrap }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 4
            visible: root.showExplanation && !!root.cue.explanation
            Text { text:qsTranslate("Tingmu", "逐句讲解"); color: Theme.accent; font.family: Theme.font; font.pixelSize: 11 }
            Text { objectName:"companionExplanation"; textFormat:Text.PlainText; Layout.fillWidth: true; text: root.cue.explanation || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: root.compact ? Math.max(12,root.textSize-6) : 13; wrapMode: Text.Wrap; lineHeight:1.15 }
        }
        Text { textFormat:Text.PlainText;
            Layout.fillWidth: true
            visible: root.showReading && !!root.cue.reading
            text: root.cue.reading || ""
            color: Theme.accent; font.family: Theme.font; font.pixelSize: Math.max(12,root.textSize-7); wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true; spacing: 6
            visible: root.interactive && !!root.cue.tokens && root.cue.tokens.length > 0
            Repeater {
                model: root.interactive ? root.cue.tokens || [] : []
                delegate: Button {
                    id: tokenCard
                    required property var modelData
                    required property int index
                    objectName:"wordCard" + index
                    property var pressedWord: null
                    readonly property string collectionState: root.wordStates[modelData.text] || "unsaved"
                    readonly property string statusLabel: root.stateLabel(collectionState, root.isArmed(modelData.text, index))
                    readonly property color ink: root.stateColor(collectionState)
                    width: Math.min(root.availableWidth,Math.max(48,wordMeasure.advanceWidth+18,statusMeasure.advanceWidth+18,root.showReading ? readingMeasure.advanceWidth+18 : 0))
                    height: tokenColumn.implicitHeight + 14; padding:7; hoverEnabled:true
                    Accessible.name:modelData.text + (statusLabel ? "，" + statusLabel : qsTranslate("Tingmu", "，收藏词语"))
                    Accessible.description:tokenCard.ToolTip.text
                    background:Rectangle {
                        radius:5
                        color:tokenCard.collectionState === "unsaved" ? (tokenCard.hovered ? Theme.hover : Theme.raised)
                            : Qt.tint(Theme.panel, Qt.rgba(tokenCard.ink.r, tokenCard.ink.g, tokenCard.ink.b, tokenCard.hovered ? 0.18 : 0.13))
                        border.width:tokenCard.visualFocus ? 2 : tokenCard.collectionState === "unsaved" ? 0 : 1
                        border.color:tokenCard.ink
                    }
                    TextMetrics{id:wordMeasure;text:tokenCard.modelData.text || "";font.family:Theme.font;font.pixelSize:16}
                    TextMetrics{id:readingMeasure;text:tokenCard.modelData.reading || "";font.family:Theme.font;font.pixelSize:10}
                    TextMetrics{id:statusMeasure;text:tokenCard.collectionState === "organized" ? qsTranslate("Tingmu", "再次点击确认") : tokenCard.statusLabel;font.family:Theme.font;font.pixelSize:10}
                    contentItem:Column {
                        id: tokenColumn; spacing:3
                        Text { textFormat:Text.PlainText; width:parent.width;horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WrapAnywhere; text: tokenCard.modelData.text || ""; color: tokenCard.collectionState === "unsaved" ? Theme.text : tokenCard.ink; font.family: Theme.font; font.pixelSize: 16 }
                        Text { textFormat:Text.PlainText; visible: root.showReading && text.length>0; width:parent.width;horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WrapAnywhere; text: tokenCard.modelData.reading || ""; color: Theme.accent; font.family: Theme.font; font.pixelSize: 10 }
                        Text { text:tokenCard.statusLabel; visible:!!text; width:parent.width;horizontalAlignment:Text.AlignHCenter;color:tokenCard.ink;font.family:Theme.font;font.pixelSize:10 }
                    }
                    HoverHandler { cursorShape:Qt.PointingHandCursor }
                    onPressed:pressedWord=root.captureWord(modelData.text, index)
                    onCanceled:pressedWord=null
                    onClicked:{root.collectWord(pressedWord, root.captureWord(modelData.text, index));pressedWord=null}
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: (modelData.meaning ? modelData.meaning + "\n" : "")
                        + (collectionState === "organized" ? qsTranslate("Tingmu", "已整理，再次收藏可关联本句出处")
                            : collectionState === "pending" ? qsTranslate("Tingmu", "已收藏，待整理") : qsTranslate("Tingmu", "点击收藏词语"))
                }
            }
        }
        ColumnLayout {
            id:wordFeedbackSlot;Layout.fillWidth:true;visible:!!root.collectionMessage && root.tokenFeedback
            Text {
                objectName:"sentenceCollectionFeedback"
                parent:root.tokenFeedback ? wordFeedbackSlot : sentenceFeedbackSlot
                Layout.fillWidth:true;text:qsTranslate("TingmuStatus", root.collectionMessage || "");textFormat:Text.PlainText;wrapMode:Text.Wrap
                color:root.collectionSuccess ? Theme.accent : Theme.error;font.family:Theme.font;font.pixelSize:12
            }
        }
        Text { textFormat:Text.PlainText; visible: !!root.cue.uncertain && !root.compact; Layout.fillWidth: true; text: qsTranslate("Tingmu", "文本复核仍有疑点，可对照原声确认。"); color: Theme.muted; font.family: Theme.font; font.pixelSize: 10; wrapMode: Text.Wrap }
        Item { Layout.preferredHeight: 8 }
    }
}
