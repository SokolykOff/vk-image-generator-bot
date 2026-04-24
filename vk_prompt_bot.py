import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
import requests
from urllib.parse import quote
import time
import json
import random
import hashlib
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator

load_dotenv()

TOKEN = os.getenv("VK_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", 236907673))
API_VERSION = '5.199'
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

MODELS_ROTATION = ["flux", "dreamshaper", "turbo", "gptimage", "seedream"]
AVATAR_STYLES = [
    "cyberpunk neon glowing",
    "professional business headshot",
    "magical fantasy elf",
    "anime vibrant style",
    "steampunk mechanic",
    "space explorer futuristic",
    "medieval knight realistic"
]
RANDOM_MODIFIERS = ["close-up", "cinematic lighting", "detailed background", "sharp focus"]

user_requests = {}

def parse_payload(event):
    try:
        return json.loads(event.obj.message.get('payload') or '{}')
    except:
        return {}

def send_msg(vk, pid, txt, kb=None, att=None):
    for attempt in range(3):
        try:
            p = {'peer_id': pid, 'message': txt, 'random_id': 0}
            if kb:
                p['keyboard'] = kb
            if att:
                p['attachment'] = att
            vk.messages.send(**p)
            return
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            time.sleep(1)

def check_limit(pid, lim=3, win=60):
    now = time.time()
    reqs = user_requests.get(pid, [])
    reqs = [t for t in reqs if now - t < win]
    if len(reqs) >= lim:
        return False
    reqs.append(now)
    user_requests[pid] = reqs
    return True

def get_cache(ph):
    p = f"{CACHE_DIR}/{ph}.jpg"
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    return None

def save_cache(ph, data):
    with open(f"{CACHE_DIR}/{ph}.jpg", "wb") as f:
        f.write(data)

def translate_to_en(text):
    if not text.strip():
        return "beautiful landscape"
    try:
        return GoogleTranslator(source='ru', target='en').translate(text.strip())
    except:
        return text.strip()

def build_prompt(raw, style=None):
    translated = translate_to_en(raw)
    parts = [translated]
    if style:
        parts.append(style)
    parts.append("masterpiece, best quality, ultra high res, photorealistic, 8k, sharp focus, highly detailed, professional lighting, cinematic, no watermark, no text")
    return ", ".join(parts)

def upload_vk(vk, pid, data):
    try:
        srv = vk.photos.getMessagesUploadServer(peer_id=pid)
        up = requests.post(srv['upload_url'], files={'photo': ('img.jpg', data)}).json()
        if up.get('error'):
            return None
        saved = vk.photos.saveMessagesPhoto(server=up['server'], photo=up['photo'], hash=up['hash'])[0]
        return f"photo{saved['owner_id']}_{saved['id']}"
    except:
        return None

def gen_single(vk, pid, prompt, batch=False):
    if not check_limit(pid):
        if not batch:
            send_msg(vk, pid, "⏳ Лимит: 3 запроса в минуту. Подожди.")
        return None

    w, h = 1024, 1024
    neg = "text, watermark, signature, ugly, deformed, blurry, lowres, bad anatomy, duplicate, noisy, pixelated, distorted, cropped, jpeg artifacts, extra limbs, low quality"

    mod = random.choice(RANDOM_MODIFIERS)
    final_prompt = f"{prompt}, {mod}"

    ph = hashlib.md5(final_prompt.encode()).hexdigest()
    cached = get_cache(ph)
    if cached and not batch:
        att = upload_vk(vk, pid, cached)
        if att:
            send_msg(vk, pid, "✅ Из кэша!", att=att, kb=get_menu_kb())
        return True

    for model in MODELS_ROTATION:
        seed = int(time.time() * 1000 + random.randint(0, 9999))
        url = f"https://image.pollinations.ai/prompt/{quote(final_prompt, safe='')}?model={model}&width={w}&height={h}&enhance=true&nologo=true&seed={seed}&safe=false&private=true&negative_prompt={quote(neg)}"
        for attempt in range(2):
            try:
                r = requests.get(url, timeout=90)
                if r.status_code == 200 and 'image' in r.headers.get('Content-Type', ''):
                    save_cache(ph, r.content)
                    if batch:
                        return r.content
                    att = upload_vk(vk, pid, r.content)
                    if att:
                        send_msg(vk, pid, "✅ Готово!", att=att, kb=get_menu_kb())
                        return True
                    else:
                        send_msg(vk, pid, "❌ Ошибка загрузки", kb=get_menu_kb())
                        return False
                elif r.status_code == 429:
                    time.sleep(15)
            except:
                pass
            time.sleep(2)
    if not batch:
        send_msg(vk, pid, "❌ Не вышло. Попробуй позже.", kb=get_menu_kb())
    return False

