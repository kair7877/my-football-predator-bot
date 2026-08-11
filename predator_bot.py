# =====================================================
# PREDATOR ZETA v30.12 [PRO LEAGUES & TOP STRATEGIES]
# =====================================================
# Обновления и фиксы v30.12:
# 1. 🛡️ ФИЛЬТР ТОПОК И БК-ЛИГ (PRO_LEAGUES_ONLY):
#    Отсекаются региональные и не БК-доступные лиги.
# 2. 🔥 3 ТОПОВЫЕ СТРАТЕГИИ:
#    • LateFavoriteStrategy: Штурм фаворита (60'-78') при 0:0 / 1:1 / 0:1
#    • FirstHalfGoalStrategy: Гол в 1-м тайме (22'-36')
#    • LateOverStrategy: Поздний тотал (70'-82') при открытом футболе
# 3. 🌐 Встроенный HTTP-сервер для проходимости проверок Render (Health Check).
# 4. 🐛 Обход Cloudflare / SofaScore (HTTP 403 bypass via cloudscraper).
# =====================================================

import time
import os
import sys
import threading
import requests
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

try:
    import cloudscraper
except ImportError:
    print("❌ Ошибка: Не установлена библиотека cloudscraper.")
    print("Установите через терминал: pip install cloudscraper requests aiohttp")
    sys.exit(1)


# =====================================================
# 🌐 ВЕБ-СЕРВЕР ДЛЯ РЕНДЕРА (ДЛЯ ЗЕЛЕНОГО СТАТУСА НА RENDER)
# =====================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"<h1>PREDATOR ZETA v30.12 Football Bot is LIVE!</h1>")

    def log_message(self, format, *args):
        return  # Отключаем спам в консоли


def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌐 [Render Health Server] Веб-сервер запущен на порту {port}")


# =====================================================
# ⚙️ КОНФИГУРАЦИЯ БОТА
# =====================================================
class Config:
    VERSION = "30.12 [PRO LEAGUES]"
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "8910776648:AAGbhcQ7CBH4QVq3lT9x6GmU8kgkFSJhqY")
    CHAT_ID = os.environ.get("CHAT_ID", "-1004290840012")
    CHECK_INTERVAL = 45             # 45 секунд между циклами проверки
    BANKROLL_START = 1000.0
    FLAT_STAKE = 100.0
    CURRENCY = "KZT"

    MAX_CONCURRENT_BETS = 8
    DAILY_STOPLOSS_PCT = 30.0
    OVERALL_STOPLOSS_PCT = 50.0

    # 🕐 Окна отправки сигналов (20-36' для 1-го тайма, 60-80' для 2-го тайма)
    SEND_WINDOWS = [(20, 36), (60, 80)]
    PENDING_EXPIRE_MINUTE = 82

    # 🚫 ЖЁСТКИЙ ФИЛЬТР РЕГИОНАЛЬНЫХ И НЕПОНЯТНЫХ ЛИГ
    PRO_LEAGUES_ONLY = True         # Только профессиональные БК-турниры
    MIN_UNIQUE_USER_COUNT = 250     # Мин. количество подписчиков турнира в SofaScore

    # Черный список слов (региональные дивизионы, юниоры, любители)
    EXCLUDE_KEYWORDS = [
        "astiller", "colonia", "provincial", "regional", "distrital", "interprovincial",
        "tercera", "preferente", "oberliga", "landesliga", "kreisliga", "bezirksliga",
        "league 3", "league 4", "league 5", "liga 3", "liga 4", "division 3", "division 4",
        "division 5", "copa santa fe", "amateur", "sunday league", "regionaliga",
        "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23",
        "youth", "junior", "juvenil", "juniors", "academy", "sub 20", "sub 23", "sub-20", "sub-19",
        "women", "woman", "ladies", "femenino", "feminine", "frauen", "dames",
        "friendly", "friendlies", "testspiel", "club friendly",
        "reserve", "reserves", "réserve", " b team", "b-team", " ii "
    ]

    ODDS = {
        "late_favorite": 1.85,
        "first_half_goal": 1.75,
        "late_over": 1.90,
    }


