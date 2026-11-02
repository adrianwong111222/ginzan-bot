"""銀山溫泉空房檢查：13 間旅館，2 大人 1 晚，入住 2027-01-14 至 2027-01-18。

每間旅館查兩個地方（如果有）：
  協會網：銀山温泉観光協会嘅聯合訂房 nj-yoyaku.net
  官網：旅館自己官網「予約」掣用嘅訂房系統
兩邊房數唔一樣，所以兩邊都查。

有新空房就 send Telegram；每日 send 一次「仲喺度行緊」。
用法：
  python check.py                       正常跑
  python check.py --dry-run             唔 send，只 print
  python check.py --dates 2026-11-03    用其他日子測試（唔讀唔寫 state）
"""
import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
DATES = [date(2027, 1, d) for d in range(14, 19)]  # 入住日，19 號退房
ADULTS = 2
HEARTBEAT_HOUR = 9  # 香港時間朝早 9 點後 send 每日報告
ERROR_ALERT_RUNS = 3  # 連續幾次出錯就即刻通知
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# 協會網：https://www.ginzanonsen.jp/yado/ 每間嘅「予約」都用 nj-yoyaku.net
NJ_HOTELS = [
    ("仙峡の宿 銀山荘", "ginzanso"),
    ("古勢起屋別館", "kosekiya"),
    ("本館古勢起屋", "kosekikan"),
    ("滝と蕎麦の宿 瀧見館", "takimikan"),
    ("能登屋旅館", "notoya"),
    ("味とまごころの宿 昭和館", "showakan"),
    ("旅館 永澤平八", "nagasawa"),
    ("旅籠 いとうや", "itouya"),
    ("御宿 やなだ屋", "yanadaya"),
    ("藤屋", "fujiya"),
    ("旅館 松本", "matsumoto"),
]

# 官網：各旅館自己網站嘅訂房系統
BAN489_HOTELS = [  # reserve.489ban.net
    ("仙峡の宿 銀山荘", "ginzanso"),
    ("古勢起屋別館", "kosekiya"),
    ("本館古勢起屋", "kosekikan"),
    ("滝と蕎麦の宿 瀧見館", "takimikan"),
    ("能登屋旅館", "notoyaryokan"),
    ("御宿 やなだ屋", "yanadaya"),
]
YS_BASE = "https://www.yado-sagashi.net/yoyaku/plan/"
YS_HOTELS = [  # yado-sagashi.net：(名, yid)；古山閣嘅新館 plan 當蔵の場
    ("古山閣", "0046353751453"),
    ("旅籠 いとうや", "4116550506152"),
]
HPDSP_HEIHACHI = ("旅館 永澤平八", "https://www.hpdsp.net/heihachi/hw/hwp3100/hww3101.do", "319755")
TABICHAT_FUJIYA = ("藤屋", "fujiyaginzan")
LIBERTY_SHOWAKAN = ("味とまごころの宿 昭和館", "cc5f559e549f4f2fac443b9673014a37",
                    "facility-db690034-b45f-46b5-84d1-3f8ba4b8f522")
# 旅館 松本 官網用 rwiths.net，連唔到，所以淨係查協會網
HOTEL_COUNT = 13

WEEKDAY = "一二三四五六日"
OPEN_MARKS = {"○", "◎", "△"}


def fetch(url, data=None, headers=None, opener=None, tries=2):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": UA, "Accept-Language": "ja", **(headers or {})})
            with (opener or urllib.request.build_opener()).open(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - 網絡錯誤重試
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url.split('?')[0]}: {last}")


def months_of(dates):
    out = {}
    for d in dates:
        out.setdefault((d.year, d.month), []).append(d)
    return out


def slot(hotel, source, d, info, url, room=""):
    return {"hotel": hotel, "source": source, "date": d.isoformat(), "room": room,
            "info": info, "url": url}


def check_nj(name, code, dates):
    """協會網 nj-yoyaku：月曆每格 ○/△/×，有 onclick 即係可以訂。"""
    slots = []
    for (y, m), ds in months_of(dates).items():
        h = fetch(f"https://www.nj-yoyaku.net/{code}/re_calendar.ashx?TDT={y}{m:02d}01&NZ={ADULTS}")
        head = re.search(r"<h5>(\d{4})年(\d{1,2})月</h5>", h)
        if not head or (int(head.group(1)), int(head.group(2))) != (y, m):
            raise RuntimeError(f"calendar header not found for {y}-{m}")
        cells = {int(day): (attrs, mark.strip())
                 for attrs, day, mark in re.findall(r"<td class='\w+'([^>]*)>(\d+)<br />([^<]*)</td>", h)}
        for d in ds:
            if d.day not in cells:
                raise RuntimeError(f"day {d} missing from calendar")
            attrs, mark = cells[d.day]
            if "onclick" in attrs or mark in OPEN_MARKS:  # ※ 等冇得撳嘅格 = 未開放
                slots.append(slot(name, "協會網", d, mark,
                                  f"https://www.nj-yoyaku.net/{code}/plan.aspx?TDT={d:%Y%m%d}&NZ={ADULTS}"))
    return slots


