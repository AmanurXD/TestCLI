import os
import json
import time
import re
import subprocess
from twilio.http.http_client import TwilioHttpClient
import requests
import redis
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon import events
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementNotInteractableException,
)
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from appium import webdriver as appium_webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
# Selenium exceptions (WebDriverException only exists in Selenium)
from selenium.common.exceptions import TimeoutException, NoSuchElementException as SeleniumNoSuchElementException
from selenium.common.exceptions import WebDriverException
from bs4 import BeautifulSoup

# This loads the variables from your .env file into the environment
load_dotenv() 

# --- Configuration & Global Objects ---
# Now you can get the credentials like this, instead of hardcoding them
UPSTASH_URL = os.getenv("UPSTASH_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN")
STATE_KEY = "twilio_script_state"
NUMBER_PRICE = 0.83663  # Used for balance check logic (though currently commented out)
MAX_ACCOUNTS = 10

# Initialize Console and Redis Client
console = Console()
try:
    redis_client = redis.Redis.from_url(
        UPSTASH_URL, password=UPSTASH_TOKEN, decode_responses=True
    )
    redis_client.ping()
    console.print("✅ Successfully connected to Upstash Redis.")
except Exception as e:
    console.print(f"[red]❌ Could not connect to Upstash Redis: {e}[/red]")
    exit()



COOKIE_FILE = "mycookie.json"
CREATE_URL = "https://www.twilio.com/console/account-creation/v1/create"
SURVEY_URL = "https://www.twilio.com/console/funnel/api/v2/ahoy/answers"
PROJECT_INFO_URL = "https://www.twilio.com/console/api/v2/projects/info"
TOKEN_PAGE_URL = "https://www.twilio.com/console/projects/summary"


# --- AdsPower Configuration (Add these) ---
ADSP_API_URL = "http://local.adspower.net:50325"





# --- Telegram Configuration ---
TELEGRAM_API_ID = 11592735
TELEGRAM_API_HASH = '7862a1a7572928721adb17bdcdcbcbcb'
TELEGRAM_SESSION_FILE = 'tg_session_ws.session'
BOT_USERNAME = '@wsotp200bot' # Target Bot

# Global placeholder for the Telethon client
TG_CLIENT = None


# --- Cloud State Management Functions ---
def load_state_from_redis():
    """Loads the application state from Redis."""
    try:
        state_json = redis_client.get(STATE_KEY)
        if state_json:
            return json.loads(state_json)
        # Default empty state
        return {"subaccounts": [], "current_index": 0, "last_number_sid": None}
    except Exception as e:
        console.print(f"[red]Error loading state from Redis: {e}[/red]")
        return None


def save_state_to_redis(state):
    """Saves the application state to Redis."""
    try:
        state_json = json.dumps(state, indent=2)
        redis_client.set(STATE_KEY, state_json)
        console.print("[dim green]State saved to Redis.[/dim green]")
    except Exception as e:
        console.print(f"[red]Error saving state to Redis: {e}[/red]")


# --- Selenium Functions ---
def setup_selenium_driver(headless=True, browser_id=None):
    """
    Launches an AdsPower browser instance via local API and attaches a Selenium Chrome driver to it.
    
    Args:
        headless (bool): Set to True to launch without a visible GUI (controlled by AdsPower API).
        browser_id (str): The AdsPower profile ID to launch.
    """
    if not browser_id:
        console.print("[red]❌ Error: AdsPower profile ID is required.[/red]")
        return None
        
    try:
        # 1. Call AdsPower API to launch the browser profile
        console.print(f"[cyan]Requesting AdsPower to launch profile: {browser_id} (Headless: {headless})[/cyan]")
        
        launch_url = f"{ADSP_API_URL}/api/v1/browser/start"
        
        params = {
            "user_id": browser_id, 
            "open_url": "about:blank",
            "open_tabs": 0 if headless else 1, # 0 for headless, 1 for visible
        }
        
        response = requests.get(launch_url, params=params)
        response.raise_for_status()
        
        launch_data = response.json()
        
        if launch_data.get("code") != 0 or not launch_data.get("data"):
            console.print(f"[red]❌ AdsPower API Error (Code {launch_data.get('code')}): {launch_data.get('msg')}[/red]")
            return None
            
        data = launch_data["data"]
        # The key data point: the remote debugging address
        debugger_address = data["ws"]["selenium"]

        console.print(f"[green]✅ AdsPower Profile Launched. Debugger: {debugger_address}[/green]")
        
        # 2. Configure Selenium to connect to the running browser
        chrome_options = Options()
        chrome_options.add_experimental_option("debuggerAddress", debugger_address)
        
        CHROME_DRIVER_PATH = r"C:\Users\PC\Documents\Projects\Twilio-Portable\roppium\testCase2\chromedriver\140.0.7339.80\bin\chromedriver.exe"
        
        
        from selenium.webdriver.chrome.service import Service
        service = Service(executable_path=CHROME_DRIVER_PATH)
    
        driver = webdriver.Chrome(service=service, options=chrome_options) 
        
        driver.implicitly_wait(10)
        return driver

    except requests.exceptions.RequestException as e:
        console.print(f"[red]❌ AdsPower API Connection Error. Check if AdsPower App is running: {e}[/red]")
        return None
    except Exception as e:
        console.print(f"[red]❌ General Error during Selenium attachment: {e}[/red]")
        return None


def load_cookies(driver, cookie_file="mycookie.json"):
    """Loads cookies from a JSON file into the browser."""
    try:
        with open(cookie_file, 'r') as f:
            cookies = json.load(f)
            
        driver.get("https://www.twilio.com")
        WebDriverWait(driver, 10).until(EC.url_contains("twilio.com"))
        console.print("[yellow]Setting cookies...[/yellow]")
        
        for c in cookies:
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "path": c.get("path", "/"),
            }
            domain = c.get("domain")
            if domain:
                cookie["domain"] = domain.lstrip(".")
            if "secure" in c:
                cookie["secure"] = c["secure"]
            if "expirationDate" in c:
                try:
                    cookie["expiry"] = int(c["expirationDate"])
                except Exception:
                    pass
            
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                console.print(
                    f"[yellow]Could not add cookie {cookie['name']}: {e}[/yellow]"
                )
                
        console.print("[green]Cookies set successfully, page refreshed.[/green]")
        driver.refresh()
        time.sleep(3)
        
    except FileNotFoundError:
        console.print(f"[red]Error: {cookie_file} not found.[/red]")
        driver.quit()
        exit()
    except json.JSONDecodeError:
        console.print(f"[red]Error: {cookie_file} has invalid JSON format.[/red]")
        driver.quit()
        exit()
    except Exception as e:
        console.print(f"[red]Error loading cookies: {e}[/red]")
        driver.save_screenshot("cookie_error.png")
        driver.quit()
        exit()


def get_tg_client(console):
    """Initializes the synchronous Telegram client or returns the existing one."""
    global TG_CLIENT
    if TG_CLIENT is None:
        console.print("[cyan]Initializing Telegram Client for session persistence...[/cyan]")
        try:
            # Connect synchronously using the main event loop
            client = TelegramClient(TELEGRAM_SESSION_FILE, TELEGRAM_API_ID, TELEGRAM_API_HASH)
            # Use a context manager to ensure the client is properly disconnected
            # Note: For the first run, it will require manual login inputs in the terminal.
            
            # Use start() without the loop for simple session file creation/loading
            client.start() 

            if not client.is_user_authorized():
                console.print(Panel("[bold red]❌ Telegram not authorized. Please run the script once and follow the login prompts in the terminal to create the session file.[/bold red]"))
                client.disconnect()
                return None
            
            TG_CLIENT = client
            console.print("✅ [green]Telegram Client ready.[/green]")
            
        except Exception as e:
            console.print(f"[bold red]❌ Telegram connection/authorization failed. Error: {e.__class__.__name__}[/bold red]")
            TG_CLIENT = None
            return None
            
    return TG_CLIENT
            
# --- Appium OTP Extraction Helper ---


# Helper to run a synchronous/blocking function in a separate thread
async def run_sync_in_thread(sync_func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sync_func, *args)



