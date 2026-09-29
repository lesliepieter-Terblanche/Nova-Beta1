"""Creates your personal config.yaml from the template and asks three quick questions."""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = ROOT / "config.yaml"
if cfg.exists():
    print("config.yaml already exists — leaving it alone.")
    raise SystemExit
shutil.copy(ROOT / "config.example.yaml", cfg)
text = cfg.read_text(encoding="utf-8")

name = input("Your first name (what Nova calls you): ").strip() or "Friend"
print("Time zone examples: Africa/Johannesburg, Europe/London, America/New_York, Asia/Dubai, Australia/Sydney")
tz = input("Your time zone [UTC]: ").strip() or "UTC"
try:
    from zoneinfo import ZoneInfo
    ZoneInfo(tz)
except Exception:
    print(f"'{tz}' isn't a valid time zone name — using UTC (change it later in config.yaml).")
    tz = "UTC"
city = input("Your city (for weather in briefings, optional): ").strip()

text = re.sub(r"(?m)^  owner: .*$", f"  owner: {name}", text)
text = re.sub(r"(?m)^  timezone: .*$", f"  timezone: {tz}", text)
text = re.sub(r"(?m)^  city: .*$", f'  city: "{city}"', text)
cfg.write_text(text, encoding="utf-8")
print(f"Saved config.yaml for {name}.")