def check_489ban(name, code, dates):
    """官網 489ban：空室カレンダー JSON，一次俾 6 個月；每格有 icon：
    fa-circle 空室あり、fa-xmark 満室、fa-square-phone 要打電話、fa-minus 受付できません。"""
    base = f"https://reserve.489ban.net/client/{code}/0/plan/availability/"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    fetch(base + "daily", opener=opener, tries=1)  # 攞 session cookie
    first = min(dates)
    raw = fetch(f"{base}dailycalendar?year={first.year}&month={first.month}&planType=1",
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
                opener=opener, tries=1)
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"not JSON: {raw[:150]!r}") from None
    cells = {}
    for i in range(12):
        month = data.get(str(i)) if isinstance(data, dict) else (data[i] if i < len(data) else None)
        if not month:
            break
        cap = re.search(r"(\d{4})\D+(\d{1,2})", str(month.get("calendarCaption", "")))
        if not cap:
            raise RuntimeError(f"bad caption: {str(month)[:150]!r}")
        y, m = int(cap.group(1)), int(cap.group(2))
        for week in month.get("data", []):
            for html in (week.values() if isinstance(week, dict) else week):
                html = str(html or "")
                day = re.search(r"\d+", re.sub(r"<[^>]+>", " ", html))
                if day:
                    cells[(y, m, int(day.group()))] = html
    slots = []
    for d in dates:
        html = cells.get((d.year, d.month, d.day))
        if html is None:
            raise RuntimeError(f"day {d} missing; sample cell: {next(iter(cells.values()), '')[:150]!r}")
        if re.search(r"fa-(circle|triangle)", html) and "fa-circle-xmark" not in html:
            slots.append(slot(name, "官網", d, "空室あり", base + "daily"))
    return slots


def ys_rooms(yid):
    """yado-sagashi 計劃一覽：每個 plan/房 嘅 p 值同房名；冇 plan 就俾空 dict。"""
    h = fetch(f"{YS_BASE}index2.jsp?beg&all&yid={yid}")
    rooms = {}
    for mt in re.finditer(r"open2\('(\d+)',(\d+),'[^']*','(\d+)'", h):
        p = int(mt.group(2))
        text = re.sub(r"<[^>]+>", "|", h[max(0, mt.start() - 1500):mt.start()])
        found = re.findall(r"([^|()（）]+?)\s*[(（](\d+)名～(\d+)名[)）]", text)
        if not found:
            continue
        room, lo, hi = found[-1]
        if int(lo) <= ADULTS <= int(hi):
            rooms[p] = re.sub(r"\s+", " ", room).strip()
    if not rooms and "見つかりませんでした" not in h and "open2('" in h:
        raise RuntimeError("plan list changed: rooms not parsed")
    return rooms


def check_yado_sagashi(name, yid, dates):
    slots = []
    for p, room in ys_rooms(yid).items():
        hotel = "蔵の場（古山閣新館）" if ("新館" in room or "オーベルジュ" in room) else name
        for (y, m), ds in months_of(dates).items():
            h = fetch(f"{YS_BASE}plcal.jsp?y={yid}&p={p}&pe={ADULTS}&in={ds[0]:%Y/%m/%d}&cmfrm=1")
            if f"{y}年{m}月" not in h:
                raise RuntimeError(f"plan {p}: calendar {y}-{m} not shown")
            cells = {}
            for c in re.findall(r'<div class="callink">(.*?)</div>\s*</td>', h, re.S):
                day = re.search(r'cal_p_day">(\d+)<', c)
                if day:
                    cells[int(day.group(1))] = c
            for d in ds:
                c = cells.get(d.day)
                if c is None:
                    raise RuntimeError(f"plan {p}: day {d} missing")
                bg = re.search(r'cal_bg">([^<]*)<', c)
                bg = bg.group(1).strip() if bg else ""
                price = re.search(r"<b>([^<]*)</b>", c)
                if "openpl(" in c or re.search(r"\d+\s*室", bg):
                    slots.append(slot(hotel, "官網", d, f"{bg} {price.group(1) if price else ''}/人".strip(),
                                      f"{YS_BASE}plcal.jsp?y={yid}&p={p}&pe={ADULTS}&in={d:%Y/%m/%d}&cmfrm=1",
                                      room))
            time.sleep(1)
    return slots