def extract_popup_otp(driver, console, timeout=150):
    """
    Waits for the OTP popup code container to appear and extracts the code.
    This version targets the robust Resource ID of the parent container.
    """
    # CRITICAL: The Resource ID of the parent container holding the 7 TextView elements
    CODE_CONTAINER_ID = "com.whatsapp:id/code_container"
    
    try:
        # Wait until the parent container is present
        code_container = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((AppiumBy.ID, CODE_CONTAINER_ID))
        )
        console.print("    ✅ [green]OTP Popup (code_container ID) detected.[/green]")
    except TimeoutException:
        console.print("[red]❌ Timed out waiting for OTP popup to appear (code_container not found).[/red]")
        return None

    # Now, find all TextView children directly under this specific container element.
    # This severely limits the search scope, making the indices reliable.
    # Appium supports searching for children relative to a parent element.
    
    # Locate all TextView elements that are children of the container
    # Note: Using the generic ClassName here, as the elements are children of a specific parent.
    all_children_elements = code_container.find_elements(AppiumBy.CLASS_NAME, "android.widget.TextView")
    
    otp_digits = ""
    digit_count = 0
    
    # We iterate through the children elements (which should be 7 in order: D-D-D-Hyphen-D-D-D)
    for i, element in enumerate(all_children_elements):
        try:
            text = element.text
            
            # We only care about single-digit text. The hyphen (index 3) is ignored.
            if text and text.isdigit() and len(text) == 1:
                otp_digits += text
                digit_count += 1
            elif text == '-':
                # Skip the hyphen separator
                continue
            
            # Safety break after finding all 6 digits
            if digit_count == 6:
                break
                
        except Exception as e:
            # Should not happen, but safe to continue if element text retrieval fails
            console.print(f"[yellow]Warning during extraction loop: {e}[/yellow]")
            continue
            
    if len(otp_digits) == 6:
        console.print(f"    ✅ [bold green]Extracted OTP from popup (Resource ID Method):[/bold green] {otp_digits}")
        return otp_digits
    else:
        console.print(f"[red]❌ Failed to extract 6-digit OTP from children elements. Found {len(otp_digits)} digits.[/red]")
        # Optional: Print the texts found for debugging the child index order
        # console.print(f"[dim]Found child texts: {[el.text for el in all_children_elements]}[/dim]")
        return None

async def telegram_flow_get_otp_and_reply(client, driver, phone_number, console):
    """
    Handles the entire TG flow, waits for Appium OTP, and correctly monitors for 'Try later' edits.
    """
    message = phone_number.lstrip('+')

    # --- 1. Get the initial 'In Progress' message ---
    await client.send_message(BOT_USERNAME, message)
    console.print(f"  📤 [cyan]TG: Sent number '{message}' to bot. Waiting for initial 'In Progress'...[/cyan]")
    
    last_bot_message = None
    with console.status("    [yellow]Waiting for bot confirmation... (Max 30s)[/]", spinner="dots"):
        for _ in range(30):
            messages = await client.get_messages(BOT_USERNAME, limit=1)
            if messages and f"{message} 🔵 In Progress" in messages[0].text:
                last_bot_message = messages[0]
                break
            await asyncio.sleep(1)

    # Check if we failed to get "In Progress" and maybe got "Try later" instead
    if not last_bot_message:
        messages = await client.get_messages(BOT_USERNAME, limit=1)
        if messages and f"🟡 Try later" in messages[0].text:
            last_bot_message = messages[0] # The loop below will handle this
        else:
            console.print("[red]❌ TG: Timed out waiting for an initial reply from the bot.[/red]")
            return None

    # --- 2. Main Loop: Start waiting for OTP while monitoring Telegram ---
    console.print("  ⏳ [yellow]Appium: Waiting for OTP popup... (Also monitoring TG for status changes)[/yellow]")
    
    # This runs the blocking `extract_popup_otp` in the background
    appium_task = asyncio.create_task(
        run_sync_in_thread(extract_popup_otp, driver, console)
    )

    while not appium_task.done():
        # Check Telegram status every couple of seconds
        await asyncio.sleep(2)
        
        try:
            current_message = await client.get_messages(BOT_USERNAME, ids=last_bot_message.id)
            if not (current_message and "🟡 Try later" in current_message.text):
                continue # All good, message is still 'In Progress' or gone, keep waiting

            # --- If we are here, the message is "Try later" ---
            console.print("  ⚠️ [yellow]TG: Status is 'Try later'! Cancelling Appium wait to handle it...[/yellow]")
            appium_task.cancel() # Stop waiting for the OTP that will never come

            # --- Resend Loop ---
            while True:
                await client.send_message(BOT_USERNAME, message)
                console.print(f"    📤 [cyan]TG: Resent number to get new status.[/cyan]")
                await asyncio.sleep(3) # Wait for the bot's reply

                new_reply = (await client.get_messages(BOT_USERNAME, limit=1))[0]

                if f"🔵 In Progress" in new_reply.text:
                    console.print("    📥 [green]TG: Got new 'In Progress' status. Restarting OTP wait.[/green]")
                    last_bot_message = new_reply # IMPORTANT: Update the message to reply to
                    
                    # RESTART the Appium waiting task from scratch
                    appium_task = asyncio.create_task(
                        run_sync_in_thread(extract_popup_otp, driver, console)
                    )
                    break # Exit the resend loop and go back to the main monitoring loop
                
                # Check for cooldown and wait
                cooldown_match = re.search(r"in (\d+) seconds", new_reply.text)
                delay = int(cooldown_match.group(1)) + 1 if cooldown_match else 5
                console.print(f"    [dim]Waiting for {delay} seconds before next retry...[/dim]")
                await asyncio.sleep(delay)
            
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not check Telegram status: {e}[/yellow]")

    # --- 3. Process the final result ---
    try:
        otp_code = await appium_task
        if otp_code:
            await client.send_message(last_bot_message.chat_id, otp_code, reply_to=last_bot_message)
            console.print(f"  📤 [green]TG: Replied to bot with OTP: {otp_code}[/green]")
            return otp_code
        else:
            console.print("[red]❌ Appium: OTP extraction failed or returned an empty code.[/red]")
            return None
    except asyncio.CancelledError:
        # This is expected if the loop was cancelled and never recovered
        console.print("[red]❌ TG/Appium: Process was cancelled and could not recover.[/red]")
        return None








def get_single_adspower_profile_id(adsp_api_url):
    """
    Calls the local AdsPower API to get the list of profiles and returns the ID of the first one found.
    """
    console.print("[cyan]--- Fetching AdsPower Profile ID ---[/cyan]")
    list_url = f"{adsp_api_url}/api/v1/user/list"
    
    try:
        response = requests.get(list_url)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get("code") != 0 or not data.get("data") or not data["data"].get("list"):
            console.print(f"[red]❌ Error fetching profile list. Code {data.get('code')}: {data.get('msg')}[/red]")
            return None
            
        first_profile = data["data"]["list"][0]
        profile_id = first_profile["user_id"]
        profile_name = first_profile["name"]
        
        console.print(f"[green]✅ Found Profile:[/green] [bold magenta]'{profile_name}'[/bold magenta]")
        console.print(f"[green]   ID (user_id):[/green] [bold yellow]{profile_id}[/bold yellow]")
        
        return profile_id
        
    except requests.exceptions.RequestException as e:
        console.print(f"[red]❌ API Connection Error. Is AdsPower running? -> {e}[/red]")
        return None





