import os
import re
import time
import requests
import urllib.parse
import threading
import sys
from playwright.sync_api import Page

bot_paused = False

def input_listener():
    global bot_paused
    while True:
        try:
            sys.stdin.readline()
            bot_paused = not bot_paused
            if bot_paused:
                print("\n========================================================")
                print("[TẠM DỪNG] Bot đang nghỉ ngơi! Bấm Enter lần nữa để chạy tiếp.")
                print("========================================================\n")
            else:
                print("\n========================================================")
                print("[TIẾP TỤC] Bot đã thức dậy và tiếp tục cày!")
                print("========================================================\n")
        except:
            break

LOGIN_URL = "https://edux.cmcu.edu.vn/login"
ENV_PATH = os.path.join(os.path.dirname(__file__), "EDUX-SLIDE-BRUTEFORCE", ".env")

# Global caches for Slide mode
slide_wrong_answers: dict[str, set[str]] = {}
slide_last_clicked_text: str = ""

import json
EXTERNAL_ANSWERS_PATH = os.path.join(os.path.dirname(__file__), "answers.json")

def get_external_answer(question_text: str) -> str | None:
    if not os.path.exists(EXTERNAL_ANSWERS_PATH):
        return None
    try:
        with open(EXTERNAL_ANSWERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    qt_lower = question_text.lower()
    for key, val in data.items():
        if key.lower() in qt_lower:
            return str(val).strip()
    return None

def load_env_file() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as env_file:
        for line in env_file:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value

def ensure_login_env() -> tuple[str, str]:
    load_env_file()
    email = os.environ.get("EDUX_EMAIL", "").strip()
    password = os.environ.get("EDUX_PASSWORD", "").strip()
    return email, password

def _call_gemini(prompt: str, api_key: str, result_list: list, idx: int):
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt)
        result_list[idx] = response.text.strip()
    except:
        result_list[idx] = None

def _call_public_endpoint(url_template: str, prompt: str, result_list: list, idx: int):
    try:
        url = url_template.format(prompt=urllib.parse.quote(prompt))
        response = requests.get(url, timeout=15)
        if response.status_code == 200 and response.text.strip():
            try:
                data = response.json()
                if isinstance(data, dict):
                    if "response" in data:
                        result_list[idx] = str(data["response"]); return
                    if "result" in data:
                        result_list[idx] = str(data["result"]); return
            except:
                pass
            result_list[idx] = response.text.strip()
    except:
        result_list[idx] = None

# ─── Google Search Fallback ────────────────────────────────────────────────────

