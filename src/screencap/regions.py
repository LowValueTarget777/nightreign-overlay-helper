"""Physical-pixel regions calibrated on the default 2560 x 1440 HUD."""

REFERENCE_SIZE = (2560, 1440)
REFERENCE_REGIONS = {
    "day1_detect_region": (1112, 733, 342, 95),
    "hpcolor_detect_region": (240, 58, 20, 8),
    "map_region": (1454, 228, 990, 990),
    "hpbar_region": (223, 56, 112, 12),
    "art_region": (1206, 1260, 143, 140),
}


def adaptive_regions(client_rect):
    """Return absolute screen regions; never guess a non-16:9 HUD layout."""
    x, y, width, height = client_rect
    if width < 640 or height < 360:
        raise ValueError("游戏客户区过小，等待游戏窗口稳定")
    if abs(width / height - 16 / 9) > 0.01:
        raise ValueError("自动区域仅支持 16:9，请关闭自动模式后手动框选")
    sx, sy = width / REFERENCE_SIZE[0], height / REFERENCE_SIZE[1]
    return {
        name: [x + round(rx * sx), y + round(ry * sy),
               max(1, round(rw * sx)), max(1, round(rh * sy))]
        for name, (rx, ry, rw, rh) in REFERENCE_REGIONS.items()
    }


def detection_region(name):
    """Keep manual settings separate from the current automatic snapshot."""
    def get(instance):
        if instance.auto_regions_enabled:
            return instance._auto_regions.get(name)
        return instance.__dict__.get("_manual_" + name)

    def set_(instance, value):
        instance.__dict__["_manual_" + name] = value

    return property(get, set_)
