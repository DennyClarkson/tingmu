.pragma library

var icons = {
    "arrow-down-left": "<path d=\"m17 7-10 10M17 17H7V7\"/>",
    "arrow-up-right": "<path d=\"M7 17 17 7M7 7h10v10\"/>",
    "close": "<path d=\"m6 6 12 12M18 6 6 18\"/>",
    "folder": "<path d=\"M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z\"/>",
    "maximize": "<path d=\"M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5\"/>",
    "minimize": "<path d=\"M3 8h5V3M16 3v5h5M21 16h-5v5M8 21v-5H3\"/>",
    "pause": "<path d=\"M8 4v16M16 4v16\"/>",
    "play": "<path d=\"m8 4 12 8-12 8Z\"/>",
    "plus": "<path d=\"M12 5v14M5 12h14\"/>",
    "repeat": "<path d=\"m17 2 4 4-4 4M3 11V8a2 2 0 0 1 2-2h16M7 22l-4-4 4-4M21 13v3a2 2 0 0 1-2 2H3\"/>",
    "send": "<path d=\"m22 2-7 20-4-9-9-4ZM22 2 11 13\"/>",
    "settings": "<path d=\"m9.5 4 .6-2h3.8l.6 2 2.1 1.2 2-.5 1.9 3.3-1.4 1.6v2.4l1.4 1.6-1.9 3.3-2-.5-2.1 1.2-.6 2h-3.8l-.6-2-2.1-1.2-2 .5-1.9-3.3L5 12v-2.4L3.6 8l1.9-3.3 2 .5Z\"/><circle cx=\"12\" cy=\"10.8\" r=\"3\"/>",
    "skip-back": "<path d=\"M5 4v16M19 4 7 12l12 8Z\"/>",
    "skip-forward": "<path d=\"M19 4v16M5 4l12 8-12 8Z\"/>",
    "volume-off": "<path d=\"m11 5-6 4H2v6h3l6 4ZM17 9l5 6M22 9l-5 6\"/>",
    "volume": "<path d=\"m11 5-6 4H2v6h3l6 4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14\"/>",
    "check": "<path d=\"m4 12 5 5L20 6\"/>",
    "chevron-right": "<path d=\"m9 5 7 7-7 7\"/>",
    "search": "<circle cx=\"10\" cy=\"10\" r=\"6.5\"/><path d=\"m15 15 6 6\"/>",
    "copy": "<rect x=\"8\" y=\"8\" width=\"12\" height=\"13\" rx=\"2\"/><path d=\"M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3\"/>",
    "trash": "<path d=\"M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7\"/>",
    "grip": "<path d=\"M9 5h.1M15 5h.1M9 12h.1M15 12h.1M9 19h.1M15 19h.1\" stroke-width=\"3.5\"/>",
    "resize": "<path d=\"m7 18 11-11m-5 11 5-5\"/>"
}

var sources = Object.create(null)

function source(name, color) {
    if (!icons[name]) return ""
    var key = name + "|" + String(color)
    if (sources[key]) return sources[key]
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="'
              + String(color) + '" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
              + icons[name] + '</svg>'
    sources[key] = "data:image/svg+xml," + encodeURIComponent(svg)
    return sources[key]
}
