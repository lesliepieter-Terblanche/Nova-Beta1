"""Skills are plain Python modules that register tools with @tool.

To add your own: create nova/skills/my_skill.py, decorate functions with
@tool(group="my_group"), call register_group("my_group", [trigger words]),
and add the module name to SKILLS below.
"""
import importlib

SKILLS = ["system", "memory", "files", "web", "browser", "google_ws", "media", "camera_ads", "meetings", "maintenance"]


def load_all() -> list[str]:
    loaded = []
    for name in SKILLS:
        try:
            importlib.import_module(f"{__name__}.{name}")
            loaded.append(name)
        except Exception as e:
            print(f"[skills] {name} not loaded: {e}")
    return loaded