def create_twilio_subaccount(account_name):
    """
    Creates a Twilio subaccount using a fast, requests-based method.
    The UI output is styled to match the main application.
    """
    # This print matches the style of the old selenium function
    console.print(f"  [yellow]Creating account: {account_name}[/yellow]")
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://www.twilio.com/console/projects/summary"
    })
    
    # 1. Log In & Get CSRF Token
    try:
        with open("mycookie.json", 'r') as f:
            cookies = json.load(f)
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
        
        console.print("    [green]Session created and cookies loaded.[/green]")

        response = session.get("https://www.twilio.com/console/projects/summary")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        csrf_meta_tag = soup.find('meta', {'name': 'csrfToken'})
        csrf_token = csrf_meta_tag['content']
        console.print(f"    [green]CSRF Token acquired.[/green]")
    except Exception as e:
        console.print(f"    [bold red]❌ Initial login or token scrape failed: {e}[/bold red]")
        return None

    # 2. Create the Account
    account_files_payload = {
        'friendlyName': (None, account_name), 'type': (None, 'twilio'),
        'source': (None, 'account-switcher'), 'CSRF': (None, csrf_token),
        'billingCountry': (None, 'GB')
    }
    try:
        create_response = session.post("https://www.twilio.com/console/account-creation/v1/create", files=account_files_payload)
        create_response.raise_for_status()
        new_account_sid = create_response.json().get('accountSid')
        if not new_account_sid:
            raise ValueError("API response did not contain 'accountSid'.")
        console.print(f"    [green]Account created successfully.[/green]")
    except Exception as e:
        console.print(f"    [bold red]❌ Account creation FAILED: {e}[/bold red]")
        if 'create_response' in locals(): console.print(f"      Server Response: {create_response.text}")
        return None

    # 3. Submit the Onboarding Survey
    try:
        survey_payload = {"ahoy": {"_ahoy_data_version": "v5.2", "org-type": "business", "is-programmer": "yes"}}
        session.post("https://www.twilio.com/console/funnel/api/v2/ahoy/answers", headers={'x-twilio-csrf': csrf_token}, json=survey_payload).raise_for_status()
        console.print("    [green]Onboarding survey submitted.[/green]")
    except Exception:
        # This step is non-critical, so we just warn the user
        console.print("    [yellow]⚠️ Onboarding survey failed, but continuing...[/yellow]")

    # 4. Fetch the Auth Token
    try:
        info_response = session.get("https://www.twilio.com/console/api/v2/projects/info")
        info_response.raise_for_status()
        auth_token = info_response.json().get('authToken')
        if not auth_token:
            raise ValueError("API response did not contain 'authToken'.")
        console.print("    [green]Auth Token retrieved.[/green]")
    except Exception as e:
        console.print(f"    [bold red]❌ Fetching Auth Token FAILED: {e}[/bold red]")
        if 'info_response' in locals(): console.print(f"      Server Response: {info_response.text}")
        return None

    # 5. Compile and return the final result
    return {
        "sid": new_account_sid,
        "token": auth_token,
        "name": account_name,
        "status": "active"
    }




def check_and_handle_ban_screen(driver, console):
    """
    Checks for the WhatsApp ban screen. If found, it navigates back to the
    number entry screen and returns True. Otherwise, returns False.
    """
    try:
        # 1. Detect the ban icon
        ban_icon = wait_for_element(driver, AppiumBy.ID, "com.whatsapp:id/ban_icon", timeout=3)
        if not ban_icon:
            return False # Not a ban screen, continue normally

        console.print("  ⛔️ [bold red]BANNED NUMBER DETECTED![/] Navigating back...")

        # 2. Click the 'More options' (three dots) menu
        more_options_menu = wait_for_element(driver, AppiumBy.ID, "com.whatsapp:id/menuitem_overflow", timeout=5)
        if more_options_menu:
            more_options_menu.click()
            time.sleep(1)
        else:
            console.print("  [red]Ban handle error: Could not find 'More options' menu.[/red]")
            return False

        # 3. Click 'Register new number'
        register_new_locator = 'new UiSelector().resourceId("com.whatsapp:id/title").text("አዲስ ቁጥር መዝግብ")'
        register_new_button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, register_new_locator, timeout=5)
        if register_new_button:
            register_new_button.click()
            time.sleep(2)
        else:
            console.print("  [red]Ban handle error: Could not find 'Register new number' button.[/red]")
            return False

        # 4. --- ✅ CORRECTED AGREE BUTTON LOGIC ---
        # We are now at the language screen. Click 'Agree' to get back to number entry.
        # This uses the same robust locator from the script's initial setup.
        AGREE_BTN_TEXT = "ይስማሙ እና ይቀጥሉ"
        agree_btn_locator = f'new UiSelector().className("android.widget.Button").textContains("{AGREE_BTN_TEXT}")'
        
        agree_btn = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, agree_btn_locator, timeout=10)
        
        if agree_btn:
            console.print("  [green]Found 'Agree' button. Clicking to return to number entry...[/green]")
            agree_btn.click()
            time.sleep(2) # Wait for screen transition
            return True # Ban was successfully handled
        else:
            console.print("  [red]Ban handle error: FAILED to find 'Agree' button after reset.[/red]")
            return False

    except TimeoutException:
        return False # This is normal, means no ban icon was found
    except Exception as e:
        console.print(f"  [red]An unexpected error occurred during ban check: {e}[/red]")
        return False




# --- APPIUM Worker and Helper Functions ---
def extract_otp_code(sms_body):
    """
    Robustly extracts a 6-digit code (XXX-XXX) from the SMS body, regardless of surrounding text or order.
    """
    
    # 1. Pattern: Looks for 3 digits, followed by an optional space/hyphen/space, followed by 3 digits.
    # Pattern explanation: 
    #   \d{3} - three digits
    #   [\s\-]* - zero or more spaces or hyphens (handles "XXX-XXX", "XXX --- XXX", "XXX XXX")
    #   \d{3} - three digits
    
    pattern = r"(\d{3})[\s\-]*(\d{3})"
    
    match = re.search(pattern, sms_body)
    
    if match:
        # Concatenate the two captured groups to form the 6-digit code
        code = match.group(1) + match.group(2)
        console.print(f"    ✅ [bold green]Extracted OTP Code (Robust):[/ bold green] {code}")
        return code
    
    # Fallback/Old Pattern check (Optional, but good for safety)
    match_old = re.search(r"ኮድዎ፦\s*(\d{3})-(\d{3})", sms_body)
    if match_old:
        code = match_old.group(1) + match_old.group(2)
        console.print(f"    ✅ [bold green]Extracted OTP Code (Fallback):[/ bold green] {code}")
        return code
        
    console.print("    ❌ [bold red]Could not find OTP code in the message body using any pattern.[/]")
    return None

def wait_for_element(driver, by, value, timeout=20):
    """Helper function to wait for an element."""
    try:
        # AppiumBy.ANDROID_UIAUTOMATOR uses text, resourceId, and className for robust location
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
    except TimeoutException:
        return None



def force_stop_app(package):
    # ... (your existing force_stop_app function)
    subprocess.run(["adb", "shell", "am", "force-stop", package])



def check_and_handle_post_otp_steps(driver, step_check_map, timeout=80):
    """
    Polls the screen repeatedly to handle post-OTP dialogs in a non-blocking, adaptive way.
    
    Args:
        driver: The Appium driver instance.
        step_check_map: A dictionary of steps to check for (Resource ID and Action Text).
        timeout: Total time in seconds to wait for all steps to pass/fail.
    
    Returns:
        True if the name input field is reached, False otherwise.
    """
    start_time = time.time()
    steps_completed = set()
    
    # 1. Name Input Field Locator (Final goal for this polling loop)
    NAME_FIELD_LOCATOR = 'new UiSelector().resourceId("com.whatsapp:id/registration_name")'
    
    # 2. OTP Input Field Locator (To ensure we are past the OTP screen)
    OTP_INPUT_LOCATOR = 'new UiSelector().resourceId("com.whatsapp:id/verify_sms_code_input")'
    
    while time.time() - start_time < timeout:
        
        # Check if we are still on the OTP screen and haven't input the code successfully
        try:
             # Fast check if the OTP input field is still present but not focused. If it is, sleep and continue.
            driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, OTP_INPUT_LOCATOR)
            time.sleep(1) 
            continue
        except WebDriverException:
            # Good: OTP input screen is gone. Proceed to check for dialogs/name screen.
            pass

        # Check for the final name input field - if found, we are past all dialogs.
        try:
            name_field = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, NAME_FIELD_LOCATOR)
            console.print("    ✅ [green]Reached Name Input Screen. Ending dialog handling.[/]")
            return True
        except WebDriverException:
            # Not yet at the name screen. Check for the two conditional dialogs.
            pass

        # Check and handle conditional dialogs
        for step_name, locators in step_check_map.items():
            if step_name in steps_completed:
                continue
            
            # Use the combined locator for maximum reliability
            try:
                # Locator for the dialog message using Resource ID and Class for high fidelity
                dialog_locator = f'new UiSelector().resourceId("{locators["Check ID"]}").className("{locators["Check Class"]}")'
                
                # Check if the element exists *without* a hard wait
                dialog_message = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, dialog_locator)
                
                if dialog_message:
                    # Found the dialog! Now click the associated action button.
                    action_button_locator = f'new UiSelector().resourceId("{locators["Action ID"]}").textContains("{locators["Action Text"]}")'
                    action_button = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, action_button_locator)
                    
                    if action_button:
                        action_button.click()
                        steps_completed.add(step_name)
                        console.print(f"    ✅ [green]Handled '{step_name}'. Clicking '{locators['Action Text']}'.[/]")
                        time.sleep(1) # Short pause after clicking to let the screen dismiss
                        break # Start the loop over to check the new screen state
            
            except Exception:
                # Dialog not present or element not found quickly. Ignore and check next step/loop iteration.
                pass 

        time.sleep(0.5) # Short, consistent sleep for polling

    console.print("  ❌ [red]Timed out waiting to reach Name Input Screen or unexpected screen appeared.[/]")
    return False

