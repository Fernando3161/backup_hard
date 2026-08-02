"""Portable Android-policy example; no phone or personal path is required."""

from backup_hard.android import classify, exclusion_reason


SYNTHETIC_FILES = [
    ("DCIM/Camera/photo.jpg", 20 * 1024),
    ("Android/media/example.app/exports/video.mp4", 2 * 1024 * 1024),
    ("Download/report.pdf", 4 * 1024),
    ("Android/data/example.app/cache/photo.jpg", 20 * 1024),
]


def decisions():
    result = []
    for path, size in SYNTHETIC_FILES:
        extension = "." + path.rsplit(".", 1)[-1].casefold()
        category = classify(extension)
        result.append((path, category, exclusion_reason(path, extension, category, size)))
    return result


if __name__ == "__main__":
    for decision in decisions():
        print(decision)
