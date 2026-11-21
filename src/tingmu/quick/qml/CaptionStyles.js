.pragma library

var base = {
    family: "Source Han Sans SC", size: 24, bold: false, italic: false,
    fill: "#ffffff", edge: "#101318", outline: 2, shadow: 2,
    box: false, boxColor: "#14191c", boxOpacity: 70, align: 2,
    horizontalMargin: 4, verticalMargin: 5
}
var presets = [
    {name:"影院白字", values:base},
    {name:"高对比底框", values:Object.assign({}, base, {bold:true, box:true, outline:0, shadow:0, boxOpacity:85})},
    {name:"暖黄对白", values:Object.assign({}, base, {fill:"#ffe38e", outline:2, shadow:1})}
]