# --- You will need to define this helper function if it's not in your environment ---
# This simulates an explicit wait for the 'enabled' attribute to become 'true'
def wait_for_enabled(driver, element):
    """Waits indefinitely until the given element's 'enabled' attribute is true."""
    while True:
        if element.get_attribute("enabled") == "true":
            return True
        time.sleep(1)

import time
from appium.webdriver.common.appiumby import AppiumBy
from appium.options.android import UiAutomator2Options
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from rich.console import Console
from rich.panel import Panel
import requests
# Placeholder imports for required helper functions (You must ensure these exist/are imported)
# from .helpers import wait_for_element, force_stop_app, extract_otp_code, check_and_handle_post_otp_steps, fetch_new_available_number, wait_for_enabled

console = Console()


from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException # <-- ADD THIS LINE

def wait_for_post_otp_screen(driver, timeout=30):
    """
    Waits for the successful post-OTP screen, which is typically one of:
    1. The Name Input Field.
    2. A Permission Dialog (e.g., Contacts, Backup).
    """
    NAME_FIELD_UIAUTOMATOR = 'new UiSelector().resourceId("com.whatsapp:id/registration_name")'
    PERMISSION_MESSAGE_ID = "com.whatsapp:id/permission_message"
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # Check 1: Is the Name Input field present?
            driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, NAME_FIELD_UIAUTOMATOR)
            return True # Success: We reached the Name screen
        except NoSuchElementException:
            pass
            
        try:
            # Check 2: Is a Permission Dialog present? (Use a highly specific element)
            driver.find_element(AppiumBy.ID, PERMISSION_MESSAGE_ID)
            return True # Success: We are on a permission dialog screen
        except NoSuchElementException:
            pass
            
        time.sleep(1) # Check every second
        
    raise TimeoutException("Timed out waiting for expected post-OTP screen state.")



# --- NEW HELPER FUNCTION for IQ VALIDATION LOOP (Included for completeness) ---
def is_error_dialog_present(driver, timeout=10):
    """
    Checks for the WhatsApp number validation error dialog (the 'እሺ' button)
    using a short, explicit wait.
    Returns the element if found, or None if the timeout is exceeded.
    """
    try:
        error_button = wait_for_element(
            driver,
            AppiumBy.ID,
            "android:id/button1", # Resource ID for the 'OK' button in the dialog
            timeout=timeout
        )
        if error_button and error_button.text == "እሺ":
            console.print("    ❌ [bold red]IQ Check FAILED: Error Dialog ('እሺ') found![/]")
            return error_button
        return None
    except Exception:
        # Expected to fail (Time out) if the element is NOT present
        return None




def handle_optional_dialog(driver, locator, timeout=5):
    """
    Waits for an optional element for a short period. If found, it clicks it.
    If not found within the timeout, it continues without an error.

    Args:
        driver: The Appium driver instance.
        locator (tuple): The locator strategy and value (e.g., (AppiumBy.ID, "..."))
        timeout (int): The maximum time in seconds to wait for the element.
    """
    try:
        console.print(f"  ⏳ [yellow]Checking for optional element for {timeout} seconds...[/]")
        
        # Wait for the element to be clickable (visible and enabled)
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(locator)
        )
        
        console.print("  ✅ [green]Optional element found. Clicking it.[/]")
        element.click()
        time.sleep(1) # A small pause for the UI to settle after the click
        return True # Indicate that the element was found and clicked

    except TimeoutException:
        # This is the expected outcome if the element does not appear
        console.print("  ℹ️ [blue]Optional element did not appear. Continuing script.[/]")
        return False # Indicate that the element was not found
    except Exception as e:
        # Catch any other unexpected errors
        console.print(f"  ❌ [red]An unexpected error occurred while handling optional element: {e}[/]")
        return False