def check_hpdsp(name, url, yad_no, dates):
    """官網 hpdsp（Jalan 官網系統）：用日子同人數搜尋，有 plan 即係有房。"""
    slots = []
    for d in dates:
        q = urllib.parse.urlencode({"yadNo": yad_no, "stayYear": d.year, "stayMonth": d.month,
                                    "stayDay": d.day, "stayCount": 1, "roomCount": 1,
                                    "adultNum": ADULTS, "roomCrack": f"{ADULTS}00000", "dateUndecided": 0})
        for attempt in range(2):  # 間中會俾一版唔完整嘅頁，再試一次
            h = fetch(f"{url}?{q}")
            none = "ご利用できるプランがない" in h
            n = len(re.findall(r"このプランの詳細・予約", h))
            if none or n:
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"{d}: page format changed")
        if none:
            continue
        slots.append(slot(name, "官網", d, f"{n} 個 plan", f"{url}?{q}"))
        time.sleep(1)
    return slots


def check_tabichat(name, code, dates):
    """官網 tabichat：calendar.json 淨係列出有得訂嘅日子。"""
    q = urllib.parse.urlencode({"from_date": min(dates).isoformat(), "to_date": max(dates).isoformat(),
                                "adults": ADULTS})
    data = json.loads(fetch(f"https://tabichat.jp/engine/api/v1/hotels/{code}/calendar.json?{q}",
                            headers={"Accept": "application/json"}))
    if "rates" not in data:
        raise RuntimeError(f"unexpected reply: {str(data)[:150]}")
    open_days = {r["date"]: r for r in data["rates"]}
    return [slot(name, "官網", d, f"{open_days[d.isoformat()].get('price', '')}",
                 f"https://tabichat.jp/engine/hotels/{code}")
            for d in dates if d.isoformat() in open_days]


def check_liberty(name, site, facility, dates):
    """官網 liberty（新版 yado-sagashi）：搜尋 API，房嘅 status.isAvailable 已計埋人數。"""
    headers = {"X-Site-Code": site, "X-Facility-Code": facility, "Time-Zone-Offset": "-540",
               "Content-Type": "application/json-patch+json", "Accept": "application/json"}
    info = json.loads(fetch("https://webapi.site.reservation.liberty-service.com/api/booking/facility?api-version=1",
                            headers=headers))
    adult_id = next(t["id"] for t in info["personAgeTypes"] if t.get("isMain"))
    page = f"https://site.reservation.liberty-service.com/{site}/{facility}/search"
    slots = []
    for d in dates:
        n, o = int(f"{d:%Y%m%d}"), int(f"{d + timedelta(days=1):%Y%m%d}")
        body = {"checkInDate": n, "checkOutDate": o, "restNumber": 1, "roomNumber": 1,
                "guestsPerRoom": [{"personAgeTypeId": adult_id, "appDateId": n, "restIndex": 0,
                                   "roomGroupIndex": 0, "persons": ADULTS, "femalePersons": 1,
                                   "malePersons": ADULTS - 1}],
                "optionItems": [], "displayCheckInDate": n, "displayCheckOutDate": o, "dayUse": None}
        plans = json.loads(fetch(
            "https://webapi.site.reservation.liberty-service.com/api/booking/search"
            "?api-version=1&page=1&size=50&enabled=true",
            data=json.dumps(body).encode(), headers=headers))
        if not isinstance(plans, list):
            raise RuntimeError(f"unexpected reply: {str(plans)[:150]}")
        rooms = {rm["name"] for p in plans for rm in p.get("rooms", [])
                 for a in rm.get("appDatePrices", [])
                 if a.get("appDateId") == n and (a.get("status") or {}).get("isAvailable")}
        for room in sorted(rooms):
            slots.append(slot(name, "官網", d, "", page, room))
        time.sleep(1)
    return slots


