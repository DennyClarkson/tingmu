import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    spacing: 6
    Toggle {
        objectName: "llmHighConcurrency"
        text: "高并发请求 LLM"
        checked: backend.settings.llm_high_concurrency
        onToggled: backend.setting("llm_high_concurrency", checked)
    }
    RowLayout {
        Layout.fillWidth: true
        enabled: backend.settings.llm_high_concurrency
        Text {
            Layout.fillWidth: true
            text: "同时请求上限"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
        }
        Choice {
            objectName: "llmConcurrencyCount"
            Layout.preferredWidth: 88
            Accessible.name: "同时请求上限"
            model: Array.from({length: backend.llmConcurrencyLimit}, function(_, i) { return String(i + 1) })
            currentIndex: backend.settings.llm_max_concurrency - 1
            onActivated: function(index) { backend.setting("llm_max_concurrency", index + 1) }
        }
    }
    Text {
        Layout.fillWidth: true
        text: "作用于原文复核、逐句讲解和译稿核验。关闭时逐个发送请求。下个视频开始或当前视频暂停后继续时生效。"
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
        wrapMode: Text.Wrap
    }
}
