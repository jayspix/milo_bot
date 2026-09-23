import random
import re
import time
import threading
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8982012958:AAEYxhk9rbm7WcLntD41OpxADY7HW08qSDA"
TELEGRAM_CHAT_ID = "7493468196"

# --- COUNTERS ---
dismiss_count = 0
entry_count = 0
count_lock = threading.Lock()

# Track the ID of the last status message so we can delete it
last_status_message_id = None
status_lock = threading.Lock()


def send_telegram(message, parse_mode=None):
    """Send a Telegram message. Returns the message_id or None."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Telegram send failed: {data}")
        return None
    except Exception as e:
        print(f"Telegram send failed: {type(e).__name__}: {e}")
        return None


def delete_telegram_message(message_id):
    """Delete a Telegram message by ID. Silently fails if already gone."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram delete failed: {type(e).__name__}: {e}")


def send_status(phone_number, dismisses, entries):
    """
    Send (or replace) the status message.
    Deletes the previous status message first, then sends a new one
    with the phone number in bold at the top.
    """
    global last_status_message_id
    with status_lock:
        if last_status_message_id is not None:
            delete_telegram_message(last_status_message_id)
            last_status_message_id = None

        text = (
            f"<b>{phone_number}</b>\n"
            f"Dismiss clicks: {dismisses}\n"
            f"Entries: {entries}"
        )
        new_id = send_telegram(text, parse_mode="HTML")
        if new_id:
            last_status_message_id = new_id


# --- MAIL.TM API HELPER FUNCTIONS ---
MAIL_TM_BASE = "https://api.mail.tm"
PHONE_NUMBER = "07012249321"   # <-- used in the Telegram header


def create_mail_tm_account():
    resp = requests.get(f"{MAIL_TM_BASE}/domains")
    domain = resp.json()["hydra:member"][0]["domain"]

    username = f"user_{random.randint(10000, 99999)}"
    email = f"{username}@{domain}"
    password = "SecurePassword123!"

    payload = {"address": email, "password": password}
    headers = {"Content-Type": "application/json"}
    requests.post(f"{MAIL_TM_BASE}/accounts", json=payload, headers=headers)

    token_resp = requests.post(f"{MAIL_TM_BASE}/token", json=payload, headers=headers)
    token = token_resp.json()["token"]

    print(f"Generated temporary email: {email}")
    return email, token


def fetch_otp_from_mail_tm(token):
    print("Waiting for OTP email to arrive via mail.tm...")
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(20):
        time.sleep(3)
        msg_resp = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers)
        messages = msg_resp.json().get("hydra:member", [])

        if messages:
            msg_id = messages[0]["id"]
            full_msg = requests.get(
                f"{MAIL_TM_BASE}/messages/{msg_id}", headers=headers
            ).json()

            text_content = full_msg.get("text", "") or full_msg.get("intro", "")
            print("Email content retrieved successfully.")

            digits = re.findall(r"\b\d{4,6}\b", text_content)
            if digits:
                otp = digits[0]
                print(f"Extracted OTP: {otp}")
                return otp

    raise Exception("Timeout: OTP email did not arrive in time.")


def try_click_dismiss(driver, timeout=5):
    """
    Try to click the Dismiss button.
    Returns True if clicked, False if not present. Never raises.
    On success: increments dismiss_count and sends/updates the Telegram status.
    """
    global dismiss_count
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[4]/div/button")
            )
        )
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        print("Dismiss button clicked.")
        with count_lock:
            dismiss_count += 1
            d = dismiss_count
            e = entry_count
        # Send a fresh status message (deletes the previous one)
        send_status(PHONE_NUMBER, d, e)
        return True
    except Exception:
        print("Dismiss button not present — skipping.")
        return False


# --- BACKGROUND REPORTER THREAD ---
def report_loop():
    """Send a Telegram summary every 10 minutes."""
    while True:
        time.sleep(600)
        with count_lock:
            d = dismiss_count
            e = entry_count
        pct = (d / e * 100) if e else 0
        send_status(PHONE_NUMBER, d, e)
        # Also send a one-off line with the percentage, so the periodic
        # ping is visibly different from the per-dismiss status.
        send_telegram(f"periodic: {d}/{e} ({pct:.1f}%)")


threading.Thread(target=report_loop, daemon=True).start()

send_telegram("milo_bot started.")


# --- MAIN LOOP ---
entry_number = 1
while True:
    print(f"\n=== Starting entry #{entry_number} ===")

    try:
        temp_email, mail_token = create_mail_tm_account()
    except Exception as e:
        print(f"Could not create temp email: {type(e).__name__}: {e}")
        time.sleep(5)
        entry_number += 1
        continue

    chrome_options = Options()
    chrome_options.add_argument("--window-size=1050,700")

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 20)

    try:
        # 1. Open the link
        driver.get("https://milotextandwinpromo.com.ng/")

        # 2. Input phone number
        phone_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div/input")
            )
        )
        phone_input.send_keys(PHONE_NUMBER)

        # 3. Input generated mail.tm email address
        email_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/div/input"
        )
        email_input.send_keys(temp_email)

        # 4. Click proceed
        proceed_btn_1 = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", proceed_btn_1)

        # 5. Fetch OTP and input
        otp_code = fetch_otp_from_mail_tm(mail_token)

        otp_field = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "/html/body/div[4]/div/div/form/div/div/div/input[1]",
                )
            )
        )
        otp_field.send_keys(otp_code)

        # 6. Click proceed (OTP modal)
        proceed_btn_2 = driver.find_element(
            By.XPATH, "/html/body/div[4]/div/div/form/button"
        )
        proceed_btn_2.click()

        # 7. First name
        first_name_input = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "/html/body/main/div/div[2]/div[2]/form/div[1]/div[1]/div/input",
                )
            )
        )
        first_name_input.send_keys("david")

        # 8. Last name
        last_name_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div[2]/div/input"
        )
        last_name_input.send_keys("danjuma")

        # 9. Open dropdown
        dropdown_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/button/div"
        )
        dropdown_btn.click()

        # 10. Select state
        state_option = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "/html/body/div[3]/div/div/div/div/div/div/div[1]/div",
                )
            )
        )
        state_option.click()

        # 11. Promo code
        random_9_digit = str(random.randint(100000000, 999999987))
        promo_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[3]/div/input"
        )
        promo_input.send_keys(random_9_digit)
        print(f"Generated and entered promo code: {random_9_digit}")

        # 12. Enter promo
        enter_promo_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", enter_promo_btn)
        print("Automation sequence completed successfully!")

        time.sleep(3)

        try_click_dismiss(driver)

    except Exception as e:
        print(f"Entry #{entry_number} failed: {type(e).__name__}: {e}")

    finally:
        driver.quit()

    with count_lock:
        entry_count += 1

    print("1 entry")
    entry_number += 1
    time.sleep(2)