# --- Main Appium Execution Function (FULL CODE) ---
# OLD: def run_appium_registration(client, render_url, area_code, prefix_digits):
def run_appium_registration(client, render_url, area_code, prefix_digits, driver=None, is_short_form=False):
    """
    Core Appium flow: Fetches first number, loops through validation, 
    conditionally purchases, and completes registration/OTP flow.
    """
    PACKAGE_NAME = "com.whatsapp"
    ACTIVITY_NAME = ".Main"
    DEVICE_NAME = "Android Device"
    APPIUM_SERVER = "http://localhost:4723"
    COUNTRY_CODE = "1"
    

    purchased_number_obj = None
    registration_success = False
    
    # Locators for new steps
    FALLBACK_BUTTON_ID = "com.whatsapp:id/fallback_methods_entry_button"
    RADIO_BUTTON_UIAUTOMATOR = 'new UiSelector().resourceId("com.whatsapp:id/reg_method_checkbox").className("android.widget.RadioButton")'
    CONTINUE_BUTTON_UIAUTOMATOR = 'new UiSelector().resourceId("com.whatsapp:id/continue_button").className("android.widget.Button")'

    # Map for conditional post-OTP checks
    POST_OTP_STEPS = {
        "Contacts/Media Permission": {
            "Check ID": "com.whatsapp:id/permission_message",
            "Check Class": "android.widget.TextView",
            "Action ID": "com.whatsapp:id/cancel",
            "Action Text": "አሁን አይደለም"
        },
        "Google Backup Permission": {
            "Check ID": "com.whatsapp:id/permission_message",
            "Check Class": "android.widget.TextView",
            "Action ID": "com.whatsapp:id/cancel",
            "Action Text": "ይሰርዙ"
        }
    }

    # --- 1. INITIAL NUMBER FETCH ---
    console.print(f"  🔎 [cyan]Twilio: Fetching initial number for criteria: {area_code}{prefix_digits or ''}...[/]")
    current_available_num = fetch_new_available_number(client, area_code, prefix_digits)
    
    if not current_available_num:
        console.print("  🛑 [red]Twilio: Failed to find ANY number matching criteria. Exiting flow.[/]")
        return None, False , driver

    try:
        # --- Appium Setup (Continuous Flow Logic) ---
        if driver is None:
            force_stop_app(PACKAGE_NAME)
            options = UiAutomator2Options()
            options.device_name = DEVICE_NAME
            options.platform_name = "Android"
            options.app_package = PACKAGE_NAME
            options.app_activity = ACTIVITY_NAME
            options.no_reset = True
            
            console.print(f"  ▶️ [cyan]Appium: Starting new driver...[/]")
            driver = appium_webdriver.Remote(APPIUM_SERVER, options=options)
            time.sleep(2)
        else:
            console.print("  ▶️ [cyan]Appium: Reusing existing driver.[/]")
            driver.activate_app(PACKAGE_NAME)
            time.sleep(1) # Small pause after activating app

        # -------- 2. Language Change & Agree --------
        # NOTE: Using the Amharic button texts from your previous code where they are located.
        # Locators/Texts for Language Check
        LANGUAGE_PICKER_ID = "com.whatsapp:id/language_picker"
        AMHARIC_TEXT = "አማርኛ"
        AGREE_BTN_TEXT = "ይስማሙ እና ይቀጥሉ"

        # -------- 2. Language Change & Agree (ROBUST CHECK) --------
        language_picker = wait_for_element(driver, AppiumBy.ID, LANGUAGE_PICKER_ID, timeout=5)
        
        if language_picker:
            # Language picker found: Must be the initial screen state
            if language_picker.text != AMHARIC_TEXT:
                # Omitted language correction logic for brevity, assuming you will re-add.
                language_picker.click()
                console.print("  ✅ [green]Appium: Language Picker Found , Clicking...[/]")
                amharic_button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Amharic")', timeout=10)
                
                if amharic_button:
                    console.print("  ✅ [green]Appium: Amharic Button Found , Clicking...[/]")
                    amharic_button.click()
                    time.sleep(1)
                pass
            
            # CRITICAL: Click AGREE to proceed to number entry.
            agree_btn_amharic = wait_for_element(
                driver, AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().className("android.widget.Button").textContains("{AGREE_BTN_TEXT}")', timeout=10
            )
            
            if agree_btn_amharic:
                agree_btn_amharic.click()
                time.sleep(2)
            else:
                return None, False, driver # Failure: Couldn't agree
        
        
            # --- ROBUSTLY HANDLE THE OPTIONAL CANCEL DIALOG ---
            # Define the super-specific locator
            CANCEL_BUTTON_LOCATOR = (
                AppiumBy.XPATH, 
                "//android.widget.Button[@resource-id='android:id/button2' and @text='ይሰርዙ']"
            )
        
            # Call our new helper function
            handle_optional_dialog(driver, CANCEL_BUTTON_LOCATOR, timeout=5)
        
        
        
        country_code_field = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.EditText")', timeout=10)
        rest_number_field = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.EditText").index(1)', timeout=10)
        
        if not country_code_field or not rest_number_field:
            console.print("  ❌ [red]Appium: Phone number input fields not found. Exiting flow.[/]")
            return None, False, driver # Return on failure
            
        # Set Country Code once
        country_code_field.clear()
        country_code_field.send_keys(COUNTRY_CODE)
        
        # -------- 3. Number Validation Loop (IQ TWEAK / MODE PIVOT) --------
        while True:
            if not current_available_num:
                console.print("  🛑 [red]Twilio: No more available numbers to check. Breaking loop.[/]")
                break
            
            full_phone_number_with_plus = current_available_num.phone_number
            phone_number_for_appium = full_phone_number_with_plus.replace(f"+{COUNTRY_CODE}", "")

            console.print(f"  ➡️ [cyan]Appium: Validating {full_phone_number_with_plus}...[/]")

            # 3a. Paste and Click Next
            rest_number_field.clear()
            rest_number_field.send_keys(phone_number_for_appium)
            
            next_button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.Button").textContains("ቀጣይ")', timeout=5)
            next_button.click()
            time.sleep(2)
            
            
            
            
            # --- 🔥 NEW BAN CHECK LOGIC 🔥 ---
            if check_and_handle_ban_screen(driver, console):
                # If the function returns True, the number was banned and handled.
                # We fetch a new number and use 'continue' to restart the loop.
                console.print("  🔄 [yellow]Number was BANNED. Fetching next available number...[/]")
                current_available_num = fetch_new_available_number(client, area_code, prefix_digits)
                continue # Skips the rest of the code and starts the next loop iteration
            # --- END OF BAN CHECK ---
            
            
            
            
            # 3b. Wait for 'Yes' confirmation dialog (CRITICAL POINT)
            yes_button = wait_for_element(
                driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("android:id/button1").textContains("አዎ")', timeout=5
            )
            
            if not yes_button:
                console.print("  ❌ [red]Appium: 'Yes' confirmation dialog not found. WhatsApp rejected number format. Breaking loop.[/]")
                current_available_num = fetch_new_available_number(client, area_code, prefix_digits)
                continue
            
            # --- MODE PIVOT LOGIC STARTS HERE ---
            
            if is_short_form:
                # SHORT FORM: Buy immediately after confirming dialog is present, then click YES.
                
                # 3c-Short: Purchase the number immediately
                console.print(f"  💰 [yellow]Short Form: Purchasing VALID number {full_phone_number_with_plus}...[/]")
                purchased_number_obj = client.incoming_phone_numbers.create(
                    phone_number=current_available_num.phone_number,
                    sms_url=f"{render_url}/sms",
                    sms_method='POST'
                )
                console.print(f"    ✅ [green]Purchase complete. SID: {purchased_number_obj.sid}[/]")
                
                # 3d-Short: Click YES to send the OTP request immediately
                console.print("  ✅ [green]Appium: Clicking 'Yes' to start immediate OTP request...[/]")
                yes_button.click()
                
                # Since OTP request is sent, break the validation loop and proceed to polling
                break

            else:
                # LONG FORM (Original IQ Check Logic)
                console.print("  ✅ [green]Appium: Clicking 'Yes' to start IQ check...[/]")
                yes_button.click()
                
                # 3c-Long: Core IQ Check (Wait for error dialog)
                error_button = is_error_dialog_present(driver, timeout=10) # Using your custom timeout
                # NOTE: You will need to re-add the BAN check logic here too if you want it in the long form.
                
                if error_button:
                    # Number is INVALID, click 'OK' and loop back
                    error_button.click()
                    time.sleep(1)
                    
                    console.print("  🔄 [yellow]Number is INVALID. Fetching next number...[/]")
                    current_available_num = fetch_new_available_number(client, area_code, prefix_digits)
                    continue
                
                else:
                    # Number is VALID, break the validation loop
                    console.print("  🎉 [bold green]IQ Check SUCCESS: Number is VALID and Working![/]")
                    break

 
        
        # --- END OF VALIDATION LOOP ---
        
        # NOTE: If we are in Long Form, the purchase happens NOW.
        if not is_short_form:
            # -------- 4. PURCHASE THE VALID NUMBER (LONG FORM ONLY) --------
            console.print(f"  💰 [yellow]Purchasing VALID number {full_phone_number_with_plus}...[/]")
            purchased_number_obj = client.incoming_phone_numbers.create(
                phone_number=current_available_num.phone_number,
                sms_url=f"{render_url}/sms",
                sms_method='POST'
            )
            console.print(f"    ✅ [green]Purchase complete. SID: {purchased_number_obj.sid}[/]")
        
        # -------- 5. REQUEST NEW CODE FLOW (CONDITIONAL) --------

        if not is_short_form:
            # This section is ONLY required for LONG FORM, as the short form clicks 'Yes' already.
            
            # 5a. Click 'Request New Code' button
            console.print(" ➡️ [cyan]Appium: Executing 'Request New Code' flow...[/]")
            fallback_button = wait_for_element(driver, AppiumBy.ID, FALLBACK_BUTTON_ID, timeout=10)
            
            if not fallback_button:
                console.print(" ❌ [red]Appium: 'Request New Code' button not found. Exiting flow.[/]")
                return purchased_number_obj, False, driver # Modified return
            
            fallback_button.click()
            time.sleep(2)
            
            # 5b. Wait for Radio Button to be enabled and click
            radio_button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, RADIO_BUTTON_UIAUTOMATOR, timeout=15)
            if not radio_button:
                console.print(" ❌ [red]Appium: Radio Button element not found. Exiting flow.[/]")
                return purchased_number_obj, False, driver # Modified return
                
            wait_for_enabled(driver, radio_button)
            radio_button.click()
            console.print("   ✅ [green]Radio Button clicked after becoming enabled.[/]")
            time.sleep(2)
            
            # 5c. Wait for 'Continue' button to be enabled and click
            continue_button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, CONTINUE_BUTTON_UIAUTOMATOR, timeout=5)
            if not continue_button:
                console.print(" ❌ [red]Appium: Continue Button element not found. Exiting flow.[/]")
                return purchased_number_obj, False, driver # Modified return
            
            wait_for_enabled(driver, continue_button)
            continue_button.click()
            console.print("   ✅ [green]'Continue' button clicked after becoming enabled.[/]")
            time.sleep(5)
            
        else:
            # SHORT FORM: We assume the 'Yes' click in Step 3d already sent the code.
            console.print(" ⏸️ [dim]Short Form: Skipping 'Request New Code' flow (OTP sent immediately).[/dim]")
            time.sleep(5) # Add a slightly longer initial wait for the SMS to arrive at Twilio/Render.
        
        # -------- 6. OTP Handling (Start Polling) --------
        console.print(f"  ⏳ [yellow]Appium: Polling {render_url} for SMS...[/]")
        otp_code = None
        
        with console.status("    [yellow]Waiting for OTP... (5 min timeout)[/]", spinner="dots"):
            for _ in range(300):
                try:
                    res = requests.get(f"{render_url}/get-message/{full_phone_number_with_plus}")
                    
                    if res.ok and res.json().get("status") == "found":
                        body = res.json().get("body")
                        otp_code = extract_otp_code(body)
                        if otp_code:
                            break
                            
                except requests.exceptions.RequestException:
                    pass
                    
                time.sleep(1)

        if not otp_code:
            console.print("  ❌ [red]Appium: Timed out waiting for OTP.[/]")
            return purchased_number_obj, False , driver
            
        console.print(f"    ✍️ [cyan]Appium: Entering REAL OTP {otp_code}...[/]")
         

        code_input = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.whatsapp:id/verify_sms_code_input")' , timeout=15)

        if code_input:
            code_input.send_keys(otp_code)
            console.print("    ✅ [green]OTP entered. Waiting for network verification to complete...[/]")

            # -----------------------------------------------------------
            # 🔥 NEW & IMPROVED SMART WAIT: Wait for the OTP field to vanish.
            # -----------------------------------------------------------
            OTP_INPUT_LOCATOR = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.whatsapp:id/verify_sms_code_input")')
            
            try:
                # Max 30s for the network verification step
                WebDriverWait(driver, 30).until(
                    EC.invisibility_of_element_located(OTP_INPUT_LOCATOR)
                )
                console.print("    ✅ [green]OTP screen disappeared. Verification SUCCESS.[/]")

            except TimeoutException:
                # If it's still visible, verification failed or the app is stuck.
                console.print("    ❌ [bold red]Appium: Timed out (30s). OTP screen did NOT disappear. Verification failed/stuck.[/]")
                # We check for the error dialog again just in case the app is showing a static error
                if is_error_dialog_present(driver, timeout=10):
                    console.print("    ❌ [bold red]Verification FAILED via known error dialog during wait.[/]")
                return purchased_number_obj, False , driver
            
            
            
                        # ---------------------------------------------------------------------
            # 🔥 NEW LOGIC: Handle the optional Biometric/Fingerprint system dialog
            # ---------------------------------------------------------------------
            BIOMETRIC_ICON_LOCATOR = (AppiumBy.ID, "com.android.systemui:id/biometric_icon")
            
            try:
                # Wait for a short time (e.g., 5 seconds) to see if the dialog appears.
                console.print("  ⏳ [yellow]Checking for optional biometric dialog...[/]")
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(BIOMETRIC_ICON_LOCATOR)
                )
                # If the wait succeeds, the element was found!
                console.print("  ✅ [green]Biometric dialog detected. Pressing 'Back' to dismiss it.[/]")
                driver.back() # This simulates pressing the phone's back button
                time.sleep(1) # Small pause for UI to settle after dismissing dialog

            except TimeoutException:
                # This is the normal case if the dialog does NOT appear. We do nothing.
                console.print("  ℹ️ [blue]Biometric dialog did not appear. Continuing.[/]")
                pass
            except Exception as e:
                console.print(f"  ❌ [red]An unexpected error occurred checking for biometric dialog: {e}[/]")
            


            
            # -----------------------------------------------------------
            # Now proceed to check the resulting screen state (your original function)
            # -----------------------------------------------------------
            try:
                wait_for_post_otp_screen(driver, timeout=15) # Reduced timeout as most of the wait was in the previous step.
                console.print("    ✅ [green]Post-OTP screen state detected. Proceeding to dialogs.[/]")
                
            except TimeoutException:
                console.print("    ❌ [bold red]Appium: Failed to find Name/Permission screen after OTP disappeared![/]")
                return purchased_number_obj, False , driver
                
        else:
            console.print("    ❌ [bold red]Appium: Could not find OTP input field after purchase/SMS.[/]")
            return purchased_number_obj, False , driver

        # -------- 7. POST-OTP STEPS: PERMISSIONS & NAME --------
        name_screen_reached = check_and_handle_post_otp_steps(driver, POST_OTP_STEPS)

        if not name_screen_reached:
            console.print("  ❌ [red]Post-OTP handling failed to reach the Name Input screen.[/]")
            return purchased_number_obj, False , driver
            
        # --- Name Input and Next ---
        console.print("  ✍️ [cyan]Appium: Entering Name and proceeding...[/]")
        name_field = wait_for_element(
            driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.whatsapp:id/registration_name").className("android.widget.EditText")', timeout=5
        )
        
        next_button_name = wait_for_element(
            driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.whatsapp:id/register_name_accept").textContains("ቀጣይ")', timeout=5
        )

        if name_field and next_button_name:
            name_field.clear()
            name_field.send_keys("Katie eve")
            next_button_name.click()
            console.print("    ✅ [green]Name entered and 'Next' clicked.[/]")
            time.sleep(5) 
        else:
            console.print("    ❌ [red]Name input field or Next button not found after dialogs.[/]")
            return purchased_number_obj, False , driver


        # --- ✨ NEW: HANDLE OPTIONAL 'NOT NOW' BUTTON ✨ ---
        # This screen for "Add a passkey" or similar sometimes appears here.
        # We'll check for the "Not now" button for a few seconds and click it if it appears.
        console.print("  ⏳ [yellow]Checking for optional 'Not Now' screen...[/]")
        
        # The resource-id is the most robust locator from the data you sent
        not_now_locator = (AppiumBy.ID, "com.whatsapp:id/secondary_button")
        
        # We reuse our existing helper function to handle this optional step
        handle_optional_dialog(driver, not_now_locator, timeout=7)
        # --- END OF NEW LOGIC ---


        # --- FINAL VERIFICATION: Registration Success ---
        console.print("  🔎 [cyan]Appium: Final verification...[/]")
        success_element = wait_for_element(
            driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.whatsapp:id/menuitem_camera").className("android.widget.ImageButton")', timeout=20
        )
        
        if success_element:
            console.print("  🎉 [bold green]Appium: Initial Registration SUCCESS! Starting TG Flow.[/]")
            registration_success = True
            console.print("    ✅ [green]Waiting For The CoolDown...[/]")
            

            # --- 8. TELEGRAM OTP LOOP ---
            tg_client_instance = get_tg_client(console)
            
            if tg_client_instance:
                # Get the event loop and run the async TG function synchronously
                loop = asyncio.get_event_loop()
                
                try:
                    otp_code_from_tg = loop.run_until_complete(
                        telegram_flow_get_otp_and_reply(tg_client_instance, driver, full_phone_number_with_plus, console)
                    )
                except RuntimeError:
                    # Handle "Event loop is closed" if the script ran previously
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    otp_code_from_tg = loop.run_until_complete(
                        telegram_flow_get_otp_and_reply(tg_client_instance, driver, full_phone_number_with_plus, console)
                    )
                
                # Check the result of the Telegram flow
                if otp_code_from_tg:
                    console.print("  ⏳ [yellow]Appium: Waiting for Final Confirmation Button ('ይግቡ')...[/yellow]")
                    
                    # Wait for the confirmation button (the final step)
                    CONFIRM_BUTTON_LOCATOR = (AppiumBy.ID, "com.whatsapp:id/primary_button")
                    
                    try:
                        confirm_button = WebDriverWait(driver, 60).until(
                            EC.presence_of_element_located(CONFIRM_BUTTON_LOCATOR)
                        )
                        if confirm_button.text == "ይግቡ": # Text is Amharic for 'Log In' / 'Enter'
                            confirm_button.click()
                            console.print("  ✅ [green]Clicked 'ይግቡ' to complete login.[/green]")
                        else:
                            console.print("[red]❌ Found primary button but text was incorrect. Exiting flow.[/red]")
                            return purchased_number_obj, False , driver
                            
                    except TimeoutException:
                        console.print("[red]❌ Timed out waiting for final 'ይግቡ' confirmation button. Exiting flow.[/red]")
                        return purchased_number_obj, False , driver

                    # --- FINAL CHECK FOR LANGUAGE PAGE (Cleanup) ---
                    
                    # Pause for UI stability after the final transition
                    console.print("  ⏸️ [dim]Waiting 3 seconds for Language Screen to load...[/dim]")
                    time.sleep(3) 

                    # CRITICAL VALIDATION: Check for the language picker element.
                    LANGUAGE_PICKER_ID = "com.whatsapp:id/language_picker"
                    AMHARIC_TEXT = "አማርኛ" # Amharic text for Amharic language selection
                    
                    picker_element = wait_for_element(
                        driver, AppiumBy.ID, LANGUAGE_PICKER_ID, timeout=10
                    )
                    
                    if picker_element:
                        # Success: We are on the start screen.
                        console.print("  🎉 [bold green]TG OTP Loop SUCCESS & Logged In! Back at reliable start screen.[/bold green]")
                        
                        # --- Language Correction Check ---
                        if picker_element.text != AMHARIC_TEXT:
                            # If the text is NOT Amharic, click the picker to open the list
                            picker_element.click()
                            time.sleep(1)
                            
                            # Find and click the Amharic option in the list
                            amharic_option = wait_for_element(
                                driver, AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{AMHARIC_TEXT}")', timeout=5
                            )
                            if amharic_option:
                                amharic_option.click()
                                console.print("  ✅ [green]Language corrected to Amharic for the next run.[/green]")
                            else:
                                console.print("  ⚠️ [yellow]Language picker opened, but Amharic option not found. Proceeding.[/yellow]")
                        else:
                            console.print("  ✅ [green]Language already set to Amharic. Proceeding.[/green]")
                            
                        # Now, we must click 'AGREE' to proceed to the number entry screen for the next cycle's *validation*.
                        # The code will naturally flow out of the Appium function, and the new run will start clean.
                        # For now, we only need to confirm successful navigation back to the start.
                        return purchased_number_obj, True , driver
                    else:
                        console.print("[red]❌ After final click, did not return to Language Selection screen. Exiting flow.[/red]")
                        return purchased_number_obj, False , driver
                else:
                    console.print("[red]❌ Telegram Flow FAILED (OTP not received/extracted). Exiting flow.[/red]")
                    return purchased_number_obj, False , driver
            else:
                 console.print("[red]❌ Telegram client not available. Skipping TG flow and failing this number.[/red]")
                 return purchased_number_obj, False , driver

        else:
            console.print("  ❌ [bold red]Appium: FINAL VERIFICATION FAILED (Camera button not found).[/bold red]")
            registration_success = False

        return purchased_number_obj, registration_success, driver # <--- MODIFIED RETURN
        
    except TwilioRestException as e:
        console.print(f"  💥 [bold red]Twilio Purchase Error during Appium flow: {e.msg}[/]")
        return purchased_number_obj, False, driver # <--- MODIFIED RETURN
    except Exception as e:
        console.print(f"  💥 [bold red]Appium/General Error: {e}[/]")
        return purchased_number_obj, False, driver # <--- MODIFIED RETURN


