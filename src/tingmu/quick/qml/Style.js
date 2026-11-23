.pragma library
function time(ms) {
    var n = Math.max(0, Math.floor((ms || 0) / 1000))
    var h = Math.floor(n / 3600), m = Math.floor(n / 60) % 60, s = n % 60
    return (h ? h + ":" : "") + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s
}
var statuses = {
    cleared: "已清除", queued: "等待", running: "处理中", paused: "已暂停", failed: "失败",
    completed: "已完成", ready: "已准备", subtitleOnly: "仅有原文字幕", lessonDraft: "译稿待复核",
    new: "未准备", unprepared: "未准备", partial: "已识别", missing: "文件缺失"
}
function status(s) {
    return qsTranslate("Tingmu", statuses[s] || s || statuses.unprepared)
}
function bytes(value) {
    var units = ["B", "KiB", "MiB", "GiB", "TiB"]
    var amount = Number(value || 0), i = 0
    while (amount >= 1024 && i < units.length - 1) { amount /= 1024; ++i }
    return amount.toFixed(i === 0 ? 0 : 1) + " " + units[i]
}
