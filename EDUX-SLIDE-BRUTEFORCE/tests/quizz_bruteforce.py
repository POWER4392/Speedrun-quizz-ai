import json
import os
import random

from playwright.sync_api import Page

LOGIN_URL = "https://edux.cmcu.edu.vn/login"
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
EXTERNAL_ANSWERS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "answers.json")

def get_external_answer(question_text: str) -> str | None:
    if not os.path.exists(EXTERNAL_ANSWERS_PATH):
        return None
    try:
        with open(EXTERNAL_ANSWERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[WARN] Error reading answers.json: {e}")
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
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not email:
        email = input("Enter EDUX email: ").strip()
    if not password:
        password = input("Enter EDUX password: ").strip()

    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as env_file:
        env_file.write(f"EDUX_EMAIL={email}\n")
        env_file.write(f"EDUX_PASSWORD={password}\n")
        if gemini_key:
            env_file.write(f"GEMINI_API_KEY={gemini_key}\n")

    os.environ["EDUX_EMAIL"] = email
    os.environ["EDUX_PASSWORD"] = password
    return email, password


def extract_answer_texts(answers_locator) -> list[str]:
        return answers_locator.evaluate_all(
                """
                nodes => nodes.map(node => {
                    const letter = node.querySelector('span.font-bold')?.innerText?.trim() || '';
                    const text = node.querySelector('div.prose p')?.innerText?.trim() || '';
                    return `${letter} ${text}`.trim();
                })
                """
        )


def test_wait_for_user_login(page: Page) -> None:
    email, password = ensure_login_env()
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    
    try:
        page.get_by_role("button", name="Microsoft").click(timeout=5000)
        print("\n[INFO] Đã tự động click nút đăng nhập Microsoft.")
        
        if email and password:
            print("[INFO] Đang tự động điền tài khoản Microsoft...")
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
        
    print("[INFO] Vui lòng hoàn thành các bước đăng nhập trên trình duyệt (điền mật khẩu, xác thực 2 bước...).")
    print("[INFO] Sau khi bạn đã truy cập được vào màn hình bài giảng, hãy ấn phím Enter ở đây để Tool bắt đầu chạy!\n")
    input()

    wrong_answers: dict[str, set[str]] = {}
    question_answer_cache: dict[str, list[str]] = {}

    no_question_button = page.get_by_role("button", name="Không có câu hỏi")
    answer_button = page.locator("text=/Trả lời (trên lớp|câu hỏi)/i").last
    check_button = page.get_by_role("button", name="Kiểm tra")
    next_button = page.get_by_role("button", name="Câu tiếp theo")
    retry_button = page.get_by_role("button", name="Thử lại")
    next_page_button = page.get_by_role("button", name="Trang sau")
    question_locator = page.locator("p.my-3.text-gray-800.leading-relaxed").first
    answers_locator = page.locator(
        "div.flex.items-center.space-x-6.p-8.rounded-xl.border-2.transition-colors.cursor-pointer.min-h-\\[80px\\]"
    )

    while not page.is_closed():
        if no_question_button.is_visible():
            next_page_button.click()
            print("[INFO] No question on this slide. Clicked 'Trang sau'.")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(200)
            continue

        if not question_locator.is_visible():
            try:
                answer_button.wait_for(state="visible", timeout=3000)
                answer_button.click()
            except Exception:
                page.wait_for_timeout(1000)
                continue

        try:
            question_locator.wait_for(state="visible", timeout=10000)
        except Exception:
            print("[WARN] Question not visible yet. Retrying loop.")
            page.wait_for_timeout(200)
            continue

        question_text = question_locator.inner_text().strip()
        print(f"[INFO] Question: {question_text}")

        answer_texts = question_answer_cache.get(question_text)
        if answer_texts is None:
            try:
                answers_locator.first.wait_for(state="visible", timeout=3000)
                answer_texts = extract_answer_texts(answers_locator)
            except Exception:
                textarea_locator = page.locator("textarea, input[type='text']")
                if textarea_locator.count() > 0 and textarea_locator.first.is_visible():
                    answer_texts = []
                else:
                    print("[WARN] Answers not visible yet. Retrying loop.")
                    page.wait_for_timeout(200)
                    continue

            question_answer_cache[question_text] = answer_texts

        answer_count = len(answer_texts)
        print(f"[INFO] Answers found: {answer_count}")

        if answer_count == 0:
            print("[INFO] Phát hiện Câu Tự Luận! Điền bừa dấu chấm để qua nhanh.")
            textarea_locator = page.locator("textarea, input[type='text']").first
            if textarea_locator.is_visible():
                textarea_locator.fill(".")
                page.wait_for_timeout(200)
                if check_button.is_visible():
                    check_button.click()
            page.wait_for_timeout(1000)
            continue
        target_answer = os.environ.get("AUTO_ANSWER_TEXT", "").strip()
        external_ans = get_external_answer(question_text)
        if external_ans:
            target_answer = external_ans

        clicked_answer = False
        chosen_answer_text = ""

        if target_answer:
            print(f"[INFO] Target answer: {target_answer}")
            chosen_index = None
            if len(target_answer) == 1 and target_answer.upper() in {"A", "B", "C", "D"}:
                for i, text in enumerate(answer_texts):
                    if text.upper().startswith(target_answer.upper()):
                        chosen_index = i
                        break
            else:
                target_lower = target_answer.lower()
                for i, text in enumerate(answer_texts):
                    if target_lower in text.lower():
                        chosen_index = i
                        break

            if chosen_index is not None:
                answers_locator.nth(chosen_index).click()
                clicked_answer = True
                chosen_answer_text = answer_texts[chosen_index]
            else:
                print(f"[WARN] Could not find matching option for target: {target_answer}")

        if not clicked_answer and answer_count > 0:
            tried_for_question = wrong_answers.get(question_text, set())
            next_index = next(
                (i for i, text in enumerate(answer_texts) if text not in tried_for_question),
                None,
            )
            if next_index is None:
                tried_for_question.clear()
                next_index = 0

            chosen_answer_text = answer_texts[next_index]
            print(f"[INFO] Fallback to Brute-force. Pick: {chosen_answer_text}")
            answers_locator.nth(next_index).click()
            clicked_answer = True
        elif not clicked_answer:
            print("[WARN] No answers available to click.")

        if clicked_answer:
            check_button.click()

            try:
                page.wait_for_function(
                    """
                    () => {
                      const labels = ['Trang sau', 'Câu tiếp theo', 'Thử lại'];
                      return labels.some(label => {
                        const btn = Array.from(document.querySelectorAll('button'))
                          .find(b => (b.textContent || '').trim() === label);
                        return btn && !btn.disabled && btn.offsetParent !== null;
                      });
                    }
                    """,
                    timeout=1200,
                )

                if next_page_button.is_visible():
                    next_page_button.click()
                    print("[INFO] Đúng! Clicked 'Trang sau'.")
                elif next_button.is_visible():
                    next_button.click()
                    print("[INFO] Đúng! Clicked 'Cau tiep theo'.")
                elif retry_button.is_visible():
                    retry_button.click()
                    if chosen_answer_text:
                        wrong_answers.setdefault(question_text, set()).add(chosen_answer_text)
                        print(f"[INFO] Marked wrong answer: {chosen_answer_text}")
                else:
                    pass
            except Exception:
                pass

        page.wait_for_timeout(25)

    # Keep this test non-failing while we are still wiring selectors.
    print(f"[INFO] Current URL after click: {page.url}")
    print("[INFO] Browser will stay open. Close the browser window to finish.")
    page.wait_for_event("close")