def cl(t, c="WH"):
    C = {"R": "\033[0m", "CY": "\033[1;36m", "GR": "\033[1;32m", "YE": "\033[1;33m",
         "RE": "\033[1;31m", "BL": "\033[1;34m", "MA": "\033[1;35m", "WH": "\033[1;37m"}
    return f"{C.get(c,'')}{t}{C['R']}"


def in_send_window(minute: int) -> bool:
    return any(lo <= minute <= hi for lo, hi in Config.SEND_WINDOWS)


def is_excluded_match(match: dict) -> Optional[str]:
    """Проверка турнира на БК-доступность и отсутствие любительских статусов."""
    tournament = match.get("tournament") or {}
    unique_t = tournament.get("uniqueTournament") or {}
    category = tournament.get("category") or {}
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}

    if Config.PRO_LEAGUES_ONLY:
        if not unique_t:
            return "No uniqueTournament (Региональная/Любительская лига)"
        user_count = int(unique_t.get("userCount") or 0)
        if user_count < Config.MIN_UNIQUE_USER_COUNT:
            return f"Низкий статус турнира (подписчиков: {user_count})"

    haystack = " ".join([
        str(tournament.get("name") or ""),
        str(unique_t.get("name") or ""),
        str(category.get("name") or ""),
        str(home.get("name") or ""),
        str(away.get("name") or ""),
    ]).lower()
    haystack = f" {haystack} "

    for kw in Config.EXCLUDE_KEYWORDS:
        if kw in haystack:
            return kw
    return None


@dataclass
class ActiveBet:
    match_id: str
    message_id: int
    strategy_id: str
    strategy_name: str
    emoji: str
    market: str
    selection: str
    stake: float
    home_name: str
    away_name: str
    entry_score_h: int
    entry_score_a: int
    entry_minute: str
    meta: dict = field(default_factory=dict)
    status: str = "active"
    settled: bool = False


class BaseStrategy:
    id = "base"
    name = "BASE"
    emoji = "•"

    def scan(self, match: dict, incidents: List[dict], stats: Optional[dict]) -> Optional[dict]:
        raise NotImplementedError

    def settle(self, bet: ActiveBet, cur_h: int, cur_a: int, minute: int, period: str) -> Optional[bool]:
        raise NotImplementedError


def _extract_stat_val(stats: dict, target_names: List[str]) -> Tuple[int, int]:
    """Вспомогательная функция для парсинга любых статистических показателей SofaScore."""
    if not stats:
        return (0, 0)
    try:
        for period_block in stats.get("statistics", []):
            if period_block.get("period") != "ALL":
                continue
            for group in period_block.get("groups", []):
                for item in group.get("statisticsItems", []):
                    name = str(item.get("name") or "").lower()
                    if any(t in name for t in target_names):
                        h_val = item.get("homeValue", item.get("home", 0))
                        a_val = item.get("awayValue", item.get("away", 0))
                        try:
                            return int(str(h_val).replace("%", "").strip()), int(str(a_val).replace("%", "").strip())
                        except (TypeError, ValueError):
                            return (0, 0)
    except Exception:
        pass
    return (0, 0)


