"""Build claims_test/: ten ready-to-send test messages, one folder each.

Audio comes from ElevenLabs text-to-speech, images from the ElevenLabs image
API. If the image service refuses a poster (it moderates some misinformation-
style text), that poster is rendered with headless Edge instead and reported.
Existing files are kept; pass --force to rebuild.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "claims_test"
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: E402

H = {"xi-api-key": settings.ELEVENLABS_API_KEY, "Content-Type": "application/json"}
BASE = "https://api.elevenlabs.io"
FORCE = "--force" in sys.argv
EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
REPORT: list[str] = []


def path(folder: str, name: str) -> Path:
    d = OUT / folder
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def need(p: Path) -> bool:
    return FORCE or not p.exists()


def write_text(folder: str, name: str, text: str):
    p = path(folder, name)
    if need(p):
        p.write_text(text.strip() + "\n", encoding="utf-8")


def ffmpeg(*args: str):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


# ------------------------------------------------------------------ images


def edge_poster(p: Path, lines: list[str], bg: str, title: str | None, size: int = 54):
    body = "".join(f"<p>{ln}</p>" for ln in lines)
    head = f"<h1>{title}</h1>" if title else ""
    html = (
        "<meta charset='utf-8'><style>"
        f"body{{margin:0;width:900px;height:1100px;background:{bg};font-family:'Nirmala UI','Segoe UI',sans-serif;"
        f"padding:60px;box-sizing:border-box;color:#141414}}h1{{color:#b00000;font-size:{size + 12}px;margin:0 0 30px}}"
        f"p{{font-size:{size}px;font-weight:700;line-height:1.35;margin:0 0 28px}}</style>{head}{body}"
    )
    page = p.with_suffix(".html")
    page.write_text(html, encoding="utf-8")
    subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=900,1100",
         f"--screenshot={p}", page.as_uri()],
        capture_output=True, timeout=60,
    )
    page.unlink()


def api_image(p: Path, prompt: str) -> bool:
    """One ElevenLabs image. Returns False if it was refused or failed."""
    body = {"model_id": "gpt-image-2", "prompt": prompt, "aspect_ratio": "9:16", "resolution": "1K", "quality": "high"}
    r = httpx.post(f"{BASE}/v1/flows/image", headers=H, json=body, timeout=60)
    if r.status_code != 200:
        REPORT.append(f"{p.parent.name}/{p.name}: image API create failed {r.status_code}")
        return False
    gid = r.json()["id"]
    for _ in range(90):
        time.sleep(4)
        try:
            d = httpx.get(f"{BASE}/v1/flows/image/{gid}", headers=H, timeout=30).json()
        except Exception:
            continue
        if d.get("status") == "completed":
            p.write_bytes(httpx.get(d["content_url"], timeout=60).content)
            return True
        if d.get("status") not in ("pending", "generating"):
            REPORT.append(f"{p.parent.name}/{p.name}: image API said {d.get('failure_reason', d.get('status'))}")
            return False
    REPORT.append(f"{p.parent.name}/{p.name}: image API timed out")
    return False


POSTER = (
    " Style: a forwarded WhatsApp graphic. High-contrast, very large bold readable text, flat background, "
    "nothing else on it. The text must be exactly as given, spelled perfectly, no extra words."
)


def poster(folder: str, name: str, prompt: str, lines: list[str], bg: str, title: str | None, size: int = 54):
    p = path(folder, name)
    if not need(p):
        return
    if api_image(p, prompt + POSTER):
        REPORT.append(f"{folder}/{name}: made with the ElevenLabs image API")
    else:
        edge_poster(p, lines, bg, title, size)
        REPORT.append(f"{folder}/{name}: rendered with headless Edge instead (image API refused or failed)")


def scene(folder: str, name: str, prompt: str):
    p = path(folder, name)
    if need(p):
        ok = api_image(p, prompt)
        REPORT.append(f"{folder}/{name}: {'made with the ElevenLabs image API' if ok else 'FAILED'}")


# ------------------------------------------------------------------ audio

_voice = None


def voice() -> str:
    global _voice
    if not _voice:
        voices = httpx.get(f"{BASE}/v1/voices", headers=H, timeout=30).json().get("voices", [])
        _voice = ([v for v in voices if v.get("category") == "premade"] or voices)[0]["voice_id"]
    return _voice


def speech(p: Path, text: str, model: str = "eleven_multilingual_v2"):
    if not need(p):
        return
    r = httpx.post(
        f"{BASE}/v1/text-to-speech/{voice()}?output_format=mp3_44100_128",
        headers=H, json={"text": text, "model_id": model}, timeout=120,
    )
    if r.status_code != 200:
        REPORT.append(f"{p.parent.name}/{p.name}: TTS failed {r.status_code} {r.text[:120]}")
        return
    p.write_bytes(r.content)
    REPORT.append(f"{p.parent.name}/{p.name}: made with ElevenLabs text-to-speech")


def video(p: Path, image: Path, audio: Path | None = None, tone: bool = False):
    if not need(p) or not image.exists():
        return
    if audio is not None:
        src = ["-i", str(audio)]
    elif tone:
        src = ["-f", "lavfi", "-i", "sine=frequency=300:duration=8"]
    else:
        src = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
    ffmpeg(
        "-loop", "1", "-i", str(image), *src, "-shortest", "-t", "30",
        "-vf", "scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast", "-c:a", "aac", str(p),
    )
    REPORT.append(f"{p.parent.name}/{p.name}: built with ffmpeg")


# ------------------------------------------------------------------ the ten claims


def claim1():
    write_text("claim1", "message.txt", """