def search_google(query: str, num_results: int = 5) -> str:
    """Scrape Google Search và trả về đoạn snippet ngắn gọn nhất."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        }
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=vi&num={num_results}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return ""
        text = resp.text
        # Lấy featured snippet (thẻ div data-attrid hoặc block đầu tiên)
        snippets = re.findall(r'<span[^>]*>([^<]{40,300})</span>', text)
        # Lọc bỏ các đoạn HTML noise
        clean = []
        for s in snippets:
            s2 = re.sub(r'<[^>]+>', '', s).strip()
            if len(s2) > 30 and not s2.startswith('http') and 'javascript' not in s2.lower():
                clean.append(s2)
        if not clean:
            return ""
        # Nối tối đa 3 snippet đầu
        result = ' | '.join(clean[:3])
        print(f"[GOOGLE] Kết quả tìm kiếm: {result[:120]}...")
        return result
    except Exception as e:
        print(f"[GOOGLE] Lỗi tìm kiếm: {e}")
        return ""

# ─── Gemini Vision ─────────────────────────────────────────────────────────────

def google_lens_search(page, image_bytes: bytes) -> str:
    """Upload ảnh lên Google Lens qua Playwright, trả về text kết quả Google tìm được."""
    import tempfile, os as _os
    tmp_path = ""
    lens_page = None
    try:
        # Lưu ảnh ra file tạm
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name

        print("[LENS] Đang upload ảnh lên Google Lens...")
        lens_page = page.context.new_page()
        lens_page.goto("https://lens.google.com/", wait_until="domcontentloaded", timeout=15000)

        # Click nút upload ảnh (icon camera / upload)
        try:
            lens_page.get_by_role("button", name="Search by image").click(timeout=5000)
        except:
            try:
                lens_page.locator("[data-action='upload'], input[type='file']").first.wait_for(timeout=5000)
            except:
                pass

        # Upload file
        file_input = lens_page.locator("input[type='file']").first
        file_input.set_input_files(tmp_path)
        lens_page.wait_for_timeout(4000)  # Chờ Google xử lý

        # Lấy tất cả text kết quả từ trang
        result_text = lens_page.evaluate("""
            () => {
                const selectors = [
                    '[data-docid]', '.UAiK1e', '.vUMiOe', '.Wr8T0d',
                    'h3', '.LC20lb', '.BNeawe'
                ];
                const texts = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const t = el.innerText?.trim();
                        if (t && t.length > 5 && !texts.includes(t)) texts.push(t);
                    });
                }
                return texts.slice(0, 10).join(' | ');
            }
        """)

        if result_text and result_text.strip():
            print(f"[LENS] Kết quả Google Lens: {result_text[:150]}...")
            return result_text.strip()
        print("[LENS] Google Lens không trả về kết quả.")
        return ""
    except Exception as e:
        print(f"[LENS] Lỗi Google Lens: {e}")
        return ""
    finally:
        if lens_page:
            try: lens_page.close()
            except: pass
        if tmp_path and _os.path.exists(tmp_path):
            try: _os.remove(tmp_path)
            except: pass

def extract_question_image(container) -> bytes | None:
    """Tìm và download hình ảnh đầu tiên trong container câu hỏi."""
    try:
        imgs = container.locator("img").all()
        for img in imgs:
            src = img.get_attribute("src") or ""
            if not src or 'icon' in src.lower() or 'logo' in src.lower():
                continue
            if src.startswith("data:image"):
                import base64
                _, b64data = src.split(",", 1)
                return base64.b64decode(b64data)
            if src.startswith("http"):
                resp = requests.get(src, timeout=8)
                if resp.status_code == 200:
                    return resp.content
        return None
    except:
        return None

def _extract_letter(raw: str) -> str | None:
    """Trích xuất chữ cái A-D từ câu trả lời AI một cách nghiêm ngặt."""
    text = raw.strip().upper()
    # Ưu tiên: chữ đứng đầu câu hoặc sau dấu :/-/.
    strict = re.search(r'(?:^|[\:\-\.\s])\s*([A-D])(?:\s*[\.\)\:\s]|$)', text)
    if strict:
        return strict.group(1)
    # Fallback: câu trả lời rất ngắn (< 10 ký tự)
    if len(text) <= 10:
        m = re.search(r'[A-D]', text)
        return m.group(0) if m else None
    return None

def ask_ai(prompt: str, is_multiple_choice: bool = True, force_public_api: bool = False) -> str | None:
    """Chạy song song nhiều AI, tự động thử lại nếu chưa đồng thuận (tối đa 5 lần)."""
    keys_str = os.environ.get("GEMINI_API_KEY", "")
    api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]

    public_endpoints = [
        "https://text.pollinations.ai/{prompt}",
        "https://text.pollinations.ai/{prompt}?model=openai",
        "https://text.pollinations.ai/{prompt}?model=mistral",
    ]

    MAX_RETRIES = 5
    last_votes: dict[str, int] = {}

    for attempt in range(1, MAX_RETRIES + 1):
        workers: list[threading.Thread] = []
        results: list = []

        if not force_public_api:
            for key in api_keys[:3]:
                idx = len(results)
                results.append(None)
                workers.append(threading.Thread(target=_call_gemini, args=(prompt, key, results, idx)))

        for ep in public_endpoints:
            idx = len(results)
            results.append(None)
            workers.append(threading.Thread(target=_call_public_endpoint, args=(ep, prompt, results, idx)))

        if not workers:
            return None

        print(f"[AI] Lần {attempt}/{MAX_RETRIES}: Hỏi {len(workers)} nguồn AI cùng lúc...")
        for t in workers:
            t.start()
        # Timeout ngắn hơn ở lần đầu, lâu hơn ở lần sau
        _timeout = 8 if attempt <= 2 else 12
        for t in workers:
            t.join(timeout=_timeout)

        if is_multiple_choice:
            votes: dict[str, int] = {}
            for raw in results:
                if not raw:
                    continue
                letter = _extract_letter(raw)
                if letter:
                    votes[letter] = votes.get(letter, 0) + 1

            last_votes = dict(votes)

            if not votes:
                print(f"[AI] Lần {attempt}: Không AI nào trả lời được. Thử lại...")
                continue

            winner = max(votes, key=lambda k: votes[k])
            total_votes = sum(votes.values())
            vote_summary = ", ".join(f"{k}:{v}" for k, v in sorted(votes.items()))

            # Cần ít nhất 2 phiếu khi có >= 3 nguồn AI hoạt động
            if votes[winner] < 2 and total_votes >= 3:
                print(f"[AI] Lần {attempt}: [{vote_summary}] — Chưa đồng thuận. Thử lại...")
                continue

            print(f"[AI] Chốt sau {attempt} lần: [{vote_summary}] ({total_votes} phiếu) => {winner}")
            return winner

        else:
            # Tự luận: lấy câu trả lời ngắn gọn nhất
            essay_answers = [r.strip() for r in results if r and r.strip()]
            if not essay_answers:
                print(f"[AI] Lần {attempt}: Không AI nào trả lời tự luận. Thử lại...")
                continue
            filtered = [a for a in essay_answers if 5 <= len(a) <= 300]
            pool = filtered if filtered else essay_answers
            best = min(pool, key=len)
            print(f"[AI] Tự luận (lần {attempt}): {len(pool)} đáp án → chọn ngắn nhất ({len(best)} ký tự): {best[:60]}...")
            return best

    # Hết MAX_RETRIES — ép chọn đáp án nhiều phiếu nhất trong lần cuối
    if is_multiple_choice and last_votes:
        winner = max(last_votes, key=lambda k: last_votes[k])
        print(f"[AI] Hết {MAX_RETRIES} lần thử. Ép chọn đáp án phổ biến nhất: {winner}")
        return winner

    # ─── Google Search Fallback ───────────────────────────────────────────────
    # Tách phần câu hỏi gốc từ prompt (lấy dòng sau "Câu hỏi:")
    q_match = re.search(r'Câu hỏi[:\s]+(.+?)(?:\nCác đáp án|\nHãy|$)', prompt, re.DOTALL)
    search_query = q_match.group(1).strip()[:150] if q_match else prompt[:150]
    print(f"[GOOGLE] AI không trả lời được. Đang tìm Google: '{search_query[:60]}...'")
    google_context = search_google(search_query)
    if google_context:
        enriched_prompt = f"[Thông tin bổ sung từ Google]: {google_context}\n\n{prompt}"
        print("[GOOGLE] Hỏi lại AI với context từ Google...")
        keys_str = os.environ.get("GEMINI_API_KEY", "")
        api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        results: list = []
        workers = []
        for key in api_keys[:2]:
            idx = len(results); results.append(None)
            workers.append(threading.Thread(target=_call_gemini, args=(enriched_prompt, key, results, idx)))
        for ep in ["https://text.pollinations.ai/prompt/{prompt}"]:
            idx = len(results); results.append(None)
            workers.append(threading.Thread(target=_call_public_endpoint, args=(ep, enriched_prompt, results, idx)))
        for t in workers: t.start()
        for t in workers: t.join(timeout=20)
        if is_multiple_choice:
            votes: dict[str, int] = {}
            for raw in results:
                if not raw: continue
                letter = _extract_letter(raw)
                if letter: votes[letter] = votes.get(letter, 0) + 1
            if votes:
                winner = max(votes, key=lambda k: votes[k])
                print(f"[GOOGLE] Fallback thành công → {winner}")
                return winner
        else:
            answers = [r.strip() for r in results if r and r.strip()]
            if answers:
                best = min(answers, key=len)
                print(f"[GOOGLE] Fallback tự luận → {best[:60]}")
                return best
    print("[GOOGLE] Fallback cũng thất bại. Bỏ qua câu này.")
    return None

def extract_test_options(options_locator) -> list[dict]:
    return options_locator.evaluate_all(
        """
        nodes => nodes.map(node => {
            const letter = node.querySelector('span.flex-shrink-0')?.innerText?.trim() || '';
            const text = node.querySelector('div.prose p')?.innerText?.trim() || '';
            const input = node.querySelector('input');
            const isChecked = input ? input.checked : false;
            return {letter, text, isChecked};
        })
        """
    )


def handle_slide(page: Page) -> None:
    """Xử lý siêu tốc phần bài giảng (slide) với mục tiêu 100+ câu/phút."""
    global slide_last_clicked_text

    # 1. Kiểm tra nhanh dialog đang mở
    btn_change = page.locator("text='Đổi câu hỏi'").last
    btn_check  = page.locator("text='Kiểm tra'").last

    # Đang trong dialog trả lời
    if btn_change.is_visible() or btn_check.is_visible():
        start_time = time.time()
        while time.time() - start_time < 6.0:
            if bot_paused:
                return

            # Kiểm tra xem có nút chuyển tiếp sẵn không (đã đúng từ trước)
            btn_next_q  = page.locator("text='Câu tiếp theo'").last
            btn_next_pg = page.locator("text='Trang sau'").last
            btn_next = btn_next_q if btn_next_q.is_visible() else btn_next_pg
            if btn_next.is_visible() and not btn_check.is_visible():
                btn_next.click(force=True)
                return

            # Nếu nút Thử lại đang hiện sẵn
            btn_retry = page.locator("text='Thử lại'").last
            if btn_retry.is_visible():
                btn_retry.click(force=True)

            if btn_check.is_visible():
                q_text = _read_question_text(page)
                wrong_set = slide_wrong_answers.setdefault(q_text, set()) if q_text else set()

                opt_info = _find_options_info(page)
                if not opt_info or opt_info.get("count", 0) == 0:
                    # Câu tự luận: điền nhanh dấu chấm
                    ta = page.locator("textarea, input[type='text']").first
                    if ta.is_visible():
                        ta.fill(".")
                        btn_check.click(force=True)
                        try:
                            page.wait_for_function(
                                "() => Array.from(document.querySelectorAll('button')).some(b => ['Câu tiếp theo', 'Trang sau', 'Thử lại'].includes((b.textContent||'').trim()) && !b.disabled)",
                                timeout=1200
                            )
                        except:
                            pass
                    return

                count = opt_info["count"]
                opts = opt_info["texts"]

                # 1. Ưu tiên answers.json
                chosen = -1
                if q_text:
                    ext = get_external_answer(q_text)
                    if ext:
                        ext_up = ext.strip().upper()
                        idx = {"A": 0, "B": 1, "C": 2, "D": 3}.get(ext_up, -1)
                        if idx != -1 and idx < count:
                            chosen = idx
                        else:
                            for i, t in enumerate(opts):
                                if ext.lower() in t.lower() and t not in wrong_set:
                                    chosen = i
                                    break

                # 2. Brute-force: chọn đáp án chưa thử
                if chosen == -1:
                    for i, t in enumerate(opts):
                        if t not in wrong_set:
                            chosen = i
                            break

                # 3. Hết đáp án -> reset và thử lại từ đầu
                if chosen == -1:
                    wrong_set.clear()
                    chosen = 0

                clicked_text = opts[chosen] if chosen < len(opts) else ""
                sel = opt_info["selector"]

                # Click chọn đáp án
                page.locator(sel).nth(chosen).click(force=True)
                # Click kiểm tra ngay lập tức
                btn_check.click(force=True)

                # Chờ kết quả xuất hiện qua wait_for_function siêu tốc (polling ~16ms)
                try:
                    res = page.wait_for_function(
                        """() => {
                            const btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
                            for (const b of btns) {
                                const t = (b.textContent || '').trim();
                                if ((t === 'Câu tiếp theo' || t === 'Trang sau' || t === 'Thử lại') && !b.disabled && b.offsetParent !== null) {
                                    return t;
                                }
                            }
                            return null;
                        }""",
                        timeout=1200
                    )
                    action = res.json_value()
                    if action in ('Câu tiếp theo', 'Trang sau'):
                        page.locator(f"text='{action}'").last.click(force=True)
                        print(f"[SLIDE ⚡] Đúng! Đã chuyển tiếp ({action}).")
                        return
                    elif action == 'Thử lại':
                        if clicked_text and q_text:
                            wrong_set.add(clicked_text)
                        page.locator("text='Thử lại'").last.click(force=True)
                        # Tiếp tục vòng lặp ngay lập tức để thử phương án tiếp theo!
                        continue
                except:
                    return
        return

    # 2. Slide không có câu hỏi -> Trang sau
    btn_no_q    = page.locator("text='Không có câu hỏi'").last
    btn_next_pg = page.locator("text='Trang sau'").last
    if btn_no_q.is_visible():
        if btn_next_pg.is_visible():
            btn_next_pg.click(force=True)
        return

    # 3. Nút mở câu hỏi ("Trả lời câu hỏi" / "Trả lời trên lớp")
    btn_ans_q = page.locator("text=/Trả lời (trên lớp|câu hỏi)/i").last
    if btn_ans_q.is_visible():
        btn_ans_q.click(force=True)
        try:
            page.locator("text='Kiểm tra', text='Đổi câu hỏi'").first.wait_for(state="visible", timeout=600)
        except:
            pass
        return

    # 4. Slide thường đang xem -> Trang sau hoặc Câu tiếp theo
    if btn_next_pg.is_visible():
        btn_next_pg.click(force=True)
        return

    btn_next_q = page.locator("text='Câu tiếp theo'").last
    if btn_next_q.is_visible():
        btn_next_q.click(force=True)
        return


def _read_question_text(page: Page) -> str:
    """Lấy text câu hỏi hiện tại cực nhanh qua evaluate."""
    try:
        return page.evaluate("""() => {
            const selectors = ["div[role='dialog'] p.my-3", "p.my-3", "div.prose p", "p.text-gray-800", "h3", "p"];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const t = (el.innerText || '').trim();
                    if (t.length > 8 && !t.includes('Đổi câu hỏi') && !t.includes('Kiểm tra') && !t.includes('Thử lại')) {
                        return t;
                    }
                }
            }
            return "";
        }""")
    except:
        return ""


def _find_options_info(page: Page) -> dict | None:
    """Tìm thông tin và selector đáp án trong 1 roundtrip duy nhất."""
    try:
        return page.evaluate("""() => {
            const selectors = [
                "div.relative.flex.items-center.space-x-2.p-2.border.rounded-lg.cursor-pointer",
                "div.flex.items-center.space-x-6.p-8.rounded-xl.border-2.transition-colors.cursor-pointer",
                "div[class*='rounded'][class*='cursor-pointer'][class*='border']",
                "div.flex.cursor-pointer",
                "label.cursor-pointer",
                "div[class*='option']"
            ];
            for (const sel of selectors) {
                const els = Array.from(document.querySelectorAll(sel));
                if (els.length >= 2) {
                    return {
                        selector: sel,
                        count: els.length,
                        texts: els.map(el => (el.innerText || '').trim().replace(/\\n/g, ' '))
                    };
                }
            }
            return null;
        }""")
    except:
        return None


def _find_options(page: Page):
    """Tìm locator các đáp án trắc nghiệm bằng nhiều selector (fallback)."""
    for sel in [
        "div.relative.flex.items-center.space-x-2.p-2.border.rounded-lg.cursor-pointer",
        "div.flex.items-center.space-x-6.p-8.rounded-xl.border-2.transition-colors.cursor-pointer",
        "div[class*='rounded'][class*='cursor-pointer'][class*='border']",
        "div.flex.cursor-pointer",
        "label.cursor-pointer",
        "div[class*='option']",
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() >= 2:
                return loc
        except:
            pass
    return None



def solve_test_full(page: Page) -> None:
    print("\n[TEST] Phát hiện Bài Tập (Test)! Bắt đầu làm bài tự động...")
    try:
        page.locator("div[role='dialog'][data-slot='dialog-content']").wait_for(state="visible", timeout=3000)
        container = page.locator("div[role='dialog'][data-slot='dialog-content']")
    except:
        container = page

    options_locator = container.locator("div.relative.flex.items-center.space-x-2.p-2.border.rounded-lg.cursor-pointer")
    textarea_locator = container.locator("textarea, input[type='text']")
    next_button = container.get_by_role("button", name="Câu tiếp")
    prev_button = container.get_by_role("button", name="Câu trước")
    submit_button = container.get_by_role("button", name="Nộp bài")
    skipped_questions: set[str] = set()

    def get_question_label():
        try:
            return container.evaluate(
                """
                (node) => {
                  const label = Array.from(node.querySelectorAll('span, h3, div')).find(s => (s.textContent || '').trim().startsWith('Câu '));
                  if (!label) return '';
                  return label.parentElement ? label.parentElement.innerText.trim() : label.textContent.trim();
                }
                """
            )
        except:
            return ""

    def run_pass():
        nonlocal skipped_questions
        print(f"\nVÒNG 1 (GIẢI BÀI BẰNG GEMINI AI)")
        
        while not page.is_closed():
            if bot_paused:
                page.wait_for_timeout(1000)
                continue
            page.wait_for_timeout(1000)
            label_text = get_question_label()
            
            q_num_match = re.search(r'Câu\s+(\d+)', label_text)
            q_num = q_num_match.group(1) if q_num_match else "?"
            print(f"--- Câu {q_num} ---")
            
            # Check True/False Grid
            tf_true_elements = container.get_by_text("Đúng", exact=True)
            tf_sai_elements = container.get_by_text("Sai", exact=True)
            
            if tf_true_elements.count() > 1 and tf_sai_elements.count() > 1:
                print("[TEST] Phát hiện dạng bài True/False nhiều mệnh đề!")
                count = tf_true_elements.count()
                for i in range(count):
                    true_element = tf_true_elements.nth(i)
                    row_text = true_element.evaluate('''(_el) => {
                        let curr = _el.parentElement;
                        for (let k=0; k<6; k++) {
                            if (curr && curr.innerText.length > 20) {
                                return curr.innerText.replace(/Đúng|Sai|True|False/g, '').replace(/\\n/g, ' ').replace(/\\s+/g, ' ').trim();
                            }
                            curr = curr.parentElement;
                        }
                        return "";
                    }''')
                    
                    print(f"[TEST] Mệnh đề {i+1}: {row_text[:50]}...")
                    prompt = f"Ngữ cảnh câu hỏi chính: {label_text}\nĐánh giá mệnh đề sau: '{row_text}'\nMệnh đề này Đúng hay Sai? Trả lời CHỈ 1 TỪ DUY NHẤT là 'Đúng' hoặc 'Sai'."
                    ai_ans = ask_ai(prompt, is_multiple_choice=False)
                    
                    if ai_ans and "đúng" in ai_ans.lower():
                        print(f"       => AI chọn: ĐÚNG")
                        tf_true_elements.nth(i).click(force=True)
                    else:
                        print(f"       => AI chọn: SAI")
                        tf_sai_elements.nth(i).click(force=True)
                        
                    page.wait_for_timeout(200)
                    
                page.wait_for_timeout(500)
                current_label = label_text
                if next_button.is_visible() and not next_button.is_disabled():
                    next_button.click(force=True)
                    page.wait_for_timeout(1500)
                    new_label = get_question_label()
                    if new_label == current_label:
                        break
                else:
                    break
                continue
            
            try:
                options_locator.first.wait_for(state="visible", timeout=2000)
            except:
                pass
            
            has_options = options_locator.count() > 0
            ai_ans = None  # reset cho mỗi câu

            # ── Google Lens: đọc ảnh trong câu hỏi (nếu có) ─────────────────
            image_context = ""
            img_bytes = extract_question_image(container)
            if img_bytes:
                print("[LENS] Phát hiện hình ảnh trong câu hỏi. Đang tra Google Lens...")
                image_context = google_lens_search(page, img_bytes)

            if has_options:
                options = extract_test_options(options_locator)
                prompt = f"Câu hỏi: {label_text}\n"
                if image_context:
                    prompt += f"[Hình ảnh đính kèm]: {image_context}\n"
                prompt += "Các đáp án:\n"
                for opt in options:
                    prompt += f"- {opt['letter']} {opt['text']}\n"
                prompt += "\nHãy tìm đáp án đúng nhất. CHỈ trả về đúng 1 chữ cái (A hoặc B hoặc C hoặc D) của đáp án đó."
                
                ai_ans = ask_ai(prompt, is_multiple_choice=True)
                if ai_ans:
                    target_index = -1
                    for i, opt in enumerate(options):
                        # Match linh hoạt: "A", "A.", "A)", "A " đều khớp
                        letter = opt["letter"].strip().upper().rstrip(".)")
                        if letter == ai_ans.upper():
                            target_index = i
                            break
                    # Fallback: ghép letter+text rồi check startswith
                    if target_index == -1:
                        for i, opt in enumerate(options):
                            opt_str = (opt["letter"] + " " + opt["text"]).strip().upper()
                            if re.match(rf'^{ai_ans}[\s\.\)\:]', opt_str):
                                target_index = i
                                break
                    # Fallback cuối: map theo thứ tự A=0, B=1, C=2, D=3
                    if target_index == -1:
                        letter_map = {"A": 0, "B": 1, "C": 2, "D": 3}
                        target_index = letter_map.get(ai_ans.upper(), -1)
                        if target_index >= len(options):
                            target_index = -1
                            
                    if target_index != -1:
                        print(f"-> AI chọn: {ai_ans} (index {target_index})")
                        if not options[target_index]["isChecked"]:
                            options_locator.nth(target_index).click()
                    else:
                        print(f"[WARN] Không tìm thấy đáp án {ai_ans} trong danh sách options!")
            else:
                if textarea_locator.count() > 0 and textarea_locator.first.is_visible():
                    prompt = f"Câu hỏi tự luận: {label_text}."
                    if image_context:
                        prompt += f" [Hình ảnh đính kèm]: {image_context}."
                    prompt += " Hãy trả lời cực kỳ ngắn gọn bằng tiếng Việt (khoảng 1 câu)."
                    ai_ans = ask_ai(prompt, is_multiple_choice=False)
                    if ai_ans:
                        print(f"-> AI Tự luận: {ai_ans}")
                        textarea_locator.first.fill(ai_ans)

            current_label = label_text
            answered = ai_ans is not None
            if next_button.is_visible() and not next_button.is_disabled():
                next_button.click()
                page.wait_for_timeout(1500)
                new_label = get_question_label()
                if new_label == current_label:
                    break
                if not answered:
                    skipped_questions.add(current_label)
                    print(f"[SKIP] Đã đánh dấu câu bỏ qua: {current_label[:40]}...")
            else:
                break

    run_pass()

    # ─── Quá trình xử lý câu bỏ qua ─────────────────────────────────────────────
    if skipped_questions:
        print(f"\n[RETRY] Có {len(skipped_questions)} câu bỏ qua. Đang quay lại...")
        # Quay về câu đầu bằng cách nhấn Prev liên tục
        for _ in range(50):
            try:
                pb = container.get_by_role("button", name="Câu trước")
                if pb.is_visible() and not pb.is_disabled():
                    pb.click()
                    page.wait_for_timeout(500)
                else:
                    break
            except:
                break
        # Giải lại các câu bỏ qua
        retry_done: set[str] = set()
        for _ in range(len(skipped_questions) * 3):
            if not page.is_closed():
                lbl = get_question_label()
                if lbl in skipped_questions and lbl not in retry_done:
                    print(f"[RETRY] Đang giải lại: {lbl[:50]}...")
                    # Chạy lại logic giải
                    opts = options_locator.count()
                    if opts > 0:
                        options = extract_test_options(options_locator)
                        prompt = f"Câu hỏi: {lbl}\nCác đáp án:\n"
                        for opt in options:
                            prompt += f"- {opt['letter']} {opt['text']}\n"
                        prompt += "\nCHỈ trả về 1 chữ cái A/B/C/D."
                        ai_ans = ask_ai(prompt, is_multiple_choice=True)
                        if ai_ans:
                            for i, opt in enumerate(options):
                                opt_str = (opt["letter"] + " " + opt["text"]).strip().upper()
                                if opt_str.startswith(f"{ai_ans}.") or opt_str.startswith(f"{ai_ans} ") or opt_str.startswith(f"{ai_ans})"):
                                    options_locator.nth(i).click()
                                    print(f"[RETRY] Đã chọn: {ai_ans}")
                                    break
                    retry_done.add(lbl)
                # Chuyển câu tiếp
                nb = container.get_by_role("button", name="Câu tiếp")
                if nb.is_visible() and not nb.is_disabled():
                    nb.click()
                    page.wait_for_timeout(1000)
                else:
                    break
            if retry_done >= skipped_questions:
                break
        print(f"[RETRY] Hoàn tất xử lý {len(retry_done)} câu bỏ qua.")

    print("\n[TEST] Hoàn tất Bài tập! Chuẩn bị nộp bài...")
    page.wait_for_timeout(500)
    try:
        submit_button.wait_for(state="visible", timeout=5000)
        submit_button.click()
        print("[TEST] Đã Nộp Bài.")
        page.wait_for_timeout(3000)
    except:
        print("[WARN] Không tìm thấy nút Nộp bài. Bỏ qua.")

def test_master_bot(page: Page) -> None:
    email, password = ensure_login_env()
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    
    try:
        page.get_by_role("button", name="Microsoft").click(timeout=5000)
        print("\n[INFO] Đã click đăng nhập Microsoft.")
        
        if email and password:
            page.locator("input[type='email']").wait_for(state="visible", timeout=10000)
            page.locator("input[type='email']").fill(email)
            page.locator("input[type='submit']").click()
            
            page.locator("input[type='password']").wait_for(state="visible", timeout=10000)
            page.locator("input[type='password']").fill(password)
            page.wait_for_timeout(500)
            page.locator("input[type='submit']").click()
            
            try:
                page.locator("input[type='submit']").wait_for(state="visible", timeout=5000)
                page.locator("input[type='submit']").click()
            except:
                pass
                
    except Exception as e:
        print("\n[WARN] Không thể tự động điền Microsoft, vui lòng tự thao tác bằng tay.")

    print("==========================================================================")
    print("                      MASTER BOT - AUTO SOLVER EDUX                       ")
    print("==========================================================================")
    print("[INFO] Bot sẽ tự động chạy khi phát hiện bài giảng/bài tập.")
    print("MẸO: Bất cứ khi nào muốn TẠM DỪNG bot, hãy bấm phím Enter ở cửa sổ này.")
    print("==========================================================================\n")
    
    threading.Thread(target=input_listener, daemon=True).start()
    
    last_action_time = time.time()
    
    while not page.is_closed():
        if bot_paused:
            page.wait_for_timeout(1000)
            continue
        try:
            # Bài tập (Test) — Nộp bài hoặc Câu tiếp (không có Đổi câu hỏi)
            btn_change_q = page.locator("text='Đổi câu hỏi'").last
            btn_submit   = page.locator("text='Nộp bài'").last
            btn_next_q   = page.locator("text='Câu tiếp'").last
            is_test = (btn_submit.is_visible() or btn_next_q.is_visible()) and not btn_change_q.is_visible()

            # Slide / Dialog — nhận ra bằng các nút đặc trưng của bài giảng
            btn_no_q       = page.locator("text='Không có câu hỏi'").last
            btn_ans_q      = page.locator("text=/Trả lời (trên lớp|câu hỏi)/i").last
            btn_check      = page.locator("text='Kiểm tra'").last
            btn_next_pg    = page.locator("text='Trang sau'").last
            btn_next_q_slide = page.locator("text='Câu tiếp theo'").last
            btn_retry      = page.locator("text='Thử lại'").last
            is_slide = (
                btn_change_q.is_visible() or
                btn_no_q.is_visible() or btn_ans_q.is_visible() or
                btn_next_pg.is_visible() or btn_retry.is_visible() or
                btn_next_q_slide.is_visible() or btn_check.is_visible()
            ) and not is_test

            if is_test:
                solve_test_full(page)
                last_action_time = time.time()
                page.wait_for_timeout(300)
            elif is_slide:
                handle_slide(page)
                last_action_time = time.time()
                page.wait_for_timeout(25)
            else:
                page.wait_for_timeout(200)
                if time.time() - last_action_time > 30:
                    print("[INFO] Đang chờ bạn mở bài học...")
                    last_action_time = time.time()
        except Exception as e:
            page.wait_for_timeout(200)