# =====================================================
# СТРАТЕГИЯ 1: 🔥 LATE FAVORITE PRESSURE (Штурм фаворита 60'-78')
# =====================================================
class LateFavoriteStrategy(BaseStrategy):
    id = "late_favorite"
    name = "ШТУРМ ФАВОРИТА (60'-78')"
    emoji = "🔥"

    def scan(self, match, incidents, stats):
        minute = match.get("_minute", 0)
        if not (60 <= minute <= 78):
            return None

        cur_h = int((match.get("homeScore") or {}).get("current") or 0)
        cur_a = int((match.get("awayScore") or {}).get("current") or 0)
        score_diff = abs(cur_h - cur_a)

        if score_diff > 1:
            return None

        sh_h, sh_a = _extract_stat_val(stats, ["shots on target", "удары в створ"])
        cn_h, cn_a = _extract_stat_val(stats, ["corner kicks", "corners", "угловые"])

        dominant = None
        if (sh_h >= 4 or cn_h >= 5) and (sh_h - sh_a >= 2):
            dominant = "home"
        elif (sh_a >= 4 or cn_a >= 5) and (sh_a - sh_h >= 2):
            dominant = "away"

        if not dominant:
            return None

        team_name = (match.get("homeTeam" if dominant == "home" else "awayTeam") or {}).get("name", "Unknown")
        return {
            "market": "late_favorite_goal",
            "selection": f"Гол фаворита ({team_name}) / ТБ",
            "meta": {"dominant": dominant, "sh_h": sh_h, "sh_a": sh_a, "cn_h": cn_h, "cn_a": cn_a},
        }

    def settle(self, bet, cur_h, cur_a, minute, period):
        dominant = bet.meta.get("dominant")
        if dominant == "home" and cur_h > bet.entry_score_h:
            return True
        if dominant == "away" and cur_a > bet.entry_score_a:
            return True
        if period == "FINISHED":
            return False
        return None


# =====================================================
# СТРАТЕГИЯ 2: ⚡ FIRST HALF GOAL STORM (Гол в 1-м тайме 22'-36')
# =====================================================
class FirstHalfGoalStrategy(BaseStrategy):
    id = "first_half_goal"
    name = "ГОЛ В 1-М ТАЙМЕ (22'-36')"
    emoji = "⚡"

    def scan(self, match, incidents, stats):
        minute = match.get("_minute", 0)
        if not (22 <= minute <= 36):
            return None

        cur_h = int((match.get("homeScore") or {}).get("current") or 0)
        cur_a = int((match.get("awayScore") or {}).get("current") or 0)
        total_goals = cur_h + cur_a

        if total_goals >= 2:
            return None

        sh_h, sh_a = _extract_stat_val(stats, ["shots on target", "удары в створ"])
        cn_h, cn_a = _extract_stat_val(stats, ["corner kicks", "corners", "угловые"])

        total_shots = sh_h + sh_a
        total_corners = cn_h + cn_a

        if total_shots >= 4 and total_corners >= 3:
            return {
                "market": "first_half_goal",
                "selection": "Гол в 1-м тайме (ИТБ 0.5 1st Half)",
                "meta": {"total_shots": total_shots, "total_corners": total_corners},
            }
        return None

    def settle(self, bet, cur_h, cur_a, minute, period):
        if (cur_h + cur_a) > (bet.entry_score_h + bet.entry_score_a):
            return True
        if period in ("HT", "2nd", "FINISHED"):
            return False
        return None


# =====================================================
# СТРАТЕГИЯ 3: 🎯 LATE TOTAL OVER (Поздний тотал 70'-82')
# =====================================================
class LateOverStrategy(BaseStrategy):
    id = "late_over"
    name = "ПОЗДНИЙ ТОТАЛ БОЛЬШЕ (70'-82')"
    emoji = "🎯"

    def scan(self, match, incidents, stats):
        minute = match.get("_minute", 0)
        if not (70 <= minute <= 82):
            return None

        cur_h = int((match.get("homeScore") or {}).get("current") or 0)
        cur_a = int((match.get("awayScore") or {}).get("current") or 0)
        score_diff = abs(cur_h - cur_a)

        if score_diff > 1:
            return None

        sh_h, sh_a = _extract_stat_val(stats, ["shots on target", "удары в створ"])
        cn_h, cn_a = _extract_stat_val(stats, ["corner kicks", "corners", "угловые"])

        if (sh_h + sh_a >= 8) and (cn_h + cn_a >= 6):
            target_total = cur_h + cur_a + 0.5
            return {
                "market": "late_over_total",
                "selection": f"Тотал Больше {target_total}",
                "meta": {"total_shots": sh_h + sh_a, "total_corners": cn_h + cn_a},
            }
        return None

    def settle(self, bet, cur_h, cur_a, minute, period):
        if (cur_h + cur_a) > (bet.entry_score_h + bet.entry_score_a):
            return True
        if period == "FINISHED":
            return False
        return None