Forwarded many times:
1) Drinking hot water every 15 minutes cures COVID-19.
2) The capital of India is Mumbai.
3) The Reserve Bank of India will stop accepting all Rs 500 notes from next month.
""")


def claim2():
    write_text("claim2", "message.txt", """
Humans use only 10 percent of their brain.
गर्म पानी पीने से कोरोना ठीक हो जाता है।
उद्यापासून महाराष्ट्रातील सर्व शाळा एक आठवडा बंद राहतील.
""")


def claim3():
    poster(
        "claim3", "image.png",
        "A graphic with a yellow background and a red heading 'BREAKING NEWS' above large black text: "
        "'Drinking hot water every 15 minutes kills the coronavirus.'",
        ["Drinking hot water every 15 minutes kills the coronavirus."], "#fff4c8", "BREAKING NEWS",
    )
    write_text("claim3", "caption.txt", "Is this true? My doctor friend forwarded this to me, please check.")


def claim4():
    poster(
        "claim4", "image.png",
        "A graphic with a light blue background and a heading 'DID YOU KNOW?' above large black text: "
        "'The Great Wall of China is visible from space with the naked eye.'",
        ["The Great Wall of China is visible from space with the naked eye."], "#dff1ff", "DID YOU KNOW?",
    )
    write_text("claim4", "caption.txt", "Also, is it true that lightning never strikes the same place twice?")


def claim5():
    poster(
        "claim5", "image.png",
        "A graphic with a cream background and a red heading 'जरूरी खबरें' above three numbered lines of large black text, "
        "exactly: '1. पृथ्वी चपटी है।' '2. Mount Everest is the tallest mountain above sea level.' "
        "'3. सरकार कल से सभी को 2000 रुपये देगी।'",
        ["1. पृथ्वी चपटी है।", "2. Mount Everest is the tallest mountain above sea level.", "3. सरकार कल से सभी को 2000 रुपये देगी।"],
        "#fff3d6", "जरूरी खबरें", size=46,
    )


def claim6():
    write_text("claim6", "message.txt", """