def gen_batch(vk, pid, prompt, count=3):
    atts = []
    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(gen_single, vk, pid, prompt, True) for _ in range(count)]
        for future in as_completed(futures):
            res = future.result()
            if res:
                att = upload_vk(vk, pid, res)
                if att:
                    atts.append(att)
    if atts:
        send_msg(vk, pid, f"✅ {len(atts)} варианта! Выбери лучший 👇", att=','.join(atts), kb=get_menu_kb())
    else:
        send_msg(vk, pid, "❌ Не удалось сгенерировать", kb=get_menu_kb())

def get_menu_kb():
    kb = {
        "one_time": False,
        "inline": False,
        "buttons": [
            [{"action": {"type": "text", "label": "🎨 Сгенерировать картинку"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "✨ Случайный аватар"}, "color": "positive"}]
        ]
    }
    return json.dumps(kb, ensure_ascii=False)

def get_empty_kb():
    return json.dumps({"one_time": False, "inline": False, "buttons": []}, ensure_ascii=False)

def main():
    if not TOKEN:
        print("❌ Нет токена. Создай .env с VK_TOKEN")
        return
    vk = vk_api.VkApi(token=TOKEN, api_version=API_VERSION).get_api()
    lp = VkBotLongPoll(vk_api.VkApi(token=TOKEN, api_version=API_VERSION), GROUP_ID)
    print("✅ Бот запущен (3 варианта, минимализм)")

    for ev in lp.listen():
        if ev.type != VkBotEventType.MESSAGE_NEW or not ev.from_user:
            continue
        pid = ev.obj.message['peer_id']
        txt = ev.obj.message.get('text', '').strip().lower()
        pl = parse_payload(ev)

        if txt in ["меню", "старт", "привет", "/start", "начать"]:
            send_msg(vk, pid, "Привет! Я генерирую картинки по твоим описаниям 🎨\n\nПросто напиши, что хочешь увидеть, или нажми кнопку ниже.", kb=get_menu_kb())
            continue

        if txt == "🎨 сгенерировать картинку":
            send_msg(vk, pid, "📝 Напиши любой запрос на русском:\nНапример: «кот в космосе» или «замок на облаке»", kb=get_empty_kb())
            continue

        if txt == "✨ случайный аватар":
            style = random.choice(AVATAR_STYLES)
            prompt = f"portrait of a person, {style}, highly detailed, 8k"
            send_msg(vk, pid, f"🎨 Генерирую аватар в стиле {style}... это может занять от 15 сек до 2 мин.")
            gen_batch(vk, pid, prompt)
            continue

        if txt and len(txt) > 2:
            send_msg(vk, pid, f"🎨 Генерирую: «{txt}»... это может занять от 15 сек до 2 мин.")
            prompt = build_prompt(txt)
            gen_batch(vk, pid, prompt)
            continue

        send_msg(vk, pid, "Напиши что-нибудь или нажми кнопку меню.", kb=get_menu_kb())

if __name__ == "__main__":
    main()