# --- Application Logic Functions ---
def handle_account_creation(state):
    """Handles the creation of new Twilio subaccounts using Selenium."""
    num_existing = len(state["subaccounts"])
    if num_existing >= MAX_ACCOUNTS:
        console.print(
            Panel(
                f"[bold red]Cannot create more accounts. You already have {num_existing}/{MAX_ACCOUNTS}.[/bold red]"
            )
        )
        return state

    accounts_to_create = Prompt.ask(
        f"You have {num_existing}/{MAX_ACCOUNTS} accounts. How many more to create?",
        choices=[str(i) for i in range(1, MAX_ACCOUNTS - num_existing + 1)], # Correct range
        default="1",
    )
    num_to_create = int(accounts_to_create)
    
    # --- NO MORE SELENIUM OR ADSPOWER! ---
    # The new function handles everything directly.
    console.print(Panel(f"🚀 Starting FAST creation of {num_to_create} account(s)...", border_style="magenta"))
    
    
    
    
    if num_existing + num_to_create > MAX_ACCOUNTS:
        # Should be caught by choices but included for safety
        console.print(
            f"[red]This would exceed the max limit of {MAX_ACCOUNTS} accounts. Please choose a smaller number.[/red]"
        )
        return state


    for i in range(num_to_create):
        account_name = f"Sub-{num_existing + i + 1}"
        new_account = create_twilio_subaccount(account_name)
        
        if new_account:
            state["subaccounts"].append(new_account)
            save_state_to_redis(state)
        else:
            console.print(
                f"[bold red]Failed to create {account_name}. Stopping creation process.[/bold red]"
            )
            break
            
    
    return state