STRATEGIES: List[BaseStrategy] = [
    LateFavoriteStrategy(),
    FirstHalfGoalStrategy(),
    LateOverStrategy(),
]


class BankrollManager:
    def __init__(self):
        self.balance = Config.BANKROLL_START
        self.day_start_balance = Config.BANKROLL_START
        self.current_day = date.today().isoformat()
        self.active_bets: Dict[str, ActiveBet] = {}

    def can_open_new_bet(self) -> bool:
        return len(self.active_bets) < Config.MAX_CONCURRENT_BETS

    def place_bet(self, match_id, msg_id, strategy: BaseStrategy, signal: dict, info: dict):
        if not msg_id or match_id in self.active_bets:
            return None
        bet = ActiveBet(
            match_id=match_id, message_id=msg_id,
            strategy_id=strategy.id, strategy_name=strategy.name, emoji=strategy.emoji,
            market=signal["market"], selection=signal["selection"],
            stake=Config.FLAT_STAKE,
            home_name=info["home"], away_name=info["away"],
            entry_score_h=info["score_h"], entry_score_a=info["score_a"],
            entry_minute=info["minute"], meta=signal.get("meta", {}),
        )
        self.active_bets[match_id] = bet
        return bet


class SofaFetcher:
    API = "https://api.sofascore.com/api/v1"

    def __init__(self):
        self.sc = cloudscraper.create_scraper()
        self.sc.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        })
        self.last_req = 0.0

    def _wait(self):
        if time.time() - self.last_req < 0.6:
            time.sleep(0.6)
        self.last_req = time.time()

    def _get(self, ep):
        self._wait()
        try:
            r = self.sc.get(f"{self.API}/{ep}", timeout=12)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 403:
                print(f"[!] SofaScore API 403 Forbidden on {ep}. Пауза перед следующей попыткой...")
            return None
        except Exception:
            return None

    def get_live_matches(self):
        res = self._get("sport/football/events/live")
        return res.get("events", []) if res else []

    def get_match_incidents(self, mid) -> List[Dict]:
        res = self._get(f"event/{mid}/incidents")
        return res.get("incidents", []) if res else []

    def get_match_statistics(self, mid) -> Optional[Dict]:
        return self._get(f"event/{mid}/statistics")


class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def _post(self, method: str, payload: Dict):
        try:
            return requests.post(f"{self.base}/{method}", json=payload, timeout=10).json()
        except Exception as e:
            return {"ok": False, "description": str(e)}

    def test_and_notify(self):
        strategies_txt = "\n".join(f"  {s.emoji} {s.name}" for s in STRATEGIES)
        resp = self._post("sendMessage", {
            "chat_id": self.chat_id, "parse_mode": "HTML",
            "text": (f"🤖 <b>PREDATOR ZETA v{Config.VERSION} ЗАПУЩЕН НА СЕРВЕРЕ!</b>\n"
                     f"🟢 Сервер статус: <b>ONLINE</b>\n"
                     f"💰 Ставка: {Config.FLAT_STAKE} {Config.CURRENCY}\n\n"
                     f"<b>Активные стратегии:</b>\n{strategies_txt}\n\n"
                     f"🔍 <i>Начинаю непрерывный сканинг Live-матчей...</i>")
        })
        return resp.get("ok", False)

    def send_signal(self, strategy: BaseStrategy, info: dict, signal: dict, match_id: str) -> int:
        url = f"https://www.sofascore.com/event/{match_id}"
        text = (
            f"{strategy.emoji} <b>СТРАТЕГИЯ: {strategy.name}</b>\n\n"
            f"🏆 <b>{info['league']}</b>\n"
            f"🏟 <b>{info['home']}</b> vs <b>{info['away']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 <b>Вход:</b> {info['minute']} • Счёт: <b>{info['score_h']}:{info['score_a']}</b>\n"
            f"💰 <b>СТАВКА:</b> {signal['selection']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ <i>Отслеживаем результат...</i>"
        )
        resp = self._post("sendMessage", {
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🔗 Открыть на SofaScore", "url": url}]]},
        })
        return resp.get("result", {}).get("message_id", 0)