Good morning everyone! 🙏
My grandmother says the old well in our village has magical water that never dries.
Can you also write me a short poem about the rain?
And I personally think cricket is the best sport in the world.
""")


def claim7():
    folder = "claim7"
    img = path(folder, "image.png")
    if need(img):
        src = path(folder, "_src.png")
        edge_poster(src, ["The government will give a free laptop to every student from next week."], "#e8f5e9", "NOTICE")
        im = Image.open(src).convert("RGB")
        im.filter(ImageFilter.GaussianBlur(28)).resize((450, 550)).save(img)
        src.unlink()
        REPORT.append("claim7/image.png: a poster blurred until the text cannot be read")
    # Plain low-passed noise (wind / a bad line). Mumbled speech is a poor test: the
    # speech-to-text engine invents sentences from it.
    aud = path(folder, "audio.mp3")
    if need(aud):
        ffmpeg(
            "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.35:duration=8",
            "-af", "lowpass=f=700,tremolo=f=0.6:d=0.5", str(aud),
        )
        REPORT.append("claim7/audio.mp3: low-passed pink noise, no speech")
    video(path(folder, "video.mp4"), img, tone=True)


def claim8():
    text = "Hello everyone, I just heard that the government has announced that petrol will cost two hundred rupees per litre from midnight tonight. Is this true?"
    speech(path("claim8", "audio.mp3"), text)
    write_text("claim8", "transcript_for_reference.txt", text)


def claim9():
    text = (
        "Teen baatein check karo. Pehli baat, गर्म पानी पीने से कोरोना ठीक हो जाता है। "
        "Second, the Reserve Bank of India has banned all two thousand rupee notes. "
        "तिसरी गोष्ट, लसूण खाल्ल्याने कॅन्सर बरा होतो."
    )
    speech(path("claim9", "audio.mp3"), text, model="eleven_v3")
    write_text("claim9", "transcript_for_reference.txt", text)


def claim10():
    folder = "claim10"
    img = path(folder, "_still.png")
    scene(folder, "_still.png", "A phone photo of a television news studio desk with two empty chairs and a blank screen behind them, no text, no logos, no people.")
    text = "Attention everyone. WhatsApp has announced that from tomorrow it will charge ninety nine rupees per month. Forward this message to ten people to keep your account free."
    aud = path(folder, "_speech.mp3")
    speech(aud, text)
    video(path(folder, "video.mp4"), img, audio=aud)
    for tmp in (img, aud):
        tmp.unlink(missing_ok=True)
    write_text(folder, "caption.txt", "Please check this video, my whole family group is forwarding it.")
    write_text(folder, "transcript_for_reference.txt", text)


README = """# Claim test messages

Ten ready-to-send test messages for the InfoBot WhatsApp number. Send the files in each
folder the way the table says. `transcript_for_reference.txt` files only show what is said
in the audio or video: do not send them.

| Folder | What it tests | Send | A good reply |
|---|---|---|---|
| claim1 | Several claims in one text | `message.txt` pasted as text | Three numbered verdicts |
| claim2 | Several claims, one language each (English, Hindi, Marathi) | `message.txt` pasted as text | Three verdicts, each in its claim's language |
| claim3 | Image + text about the same claim | `image.png`, with `caption.txt` as its caption | One verdict on the hot-water claim |
| claim4 | Image + text about two unrelated claims | `image.png`, with `caption.txt` as its caption | Two verdicts: Great Wall and lightning |
| claim5 | Several claims in one image | `image.png` only | Three verdicts read from the picture |
| claim6 | Unrelated / unverifiable / off-topic text | `message.txt` pasted as text | A friendly reply about what you wrote, no verdict card |
| claim7 | Unrecognisable image, audio and video | `image.png`, `audio.mp3`, `video.mp4`, one at a time | A polite "couldn't read or hear anything", never an error |
| claim8 | Audio claim, one language | `audio.mp3` | A verdict on the petrol claim |
| claim9 | Audio with several claims in three languages | `audio.mp3` | Verdicts for each claim |
| claim10 | Video with a spoken claim, plus a caption | `video.mp4`, with `caption.txt` as its caption | A verdict on the WhatsApp-charges claim |
"""


def main():
    OUT.mkdir(exist_ok=True)
    for fn in (claim1, claim2, claim3, claim4, claim5, claim6, claim7, claim8, claim9, claim10):
        fn()
    (OUT / "README.md").write_text(README, encoding="utf-8")
    print("\n".join(REPORT))
    files = sorted(str(f.relative_to(OUT)) for f in OUT.rglob("*") if f.is_file())
    print("\n".join(files))


if __name__ == "__main__":
    main()