def handle_account_merge(state):
    """Allows manual merging of existing Twilio credentials into the state."""
    console.print(
        Panel(
            "[bold yellow]Merge Existing Accounts[/bold yellow]\nYou can manually add accounts to the state if they were created outside this script."
        )
    )
    
    num_existing = len(state["subaccounts"])
    remaining_slots = MAX_ACCOUNTS - num_existing
    
    if remaining_slots <= 0:
        console.print(Panel(f"[bold red]Cannot merge. You are already at the limit of {MAX_ACCOUNTS} accounts.[/bold red]"))
        return state
        
    num_to_merge_str = Prompt.ask(
        f"How many accounts to merge? (Max: {remaining_slots})", default="1"
    )
    
    try:
        num_to_merge = int(num_to_merge_str)
        if not 1 <= num_to_merge <= remaining_slots:
            console.print(
                f"[red]Please enter a number between 1 and {remaining_slots}.[/red]"
            )
            return state
    except ValueError:
        console.print("[red]Invalid number.[/red]")
        return state

    console.print(
        "Please provide credentials in this format: [bold cyan]SID:AUTHTOKEN|SID:AUTHTOKEN[/bold cyan]"
    )
    creds_input = Prompt.ask("Paste credentials string")
    
    try:
        pairs = creds_input.strip().split('|')
        if len(pairs) != num_to_merge:
            console.print(
                f"[red]Error: Expected {num_to_merge} credential pairs, but found {len(pairs)}.[/red]"
            )
            return state
            
        for i, pair in enumerate(pairs):
            if ':' not in pair:
                 raise ValueError("Missing ':' separator.")
            sid, token = pair.split(':')
            
            # Simple validation check
            if not (sid.startswith("AC") and len(sid) == 34 and len(token) == 32):
                raise ValueError(f"Invalid format for pair {i + 1}")
                
            new_account = {
                "sid": sid,
                "token": token,
                "name": f"Sub-{num_existing + i + 1}",
                "status": "active",
            }
            state["subaccounts"].append(new_account)
            
        console.print(f"[green]Successfully parsed and added {num_to_merge} accounts.[/green]")
        save_state_to_redis(state)
        
    except Exception as e:
        console.print(
            f"[bold red]Failed to parse credentials: {e}. Please check the format.[/bold red]"
        )
        
    return state


def check_balance(client):
    """Checks if the account balance is sufficient for a single purchase."""
    try:
        # Note: The Twilio Balance API is not available on free trial accounts.
        # It's good practice to fetch it, but it often fails without a paid account.
        balance_data = client.balance.fetch()
        balance = float(balance_data.balance)
        currency = balance_data.currency
        
        # Check if balance is greater than the price of one number
        is_sufficient = balance > NUMBER_PRICE
        
        console.print(
            f"Balance: {balance:.2f} {currency} | Required: > {NUMBER_PRICE:.2f} | Sufficient: {is_sufficient}"
        )
        
        return is_sufficient
        
    except TwilioRestException as e:
        # Check for error code 20003 (Authentication failure / trial account limitation)
        if e.status == 404:
             console.print("[yellow]Twilio API: Balance endpoint not found (common on some subaccounts/plans). Assuming sufficient for flow continuation.[/yellow]")
             return True
        elif e.status == 401:
             console.print("[red]Twilio API Error 401: Authentication/Permission denied. Check credentials or trial limits.[/red]")
             return False
        else:
             console.print(f"[bold red]Twilio API Error {e.status} while fetching balance: {e.msg}[/bold red]")
             return False
    except Exception as e:
        console.print("[bold red]An unexpected error occurred while fetching the balance. Raw error below:[/bold red]")
        print(e)
        return False


# Assuming Rich's Table, Prompt, Panel, Client, TwilioRestException, check_balance, 
# and save_state_to_redis are imported and available.
# We also assume run_appium_registration (above) is available.



# --- NEW HELPER FUNCTION for IQ VALIDATION LOOP ---
def is_error_dialog_present(driver, timeout=10):
    """
    Checks for the WhatsApp number validation error dialog (the 'እሺ' button)
    using a short, explicit wait.
    Returns the element if found, or None if the timeout is exceeded.
    """
    try:
        error_button = wait_for_element(
            driver,
            AppiumBy.ID,
            "android:id/button1", # Resource ID for the 'OK' button in the dialog
            timeout=timeout
        )
        if error_button and error_button.text == "እሺ":
            console.print("    ❌ [bold red]IQ Check FAILED: Error Dialog ('እሺ') found![/]")
            return error_button
        return None
    except Exception:
        # Expected to fail (Time out) if the element is NOT present
        return None

