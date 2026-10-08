"""r14 句庫：40 句（chat 25／skill 10／status 5）。mode 用 PawAI 81e5018 的 classify_mode 算，不手標。
用法：python3 -I make_bank.py <PawAI 81e5018 checkout> → 寫出 bank.json 並跑禁用字檢查。"""
import json, re, sys
from pathlib import Path
src = Path(sys.argv[1])
sys.path.insert(0, str(src / "pawai_brain"))
from pawai_brain.nodes.mode_classifier import classify_mode  # noqa: E402

CHAT = ["今天天氣怎麼樣", "你喜歡什麼顏色", "講一個簡短的笑話", "你最喜歡吃什麼", "現在幾點了",
        "台北今天會下雨嗎", "推薦一部好看的電影", "你覺得貓可愛還是狗可愛", "明天是星期幾", "你平常喜歡聽什麼音樂",
        "我晚餐吃了牛肉麵", "你知道珍珠奶茶嗎", "幫我想一個週末的活動", "你覺得學程式難不難", "給我一句鼓勵的話",
        "你會不會怕黑", "我們來聊聊夏天", "你有朋友嗎", "今天是我朋友的生日", "用一句話形容秋天",
        "你喜歡下雨天還是晴天", "最近有什麼好玩的事", "我考試考得不錯", "你覺得機器人會做夢嗎", "晚上適合喝咖啡嗎"]
SKILL = [("幫我揮個手", ["wave_hello"]), ("跟大家揮揮手", ["wave_hello"]),
         ("坐下來陪我一下", ["sit_along"]), ("請你坐下", ["sit_along"]),
         ("站起來", ["stand"]), ("可以站起來嗎", ["stand"]),
         ("扭一下屁股", ["wiggle"]), ("搖搖屁股給我看", ["wiggle"]),
         ("伸個懶腰", ["stretch"]), ("做一個伸展", ["stretch"])]
STATUS = ["你會做什麼", "你有哪些功能", "你現在狀態還好嗎", "你是誰", "介紹一下你自己"]

# 協定 §1.5 禁用字（前進、停止、危險動作、召喚）；問候快速路徑另判（長度 ≤ 6 且含問候詞）
FORBID = ["往前走", "往前移動", "往前一點", "前進", "走一點", "過來一點", "往前",
          "停", "stop", "煞車", "暫停", "緊急",
          "翻跟斗", "翻跟头", "後空翻", "后空翻", "前空翻", "倒立", "backflip", "front flip", "frontflip", "handstand",
          "過來", "來這裡", "來我這", "靠近", "跟我來", "這邊", "來一下"]
GREET = ["你好", "妳好", "您好", "哈囉", "哈嘍", "哈摟", "哈啰", "哈喽", "嗨", "早安", "午安", "晚安", "hello", "hi"]
# chat 子集要給 agent 無人自跑：不得落到 action／safety 模式，也不得帶 intent 關鍵字「站」「坐」
CHAT_EXTRA = ["站", "坐", "陪我", "累"]

bank, errors = [], []
def add(i, text, subset, gold, prefix=None):
    mode = classify_mode(text)
    low = text.lower()
    hits = [w for w in FORBID if w in low]
    if len(text) <= 6 and any(g in low for g in GREET):
        hits.append("greet_fast_path")
    if subset == "chat":
        hits += [w for w in CHAT_EXTRA if w in text]
        if mode != "chat":
            hits.append(f"mode={mode}")
    if hits:
        errors.append((text, hits))
    bank.append({"id": f"{prefix or subset[0]}{i:02d}", "text": text, "subset": subset, "mode": mode, "gold_skill": gold, "notes": ""})

for i, t in enumerate(CHAT, 1): add(i, t, "chat", [])
for i, (t, g) in enumerate(SKILL, 1): add(i, t, "skill", g, "k")
for i, t in enumerate(STATUS, 1): add(i, t, "status", [])
json.dump({"version": "r14-2026-10-07", "source": "PawAI 81e5018 mode_classifier", "items": bank},
          open(Path(__file__).with_name("bank.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(bank), "items;", "errors:", errors or "none")
for b in bank:
    print(b["id"], b["mode"].ljust(20), b["text"], b["gold_skill"] or "")