def check_all(dates):
    jobs = [(f"{n}（協會網）", check_nj, (n, c)) for n, c in NJ_HOTELS]
    jobs += [(f"{n}（官網）", check_489ban, (n, c)) for n, c in BAN489_HOTELS]
    jobs += [(f"{n}（官網）", check_yado_sagashi, (n, y)) for n, y in YS_HOTELS]
    jobs += [(f"{HPDSP_HEIHACHI[0]}（官網）", check_hpdsp, HPDSP_HEIHACHI),
             (f"{TABICHAT_FUJIYA[0]}（官網）", check_tabichat, TABICHAT_FUJIYA),
             (f"{LIBERTY_SHOWAKAN[0]}（官網）", check_liberty, LIBERTY_SHOWAKAN)]
    slots, errors = [], []
    for label, fn, args in jobs:
        try:
            slots += fn(*args, dates)
        except Exception as e:  # noqa: BLE001 - 一個壞咗唔好影響其他
            errors.append(f"{label}: {e}")
        time.sleep(1)
    unique = {}
    for s in slots:  # 同一間同一日同一款房，唔同 plan 只報一次
        unique.setdefault(slot_key(s), s)
    return list(unique.values()), errors


def fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def slot_key(s):
    return f"{s['hotel']}|{s.get('source', '')}|{s['date']}|{s['room']}"


def alert_text(slots, new_keys):
    lines = [f"銀山溫泉有房！（{ADULTS} 大人，1 晚）", ""]
    for s in sorted(slots, key=lambda s: (s["date"], s["hotel"])):
        tag = "[新] " if slot_key(s) in new_keys else ""
        extra = " ".join(x for x in (s["room"], s["info"]) if x)
        lines += [f"{tag}{s['hotel']}（{s.get('source', '')}）入住 {fmt_date(s['date'])} {extra}".rstrip(),
                  s["url"], ""]
    lines.append("快啲去訂。")
    return "\n".join(lines)


def heartbeat_text(slots, errors, dates):
    span = f"{fmt_date(dates[0].isoformat())}–{fmt_date(dates[-1].isoformat())}" if dates else "-"
    lines = [
        "銀山溫泉 bot 仲喺度行緊。",
        f"每個鐘查 {HOTEL_COUNT} 間（協會網 + 官網），入住 {span}，{ADULTS} 大人 1 晚。",
        f"而家有房：{len(slots)} 個" + ("（之前已經 send 過）" if slots else ""),
    ]
    if errors:
        lines += ["", "今次查唔到："] + [f"- {e}" for e in errors]
    return "\n".join(lines)


def send(text, dry_run):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if dry_run or not token or not chat_ids:
        print("----- (not sent) -----\n" + text + "\n----------------------")
        return
    for chat_id in chat_ids:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000],
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            print(f"sent to {chat_id}")
        except Exception as e:  # noqa: BLE001 - 一個 chat 失敗照 send 其他
            print(f"telegram send failed for {chat_id}: {e}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="唔 send Telegram")
    ap.add_argument("--dates", help="測試用入住日，逗號分隔，例如 2026-11-03,2026-11-04")
    args = ap.parse_args()

    now = datetime.now(HKT)
    if args.dates:
        dates = [date.fromisoformat(x.strip()) for x in args.dates.split(",")]
    else:
        dates = [d for d in DATES if d >= now.date()]
        if not dates:
            print("所有入住日已經過咗，唔再查。")
            return

    slots, errors = check_all(dates)
    print(f"{now:%Y-%m-%d %H:%M} HKT  slots={len(slots)}  errors={len(errors)}")
    for e in errors:
        print("ERROR", e, file=sys.stderr)

    if args.dates:  # 測試模式：有就 send，唔郁 state
        msg = alert_text(slots, {slot_key(s) for s in slots}) if slots else f"測試：{args.dates} 冇房。"
        if errors:
            msg += "\n\n今次查唔到：\n" + "\n".join(f"- {e}" for e in errors)
        send(msg, args.dry_run)
        return

    state = load_state()
    current = {slot_key(s) for s in slots}
    new_keys = current - set(state.get("seen", []))
    if new_keys:
        send(alert_text(slots, new_keys), args.dry_run)
    state["seen"] = sorted(current)

    state["error_runs"] = state.get("error_runs", 0) + 1 if errors else 0
    if state["error_runs"] == ERROR_ALERT_RUNS:
        send("銀山溫泉 bot：連續 3 次查唔到部分旅館，可能個網改咗：\n"
             + "\n".join(f"- {e}" for e in errors), args.dry_run)

    today = now.date().isoformat()
    if now.hour >= HEARTBEAT_HOUR and state.get("last_heartbeat") != today:
        send(heartbeat_text(slots, errors, dates), args.dry_run)
        state["last_heartbeat"] = today

    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
