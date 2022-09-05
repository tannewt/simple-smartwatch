import analogio
import board
import digitalio
import displayio
import gc
import pwmio
import rtc
import terminalio
import time
import vectorio
import adafruit_ble
from adafruit_ble.services.standard import CurrentTimeService
import adafruit_ble_apple_notification_center as ancs
import adafruit_display_text
from adafruit_display_text import label
from adafruit_bitmap_font import bitmap_font

APP_PRIORITY = {
    "com.apple.MobileSMS": 10,
    "com.flexibits.fantastical2.iphone": 2,
    "com.fastmail.FastMail": 1,
    "com.google.Gmail": 1,
}

class TimeAndNotification(displayio.Group):
    def __init__(self):
        super().__init__()
        self.known_notifications = set()
        self.rtc = rtc.RTC()
        self.last_rtc_update = None
        self.last_battery_update = None

        self.was_connected = False
        
        self.vibrate = digitalio.DigitalInOut(board.VIBRATE)
        self.vibrate.switch_to_output(False, digitalio.DriveMode.PUSH_PULL)

        self.font64 = bitmap_font.load_font("share_tech_64.bdf")
        self.font64.load_glyphs("0123456789:")
        self.font32 = bitmap_font.load_font("share_tech_32.bdf")
        self.font16 = terminalio.FONT

        self.white = displayio.Palette(1)
        self.white[0] = 0xffffff

        self.bg = vectorio.Rectangle(pixel_shader=self.white, width=1000, height=1000)
        self.append(self.bg)

        self.time_label = label.Label(self.font64, text="00:00", color=0x000000)
        self.time_label.y = self.time_label.height // 2
        self.append(self.time_label)

        self.title_label = label.Label(self.font32, text="Starting", color=0x000000)
        self.title_label.y = self.time_label.height + self.title_label.height // 2
        self.append(self.title_label)

        self.body_label = label.Label(self.font16, text="Just started code.py", color=0x000000)
        self.body_label.y = self.time_label.height + self.title_label.height + self.body_label.height // 2
        self.append(self.body_label)
        
        self.battery = analogio.AnalogIn(board.VOLTAGE_MONITOR)
        self.charging = digitalio.DigitalInOut(board.CHARGE_PORT)
        self.charging.pull = digitalio.Pull.UP
        self.charged = digitalio.DigitalInOut(board.CHARGE_COMPLETE)
        self.charged.pull = digitalio.Pull.DOWN
        
        self.battery_label = label.Label(self.font16, text="0.00v", color=0xff0000)
        self.battery_label.x = board.DISPLAY.width - self.battery_label.width
        self.battery_label.y = board.DISPLAY.height - self.battery_label.height // 2
        self.append(self.battery_label)
        
        self.mem_label = label.Label(self.font16, text="00000", color=0xff00ff)
        self.mem_label.y = self.battery_label.y
        self.append(self.mem_label)

        self.current_notification = None
        self.displayed_notification = 0

    def set_notification(self, title, body, app=""):
        if title is None:
            title = ""
        if body is None:
            body = ""
        self.title_label.text = title
        wrapped = adafruit_display_text.wrap_text_to_pixels(body, board.DISPLAY.width, self.font16)[:4]
        if len(wrapped) < 4:
            wrapped.append(app)
        self.body_label.text = "\n".join(wrapped)

    def notification_priority(self, n):
        if n.app_id == "com.apple.MobileSMS" and n.title == "Becca Minich":
            return 1000
        if n.app_id in APP_PRIORITY:
            return APP_PRIORITY[n.app_id]
        if n.important:
            return 1
        return 0

    def update(self, connection):
        if connection and (not self.last_rtc_update or self.last_rtc_update - time.time() > 60 * 60):
            cts = connection[CurrentTimeService]
            self.rtc.datetime = cts.current_time
            self.last_rtc_update = time.time()
        year, month, day, hour, minute, second, _, _, _ = self.rtc.datetime
        h12 = hour
        if hour > 12:
            h12 -= 12
        space = ""
        if h12 < 10:
            space = " "
        self.time_label.text = f"{space}{h12:d}:{minute:02d}"
        
        if self.charged.value:
            self.battery_label.text = " 100%"
        elif not self.charging.value:
            self.battery_label.text = "  chg"
        elif not self.last_battery_update or time.time() - self.last_battery_update > 60 * 5:
            v = self.battery.value * self.battery.reference_voltage * 4 / 65536
            self.battery_label.text = f"{v:.2f}v"
            gc.collect()
            mem = gc.mem_free()
            self.mem_label.text = f"{mem:d}"
            self.last_battery_update = time.time()

        if not connection:
            if self.was_connected:
                self.set_notification("Disconnected", f"{hour:d}:{minute:02d}.")
                self.was_connected = False
                self.displayed_notification = 0
            if self.known_notifications:
                self.known_notifications = set()
            return
        else:
            self.was_connected = True

        ans = connection[ancs.AppleNotificationCenterService]
        if self.current_notification and self.current_notification.removed:
            self.current_notification = None
        for notification_id in ans.active_notifications:
            notification = ans.active_notifications[notification_id]
            if not self.current_notification or notification_id not in self.known_notifications:
                self.vibrate.value = not notification.silent
                # print(notification.app_id, notification)
                priority = self.notification_priority(notification)
                if priority >= 0 and (not self.current_notification or priority >= self.current_notification.priority):
                    notification.priority = priority
                    self.current_notification = notification

                self.known_notifications.add(notification_id)
                self.vibrate.value = False

        if self.displayed_notification != self.current_notification:
            if self.current_notification:
                self.set_notification(self.current_notification.title, self.current_notification.message, self.current_notification.app_id)
            else:
                self.set_notification("", "")
            self.displayed_notification = self.current_notification

tn = TimeAndNotification()
board.DISPLAY.show(tn)

button = digitalio.DigitalInOut(board.BUTTON)
button.switch_to_input(pull=digitalio.Pull.UP)
backlight = None

gps_power = digitalio.DigitalInOut(board.GPS_POWER)
gps_power.switch_to_output()

hrm_power = digitalio.DigitalInOut(board.HRM_POWER)
hrm_power.switch_to_output()

# Wrap the update function in a connection management loop.
radio = adafruit_ble.BLERadio()
while True:
    if not button.value:
        if backlight is None:
            backlight = pwmio.PWMOut(board.BACKLIGHT, duty_cycle=2 ** 8)
    elif backlight is not None:
        backlight.deinit()
        backlight = None
    if radio.connected:
        for connection in radio.connections:
            if not connection.paired:
                connection.pair()
                print("paired")
            try:
                tn.update(connection)
                time.sleep(0.25)
            except ConnectionError:
                pass
            break
    else:
        tn.update(None)
        time.sleep(0.25)