class LiveMonitor:
    def __init__(self, token, chat_id):
        self.fetcher = SofaFetcher()
        self.bankroll = BankrollManager()
        self.tg = TelegramNotifier(token, chat_id)
        self.sent_signals: Dict[str, float] = {}
        self.cycle = 0

    def _get_minute(self, match):
        code = (match.get("status") or {}).get("code", 0)
        td = match.get("time") or {}
        m = td.get("currentMinute")
        m = int(m) if m is not None else 0
        if code in (100, 12):
            return m if m >= 90 else 90
        if code == 31:
            return 45
        if not m and td.get("currentPeriodStartTimestamp"):
            elapsed = int((time.time() - td["currentPeriodStartTimestamp"]) / 60)
            return 45 + elapsed if code == 7 else elapsed
        return m

    def run(self):
        start_dummy_server()
        if not self.tg.test_and_notify():
            print(cl("\n[!] Внимание: Сообщение в Telegram не отправлено. Проверьте BOT_TOKEN и CHAT_ID.", "YE"))

        print(cl("\n==================================================", "CY"))
        print(cl(f"   PREDATOR ZETA v{Config.VERSION} ЗАПУЩЕН", "GR"))
        print(cl("==================================================\n", "CY"))

        while True:
            try:
                self.cycle += 1
                self._run_cycle()
                time.sleep(Config.CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("Остановка по команде пользователя.")
                break
            except Exception as e:
                print(cl(f"[CRITICAL ERROR] {e}", "RE"))
                time.sleep(10)

    def _run_cycle(self):
        matches = self.fetcher.get_live_matches()
        if not matches:
            print(cl(f"[{datetime.now().strftime('%H:%M:%S')}] 💤 Live матчей нет или временно заблокировано...", "YE"))
            return

        print(cl(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ Сканируем Live матчей: {len(matches)}", "CY"))
        for match in matches:
            mid = str(match.get("id"))
            minute = self._get_minute(match)
            match["_minute"] = minute

            exclusion_reason = is_excluded_match(match)
            if exclusion_reason:
                continue

            incidents = self.fetcher.get_match_incidents(mid)
            stats_data = self.fetcher.get_match_statistics(mid)

            for strategy in STRATEGIES:
                signal = strategy.scan(match, incidents, stats_data)
                if signal and in_send_window(minute):
                    home_name = (match.get("homeTeam") or {}).get("name", "Unknown")[:18]
                    away_name = (match.get("awayTeam") or {}).get("name", "Unknown")[:18]
                    league_name = (match.get("tournament") or {}).get("name", "League")
                    cur_h = int(((match.get("homeScore") or {}).get("current")) or 0)
                    cur_a = int(((match.get("awayScore") or {}).get("current")) or 0)
                    info = {
                        "home": home_name, "away": away_name, "league": league_name,
                        "score_h": cur_h, "score_a": cur_a,
                        "minute": f"{minute}'"
                    }
                    if mid not in self.sent_signals:
                        print(cl(f"🔥 [{strategy.name}] {league_name}: {home_name} vs {away_name} ({minute}')", "GR"))
                        msg_id = self.tg.send_signal(strategy, info, signal, mid)
                        if msg_id:
                            self.bankroll.place_bet(mid, msg_id, strategy, signal, info)
                            self.sent_signals[mid] = time.time()


if __name__ == "__main__":
    LiveMonitor(Config.BOT_TOKEN, Config.CHAT_ID).run()
