.pragma library
function time(ms) {
    var n = Math.max(0, Math.floor((ms || 0) / 1000))
    var h = Math.floor(n / 3600), m = Math.floor(n / 60) % 60, s = n % 60
    return (h ? h + ":" : "") + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s
}
function status(s) {
    return ({cleared:"已清除",queued:"等待",running:"处理中",paused:"已暂停",failed:"失败",completed:"已完成",ready:"已准备",new:"未准备",unprepared:"未准备",partial:"已识别",missing:"文件缺失"})[s] || s || "未准备"
}