# --- NEW HELPER FUNCTION for NUMBER FETCHING (must be provided from Twilio flow) ---
def fetch_new_available_number(client, area_code, prefix_digits):
    """
    Fetches the next available number from the Twilio API based on the original criteria.
    This function should be defined/imported from your Twilio handling logic.
    """
    try:
        numbers = client.available_phone_numbers('CA').local.list(
            area_code=area_code,
            contains=prefix_digits,
            limit=1 
        )
        if numbers:
            return numbers[0]
        return None
    except Exception as e:
        console.print(f"    💥 [bold red]Twilio Fetch Error: {e}[/]")
        return None



def purchase_numbers_flow(state):
    """
    Core function to find, *trigger purchase within Appium*, and manage number lifecycle.
    Implements selection for Short Form (Direct Buy) or Long Form (IQ Check).
    """
    driver = None
    render_url = Prompt.ask(
        "\n[bold magenta]Enter your permanent Render URL (e.g., https://my-app.onrender.com):[/bold magenta]"
    )

    active_accounts = [acc for acc in state["subaccounts"] if acc["status"] == "active"]
    if not active_accounts:
        console.print(Panel("[bold yellow]No active accounts available to purchase numbers.[/bold yellow]"))
        return state

    # --- NEW MODE SELECTION ---
    mode = Prompt.ask(
        "Choose Registration Mode",
        choices=["short", "long"],
        default="long",
    ).lower()
    is_short_form = (mode == "short")
    
    console.print(f"[cyan]Selected Mode:[/cyan] [bold magenta]{'Short Process (Buy First)' if is_short_form else 'Long Process (IQ Check First)'}[/bold magenta]")
    # --- END MODE SELECTION ---


        # --- CRITERIA PROMPT (FIXED SCOPE) ---
    area_code_input = Prompt.ask("Enter US area code (or area code + prefix) for ALL continuous runs")
    area_code = area_code_input[:3]
    prefix_digits = area_code_input[3:] or None
    # --- END CRITERIA PROMPT ---
    
    max_regs = state.get("max_regs_per_acc", 3)



    console.print(f"Starting number purchase flow with {len(active_accounts)} active account(s).")

    for i, account in enumerate(state["subaccounts"]):
        if account["status"] != "active":
            continue

        console.print(
            Panel(
                f"Activating account: [bold cyan]{account['name']} ({account['sid']})[/bold cyan]"
            )
        )

        
        client = Client(account["sid"], account["token"])
        
        max_regs = state.get("max_regs_per_acc", 3)
        
        # LOOP: Runs continuously until 3/3 is hit OR failure
        while check_balance(client) and account.get("registrations_done", 0) < max_regs:
            
            purchased_number_obj = None
            registration_success = False

            try:
                # 2. RUN APPIUM REGISTRATION (SEAMLESS FLOW)
                current_reg_count = account.get('registrations_done', 0) + 1
                console.print(Panel(f"🚀 Starting Reg {current_reg_count}/{max_regs} for {account['name']}...", border_style="magenta"))

                # CRITICAL: NO MANUAL PROMPTS HERE!
                purchased_number_obj, registration_success, driver = run_appium_registration(
                    client, render_url, area_code, prefix_digits, driver=driver, is_short_form=is_short_form 
                )
                
                # 3. POST-APPIUM STATUS CHECK & STATE UPDATE
                if purchased_number_obj and registration_success:
                    console.print(Panel("✅ [bold green]Appium registration Mission: SUCCESS[/]"))
                    account["registrations_done"] = account.get("registrations_done", 0) + 1
                    console.print(f" 📊 [magenta]Account {account['name']} count: {account['registrations_done']}/{max_regs}[/magenta]")
                    save_state_to_redis(state)
                    # Loop continues automatically (no prompt)
                    
                elif purchased_number_obj and not registration_success:
                    console.print(Panel("❌ [bold red]Appium registration Mission: FAILED (Number was purchased).[/bold red]"))
                    # Loop continues automatically to try next available number
                else:
                    # IQ check loop failed to find a valid number, or other pre-purchase failure.
                    console.print(Panel("🛑 [bold yellow]Pre-purchase validation failed. Moving to next account/Breaking loop.[/bold yellow]"))
                    break # Break the inner while loop

            except Exception as e:
                console.print(f"💥 [bold red]Critical error in purchase flow: {e}[/bold red]")
                break 

            finally:
                # 6. ALWAYS DELETE/RELEASE IF A NUMBER WAS PURCHASED. (Logic remains the same)
                if purchased_number_obj and purchased_number_obj.sid:
                    try:
                        client.incoming_phone_numbers(purchased_number_obj.sid).delete()
                        console.print(f" ✅ [green]Number successfully deleted from Twilio.[/green]")
                    except Exception as e_del:
                        console.print(f" 💥 [bold red]CRITICAL: Failed to delete number: {e_del}[/bold red]")

            # Check if max registrations were hit inside the inner loop
            if account.get("registrations_done", 0) >= max_regs:
                console.print(f"[yellow]Account {account['name']} hit its registration limit ({max_regs}). Moving to next account.[/yellow]")
                break

            # Check if balance runs out
            if not check_balance(client):
                 break
            
    console.print(Panel("[bold green]Number purchasing flow complete for all active accounts.[/bold green]"))
    return state


def main():
    state = load_state_from_redis()
    
    if state is None:
        console.print(Panel("[bold red]Failed to load state. Exiting.[/bold red]"))
        return

    console.print(Panel.fit("🔥 Twilio + Selenium + Appium Manager 🔥"))
    num_accounts = len(state["subaccounts"])
    console.print(f"Cloud state loaded. Found [bold cyan]{num_accounts}[/bold cyan] accounts.")

    # Initial check for account creation
    if num_accounts < MAX_ACCOUNTS:
        if Confirm.ask(
            f"You have {num_accounts}/{MAX_ACCOUNTS} accounts. Do you want to create more now?"
        ):
            state = handle_account_creation(state)
        else:
            console.print("Skipping initial account creation.")
            
    
    

    while True:
        active_accounts = [acc for acc in state["subaccounts"] if acc["status"] == "active"]
        
        if not active_accounts:
            console.print(
                Panel(
                    "[bold red]No active accounts found![/bold red]\nYou must create or merge accounts to proceed."
                )
            )
            # Simplified menu when no active accounts are present
            choice = Prompt.ask(
                "Choose an action", choices=["c", "m", "v", "e"], default="e"
            ).lower()
            
            if choice == 'c':
                state = handle_account_creation(state)
                continue
            elif choice == 'm':
                state = handle_account_merge(state)
                continue
            elif choice == 'v':
                 console.print(Panel(json.dumps(state, indent=2), title="Current Redis State"))
                 continue
            else:
                break

        # Main operational menu
        console.print(
            Panel(
                "[bold]Main Menu[/bold]\n\n1. [C]ommence Number Purchasing & Appium Flow\n2. [M]anage Accounts (Create/Merge)\n3. [V]iew State\n4. [E]xit"
            )
        )
        choice = Prompt.ask("Select an option", choices=["c", "m", "v", "e"], default="e").lower()

        if choice == 'c':
            state = purchase_numbers_flow(state)
        elif choice == 'm':
            sub_choice = Prompt.ask(
                "Manage Accounts: [C]reate new or [M]erge existing?",
                choices=["c", "m"],
                default="c",
            ).lower()
            if sub_choice == 'c':
                state = handle_account_creation(state)
            else:
                state = handle_account_merge(state)
        elif choice == 'v':
            console.print(Panel(json.dumps(state, indent=2), title="Current Redis State"))
        elif choice == 'e':
            break

    console.print("\n[bold]Goodbye![/bold]")


# Assuming get_tg_client(console) is accessible globally
def main_sync_runner():
    """Wrapper to handle the synchronous running of the main function and init Telegram."""
    
    # Initialize the Telegram client once before starting the main application loop
    get_tg_client(console) 
    
    # Now call the original synchronous main function
    main()

if __name__ == "__main__":
    try:
        main_sync_runner()
    except KeyboardInterrupt:
        console.print("\n[bold]Exiting gracefully...[/bold]")
    except Exception as e:
        console.print(f"\n[bold red]FATAL ERROR: {e}[/bold red]")
